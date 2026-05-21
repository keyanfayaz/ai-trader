from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any
from uuid import uuid4

from app.services.skill_spec import SkillValidationError, parse_skill_md


MAX_SKILL_BYTES = 5 * 1024 * 1024
MAX_SKILL_FILES = 200
_SKILL_MD_NAME = "SKILL.md"
_SKILL_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class SkillNotFoundError(FileNotFoundError):
    pass


class SkillVersionConflictError(ValueError):
    pass


def _content_version(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_uploaded_files(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Sanitize relative paths, reject traversal, and strip a common top-level dir."""
    cleaned: list[tuple[str, bytes]] = []
    for raw_path, data in files:
        rel = PurePosixPath(str(raw_path).replace("\\", "/"))
        if rel.is_absolute() or any(part in ("..", "") for part in rel.parts):
            raise SkillValidationError(f"Unsafe path in upload: {raw_path!r}")
        if not rel.parts:
            continue
        cleaned.append((rel.as_posix(), data))

    if not cleaned:
        raise SkillValidationError("Upload contained no usable files.")

    skill_md_dirs = sorted(
        (PurePosixPath(path).parent for path, _ in cleaned if PurePosixPath(path).name == _SKILL_MD_NAME),
        key=lambda parent: len(parent.parts),
    )
    if not skill_md_dirs:
        raise SkillValidationError("Upload must include a SKILL.md file.")

    prefix = skill_md_dirs[0]
    prefix_str = prefix.as_posix()
    rebased: list[tuple[str, bytes]] = []
    for path, data in cleaned:
        pure = PurePosixPath(path)
        if prefix_str in ("", "."):
            rebased.append((pure.as_posix(), data))
            continue
        try:
            relative = pure.relative_to(prefix)
        except ValueError:
            continue
        rebased.append((relative.as_posix(), data))
    return rebased


def _extract_zip(zip_bytes: bytes) -> list[tuple[str, bytes]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise SkillValidationError("Uploaded file is not a valid zip archive.") from exc
    extracted: list[tuple[str, bytes]] = []
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in PurePosixPath(name).parts:
                raise SkillValidationError(f"Unsafe path in zip archive: {info.filename!r}")
            if "__MACOSX/" in name or PurePosixPath(name).name == ".DS_Store":
                continue
            extracted.append((name, archive.read(info)))
    if not extracted:
        raise SkillValidationError("Zip archive contained no files.")
    return extracted


class SkillStore:
    """File-backed store for Agent Skills bundles (SKILL.md + resources)."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)
        self._lock = Lock()

    # ---- creation -----------------------------------------------------------

    def create_from_editor(self, markdown: str, name: str | None = None) -> dict[str, Any]:
        parsed = parse_skill_md(markdown)
        with self._lock:
            return self._write_skill_unlocked(
                files=[(_SKILL_MD_NAME, markdown.encode("utf-8"))],
                parsed_name=parsed.name,
                description=parsed.description,
                source="editor",
            )

    def create_from_upload(
        self,
        files: list[tuple[str, bytes]] | None = None,
        zip_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        if zip_bytes is not None:
            collected = _extract_zip(zip_bytes)
        elif files:
            collected = list(files)
        else:
            raise SkillValidationError("No upload payload provided.")

        normalized = _normalize_uploaded_files(collected)
        if len(normalized) > MAX_SKILL_FILES:
            raise SkillValidationError(
                f"Skill bundle exceeds the {MAX_SKILL_FILES}-file limit."
            )
        total_bytes = sum(len(data) for _, data in normalized)
        if total_bytes > MAX_SKILL_BYTES:
            raise SkillValidationError(
                f"Skill bundle exceeds the {MAX_SKILL_BYTES // (1024 * 1024)} MiB size limit."
            )

        skill_md = next((data for path, data in normalized if path == _SKILL_MD_NAME), None)
        if skill_md is None:
            raise SkillValidationError("Upload must include a SKILL.md at the skill root.")
        parsed = parse_skill_md(skill_md.decode("utf-8", errors="replace"))

        with self._lock:
            return self._write_skill_unlocked(
                files=normalized,
                parsed_name=parsed.name,
                description=parsed.description,
                source="upload",
            )

    def _write_skill_unlocked(
        self,
        files: list[tuple[str, bytes]],
        parsed_name: str,
        description: str,
        source: str,
    ) -> dict[str, Any]:
        skill_id = str(uuid4())
        skill_dir = self.base_dir / skill_id
        bundle_dir = skill_dir / "bundle"
        bundle_dir.mkdir(parents=True, exist_ok=True)

        skill_md_bytes = b""
        for rel_path, data in files:
            target = (bundle_dir / rel_path).resolve()
            try:
                target.relative_to(bundle_dir.resolve())
            except ValueError as exc:
                shutil.rmtree(skill_dir, ignore_errors=True)
                raise SkillValidationError(f"Unsafe path in bundle: {rel_path!r}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if rel_path == _SKILL_MD_NAME:
                skill_md_bytes = data

        now = _now_iso()
        meta = {
            "id": skill_id,
            "name": parsed_name,
            "slug": parsed_name,
            "description": description,
            "source": source,
            "created_at": now,
            "updated_at": now,
            "version": _content_version(skill_md_bytes),
            "file_count": len(files),
            "total_bytes": sum(len(data) for _, data in files),
        }
        (skill_dir / "skill.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        return meta

    # ---- reads --------------------------------------------------------------

    def list_skills(self) -> list[dict[str, Any]]:
        if not self.base_dir.exists():
            return []
        skills: list[dict[str, Any]] = []
        for child in self.base_dir.iterdir():
            if not child.is_dir():
                continue
            meta = self._read_meta(child)
            if meta is not None:
                skills.append(meta)
        return sorted(skills, key=lambda item: item.get("created_at", ""), reverse=True)

    def get_skill(self, skill_id: str) -> dict[str, Any]:
        skill_dir = self._resolve_skill_dir(skill_id)
        meta = self._read_meta(skill_dir)
        if meta is None:
            raise SkillNotFoundError("Skill not found.")
        bundle_dir = skill_dir / "bundle"
        skill_md_path = bundle_dir / _SKILL_MD_NAME
        markdown = (
            skill_md_path.read_text(encoding="utf-8", errors="replace")
            if skill_md_path.exists()
            else ""
        )
        files: list[dict[str, Any]] = []
        if bundle_dir.exists():
            for path in sorted(bundle_dir.rglob("*")):
                if path.is_file():
                    files.append(
                        {
                            "path": path.relative_to(bundle_dir).as_posix(),
                            "bytes": path.stat().st_size,
                        }
                    )
        return {**meta, "markdown": markdown, "files": files}

    def read_bundle_file(self, skill_id: str, rel_path: str) -> str:
        skill_dir = self._resolve_skill_dir(skill_id)
        bundle_dir = (skill_dir / "bundle").resolve()
        candidate = (bundle_dir / rel_path).resolve()
        try:
            candidate.relative_to(bundle_dir)
        except ValueError as exc:
            raise ValueError("Invalid skill file path.") from exc
        if not candidate.is_file():
            raise SkillNotFoundError("Skill file not found.")
        return candidate.read_text(encoding="utf-8", errors="replace")

    # ---- mutation -----------------------------------------------------------

    def update_skill_md(
        self,
        skill_id: str,
        markdown: str,
        expected_version: str | None = None,
    ) -> dict[str, Any]:
        parsed = parse_skill_md(markdown)
        with self._lock:
            skill_dir = self._resolve_skill_dir(skill_id)
            meta = self._read_meta(skill_dir)
            if meta is None:
                raise SkillNotFoundError("Skill not found.")
            skill_md_path = skill_dir / "bundle" / _SKILL_MD_NAME
            current = skill_md_path.read_bytes() if skill_md_path.exists() else b""
            if expected_version and expected_version != _content_version(current):
                raise SkillVersionConflictError("Skill has changed since it was loaded.")
            new_bytes = markdown.encode("utf-8")
            skill_md_path.parent.mkdir(parents=True, exist_ok=True)
            skill_md_path.write_bytes(new_bytes)
            meta["name"] = parsed.name
            meta["slug"] = parsed.name
            meta["description"] = parsed.description
            meta["updated_at"] = _now_iso()
            meta["version"] = _content_version(new_bytes)
            (skill_dir / "skill.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )
            return self.get_skill(skill_id)

    def delete_skill(self, skill_id: str) -> dict[str, Any]:
        with self._lock:
            skill_dir = self._resolve_skill_dir(skill_id)
            if not skill_dir.exists():
                raise SkillNotFoundError("Skill not found.")
            shutil.rmtree(skill_dir, ignore_errors=True)
            return {"skill_id": skill_id, "deleted": True}

    # ---- helpers ------------------------------------------------------------

    def _resolve_skill_dir(self, skill_id: str) -> Path:
        normalized = str(skill_id).strip().lower()
        if not _SKILL_ID_RE.match(normalized):
            raise SkillNotFoundError("Invalid skill id.")
        return self.base_dir / normalized

    def _read_meta(self, skill_dir: Path) -> dict[str, Any] | None:
        meta_path = skill_dir / "skill.json"
        if not meta_path.is_file():
            return None
        try:
            parsed = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
