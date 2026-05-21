from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.dependencies import (
    get_agent_management_service,
    get_market_research_service,
    get_research_store,
    get_runtime_config_store,
    get_settings,
    get_skill_store,
    get_trading_service,
)
from app.clients.alpaca_client import AlpacaClient
from app.clients.openai_client import build_openai_client
from app.repositories.json_agent_store import JsonAgentStore
from app.repositories.markdown_research_store import MarkdownResearchStore, ResearchRecord
from app.repositories.skill_store import SkillStore
from app.services.agent_management_service import AgentManagementService, DuplicateStrategyError
from app.services.market_research_service import MarketResearchService
from app.services.trading_service import TradingService
from app.settings import Settings, load_settings


class FakeAlpacaClient:
    def __init__(self) -> None:
        self.last_bars_symbol: str | None = None
        self.last_order_side: str | None = None

    def get_bars(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, float | int]]:
        self.last_bars_symbol = symbol
        _ = timeframe
        bars = []
        for idx in range(limit):
            close = 100.0 + idx
            bars.append(
                {
                    "t": idx,
                    "o": close - 0.2,
                    "h": close + 0.5,
                    "l": close - 0.6,
                    "c": close,
                    "v": 1_000.0 + idx * 10,
                }
            )
        return bars

    def submit_order(self, symbol: str, qty: float, side: str, order_type: str, time_in_force: str) -> dict[str, Any]:
        self.last_order_side = side
        return {
            "id": "ord_123",
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
            "status": "accepted",
        }

    def list_active_tradable_equity_symbols(self, limit: int = 200) -> list[str]:
        return ["AAPL", "MSFT", "NVDA"][:limit]

    def get_market_clock(self) -> dict[str, Any]:
        return {
            "is_open": True,
            "timestamp": None,
            "next_open": None,
            "next_close": None,
        }


class FakeResponses:
    def __init__(self, outputs: list[str]) -> None:
        self._outputs = outputs
        self._index = 0

    def create(self, *args, **kwargs) -> SimpleNamespace:
        _ = args
        _ = kwargs
        output_text = self._outputs[self._index]
        self._index += 1
        return SimpleNamespace(output_text=output_text)


class FakeOpenAIClient:
    def __init__(self, outputs: list[str]) -> None:
        self.responses = FakeResponses(outputs=outputs)


class FakeResearchStore:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.documents: dict[str, dict[str, Any]] = {}

    def append_record(self, record: ResearchRecord) -> dict[str, Any]:
        date_folder = datetime.now(UTC).date().isoformat()
        file_name = f"{record.ticker}_{len(self.records)}.md"
        relative_path = f"{date_folder}/{file_name}"
        record_id = self._encode_id(relative_path)
        metadata = {
            "created_at": record.created_at,
            "strategy_fingerprint": record.strategy_fingerprint,
            "ticker": record.ticker,
            "timeframe": record.timeframe,
            "symbols": record.symbols,
            "source": record.source,
        }
        markdown = f"<!-- RESEARCH_META {json.dumps(metadata, separators=(',', ':'))} -->\n\n# Snapshot {record.ticker}\n\nGenerated test record.\n"
        now = datetime.now(UTC).isoformat()
        payload = {
            "created_at": record.created_at,
            "file_path": f"/tmp/{relative_path}",
            "strategy_fingerprint": record.strategy_fingerprint,
            "ticker": record.ticker,
            "timeframe": record.timeframe,
            "symbols": record.symbols,
            "source": record.source,
        }
        self.records.append(payload)
        self.documents[record_id] = {
            "id": record_id,
            "file_name": file_name,
            "date_folder": date_folder,
            "relative_path": relative_path,
            "metadata": metadata,
            "markdown": markdown,
            "version": self._version(markdown),
            "updated_at": now,
        }
        return payload

    def list_recent_records(self, symbols: list[str] | None = None, timeframe: str | None = None) -> list[dict[str, Any]]:
        symbols_filter = {symbol.upper() for symbol in (symbols or [])}
        timeframe_filter = (timeframe or "").lower()
        results = []
        for item in self.records:
            item_symbols = {str(symbol).upper() for symbol in item.get("symbols", [])}
            if symbols_filter and symbols_filter.isdisjoint(item_symbols):
                continue
            if timeframe_filter and str(item.get("timeframe", "")).lower() != timeframe_filter:
                continue
            results.append(item)
        return results

    def list_record_tree(self, now: datetime | None = None) -> list[dict[str, Any]]:
        _ = now
        grouped: dict[str, list[dict[str, Any]]] = {}
        for document in self.documents.values():
            folder = str(document["date_folder"])
            metadata = document.get("metadata", {})
            grouped.setdefault(folder, []).append(
                {
                    "id": document["id"],
                    "file_name": document["file_name"],
                    "created_at": str(metadata.get("created_at", "")),
                    "ticker": str(metadata.get("ticker", "")).upper(),
                    "timeframe": str(metadata.get("timeframe", "")),
                    "symbols": [str(symbol).upper() for symbol in metadata.get("symbols", [])],
                    "source": str(metadata.get("source", "unknown")),
                    "version": document["version"],
                }
            )
        return [{"folder": folder, "records": records} for folder, records in sorted(grouped.items(), reverse=True)]

    def get_record(self, record_id: str) -> dict[str, Any]:
        if record_id not in self.documents:
            raise FileNotFoundError("Research record not found.")
        return dict(self.documents[record_id])

    def update_record(self, record_id: str, markdown: str, expected_version: str | None = None) -> dict[str, Any]:
        if record_id not in self.documents:
            raise FileNotFoundError("Research record not found.")
        current = dict(self.documents[record_id])
        if expected_version and expected_version != current["version"]:
            raise ValueError("Research record has changed since it was loaded.")
        metadata = current.get("metadata", {})
        metadata_line = f"<!-- RESEARCH_META {json.dumps(metadata, separators=(',', ':'))} -->"
        body_lines = markdown.splitlines()
        if body_lines and body_lines[0].strip().startswith("<!-- RESEARCH_META "):
            body_lines = body_lines[1:]
        while body_lines and body_lines[0].strip() == "":
            body_lines = body_lines[1:]
        body = "\n".join(body_lines)
        normalized = f"{metadata_line}\n\n{body}\n" if body else f"{metadata_line}\n"
        current["markdown"] = normalized
        current["version"] = self._version(normalized)
        current["updated_at"] = datetime.now(UTC).isoformat()
        self.documents[record_id] = current
        return dict(current)

    def delete_record(self, record_id: str) -> dict[str, Any]:
        if record_id not in self.documents:
            raise FileNotFoundError("Research record not found.")
        current = self.documents.pop(record_id)
        self.records = [item for item in self.records if not str(item.get("file_path", "")).endswith(current["relative_path"])]
        return {
            "record_id": record_id,
            "relative_path": current["relative_path"],
            "ticker": str(current.get("metadata", {}).get("ticker", "")).upper(),
            "deleted": True,
        }

    def delete_records(self, record_ids: list[str]) -> dict[str, Any]:
        if not record_ids:
            raise ValueError("record_ids must include at least one item.")
        deleted_ids: list[str] = []
        missing_ids: list[str] = []
        for record_id in record_ids:
            if record_id not in self.documents:
                missing_ids.append(record_id)
                continue
            self.delete_record(record_id)
            deleted_ids.append(record_id)
        return {
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "missing_ids": missing_ids,
        }

    def delete_folder(self, folder: str) -> dict[str, Any]:
        normalized = str(folder).strip()
        if not normalized:
            raise ValueError("Invalid research folder.")
        candidates = [doc_id for doc_id, doc in self.documents.items() if doc.get("date_folder") == normalized]
        if not candidates:
            raise FileNotFoundError("Research folder not found.")
        for record_id in candidates:
            self.delete_record(record_id)
        return {
            "folder": normalized,
            "deleted_count": len(candidates),
            "deleted_ids": candidates,
        }

    @staticmethod
    def _encode_id(relative_path: str) -> str:
        return base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii").rstrip("=")

    @staticmethod
    def _version(markdown: str) -> str:
        return hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16]


class FakeSignal:
    signal = "BUY"
    latest_close = 10.0


class FakeRunResult:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.signal_result = FakeSignal()
        self.order_response = {
            "symbol": symbol,
            "qty": 1.0,
            "side": "buy",
            "status": "accepted",
        }


class FakeTradingService:
    class _Settings:
        default_symbol = "AAPL"
        bar_timeframe = "1Day"

    settings = _Settings()

    def __init__(self) -> None:
        self.open_positions: list[dict[str, Any]] = []
        self.market_open: bool = True
        self.latest_close: float = 100.0
        self.signal_orders: list[dict[str, Any]] = []

    def get_open_positions(self) -> list[dict[str, Any]]:
        return list(self.open_positions)

    def get_market_runtime(self) -> dict[str, Any]:
        return {
            "is_market_open": self.market_open,
            "next_market_open": None,
            "next_market_close": None,
            "market_status_label": "open" if self.market_open else "closed",
        }

    def buy(self, symbol: str, qty: float | None, order_type: str, time_in_force: str, asset_class: str = "stock") -> dict[str, Any]:
        return {
            "symbol": symbol.upper(),
            "qty": qty or 1.0,
            "side": "buy",
            "type": order_type,
            "time_in_force": time_in_force,
            "status": "accepted",
        }

    def sell(self, symbol: str, qty: float | None, order_type: str, time_in_force: str, asset_class: str = "stock") -> dict[str, Any]:
        return {
            "symbol": symbol.upper(),
            "qty": qty or 1.0,
            "side": "sell",
            "type": order_type,
            "time_in_force": time_in_force,
            "status": "accepted",
        }

    def get_market_data(
        self,
        symbol: str | None,
        timeframe: str | None,
        limit: int | None = None,
        asset_class: str = "stock",
    ) -> list[dict[str, float | int]]:
        _ = symbol
        _ = timeframe
        _ = asset_class
        bars = []
        count = limit or 25
        for idx in range(count):
            close = 1.5 + idx if idx < count - 1 else float(self.latest_close)
            bars.append({"t": idx, "o": 1.0, "h": 2.0, "l": 0.5, "c": close, "v": 10 + idx})
        return bars

    def calculate_signal(self, bars: list[dict[str, Any]]) -> FakeSignal:
        _ = bars
        return FakeSignal()

    def execute_signal_order(
        self,
        symbol: str,
        signal: str,
        qty: float | None = None,
        asset_class: str = "stock",
    ) -> dict[str, Any] | None:
        _ = asset_class
        normalized_signal = signal.upper()
        if normalized_signal not in {"BUY", "SELL"}:
            return None
        side = "buy" if normalized_signal == "BUY" else "sell"
        order = {
            "id": f"ord-{len(self.signal_orders) + 1}",
            "symbol": symbol.upper(),
            "qty": qty or 1.0,
            "side": side,
            "status": "filled",
            "filled_qty": qty or 1.0,
            "filled_avg_price": float(self.latest_close),
        }
        self.signal_orders.append(order)
        return order

    def run(self, symbol: str, timeframe: str, use_openai_explanation: bool) -> FakeRunResult:
        _ = timeframe
        _ = use_openai_explanation
        return FakeRunResult(symbol=symbol)


class FakeResearchResult:
    def __init__(self, symbols: list[str], timeframe: str, risk_profile: str) -> None:
        self.symbols = symbols
        self.timeframe = timeframe
        self.risk_profile = risk_profile
        self.signal_context = [{"symbol": symbols[0], "signal": "BUY"}]
        self.strategy = {"picks": [{"symbol": symbols[0], "suggested_action": "buy"}]}
        self.ticker = symbols[0]
        self.evaluation = {
            "confidence": 0.81,
            "threshold": 0.7,
            "recommendation": "recommended",
            "decision_summary": "Signal quality is strong for this setup.",
            "key_reasons": ["Momentum and volume support continuation."],
            "risk_notes": ["Monitor for sudden reversal."],
            "high_level_plan": ["Enter on confirmed BUY signal.", "Reduce exposure on SELL cross."],
        }
        self.research_refs = [
            {
                "created_at": "2026-01-01T00:00:00+00:00",
                "file_path": f"/tmp/{symbols[0]}_{timeframe}.md",
                "strategy_fingerprint": f"fp-{symbols[0]}-{timeframe}-{risk_profile}",
                "ticker": symbols[0],
                "timeframe": timeframe,
                "symbols": symbols,
            }
        ]
        self.research_thoughts = [
            {
                "stage": "data_collection",
                "message": f"Collected signal context for {len(symbols)} symbols.",
                "source": "signal_service",
            },
            {
                "stage": "strategy_output",
                "message": "Generated strategy and evaluation for selected ticker.",
                "source": "market_research_service",
            },
        ]


class FakeResearchService:
    def __init__(self) -> None:
        self.research_store = FakeResearchStore()

    def build_strategy(
        self,
        symbols: list[str],
        timeframe: str | None,
        risk_profile: str,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        skill_ids: list[str] | None = None,
        research_scope: str = "generation",
        asset_class: str = "stock",
    ) -> FakeResearchResult:
        _ = skill_ids
        _ = research_scope
        _ = asset_class
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "signal_scan",
                    "message": "AAPL: signal=BUY, volume_ratio=1.10, momentum=2.50%",
                    "source": "signal_service",
                }
            )
        result = FakeResearchResult(symbols=[sym.upper() for sym in symbols], timeframe=timeframe or "1Day", risk_profile=risk_profile)
        self.research_store.records = [*self.research_store.records, *result.research_refs]
        return result

    def generate_buy_candidate_strategy(
        self,
        timeframe: str | None = None,
        risk_profile: str = "balanced",
        max_candidates: int = 120,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        skill_ids: list[str] | None = None,
        asset_class: str = "stock",
    ) -> FakeResearchResult:
        _ = max_candidates
        _ = skill_ids
        _ = asset_class
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "candidate_selection",
                    "message": "Selected AAPL as best candidate for strategy generation.",
                    "source": "market_research_service",
                }
            )
        result = FakeResearchResult(symbols=["AAPL"], timeframe=timeframe or "1Day", risk_profile=risk_profile)
        self.research_store.records = [*self.research_store.records, *result.research_refs]
        return result

    def build_mission(
        self,
        symbols: list[str] | None = None,
        theme: str | None = None,
        timeframe: str | None = None,
        risk_profile: str = "balanced",
        skill_ids: list[str] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        asset_class: str = "stock",
    ) -> "MissionResearchResult":
        _ = asset_class
        from app.services.market_research_service import MissionResearchResult

        if progress_callback is not None:
            progress_callback(
                {"stage": "theme_expansion", "message": "Expanding theme.", "source": "agent"}
            )
        resolved = [s.upper() for s in (symbols or ["AAPL"])]
        return MissionResearchResult(
            symbols=resolved,
            ticker=resolved[0],
            timeframe=timeframe or "1Day",
            risk_profile=risk_profile,
            input_kind="theme" if theme else "ticker",
            input_theme=theme,
            theme_summary="A focused basket." if theme else "",
            basket=[{"symbol": s, "rationale": "fake", "score": 0.8} for s in resolved],
            signal_context=[{"symbol": resolved[0], "signal": "BUY"}],
            strategy={"picks": [{"symbol": resolved[0], "suggested_action": "buy"}]},
            evaluation={
                "confidence": 0.8,
                "threshold": 0.7,
                "recommendation": "recommended",
                "decision_summary": "Strong setup.",
                "key_reasons": ["momentum"],
                "risk_notes": [],
                "high_level_plan": ["enter", "exit by window"],
            },
            execution_plan={
                "recommended_window_seconds": 1800,
                "window_rationale": "Intraday momentum.",
                "plan_notes": "",
                "legs": [
                    {
                        "symbol": resolved[0],
                        "entry": {"type": "market", "ref_price": None, "condition": "immediate"},
                        "target_pct": 2.0,
                        "stop_pct": 1.0,
                        "weight": 1.0,
                    }
                ],
            },
            window_seconds=1800,
            window_rationale="Intraday momentum.",
            skill_markdown=(
                "---\nname: fake-mission-skill\n"
                "description: Fake strategy skill for tests.\n---\n\n# Fake\n"
            ),
            research_refs=[
                {
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "file_path": f"/tmp/{resolved[0]}.md",
                    "strategy_fingerprint": "fp-mission",
                    "ticker": resolved[0],
                    "timeframe": timeframe or "1Day",
                    "symbols": resolved,
                }
            ],
            research_thoughts=[{"stage": "mission", "message": "done", "source": "svc"}],
        )


class FakeAgentService:
    def __init__(self) -> None:
        self._deleted = False
        self._agent = {
            "agent_id": "agent-1",
            "name": "Test Agent",
            "status": "paused",
            "symbols": ["AAPL", "MSFT"],
            "ticker": "AAPL",
            "timeframe": "1Day",
            "risk_profile": "balanced",
            "interval_seconds": 60,
            "signal_context": [{"symbol": "AAPL", "signal": "BUY"}],
            "strategy": {"picks": [{"symbol": "AAPL"}]},
            "strategy_evaluation": {
                "confidence": 0.62,
                "threshold": 0.7,
                "recommendation": "not_recommended",
                "decision_summary": "Momentum is mixed, so conviction remains below threshold.",
                "key_reasons": ["BUY signal exists but confirmation is weak."],
                "risk_notes": ["Possible whipsaw risk in current regime."],
                "high_level_plan": ["Wait for stronger follow-through before sizing up."],
            },
            "execution_plan": {"symbols": ["AAPL", "MSFT"]},
            "research_refs": [
                {
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "file_path": "/tmp/agent_research.md",
                    "strategy_fingerprint": "fp-agent",
                    "ticker": "AAPL",
                    "timeframe": "1Day",
                    "symbols": ["AAPL", "MSFT"],
                }
            ],
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "last_run_at": None,
            "runs": [],
        }

    def create_agent(
        self,
        symbols: list[str] | None,
        timeframe: str | None,
        risk_profile: str | None,
        interval_seconds: int | None,
        name: str | None,
        generated: dict[str, Any] | None = None,
        skill_ids: list[str] | None = None,
        asset_class: str = "stock",
    ) -> dict[str, Any]:
        _ = timeframe
        _ = interval_seconds
        self._agent["asset_class"] = asset_class
        self._agent["skill_ids"] = list(skill_ids or [])
        if generated:
            if str(generated.get("name", "")).lower().startswith("duplicate"):
                raise DuplicateStrategyError("Similar strategy already exists")
            self._agent["symbols"] = [sym.upper() for sym in generated.get("symbols", [])]
            self._agent["ticker"] = generated.get("ticker", self._agent["symbols"][0] if self._agent["symbols"] else "")
            self._agent["risk_profile"] = generated.get("risk_profile", "balanced")
            self._agent["name"] = generated.get("name", "Generated Agent")
            self._agent["strategy"] = generated.get("strategy", self._agent["strategy"])
            self._agent["signal_context"] = generated.get("signal_context", self._agent["signal_context"])
            self._agent["strategy_evaluation"] = generated.get("strategy_evaluation", self._agent["strategy_evaluation"])
            self._agent["execution_plan"] = generated.get("execution_plan", self._agent["execution_plan"])
            self._agent["research_refs"] = generated.get("research_refs", self._agent["research_refs"])
            return self._agent

        self._agent["symbols"] = [sym.upper() for sym in (symbols or ["AAPL"])]
        self._agent["ticker"] = self._agent["symbols"][0]
        self._agent["risk_profile"] = risk_profile or "balanced"
        if name:
            self._agent["name"] = name
        return self._agent

    def generate_agent_draft(
        self,
        symbols: list[str] | None = None,
        timeframe: str | None = None,
        risk_profile: str | None = None,
        interval_seconds: int | None = None,
        name: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        skill_ids: list[str] | None = None,
        asset_class: str = "stock",
    ) -> dict[str, Any]:
        _ = symbols
        _ = timeframe
        _ = risk_profile
        _ = asset_class
        _ = interval_seconds
        _ = name
        _ = skill_ids
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "signal_scan",
                    "message": "AAPL: signal=BUY, volume_ratio=1.10, momentum=2.50%",
                    "source": "signal_service",
                }
            )
            progress_callback(
                {
                    "stage": "evaluation",
                    "message": "Evaluated candidate AAPL with recommendation=not_recommended.",
                    "source": "market_research_service",
                }
            )
        return {
            "name": "AAPL AI balanced strategy",
            "symbols": ["AAPL"],
            "ticker": "AAPL",
            "timeframe": "1Day",
            "risk_profile": "balanced",
            "interval_seconds": 60,
            "signal_context": [{"symbol": "AAPL", "signal": "BUY"}],
            "strategy": {"picks": [{"symbol": "AAPL", "suggested_action": "buy"}]},
            "strategy_evaluation": {
                "confidence": 0.62,
                "threshold": 0.7,
                "recommendation": "not_recommended",
                "decision_summary": "Signal exists, but confidence is under threshold.",
                "key_reasons": ["BUY signal present with limited confirmation."],
                "risk_notes": ["Treat as lower-conviction setup."],
                "high_level_plan": ["Start with smaller size.", "Re-evaluate after next signal update."],
            },
            "execution_plan": {"symbols": ["AAPL"], "interval_seconds": 60},
            "research_refs": [
                {
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "file_path": "/tmp/generated_research.md",
                    "strategy_fingerprint": "fp-generated",
                    "ticker": "AAPL",
                    "timeframe": "1Day",
                    "symbols": ["AAPL"],
                }
            ],
            "research_thoughts": [
                {
                    "stage": "data_collection",
                    "message": "Collected signal context for candidate symbols.",
                    "source": "signal_service",
                }
            ],
        }

    def generate_mission_draft(
        self,
        symbols: list[str] | None = None,
        theme: str | None = None,
        timeframe: str | None = None,
        risk_profile: str | None = None,
        skill_ids: list[str] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        asset_class: str = "stock",
    ) -> dict[str, Any]:
        _ = (timeframe, skill_ids, asset_class)
        if progress_callback is not None:
            progress_callback({"stage": "theme_expansion", "message": "x", "source": "agent"})
        return {
            "name": f"{theme or (symbols or ['AAPL'])[0]} mission",
            "symbols": [s.upper() for s in (symbols or ["AAPL"])],
            "ticker": (symbols or ["AAPL"])[0].upper(),
            "timeframe": "1Day",
            "risk_profile": risk_profile or "balanced",
            "input_kind": "theme" if theme else "ticker",
            "input_theme": theme,
            "theme_summary": "summary",
            "basket": [{"symbol": "AAPL", "rationale": "r", "score": 0.9}],
            "signal_context": [{"symbol": "AAPL", "signal": "BUY"}],
            "strategy": {"picks": []},
            "strategy_evaluation": {
                "confidence": 0.8,
                "threshold": 0.7,
                "recommendation": "recommended",
                "decision_summary": "ok",
                "key_reasons": ["a"],
                "risk_notes": [],
                "high_level_plan": ["b"],
            },
            "execution_plan": {
                "recommended_window_seconds": 1800,
                "window_rationale": "intraday",
                "plan_notes": "",
                "legs": [
                    {
                        "symbol": "AAPL",
                        "entry": {"type": "market", "ref_price": None, "condition": "now"},
                        "target_pct": 2.0,
                        "stop_pct": 1.0,
                        "weight": 1.0,
                    }
                ],
            },
            "window_seconds": 1800,
            "window_rationale": "intraday",
            "skill_markdown": "---\nname: m\ndescription: d\n---\n# m\n",
            "research_refs": [],
            "research_thoughts": [],
            "preset_skill_ids": [],
        }

    def create_mission_agent(
        self, draft: dict[str, Any], skill_ids: list[str] | None = None
    ) -> dict[str, Any]:
        _ = skill_ids
        self._agent["mission_status"] = "awaiting_approval"
        self._agent["status"] = "paused"
        self._agent["strategy_window_seconds"] = int(draft.get("window_seconds", 1800))
        self._agent["execution_plan"] = draft.get("execution_plan", {})
        self._agent["decision_log"] = []
        self._agent["generated_skill_id"] = "skill-xyz"
        return self._agent

    def approve_mission(
        self, agent_id: str, window_seconds_override: int | None = None
    ) -> dict[str, Any]:
        if agent_id != self._agent["agent_id"]:
            raise KeyError(agent_id)
        self._agent["mission_status"] = "armed"
        self._agent["status"] = "active"
        if window_seconds_override is not None:
            self._agent["strategy_window_seconds"] = int(window_seconds_override)
        return self._agent

    def reject_mission(self, agent_id: str) -> dict[str, Any]:
        if agent_id != self._agent["agent_id"]:
            raise KeyError(agent_id)
        self._agent["mission_status"] = "rejected"
        self._agent["status"] = "paused"
        return self._agent

    def list_agents(self) -> list[dict[str, Any]]:
        if self._deleted:
            return []
        return [self._agent]

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        if self._deleted:
            raise KeyError(agent_id)
        if agent_id != self._agent["agent_id"]:
            raise KeyError(agent_id)
        return self._agent

    def activate_agent(self, agent_id: str) -> dict[str, Any]:
        _ = agent_id
        self._agent["status"] = "active"
        return self._agent

    def pause_agent(self, agent_id: str) -> dict[str, Any]:
        _ = agent_id
        self._agent["status"] = "paused"
        return self._agent

    def set_agent_skills(self, agent_id: str, skill_ids: list[str] | None) -> dict[str, Any]:
        if self._deleted or agent_id != self._agent["agent_id"]:
            raise KeyError(agent_id)
        self._agent["skill_ids"] = list(skill_ids or [])
        return self._agent

    def delete_agent(self, agent_id: str) -> dict[str, Any]:
        if self._deleted or agent_id != self._agent["agent_id"]:
            raise KeyError(agent_id)
        if self._agent["status"] == "active":
            self._agent["status"] = "paused"
        self._deleted = True
        return self._agent

    def get_live_positions(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol": "AAPL",
                "side": "long",
                "qty": 1.0,
                "avg_entry_price": 100.0,
                "current_price": 101.0,
                "lastday_price": 99.0,
                "change_today": 0.01,
                "market_value": 101.0,
                "unrealized_pnl": 1.0,
                "unrealized_pnl_pct": 0.01,
            }
        ]

    def get_live_summary(self, period: str = "all") -> dict[str, Any]:
        return {
            "period": period,
            "account_id": "acct-1",
            "account_number": "ACC-123",
            "status": "ACTIVE",
            "buying_power": 1000.0,
            "cash": 500.0,
            "equity": 1005.0,
            "portfolio_value": 1005.0,
            "long_market_value": 505.0,
            "short_market_value": 0.0,
            "last_equity": 1000.0,
            "day_change": 5.0,
            "day_change_pct": 0.005,
        }

    def get_live_transactions(self, limit: int = 200) -> list[dict[str, Any]]:
        items = [
            {
                "activity_id": "fill-1",
                "activity_type": "FILL",
                "order_id": "ord-1",
                "executed_at": "2026-01-01T00:00:00+00:00",
                "symbol": "AAPL",
                "side": "buy",
                "qty": 1.0,
                "price": 100.0,
                "value": 100.0,
            },
            {
                "activity_id": "fill-2",
                "activity_type": "FILL",
                "order_id": "ord-2",
                "executed_at": "2026-01-01T00:01:00+00:00",
                "symbol": "MSFT",
                "side": "buy",
                "qty": 1.0,
                "price": 200.0,
                "value": 200.0,
            },
        ]
        normalized_limit = max(1, int(limit))
        return items[:normalized_limit]


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    def _factory(**overrides: Any) -> Settings:
        base = dict(
            alpaca_paper_api_key="test-paper-key",
            alpaca_paper_secret_key="test-paper-secret",
            alpaca_paper_base_url="https://paper-api.alpaca.markets",
            alpaca_real_api_key="test-real-key",
            alpaca_real_secret_key="test-real-secret",
            alpaca_real_base_url="https://api.alpaca.markets",
            trading_mode="paper",
            databento_api_key="",
            massive_api_key="",
            topstepx_api_key="",
            topstepx_username="",
            data_provider="alpaca",
            broker_provider="alpaca",
            openai_api_key="test-openai",
            openai_model="gpt-test",
            default_symbol="AAPL",
            bar_timeframe="1Day",
            fast_sma_window=5,
            slow_sma_window=20,
            order_qty=1.0,
            log_level="INFO",
            agent_run_interval_seconds=60,
            summary_starting_deposit=100.0,
        )
        base.update(overrides)
        return Settings(**base)

    return _factory


@pytest.fixture
def unit_settings(settings_factory: Callable[..., Settings], tmp_path: Path) -> Settings:
    return settings_factory(
        agent_store_path=str(tmp_path / "agents.json"),
        runtime_config_path=str(tmp_path / "runtime_config.json"),
        research_store_dir=str(tmp_path / "research"),
    )


@pytest.fixture
def fake_alpaca_client() -> FakeAlpacaClient:
    return FakeAlpacaClient()


@pytest.fixture
def fake_research_store() -> FakeResearchStore:
    return FakeResearchStore()


@pytest.fixture
def fake_openai_client_factory() -> Callable[[list[str]], FakeOpenAIClient]:
    def _factory(outputs: list[str]) -> FakeOpenAIClient:
        return FakeOpenAIClient(outputs=outputs)

    return _factory


@pytest.fixture
def fake_research_service() -> FakeResearchService:
    return FakeResearchService()


@pytest.fixture
def fake_trading_service() -> FakeTradingService:
    return FakeTradingService()


@pytest.fixture
def fake_agent_service() -> FakeAgentService:
    return FakeAgentService()


@pytest.fixture
def agent_management_service(
    unit_settings: Settings,
    fake_research_service: FakeResearchService,
    fake_trading_service: FakeTradingService,
) -> AgentManagementService:
    return AgentManagementService(
        settings=unit_settings,
        store=JsonAgentStore(file_path=unit_settings.agent_store_path),
        market_research_service=fake_research_service,
        trading_service=fake_trading_service,
    )


@pytest.fixture
def api_client(
    fake_trading_service: FakeTradingService,
    fake_research_service: FakeResearchService,
    fake_agent_service: FakeAgentService,
    fake_research_store: FakeResearchStore,
    tmp_path: Path,
) -> Iterator[TestClient]:
    os.environ.setdefault("ALPACA_PAPER_API_KEY", "test-paper-key")
    os.environ.setdefault("ALPACA_PAPER_SECRET_KEY", "test-paper-secret")
    os.environ.setdefault("ALPACA_PAPER_BASE_URL", "https://paper-api.alpaca.markets")
    os.environ.setdefault("ALPACA_REAL_API_KEY", "test-real-key")
    os.environ.setdefault("ALPACA_REAL_SECRET_KEY", "test-real-secret")
    os.environ.setdefault("ALPACA_REAL_BASE_URL", "https://api.alpaca.markets")
    os.environ.setdefault("TRADING_MODE", "paper")
    os.environ.setdefault("RUNTIME_CONFIG_PATH", str(Path(os.getcwd()) / ".runtime_config.test.json"))
    os.environ.setdefault("OPENAI_API_KEY", "test-openai")
    get_settings.cache_clear()
    get_runtime_config_store.cache_clear()
    app = create_app()
    app.dependency_overrides[get_trading_service] = lambda: fake_trading_service
    app.dependency_overrides[get_market_research_service] = lambda: fake_research_service
    app.dependency_overrides[get_agent_management_service] = lambda: fake_agent_service
    app.dependency_overrides[get_research_store] = lambda: fake_research_store
    skill_store = SkillStore(base_dir=str(tmp_path / "skills"))
    app.dependency_overrides[get_skill_store] = lambda: skill_store
    with TestClient(app) as client:
        yield client


def _require_integration_env() -> None:
    required = (
        "ALPACA_PAPER_API_KEY",
        "ALPACA_PAPER_SECRET_KEY",
        "ALPACA_REAL_API_KEY",
        "ALPACA_REAL_SECRET_KEY",
        "OPENAI_API_KEY",
    )
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        pytest.skip(
            "Integration credentials missing. Set "
            + ", ".join(missing)
            + " to run real integration tests."
        )


@pytest.fixture
def integration_settings(tmp_path: Path) -> Settings:
    _require_integration_env()
    loaded = load_settings()
    return Settings(
        alpaca_paper_api_key=loaded.alpaca_paper_api_key,
        alpaca_paper_secret_key=loaded.alpaca_paper_secret_key,
        alpaca_paper_base_url=loaded.alpaca_paper_base_url,
        alpaca_real_api_key=loaded.alpaca_real_api_key,
        alpaca_real_secret_key=loaded.alpaca_real_secret_key,
        alpaca_real_base_url=loaded.alpaca_real_base_url,
        trading_mode=loaded.trading_mode,
        databento_api_key=loaded.databento_api_key,
        massive_api_key=loaded.massive_api_key,
        topstepx_api_key=loaded.topstepx_api_key,
        topstepx_username=loaded.topstepx_username,
        data_provider=loaded.data_provider,
        broker_provider=loaded.broker_provider,
        openai_api_key=loaded.openai_api_key,
        openai_model=loaded.openai_model,
        default_symbol=loaded.default_symbol,
        bar_timeframe=loaded.bar_timeframe,
        fast_sma_window=loaded.fast_sma_window,
        slow_sma_window=loaded.slow_sma_window,
        order_qty=loaded.order_qty,
        log_level=loaded.log_level,
        agent_store_path=str(tmp_path / "agents.integration.json"),
        runtime_config_path=str(tmp_path / "runtime_config.integration.json"),
        research_store_dir=str(tmp_path / "research"),
        research_retention_days=loaded.research_retention_days,
        agent_run_interval_seconds=loaded.agent_run_interval_seconds,
        summary_starting_deposit=loaded.summary_starting_deposit,
    )


@pytest.fixture
def real_alpaca_client(integration_settings: Settings) -> AlpacaClient:
    mode = integration_settings.trading_mode
    return AlpacaClient(
        api_key=(
            integration_settings.alpaca_real_api_key
            if mode == "real"
            else integration_settings.alpaca_paper_api_key
        ),
        secret_key=(
            integration_settings.alpaca_real_secret_key
            if mode == "real"
            else integration_settings.alpaca_paper_secret_key
        ),
        trading_base_url=(
            integration_settings.alpaca_real_base_url
            if mode == "real"
            else integration_settings.alpaca_paper_base_url
        ),
        paper=mode != "real",
    )


@pytest.fixture
def real_openai_client(integration_settings: Settings) -> Any:
    return build_openai_client(integration_settings.openai_api_key)


@pytest.fixture
def real_research_store(integration_settings: Settings) -> MarkdownResearchStore:
    return MarkdownResearchStore(
        base_dir=integration_settings.research_store_dir,
        retention_days=integration_settings.research_retention_days,
    )


@pytest.fixture
def real_market_research_service(
    integration_settings: Settings,
    real_alpaca_client: AlpacaClient,
    real_research_store: MarkdownResearchStore,
    real_openai_client: Any,
) -> MarketResearchService:
    return MarketResearchService(
        settings=integration_settings,
        alpaca_client=real_alpaca_client,
        research_store=real_research_store,
        openai_client=real_openai_client,
    )


@pytest.fixture
def real_trading_service(
    integration_settings: Settings,
    real_alpaca_client: AlpacaClient,
    real_openai_client: Any,
) -> TradingService:
    return TradingService(
        settings=integration_settings,
        alpaca_client=real_alpaca_client,
        openai_client=real_openai_client,
    )


@pytest.fixture
def real_agent_management_service(
    integration_settings: Settings,
    real_market_research_service: MarketResearchService,
    real_trading_service: TradingService,
) -> AgentManagementService:
    return AgentManagementService(
        settings=integration_settings,
        store=JsonAgentStore(file_path=integration_settings.agent_store_path),
        market_research_service=real_market_research_service,
        trading_service=real_trading_service,
    )
