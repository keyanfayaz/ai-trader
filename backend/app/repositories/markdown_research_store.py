from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any


_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_META_PREFIX = "<!-- RESEARCH_META "
_META_SUFFIX = " -->"


@dataclass(slots=True)
class ResearchRecord:
    created_at: str
    source: str
    symbols: list[str]
    ticker: str
    timeframe: str
    risk_profile: str
    strategy_fingerprint: str
    signal_context: list[dict[str, Any]]
    strategy: dict[str, Any]
    evaluation: dict[str, Any]
    research_scope: str = "generation"


class MarkdownResearchStore:
    """File-backed markdown research history with age-based retention."""

    def __init__(self, base_dir: str, retention_days: int = 14) -> None:
        self.base_dir = Path(base_dir)
        self.retention_days = max(int(retention_days), 1)
        self._lock = Lock()

    def append_record(self, record: ResearchRecord) -> dict[str, Any]:
        with self._lock:
            now = _parse_iso_datetime(record.created_at)
            self._prune_old_entries_unlocked(now=now)
            scope = _normalize_scope(record.research_scope)
            date_dir = self.base_dir / scope / now.date().isoformat()
            date_dir.mkdir(parents=True, exist_ok=True)
            safe_ticker = _safe_fragment(record.ticker)
            safe_timeframe = _safe_fragment(record.timeframe)
            file_name = f"{now.strftime('%Y%m%dT%H%M%SZ')}_{safe_ticker}_{safe_timeframe}.md"
            file_path = date_dir / file_name
            file_path.write_text(_to_markdown(record), encoding="utf-8")
            return {
                "created_at": record.created_at,
                "file_path": str(file_path),
                "strategy_fingerprint": record.strategy_fingerprint,
                "ticker": record.ticker,
                "timeframe": record.timeframe,
                "symbols": record.symbols,
                "research_scope": scope,
            }

    def list_recent_records(
        self,
        symbols: list[str] | None = None,
        timeframe: str | None = None,
        scope: str | None = None,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            effective_now = now or datetime.now(UTC)
            self._prune_old_entries_unlocked(now=effective_now)
            symbols_filter = {symbol.upper() for symbol in (symbols or []) if str(symbol).strip()}
            timeframe_filter = str(timeframe or "").strip().lower()
            scope_filter = _normalize_optional_scope(scope)
            results: list[dict[str, Any]] = []
            for file_path in self._iter_recent_markdown_files_unlocked(now=effective_now, scope=scope_filter):
                metadata = _read_metadata(file_path)
                if metadata is None:
                    continue
                file_symbols = {str(symbol).upper() for symbol in metadata.get("symbols", [])}
                if symbols_filter and symbols_filter.isdisjoint(file_symbols):
                    continue
                file_timeframe = str(metadata.get("timeframe", "")).strip().lower()
                if timeframe_filter and file_timeframe != timeframe_filter:
                    continue
                metadata_scope = _normalize_optional_scope(metadata.get("research_scope")) or _scope_from_path(
                    file_path=file_path,
                    base_dir=self.base_dir.resolve(),
                )
                if scope_filter and metadata_scope != scope_filter:
                    continue
                results.append(
                    {
                        "created_at": str(metadata.get("created_at", "")),
                        "file_path": str(file_path),
                        "strategy_fingerprint": str(metadata.get("strategy_fingerprint", "")),
                        "ticker": str(metadata.get("ticker", "")).upper(),
                        "timeframe": str(metadata.get("timeframe", "")),
                        "risk_profile": str(metadata.get("risk_profile", "")),
                        "symbols": [str(symbol).upper() for symbol in metadata.get("symbols", [])],
                        "source": str(metadata.get("source", "unknown")),
                        "research_scope": metadata_scope,
                    }
                )
            return sorted(results, key=lambda item: item.get("created_at", ""), reverse=True)

    def find_latest_record(
        self,
        ticker: str,
        timeframe: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_ticker = str(ticker).upper().strip()
        if not normalized_ticker:
            return None
        recent = self.list_recent_records(
            symbols=[normalized_ticker],
            timeframe=timeframe,
            scope=scope,
        )
        for record in recent:
            if str(record.get("ticker", "")).upper() == normalized_ticker:
                return record
        return None

    def list_record_tree(self, now: datetime | None = None) -> list[dict[str, Any]]:
        with self._lock:
            effective_now = now or datetime.now(UTC)
            self._prune_old_entries_unlocked(now=effective_now)
            grouped: dict[str, list[dict[str, Any]]] = {}
            base_resolved = self.base_dir.resolve()
            for file_path in self._iter_recent_markdown_files_unlocked(now=effective_now):
                metadata = _read_metadata(file_path) or {}
                content = _read_text(file_path)
                if content is None:
                    continue
                scope = _normalize_optional_scope(metadata.get("research_scope")) or _scope_from_path(
                    file_path=file_path,
                    base_dir=base_resolved,
                )
                date_folder = file_path.parent.name
                group_folder = f"{scope}/{date_folder}" if scope else date_folder
                grouped.setdefault(group_folder, []).append(
                    {
                        "id": _encode_record_id(file_path.relative_to(base_resolved).as_posix()),
                        "file_name": file_path.name,
                        "created_at": str(metadata.get("created_at", "")),
                        "ticker": str(metadata.get("ticker", "")).upper(),
                        "timeframe": str(metadata.get("timeframe", "")),
                        "symbols": [str(symbol).upper() for symbol in metadata.get("symbols", [])],
                        "source": str(metadata.get("source", "unknown")),
                        "research_scope": scope,
                        "version": _content_version(content),
                    }
                )
            folders: list[dict[str, Any]] = []
            for folder in sorted(grouped.keys(), reverse=True):
                records = sorted(
                    grouped[folder],
                    key=lambda item: (item.get("created_at", ""), item.get("file_name", "")),
                    reverse=True,
                )
                folders.append({"folder": folder, "records": records})
            return folders

    def get_record(self, record_id: str) -> dict[str, Any]:
        with self._lock:
            return self._read_record_unlocked(record_id=record_id)

    def update_record(
        self,
        record_id: str,
        markdown: str,
        expected_version: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            file_path = self._resolve_record_path(record_id)
            current_text = _read_text(file_path)
            if current_text is None:
                raise FileNotFoundError("Research record not found.")
            current_version = _content_version(current_text)
            if expected_version and expected_version != current_version:
                raise ValueError("Research record has changed since it was loaded.")

            metadata_line = _metadata_line(current_text)
            normalized = _normalize_markdown(markdown=markdown, metadata_line=metadata_line)
            file_path.write_text(normalized, encoding="utf-8")
            return self._read_record_unlocked(record_id=record_id)

    def delete_record(self, record_id: str) -> dict[str, Any]:
        with self._lock:
            file_path = self._resolve_record_path(record_id)
            if not file_path.exists():
                raise FileNotFoundError("Research record not found.")
            metadata = _read_metadata(file_path) or {}
            self._delete_file_and_empty_parent_unlocked(file_path=file_path)
            return {
                "record_id": record_id,
                "relative_path": file_path.relative_to(self.base_dir.resolve()).as_posix(),
                "ticker": str(metadata.get("ticker", "")).upper(),
                "deleted": True,
            }

    def delete_records(self, record_ids: list[str]) -> dict[str, Any]:
        with self._lock:
            if not record_ids:
                raise ValueError("record_ids must include at least one item.")
            deleted_ids: list[str] = []
            missing_ids: list[str] = []
            for record_id in record_ids:
                try:
                    file_path = self._resolve_record_path(record_id)
                except ValueError:
                    missing_ids.append(record_id)
                    continue
                if not file_path.exists():
                    missing_ids.append(record_id)
                    continue
                self._delete_file_and_empty_parent_unlocked(file_path=file_path)
                deleted_ids.append(record_id)
            return {
                "deleted_count": len(deleted_ids),
                "deleted_ids": deleted_ids,
                "missing_ids": missing_ids,
            }

    def delete_folder(self, folder: str) -> dict[str, Any]:
        with self._lock:
            normalized = str(folder).strip()
            parts = [part for part in normalized.split("/") if part]
            if len(parts) == 1 and _DATE_DIR_RE.match(parts[0]):
                folder_path = (self.base_dir / parts[0]).resolve()
            elif len(parts) == 2 and _SCOPE_RE.match(parts[0]) and _DATE_DIR_RE.match(parts[1]):
                folder_path = (self.base_dir / parts[0] / parts[1]).resolve()
            else:
                raise ValueError("Invalid research folder.")
            base_resolved = self.base_dir.resolve()
            try:
                folder_path.relative_to(base_resolved)
            except ValueError as exc:
                raise ValueError("Invalid research folder.") from exc
            if not folder_path.exists() or not folder_path.is_dir():
                raise FileNotFoundError("Research folder not found.")
            deleted_ids = [
                _encode_record_id(candidate.relative_to(base_resolved).as_posix())
                for candidate in folder_path.iterdir()
                if candidate.is_file() and candidate.suffix.lower() == ".md"
            ]
            shutil.rmtree(folder_path, ignore_errors=True)
            parent = folder_path.parent
            if parent != base_resolved and parent.exists():
                try:
                    next(parent.iterdir())
                except StopIteration:
                    parent.rmdir()
                except OSError:
                    pass
            return {
                "folder": normalized,
                "deleted_count": len(deleted_ids),
                "deleted_ids": deleted_ids,
            }

    def prune_old_entries(self, now: datetime | None = None) -> None:
        with self._lock:
            self._prune_old_entries_unlocked(now=now or datetime.now(UTC))

    def _read_record_unlocked(self, record_id: str) -> dict[str, Any]:
        file_path = self._resolve_record_path(record_id)
        content = _read_text(file_path)
        if content is None:
            raise FileNotFoundError("Research record not found.")
        metadata = _read_metadata(file_path) or {}
        stat = file_path.stat()
        updated_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
        return {
            "id": record_id,
            "file_name": file_path.name,
            "date_folder": file_path.parent.name,
            "relative_path": file_path.relative_to(self.base_dir.resolve()).as_posix(),
            "metadata": metadata,
            "markdown": content,
            "version": _content_version(content),
            "updated_at": updated_at,
            "research_scope": str(metadata.get("research_scope") or _scope_from_path(file_path, self.base_dir.resolve())),
        }

    def _resolve_record_path(self, record_id: str) -> Path:
        relative_raw = _decode_record_id(record_id)
        relative = Path(relative_raw)
        if relative.is_absolute():
            raise ValueError("Invalid research record id.")
        candidate = (self.base_dir / relative).resolve()
        base_resolved = self.base_dir.resolve()
        try:
            candidate.relative_to(base_resolved)
        except ValueError as exc:
            raise ValueError("Invalid research record id.") from exc
        if candidate.suffix.lower() != ".md":
            raise ValueError("Invalid research record id.")
        return candidate

    def _iter_recent_markdown_files_unlocked(self, now: datetime, scope: str | None = None) -> list[Path]:
        results: list[Path] = []
        cutoff_date = self._cutoff_date(now=now)
        if not self.base_dir.exists():
            return results
        for date_dir in self._iter_scope_date_dirs_unlocked(scope=scope):
            if not date_dir.is_dir() or not _DATE_DIR_RE.match(date_dir.name):
                continue
            folder_date = _parse_date_dir_name(date_dir.name)
            if folder_date is None or folder_date < cutoff_date:
                continue
            for candidate in date_dir.iterdir():
                if candidate.is_file() and candidate.suffix.lower() == ".md":
                    results.append(candidate)
        return results

    def _prune_old_entries_unlocked(self, now: datetime) -> None:
        if not self.base_dir.exists():
            return
        cutoff_date = self._cutoff_date(now=now)
        for date_dir in self._iter_scope_date_dirs_unlocked():
            if not date_dir.is_dir() or not _DATE_DIR_RE.match(date_dir.name):
                continue
            folder_date = _parse_date_dir_name(date_dir.name)
            if folder_date is not None and folder_date < cutoff_date:
                shutil.rmtree(date_dir, ignore_errors=True)
                parent_scope = date_dir.parent
                if parent_scope != self.base_dir and parent_scope.exists():
                    try:
                        next(parent_scope.iterdir())
                    except StopIteration:
                        parent_scope.rmdir()
                    except OSError:
                        pass

    def _cutoff_date(self, now: datetime) -> date:
        return (now - timedelta(days=self.retention_days)).date()

    def _delete_file_and_empty_parent_unlocked(self, file_path: Path) -> None:
        try:
            file_path.unlink()
        except OSError as exc:
            raise FileNotFoundError("Research record not found.") from exc
        parent = file_path.parent
        if parent.resolve() == self.base_dir.resolve():
            return
        if _DATE_DIR_RE.match(parent.name):
            try:
                next(parent.iterdir())
            except StopIteration:
                parent.rmdir()
            except OSError:
                pass
        scope_parent = parent.parent
        if scope_parent != self.base_dir:
            try:
                next(scope_parent.iterdir())
            except StopIteration:
                scope_parent.rmdir()
            except (OSError, StopIteration):
                pass

    def _iter_scope_date_dirs_unlocked(self, scope: str | None = None) -> list[Path]:
        if not self.base_dir.exists():
            return []
        if scope:
            scope_dir = self.base_dir / scope
            if not scope_dir.exists() or not scope_dir.is_dir():
                return []
            return [candidate for candidate in scope_dir.iterdir() if candidate.is_dir()]
        scope_dirs = [candidate for candidate in self.base_dir.iterdir() if candidate.is_dir()]
        date_dirs: list[Path] = []
        for scope_dir in scope_dirs:
            if _DATE_DIR_RE.match(scope_dir.name):
                date_dirs.append(scope_dir)
                continue
            if not _SCOPE_RE.match(scope_dir.name):
                continue
            for candidate in scope_dir.iterdir():
                if candidate.is_dir():
                    date_dirs.append(candidate)
        return date_dirs


def _to_markdown(record: ResearchRecord) -> str:
    research_scope = _normalize_scope(record.research_scope)
    metadata_hints = _strategy_metadata_hints(strategy=record.strategy, evaluation=record.evaluation)
    metadata = {
        "created_at": record.created_at,
        "source": record.source,
        "research_scope": research_scope,
        "symbols": [symbol.upper() for symbol in record.symbols],
        "ticker": record.ticker.upper(),
        "timeframe": record.timeframe,
        "risk_profile": record.risk_profile,
        "strategy_fingerprint": record.strategy_fingerprint,
        **metadata_hints,
    }
    sections = [
        f"{_META_PREFIX}{json.dumps(metadata, separators=(',', ':'))}{_META_SUFFIX}",
        "",
        "# Market Research Snapshot",
        "",
        "## Metadata",
        "",
        f"- Created At: `{record.created_at}`",
        f"- Source: `{record.source}`",
        f"- Research Scope: `{research_scope}`",
        f"- Symbols: `{', '.join(symbol.upper() for symbol in record.symbols)}`",
        f"- Ticker: `{record.ticker.upper()}`",
        f"- Timeframe: `{record.timeframe}`",
        f"- Risk Profile: `{record.risk_profile}`",
        f"- Strategy Fingerprint: `{record.strategy_fingerprint}`",
        f"- Event Focused: `{metadata_hints.get('event_focused', False)}`",
        f"- Notable Event Count: `{metadata_hints.get('notable_event_count', 0)}`",
        f"- Highest Event Risk: `{metadata_hints.get('highest_event_risk', 'unknown')}`",
        f"- Recommendation: `{metadata_hints.get('recommendation', 'unknown')}`",
        "",
        "## Signal Context",
        "",
        "```json",
        json.dumps(record.signal_context, indent=2, default=str),
        "```",
        "",
        "## Strategy",
        "",
        "```json",
        json.dumps(record.strategy, indent=2, default=str),
        "```",
        "",
        "## Evaluation",
        "",
        "```json",
        json.dumps(record.evaluation, indent=2, default=str),
        "```",
        "",
    ]
    return "\n".join(sections)


def _strategy_metadata_hints(strategy: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    picks = strategy.get("picks")
    if not isinstance(picks, list):
        picks = []
    notable_event_count = 0
    has_liquidity_signal = False
    highest_event_risk = "unknown"
    risk_rank = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    top_rank = 0
    for item in picks:
        if not isinstance(item, dict):
            continue
        events = item.get("notable_events")
        if isinstance(events, list):
            notable_event_count += len([event for event in events if str(event).strip()])
        if str(item.get("liquidity_signal", "")).strip():
            has_liquidity_signal = True
        item_event_risk = str(item.get("event_risk", "unknown")).strip().lower() or "unknown"
        rank = risk_rank.get(item_event_risk, 0)
        if rank > top_rank:
            top_rank = rank
            highest_event_risk = item_event_risk
    return {
        "event_focused": notable_event_count > 0 or has_liquidity_signal,
        "notable_event_count": notable_event_count,
        "highest_event_risk": highest_event_risk,
        "recommendation": str(evaluation.get("recommendation", "unknown")),
    }


def _read_metadata(file_path: Path) -> dict[str, Any] | None:
    try:
        first_line = file_path.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    if not (first_line.startswith(_META_PREFIX) and first_line.endswith(_META_SUFFIX)):
        return None
    payload = first_line[len(_META_PREFIX) : -len(_META_SUFFIX)]
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _read_text(file_path: Path) -> str | None:
    try:
        return file_path.read_text(encoding="utf-8")
    except OSError:
        return None


def _metadata_line(markdown: str) -> str | None:
    first_line = markdown.splitlines()[0].strip() if markdown else ""
    if first_line.startswith(_META_PREFIX) and first_line.endswith(_META_SUFFIX):
        return first_line
    return None


def _normalize_markdown(markdown: str, metadata_line: str | None) -> str:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if metadata_line is None:
        normalized = "\n".join(lines).strip("\n")
        return f"{normalized}\n" if normalized else ""
    if lines and lines[0].strip().startswith(_META_PREFIX) and lines[0].strip().endswith(_META_SUFFIX):
        lines = lines[1:]
    while lines and lines[0] == "":
        lines = lines[1:]
    body = "\n".join(lines)
    return f"{metadata_line}\n\n{body}\n" if body else f"{metadata_line}\n"


def _content_version(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _encode_record_id(relative_path: str) -> str:
    encoded = base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")


def _decode_record_id(record_id: str) -> str:
    if not record_id:
        raise ValueError("Invalid research record id.")
    padding = "=" * ((4 - len(record_id) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{record_id}{padding}").decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid research record id.") from exc
    if not decoded.strip() or "\x00" in decoded:
        raise ValueError("Invalid research record id.")
    return decoded


def _safe_fragment(value: str) -> str:
    clean = "".join(ch if ch.isalnum() else "_" for ch in value.strip().upper())
    return clean or "UNKNOWN"


def _normalize_scope(value: Any) -> str:
    scope = str(value or "").strip().lower()
    if not _SCOPE_RE.match(scope):
        raise ValueError("Invalid research scope.")
    return scope


def _normalize_optional_scope(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    return _normalize_scope(raw)


def _scope_from_path(file_path: Path, base_dir: Path) -> str:
    try:
        relative = file_path.resolve().relative_to(base_dir.resolve())
    except ValueError:
        return "unknown"
    parts = relative.parts
    if len(parts) >= 3 and _SCOPE_RE.match(parts[0]) and _DATE_DIR_RE.match(parts[1]):
        return parts[0]
    return "generation"


def _parse_date_dir_name(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_iso_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
