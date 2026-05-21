from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from collections.abc import Callable
from typing import Any, Literal
from uuid import uuid4

from app.clients.openai_client import build_agent_execution_plan, evaluate_execution_checkpoint
from app.repositories.json_agent_store import JsonAgentStore
from app.services.agent_monitor_stream import AgentMonitorStream
from app.services.market_research_service import MarketResearchService
from app.services.skill_context import build_skill_prompt_block, normalize_skill_ids
from app.services.strategy_dedup import build_strategy_fingerprint
from app.services.trading_service import TradingService
from app.settings import Settings


LOGGER = logging.getLogger(__name__)

_RISK_EXPOSURE_LIMITS = {
    "conservative": 0.15,
    "balanced": 0.30,
    "aggressive": 0.50,
}
_FOLLOWUP_STALE_HOURS = 24
_FOLLOWUP_REFRESH_COOLDOWN_HOURS = 6
_FOLLOWUP_HOLD_STREAK_RUNS = 5
_FOLLOWUP_DRAWDOWN_RATIO = 0.05
_TIMEFRAME_MIN_INTERVAL_SECONDS = {
    "1min": 60,
    "1minute": 60,
    "5min": 300,
    "5minute": 300,
    "15min": 900,
    "15minute": 900,
    "1hour": 3600,
    "1day": 3600,
    "1week": 86_400,
    "1month": 86_400,
}


class DuplicateStrategyError(RuntimeError):
    pass


class InvalidGeneratedDraftError(ValueError):
    pass


class AgentManagementService:
    def __init__(
        self,
        settings: Settings,
        store: JsonAgentStore,
        market_research_service: MarketResearchService,
        trading_service: TradingService,
        monitor_stream: AgentMonitorStream | None = None,
        skill_store: Any | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.market_research_service = market_research_service
        self.trading_service = trading_service
        self.monitor_stream = monitor_stream
        self.skill_store = skill_store

    def create_agent(
        self,
        symbols: list[str] | None,
        timeframe: str | None,
        risk_profile: str | None,
        interval_seconds: int | None,
        name: str | None = None,
        generated: dict[str, Any] | None = None,
        skill_ids: list[str] | None = None,
        asset_class: Literal["stock", "future"] = "stock",
    ) -> dict[str, Any]:
        normalized_skill_ids = normalize_skill_ids(skill_ids)
        generated_payload = generated or self.generate_agent_draft(
            symbols=symbols,
            timeframe=timeframe,
            risk_profile=risk_profile,
            interval_seconds=interval_seconds,
            name=name,
            skill_ids=normalized_skill_ids,
            asset_class=asset_class,
        )
        created_at = _now_iso()
        self._ensure_strategy_is_unique(generated_payload=generated_payload)
        agent_id = str(uuid4())
        record = {
            "agent_id": agent_id,
            "name": str(generated_payload["name"]).strip(),
            "status": "paused",
            "startup_pause_pending": False,
            "startup_pause_at": None,
            "startup_pause_source_status": None,
            "symbols": generated_payload["symbols"],
            "ticker": generated_payload["ticker"],
            "timeframe": generated_payload["timeframe"],
            "asset_class": generated_payload.get("asset_class", asset_class) or "stock",
            "risk_profile": generated_payload["risk_profile"],
            "interval_seconds": int(generated_payload["interval_seconds"]),
            "signal_context": generated_payload["signal_context"],
            "strategy": generated_payload["strategy"],
            "strategy_evaluation": generated_payload["strategy_evaluation"],
            "execution_plan": generated_payload["execution_plan"],
            "research_refs": generated_payload.get("research_refs", []),
            "research_thoughts": generated_payload.get("research_thoughts", []),
            "skill_ids": normalized_skill_ids,
            "created_at": created_at,
            "updated_at": created_at,
            "last_run_at": None,
            "runs": [],
        }
        self.store.save_agent(record)
        return record

    def generate_agent_draft(
        self,
        symbols: list[str] | None = None,
        timeframe: str | None = None,
        risk_profile: str | None = None,
        interval_seconds: int | None = None,
        name: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        skill_ids: list[str] | None = None,
        asset_class: Literal["stock", "future"] = "stock",
    ) -> dict[str, Any]:
        resolved_risk_profile = (risk_profile or "balanced").strip().lower()
        run_interval = interval_seconds or self.settings.agent_run_interval_seconds
        normalized_skill_ids = normalize_skill_ids(skill_ids)
        resolved_asset_class = asset_class if asset_class in ("stock", "future") else "stock"

        if symbols:
            research_result = self.market_research_service.build_strategy(
                symbols=symbols,
                timeframe=timeframe,
                risk_profile=resolved_risk_profile,
                progress_callback=progress_callback,
                skill_ids=normalized_skill_ids,
                asset_class=resolved_asset_class,
            )
        else:
            research_result = self.market_research_service.generate_buy_candidate_strategy(
                timeframe=timeframe,
                risk_profile=resolved_risk_profile,
                progress_callback=progress_callback,
                skill_ids=normalized_skill_ids,
                asset_class=resolved_asset_class,
            )

        if not research_result.symbols:
            raise RuntimeError("Generated research did not return any symbols.")

        generated_name = (name or f"{research_result.symbols[0]} AI {resolved_risk_profile} strategy").strip()
        execution_plan = build_agent_execution_plan(
            strategy=research_result.strategy,
            fallback_symbols=research_result.symbols,
            default_interval_seconds=run_interval,
        )
        return {
            "name": generated_name,
            "symbols": research_result.symbols,
            "ticker": research_result.ticker,
            "timeframe": research_result.timeframe,
            "asset_class": resolved_asset_class,
            "risk_profile": resolved_risk_profile,
            "interval_seconds": run_interval,
            "signal_context": research_result.signal_context,
            "strategy": research_result.strategy,
            "strategy_evaluation": research_result.evaluation,
            "execution_plan": execution_plan,
            "research_refs": research_result.research_refs,
            "research_thoughts": research_result.research_thoughts,
            "quality_findings": research_result.quality_findings,
        }

    # ------------------------------------------------------------------ #
    # Mission lifecycle: research → approve → time-boxed execution → stop
    # ------------------------------------------------------------------ #

    def generate_mission_draft(
        self,
        symbols: list[str] | None = None,
        theme: str | None = None,
        timeframe: str | None = None,
        risk_profile: str | None = None,
        skill_ids: list[str] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        asset_class: Literal["stock", "future"] = "stock",
    ) -> dict[str, Any]:
        resolved_risk = (risk_profile or "balanced").strip().lower()
        normalized_skill_ids = normalize_skill_ids(skill_ids)
        resolved_asset_class = asset_class if asset_class in ("stock", "future") else "stock"
        result = self.market_research_service.build_mission(
            symbols=symbols,
            theme=theme,
            timeframe=timeframe,
            risk_profile=resolved_risk,
            skill_ids=normalized_skill_ids,
            progress_callback=progress_callback,
            asset_class=resolved_asset_class,
        )
        focus = result.input_theme or result.ticker
        return {
            "name": f"{focus} mission ({resolved_risk})".strip(),
            "symbols": result.symbols,
            "ticker": result.ticker,
            "timeframe": result.timeframe,
            "asset_class": resolved_asset_class,
            "risk_profile": result.risk_profile,
            "input_kind": result.input_kind,
            "input_theme": result.input_theme,
            "theme_summary": result.theme_summary,
            "basket": result.basket,
            "signal_context": result.signal_context,
            "strategy": result.strategy,
            "strategy_evaluation": result.evaluation,
            "execution_plan": result.execution_plan,
            "window_seconds": result.window_seconds,
            "window_rationale": result.window_rationale,
            "skill_markdown": result.skill_markdown,
            "research_refs": result.research_refs,
            "research_thoughts": result.research_thoughts,
            "preset_skill_ids": normalized_skill_ids,
        }

    def create_mission_agent(
        self,
        draft: dict[str, Any],
        skill_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_skill_ids = normalize_skill_ids(
            skill_ids if skill_ids is not None else draft.get("preset_skill_ids")
        )
        generated_skill_id: str | None = None
        skill_md = str(draft.get("skill_markdown", "")).strip()
        if skill_md and self.skill_store is not None:
            try:
                meta = self.skill_store.create_from_editor(markdown=skill_md)
                generated_skill_id = str(meta.get("id"))
                if generated_skill_id and generated_skill_id not in normalized_skill_ids:
                    normalized_skill_ids = [*normalized_skill_ids, generated_skill_id]
            except Exception as exc:
                LOGGER.warning("Failed to persist authored strategy skill: %s", exc)

        created_at = _now_iso()
        agent_id = str(uuid4())
        window_seconds = int(
            draft.get("window_seconds")
            or draft.get("execution_plan", {}).get("recommended_window_seconds")
            or self.settings.mission_window_default_seconds
        )
        record = {
            "agent_id": agent_id,
            "name": str(draft.get("name", "Mission agent")).strip(),
            "status": "paused",
            "mission_status": "awaiting_approval",
            "input_kind": draft.get("input_kind", "auto"),
            "input_theme": draft.get("input_theme"),
            "theme_summary": draft.get("theme_summary", ""),
            "basket": draft.get("basket", []),
            "startup_pause_pending": False,
            "startup_pause_at": None,
            "startup_pause_source_status": None,
            "symbols": draft.get("symbols", []),
            "ticker": draft.get("ticker", ""),
            "timeframe": draft.get("timeframe", self.settings.bar_timeframe),
            "asset_class": str(draft.get("asset_class") or "stock"),
            "risk_profile": draft.get("risk_profile", "balanced"),
            "interval_seconds": int(self.settings.mission_tick_seconds),
            "signal_context": draft.get("signal_context", []),
            "strategy": draft.get("strategy", {}),
            "strategy_evaluation": draft.get("strategy_evaluation", {}),
            "execution_plan": draft.get("execution_plan", {}),
            "strategy_window_seconds": window_seconds,
            "window_rationale": draft.get("window_rationale", ""),
            "window_started_at": None,
            "window_ends_at": None,
            "approved_at": None,
            "last_checkpoint_at": None,
            "last_mission_tick_at": None,
            "mission_entry_prices": {},
            "decision_log": [],
            "terminal_reason": None,
            "generated_skill_id": generated_skill_id,
            "research_refs": draft.get("research_refs", []),
            "research_thoughts": draft.get("research_thoughts", []),
            "skill_ids": normalized_skill_ids,
            "created_at": created_at,
            "updated_at": created_at,
            "last_run_at": None,
            "runs": [],
        }
        self.store.save_agent(record)
        self._publish_event(
            event="mission_status_changed",
            agent_id=agent_id,
            payload={"mission_status": "awaiting_approval"},
        )
        return record

    def approve_mission(
        self,
        agent_id: str,
        window_seconds_override: int | None = None,
    ) -> dict[str, Any]:
        record = self.get_agent(agent_id)
        if record.get("mission_status") != "awaiting_approval":
            raise InvalidGeneratedDraftError(
                f"Agent {agent_id} is not awaiting approval (state={record.get('mission_status')})."
            )
        window = int(
            record.get("strategy_window_seconds") or self.settings.mission_window_default_seconds
        )
        if window_seconds_override is not None:
            window = max(
                self.settings.mission_window_min_seconds,
                min(self.settings.mission_window_max_seconds, int(window_seconds_override)),
            )
        record["strategy_window_seconds"] = window
        record["mission_status"] = "armed"
        record["status"] = "active"
        record["approved_at"] = _now_iso()
        record["updated_at"] = _now_iso()
        self._append_decision_log(record, "lifecycle", "", "approved", f"Approved with {window}s window")
        updated = self.store.save_agent(record)
        self._publish_event(
            event="mission_status_changed",
            agent_id=agent_id,
            payload={"mission_status": "armed", "window_seconds": window},
        )
        return updated

    def reject_mission(self, agent_id: str) -> dict[str, Any]:
        record = self.get_agent(agent_id)
        record["mission_status"] = "rejected"
        record["status"] = "paused"
        record["terminal_reason"] = "rejected_by_user"
        record["updated_at"] = _now_iso()
        self._append_decision_log(record, "lifecycle", "", "rejected", "Rejected by user")
        updated = self.store.save_agent(record)
        self._publish_event(
            event="mission_status_changed",
            agent_id=agent_id,
            payload={"mission_status": "rejected"},
        )
        return updated

    def run_mission_cycle(self) -> list[dict[str, Any]]:
        updated: list[dict[str, Any]] = []
        now = datetime.now(UTC)
        for agent in self.list_agents():
            if agent.get("mission_status") not in {"armed", "executing"}:
                continue
            try:
                stepped = self._mission_step(agent, now)
                if stepped is not None:
                    updated.append(stepped)
            except Exception as exc:
                LOGGER.exception("Mission %s step failed: %s", agent.get("agent_id"), exc)
        return updated

    def _mission_step(self, agent: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        agent_id = str(agent.get("agent_id", ""))
        market = self._resolve_market_runtime()
        is_open = bool(market.get("is_market_open", False))

        if agent.get("mission_status") == "armed":
            if not is_open:
                return None
            window = int(
                agent.get("strategy_window_seconds") or self.settings.mission_window_default_seconds
            )
            agent["mission_status"] = "executing"
            agent["window_started_at"] = now.isoformat()
            agent["window_ends_at"] = (now + timedelta(seconds=window)).isoformat()
            agent["updated_at"] = now.isoformat()
            self._append_decision_log(
                agent, "window", "", "window_started", f"Execution window opened ({window}s)"
            )
            self.store.save_agent(agent)
            self._publish_event(
                event="mission_window_started",
                agent_id=agent_id,
                payload={"window_ends_at": agent["window_ends_at"]},
            )
            self._publish_event(
                event="mission_status_changed",
                agent_id=agent_id,
                payload={"mission_status": "executing"},
            )

        if agent.get("mission_status") != "executing":
            return None

        window_ends = _parse_optional_iso_datetime(agent.get("window_ends_at"))
        if window_ends is not None and now >= window_ends:
            return self._flatten_mission(agent, now, "window_expired")
        if not is_open:
            return self._flatten_mission(agent, now, "market_closed")

        last_tick = _parse_optional_iso_datetime(agent.get("last_mission_tick_at"))
        if (
            last_tick is not None
            and (now - last_tick).total_seconds() < self.settings.mission_tick_seconds
        ):
            return None
        agent["last_mission_tick_at"] = now.isoformat()

        timeframe = str(agent.get("timeframe") or self.settings.bar_timeframe)
        asset_class = self._agent_asset_class(agent)
        positions = {
            str(p.get("symbol", "")).upper(): p for p in self.trading_service.get_open_positions()
        }
        legs = agent.get("execution_plan", {}).get("legs", []) or []
        entry_prices: dict[str, Any] = agent.setdefault("mission_entry_prices", {})

        for leg in legs:
            if not isinstance(leg, dict):
                continue
            symbol = str(leg.get("symbol", "")).upper()
            if not symbol:
                continue
            latest = self._latest_close(symbol, timeframe, asset_class)
            if latest <= 0:
                continue
            pos = positions.get(symbol)
            held_qty = float(pos.get("qty", 0.0) or 0.0) if pos else 0.0

            if held_qty <= 0.0:
                if not self._entry_ready(leg, latest):
                    continue
                qty = self._mission_qty(agent, leg, latest)
                if qty <= 0.0:
                    continue
                order = self.trading_service.execute_signal_order(
                    symbol=symbol, signal="BUY", qty=qty, asset_class=asset_class
                ) or {}
                filled = float(order.get("filled_qty", 0.0) or 0.0)
                filled_px = order.get("filled_avg_price")
                entry_px = float(filled_px) if filled_px else latest
                entry_prices[symbol] = entry_px
                self._append_decision_log(
                    agent,
                    "entry",
                    symbol,
                    "position_opened",
                    f"Bought {filled or qty} @ {entry_px:.4f}",
                    order,
                )
                self._publish_event(
                    event="mission_position_opened",
                    agent_id=agent_id,
                    payload={"symbol": symbol, "qty": filled or qty, "price": entry_px},
                )
                continue

            entry_px = float(
                entry_prices.get(symbol) or pos.get("avg_entry_price", latest) or latest
            )
            target_px = entry_px * (1.0 + float(leg.get("target_pct", 0.0)) / 100.0)
            stop_px = entry_px * (1.0 - float(leg.get("stop_pct", 0.0)) / 100.0)
            if latest >= target_px or latest <= stop_px:
                order = self.trading_service.execute_signal_order(
                    symbol=symbol, signal="SELL", qty=held_qty, asset_class=asset_class
                ) or {}
                reason = "target_hit" if latest >= target_px else "stop_hit"
                self._append_decision_log(
                    agent,
                    "exit",
                    symbol,
                    reason,
                    f"Sold {held_qty} @ {latest:.4f} (entry {entry_px:.4f})",
                    order,
                )
                self._publish_event(
                    event="mission_position_closed",
                    agent_id=agent_id,
                    payload={"symbol": symbol, "qty": held_qty, "price": latest, "reason": reason},
                )

        terminal = self._maybe_checkpoint(agent, now, positions, entry_prices)
        if terminal is not None:
            return terminal

        agent["updated_at"] = now.isoformat()
        return self.store.save_agent(agent)

    def _flatten_mission(
        self, agent: dict[str, Any], now: datetime, reason: str
    ) -> dict[str, Any]:
        agent_id = str(agent.get("agent_id", ""))
        asset_class = self._agent_asset_class(agent)
        positions = {
            str(p.get("symbol", "")).upper(): p for p in self.trading_service.get_open_positions()
        }
        for leg in agent.get("execution_plan", {}).get("legs", []) or []:
            if not isinstance(leg, dict):
                continue
            symbol = str(leg.get("symbol", "")).upper()
            pos = positions.get(symbol)
            held_qty = float(pos.get("qty", 0.0) or 0.0) if pos else 0.0
            if held_qty <= 0.0:
                continue
            order = self.trading_service.execute_signal_order(
                symbol=symbol, signal="SELL", qty=held_qty, asset_class=asset_class
            ) or {}
            self._append_decision_log(
                agent, "exit", symbol, "position_closed", f"Flatten ({reason})", order
            )
            self._publish_event(
                event="mission_position_closed",
                agent_id=agent_id,
                payload={"symbol": symbol, "qty": held_qty, "reason": reason},
            )
        agent["mission_status"] = "completed"
        agent["status"] = "paused"
        agent["terminal_reason"] = reason
        agent["updated_at"] = now.isoformat()
        self._append_decision_log(agent, "window", "", "mission_completed", f"Completed: {reason}")
        updated = self.store.save_agent(agent)
        self._publish_event(
            event="mission_completed",
            agent_id=agent_id,
            payload={"reason": reason},
        )
        self._publish_event(
            event="mission_status_changed",
            agent_id=agent_id,
            payload={"mission_status": "completed", "reason": reason},
        )
        return updated

    def _maybe_checkpoint(
        self,
        agent: dict[str, Any],
        now: datetime,
        positions: dict[str, Any],
        entry_prices: dict[str, Any],
    ) -> dict[str, Any] | None:
        openai_client = getattr(self.market_research_service, "openai_client", None)
        if openai_client is None:
            return None
        window = int(
            agent.get("strategy_window_seconds") or self.settings.mission_window_default_seconds
        )
        checkpoint_interval = max(self.settings.mission_tick_seconds, window // 3)
        last_cp = _parse_optional_iso_datetime(agent.get("last_checkpoint_at"))
        due = last_cp is None or (now - last_cp).total_seconds() >= checkpoint_interval

        move_triggered = False
        for symbol, entry_px in entry_prices.items():
            pos = positions.get(symbol)
            try:
                entry_val = float(entry_px)
            except (TypeError, ValueError):
                continue
            if not pos or entry_val <= 0:
                continue
            current = float(pos.get("current_price", 0.0) or 0.0)
            if current <= 0:
                continue
            if abs(current - entry_val) / entry_val * 100.0 >= self.settings.mission_checkpoint_move_pct:
                move_triggered = True
                break
        if not (due or move_triggered):
            return None

        agent_id = str(agent.get("agent_id", ""))
        skill_context = build_skill_prompt_block(self.skill_store, agent.get("skill_ids"))
        state = {
            "window_ends_at": agent.get("window_ends_at"),
            "legs": agent.get("execution_plan", {}).get("legs", []),
            "entry_prices": entry_prices,
            "positions": [
                {
                    "symbol": s,
                    "qty": float(p.get("qty", 0.0) or 0.0),
                    "current_price": float(p.get("current_price", 0.0) or 0.0),
                    "unrealized_pnl": float(p.get("unrealized_pnl", 0.0) or 0.0),
                }
                for s, p in positions.items()
            ],
        }
        try:
            decision = evaluate_execution_checkpoint(
                client=openai_client,
                model=self.settings.openai_model,
                state=state,
                skill_context=skill_context,
            )
        except Exception as exc:
            LOGGER.warning("Mission %s checkpoint failed: %s", agent_id, exc)
            agent["last_checkpoint_at"] = now.isoformat()
            return None

        agent["last_checkpoint_at"] = now.isoformat()
        verdict = decision.get("decision", "continue")
        self._append_decision_log(
            agent,
            "checkpoint",
            "",
            f"checkpoint_{verdict}",
            decision.get("rationale", ""),
        )
        self._publish_event(
            event="mission_checkpoint",
            agent_id=agent_id,
            payload=decision,
        )

        if verdict in {"abort", "take_profit"}:
            return self._flatten_mission(agent, now, f"checkpoint_{verdict}")
        if verdict == "tighten_stop":
            new_stop = decision.get("stop_pct")
            new_target = decision.get("target_pct")
            for leg in agent.get("execution_plan", {}).get("legs", []) or []:
                if not isinstance(leg, dict):
                    continue
                if new_stop:
                    leg["stop_pct"] = abs(float(new_stop))
                if new_target:
                    leg["target_pct"] = abs(float(new_target))
        return None

    def _entry_ready(self, leg: dict[str, Any], latest_close: float) -> bool:
        entry = leg.get("entry", {}) if isinstance(leg.get("entry"), dict) else {}
        entry_type = str(entry.get("type", "market")).lower()
        if entry_type != "limit":
            return True
        ref = entry.get("ref_price")
        try:
            ref_price = float(ref) if ref is not None else None
        except (TypeError, ValueError):
            ref_price = None
        if ref_price is None or ref_price <= 0:
            return True
        return latest_close <= ref_price

    def _mission_qty(self, agent: dict[str, Any], leg: dict[str, Any], latest_close: float) -> float:
        if latest_close <= 0:
            return 0.0
        risk_profile = str(agent.get("risk_profile", "balanced")).lower()
        exposure_limit = _RISK_EXPOSURE_LIMITS.get(risk_profile, _RISK_EXPOSURE_LIMITS["balanced"])
        budget = float(self.settings.summary_starting_deposit) * exposure_limit
        weight = float(leg.get("weight", 0.0) or 0.0)
        if weight <= 0.0:
            legs = agent.get("execution_plan", {}).get("legs", []) or []
            weight = 1.0 / max(len(legs), 1)
        notional = budget * weight
        return round(max(notional / latest_close, 0.0), 6)

    @staticmethod
    def _agent_asset_class(agent: dict[str, Any]) -> str:
        value = str(agent.get("asset_class") or "stock").strip().lower()
        return value if value in ("stock", "future") else "stock"

    def _latest_close(
        self, symbol: str, timeframe: str, asset_class: str = "stock"
    ) -> float:
        try:
            bars = self.trading_service.get_market_data(
                symbol=symbol, timeframe=timeframe, asset_class=asset_class
            )
        except Exception as exc:
            LOGGER.warning("Mission price fetch failed for %s: %s", symbol, exc)
            return 0.0
        if not bars:
            return 0.0
        try:
            return float(bars[-1].get("c", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _append_decision_log(
        self,
        agent: dict[str, Any],
        phase: str,
        symbol: str,
        action: str,
        reason: str,
        order: dict[str, Any] | None = None,
    ) -> None:
        log = agent.setdefault("decision_log", [])
        entry: dict[str, Any] = {
            "at": _now_iso(),
            "phase": phase,
            "symbol": symbol,
            "action": action,
            "reason": reason,
        }
        if order:
            entry["order_id"] = order.get("id")
            entry["filled_qty"] = float(order.get("filled_qty", 0.0) or 0.0)
            filled_px = order.get("filled_avg_price")
            try:
                entry["filled_avg_price"] = float(filled_px) if filled_px is not None else None
            except (TypeError, ValueError):
                entry["filled_avg_price"] = None
        log.append(entry)
        if len(log) > 500:
            agent["decision_log"] = log[-500:]

    def _ensure_strategy_is_unique(self, generated_payload: dict[str, Any]) -> None:
        research_refs = generated_payload.get("research_refs")
        if not isinstance(research_refs, list) or not research_refs:
            raise InvalidGeneratedDraftError(
                "Generated draft is missing research references. Regenerate strategy before creating the agent."
            )
        known_research_paths = {str(item.get("file_path", "")).strip() for item in research_refs if isinstance(item, dict)}
        fingerprint = build_strategy_fingerprint(
            symbols=[str(symbol).upper() for symbol in generated_payload.get("symbols", [])],
            ticker=str(generated_payload.get("ticker", "")).upper(),
            timeframe=str(generated_payload.get("timeframe", "")),
            risk_profile=str(generated_payload.get("risk_profile", "balanced")),
            strategy=generated_payload.get("strategy", {}),
        )
        for existing in self.list_agents():
            existing_fingerprint = build_strategy_fingerprint(
                symbols=[str(symbol).upper() for symbol in existing.get("symbols", [])],
                ticker=str(existing.get("ticker", "")).upper(),
                timeframe=str(existing.get("timeframe", "")),
                risk_profile=str(existing.get("risk_profile", "balanced")),
                strategy=existing.get("strategy", {}),
            )
            if existing_fingerprint == fingerprint:
                raise DuplicateStrategyError(
                    f"Similar strategy already exists as agent {existing.get('agent_id')} ({existing.get('name')}). "
                    "Generate a fresh strategy before creating a new agent."
                )

        symbols = [str(symbol).upper() for symbol in generated_payload.get("symbols", [])]
        timeframe = str(generated_payload.get("timeframe", ""))
        recent_research = self.market_research_service.research_store.list_recent_records(
            symbols=symbols,
            timeframe=timeframe,
            scope="generation",
        )
        for research_entry in recent_research:
            if str(research_entry.get("file_path", "")).strip() in known_research_paths:
                continue
            if str(research_entry.get("strategy_fingerprint", "")) == fingerprint:
                raise DuplicateStrategyError(
                    f"Strategy already researched recently ({research_entry.get('created_at')}). "
                    "Regenerate to avoid repeating prior research output."
                )

    def list_agents(self) -> list[dict[str, Any]]:
        return sorted(
            self.store.list_agents(),
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        record = self.store.get_agent(agent_id)
        if record is None:
            raise KeyError(f"Agent {agent_id} not found.")
        return record

    def set_agent_skills(self, agent_id: str, skill_ids: list[str] | None) -> dict[str, Any]:
        record = self.get_agent(agent_id)
        normalized = normalize_skill_ids(skill_ids)
        if normalized and self.skill_store is not None:
            for skill_id in normalized:
                try:
                    self.skill_store.get_skill(skill_id)
                except Exception as exc:
                    raise KeyError(f"Skill {skill_id} not found.") from exc
        record["skill_ids"] = normalized
        record["updated_at"] = _now_iso()
        updated = self.store.save_agent(record)
        self._publish_event(
            event="agent_skills_changed",
            agent_id=agent_id,
            payload={"skill_ids": normalized},
        )
        return updated

    def activate_agent(self, agent_id: str) -> dict[str, Any]:
        record = self.get_agent(agent_id)
        record = self._maybe_run_activation_bootstrap(record)
        record["status"] = "active"
        record["updated_at"] = _now_iso()
        record["startup_pause_pending"] = False
        record["startup_pause_source_status"] = None
        updated = self.store.save_agent(record)
        self._publish_event(
            event="agent_status_changed",
            agent_id=agent_id,
            payload={"status": "active"},
        )
        return updated

    def _maybe_run_activation_bootstrap(self, agent: dict[str, Any]) -> dict[str, Any]:
        if agent.get("activation_bootstrap_attempted_at"):
            return agent
        if self._agent_has_executed_buy(agent):
            return agent

        attempted_at = _now_iso()
        agent["activation_bootstrap_attempted_at"] = attempted_at

        timeframe = str(agent.get("timeframe") or self.settings.bar_timeframe)
        asset_class = self._agent_asset_class(agent)
        symbol = str(agent.get("ticker") or "").upper().strip()
        if not symbol:
            symbols = [str(item).upper().strip() for item in agent.get("symbols", []) if str(item).strip()]
            symbol = symbols[0] if symbols else ""

        price = 0.0
        approved_qty = 0.0
        order_status = "bootstrap_skipped"
        policy_action = "activation_bootstrap_skipped"
        policy_reason = "bootstrap skipped because no primary ticker symbol was available"
        order: dict[str, Any] = {}
        executed_side = "hold"
        executed_qty = 0.0
        submitted_status: str | None = None
        final_status: str | None = None
        filled_avg_price: float | None = None

        if symbol:
            bars = self.trading_service.get_market_data(
                symbol=symbol, timeframe=timeframe, asset_class=asset_class
            )
            if bars:
                price = float(bars[-1].get("c", 0.0) or 0.0)
                position_state = self._build_agent_position_state(agent)
                approved_qty = self._approved_qty_for_side(
                    agent=agent,
                    symbol=symbol,
                    side="buy",
                    latest_price=price,
                    position_state=position_state,
                )
                if approved_qty > 0.0:
                    order = self.trading_service.execute_signal_order(
                        symbol=symbol,
                        signal="BUY",
                        qty=approved_qty,
                        asset_class=asset_class,
                    ) or {}
                    executed_side = str(order.get("side", "buy")).lower()
                    submitted_status = str(order.get("submitted_status", order.get("status", "")) or "")
                    final_status = str(order.get("final_status", order.get("status", "")) or "")
                    order_status = final_status or "submitted"
                    executed_qty = float(
                        order.get(
                            "filled_qty",
                            0.0,
                        )
                        or 0.0
                    )
                    raw_filled_avg = order.get("filled_avg_price")
                    try:
                        filled_avg_price = float(raw_filled_avg) if raw_filled_avg is not None else None
                    except (TypeError, ValueError):
                        filled_avg_price = None
                    policy_action = "activation_bootstrap_executed"
                    policy_reason = "initial activation bootstrap buy submitted for inventory seeding"
                else:
                    order_status = "risk_blocked"
                    policy_reason = "bootstrap buy quantity was blocked by risk or sizing guardrails"
            else:
                order_status = "market_data_unavailable"
                policy_reason = "bootstrap skipped because no market bars were available for primary ticker"

        value = executed_qty * price
        trade_snapshot = {
            "symbol": symbol,
            "signal": "BUY",
            "side": executed_side,
            "qty": executed_qty,
            "latest_close": price,
            "value": value,
            "order_status": order_status,
            "timeframe": timeframe,
            "policy_action": policy_action,
            "policy_reason": policy_reason,
            "order_id": order.get("id"),
            "submitted_status": submitted_status,
            "final_status": final_status,
            "filled_qty": executed_qty,
            "filled_avg_price": filled_avg_price,
        }
        run_record = {
            "executed_at": attempted_at,
            "trades": [trade_snapshot],
            "spent": round(value if executed_side == "buy" else 0.0, 4),
            "earned": 0.0,
            "net_pnl": round(-value if executed_side == "buy" else 0.0, 4),
        }
        updated_agent = self.store.append_run(agent_id=str(agent["agent_id"]), run_record=run_record)
        updated_agent["activation_bootstrap_attempted_at"] = attempted_at
        updated_agent["last_run_at"] = attempted_at
        updated_agent["updated_at"] = attempted_at
        updated_agent["activation_bootstrap_status"] = order_status
        if executed_side == "buy" and executed_qty > 0.0:
            updated_agent["activation_bootstrap_completed_at"] = attempted_at
        self.store.save_agent(updated_agent)
        return updated_agent

    def _agent_has_executed_buy(self, agent: dict[str, Any]) -> bool:
        for run in agent.get("runs", []):
            for trade in run.get("trades", []):
                side = str(trade.get("side", "hold")).lower().strip()
                qty = float(trade.get("qty", 0.0) or 0.0)
                if side == "buy" and qty > 0.0:
                    return True
        return False

    def pause_agent(self, agent_id: str) -> dict[str, Any]:
        record = self.get_agent(agent_id)
        record["status"] = "paused"
        record["updated_at"] = _now_iso()
        record["startup_pause_pending"] = False
        record["startup_pause_source_status"] = None
        updated = self.store.save_agent(record)
        self._publish_event(
            event="agent_status_changed",
            agent_id=agent_id,
            payload={"status": "paused"},
        )
        return updated

    def pause_active_agents_on_startup(self) -> list[str]:
        paused_agent_ids: list[str] = []
        timestamp = _now_iso()
        for record in self.list_agents():
            agent_id = str(record.get("agent_id", "")).strip()
            if not agent_id or str(record.get("status", "paused")) != "active":
                continue
            record["status"] = "paused"
            record["updated_at"] = timestamp
            record["startup_pause_pending"] = True
            record["startup_pause_at"] = timestamp
            record["startup_pause_source_status"] = "active"
            self.store.save_agent(record)
            paused_agent_ids.append(agent_id)
            self._publish_event(
                event="agent_status_changed",
                agent_id=agent_id,
                payload={"status": "paused", "reason": "startup_pause"},
            )
        return paused_agent_ids

    def delete_agent(self, agent_id: str) -> dict[str, Any]:
        record = self.get_agent(agent_id)
        if str(record.get("status", "paused")) == "active":
            record["status"] = "paused"
            record["updated_at"] = _now_iso()
            self.store.save_agent(record)
            self._publish_event(
                event="agent_status_changed",
                agent_id=agent_id,
                payload={"status": "paused"},
            )
        deleted = self.store.delete_agent(agent_id)
        self._publish_event(
            event="agent_deleted",
            agent_id=agent_id,
            payload={"name": str(deleted.get("name", ""))},
        )
        return deleted

    def run_active_agents_cycle(self) -> list[dict[str, Any]]:
        updated: list[dict[str, Any]] = []
        now = datetime.now(UTC)
        for agent in self.list_agents():
            if agent.get("status") != "active":
                continue
            if agent.get("mission_status"):
                # Mission agents are driven by run_mission_cycle, not the legacy SMA loop.
                continue
            agent = self._maybe_refresh_strategy(agent=agent, now=now)
            runtime = self.get_agent_runtime(agent=agent, now=now)
            if not runtime["can_run_now"]:
                self._publish_event(
                    event="agent_run_skipped",
                    agent_id=str(agent.get("agent_id", "")),
                    payload={
                        "reason": runtime["next_run_reason"],
                        "next_run_at": runtime["next_run_at"],
                        "market_status_label": runtime["market_status_label"],
                    },
                )
                continue
            try:
                updated.append(self.run_agent_once(agent_id=agent["agent_id"]))
            except Exception as exc:
                LOGGER.exception("Agent %s run failed: %s", agent.get("agent_id"), exc)
        return updated

    def get_agent_runtime(self, agent: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        current_time = now or datetime.now(UTC)
        status = str(agent.get("status", "paused"))
        timeframe = str(agent.get("timeframe") or self.settings.bar_timeframe)
        min_interval = _timeframe_floor_interval_seconds(timeframe)
        interval_seconds = max(
            int(agent.get("interval_seconds", self.settings.agent_run_interval_seconds) or 0),
            5,
            min_interval,
        )

        market_runtime = self._resolve_market_runtime()
        next_market_open = _parse_optional_iso_datetime(market_runtime.get("next_market_open"))

        next_interval_at: datetime | None = None
        last_run_value = agent.get("last_run_at")
        if isinstance(last_run_value, str) and last_run_value.strip():
            try:
                next_interval_at = _parse_iso_datetime(last_run_value) + timedelta(seconds=interval_seconds)
            except ValueError:
                next_interval_at = None

        can_run_now = status == "active"
        next_run_reason = "ready"
        if status != "active":
            can_run_now = False
            next_run_reason = "paused"
        elif next_interval_at is not None and current_time < next_interval_at:
            can_run_now = False
            next_run_reason = "interval_wait"

        if not bool(market_runtime["is_market_open"]):
            can_run_now = False
            if status == "active":
                next_run_reason = "market_closed"

        next_run_at_dt: datetime | None = None
        if can_run_now:
            next_run_at_dt = current_time
        elif status == "active":
            if next_run_reason == "interval_wait":
                next_run_at_dt = next_interval_at
            elif next_run_reason == "market_closed":
                candidates = [candidate for candidate in (next_interval_at, next_market_open) if candidate is not None]
                next_run_at_dt = max(candidates) if candidates else None

        return {
            "can_run_now": can_run_now,
            "next_run_at": _to_iso_optional(next_run_at_dt),
            "next_run_reason": next_run_reason,
            "is_market_open": bool(market_runtime["is_market_open"]),
            "next_market_open": _to_iso_optional(next_market_open),
            "next_market_close": _to_iso_optional(_parse_optional_iso_datetime(market_runtime.get("next_market_close"))),
            "market_status_label": str(market_runtime["market_status_label"]),
        }

    def _resolve_market_runtime(self) -> dict[str, Any]:
        if hasattr(self.trading_service, "get_market_runtime"):
            runtime = self.trading_service.get_market_runtime()
            if isinstance(runtime, dict):
                return runtime
        return {
            "is_market_open": True,
            "next_market_open": None,
            "next_market_close": None,
            "market_status_label": "market_unknown",
        }

    def run_agent_once(self, agent_id: str) -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        agent, refs_changed = self._sync_agent_ticker_research_ref(agent=agent)
        if refs_changed:
            agent["updated_at"] = _now_iso()
            self.store.save_agent(agent)
        timeframe = str(agent.get("timeframe") or self.settings.bar_timeframe)
        asset_class = self._agent_asset_class(agent)
        plan_symbols = self._execution_plan_symbols(agent)
        symbols = plan_symbols or [str(sym).upper() for sym in agent.get("symbols", [])]
        position_state = self._build_agent_position_state(agent)
        latest_bar_timestamps = {
            str(symbol).upper(): str(ts)
            for symbol, ts in (agent.get("latest_bar_timestamps", {}) or {}).items()
            if str(symbol).strip() and str(ts).strip()
        }

        self._publish_event(
            event="agent_run_started",
            agent_id=agent_id,
            payload={
                "agent_name": agent.get("name", ""),
                "timeframe": timeframe,
                "symbols": symbols,
            },
        )

        try:
            trades: list[dict[str, Any]] = []
            spent = 0.0
            earned = 0.0
            for symbol in symbols:
                bars = self.trading_service.get_market_data(
                    symbol=symbol, timeframe=timeframe, asset_class=asset_class
                )
                signal_result = self.trading_service.calculate_signal(bars)
                price = float(signal_result.latest_close)
                signal = str(signal_result.signal).upper()
                side = _signal_to_side(signal)
                latest_bar_at = _normalize_bar_timestamp(bars[-1].get("t")) if bars else None
                if latest_bar_at and latest_bar_timestamps.get(symbol) == latest_bar_at:
                    trade_snapshot = {
                        "symbol": symbol,
                        "signal": signal,
                        "side": "hold",
                        "qty": 0.0,
                        "latest_close": price,
                        "value": 0.0,
                        "order_status": "no_new_bar",
                        "timeframe": timeframe,
                        "policy_action": "skip_no_new_bar",
                        "policy_reason": "latest market bar has not changed since previous run",
                    }
                    trades.append(trade_snapshot)
                    self._publish_event(
                        event="agent_trade_decision",
                        agent_id=agent_id,
                        payload=trade_snapshot,
                    )
                    continue

                position_qty = float(position_state.get(symbol, {}).get("qty", 0.0) or 0.0)
                policy = self._evaluate_symbol_policy(
                    agent=agent,
                    symbol=symbol,
                    signal=signal,
                    position_qty=position_qty,
                )
                approved_qty = self._approved_qty_for_side(
                    agent=agent,
                    symbol=symbol,
                    side=side,
                    latest_price=price,
                    position_state=position_state,
                )

                order: dict[str, Any] = {}
                executed_side = "hold"
                executed_qty = 0.0
                order_status = "none"
                submitted_status: str | None = None
                final_status: str | None = None
                filled_avg_price: float | None = None

                if side in {"buy", "sell"} and not policy["allowed"]:
                    order_status = "policy_blocked"
                elif side in {"buy", "sell"} and approved_qty <= 0.0:
                    policy = {
                        "allowed": False,
                        "policy_action": "blocked_by_risk",
                        "policy_reason": "position/risk guardrails blocked the order",
                    }
                    order_status = "risk_blocked"
                elif side in {"buy", "sell"}:
                    submitted = self.trading_service.execute_signal_order(
                        symbol=symbol,
                        signal=signal,
                        qty=approved_qty,
                        asset_class=asset_class,
                    )
                    order = submitted or {}
                    executed_side = str(order.get("side", side)).lower()
                    submitted_status = str(order.get("submitted_status", order.get("status", "")) or "")
                    final_status = str(order.get("final_status", order.get("status", "")) or "")
                    order_status = final_status or "submitted"
                    executed_qty = float(
                        order.get(
                            "filled_qty",
                            0.0,
                        )
                        or 0.0
                    )
                    raw_filled_avg = order.get("filled_avg_price")
                    try:
                        filled_avg_price = float(raw_filled_avg) if raw_filled_avg is not None else None
                    except (TypeError, ValueError):
                        filled_avg_price = None
                    if executed_qty > 0.0:
                        self._apply_position_fill(
                            position_state=position_state,
                            symbol=symbol,
                            side=executed_side,
                            qty=executed_qty,
                            price=filled_avg_price or price,
                        )
                value = executed_qty * price
                if executed_side == "buy":
                    spent += value
                elif executed_side == "sell":
                    earned += value
                trade_snapshot = {
                    "symbol": symbol,
                    "signal": signal,
                    "side": executed_side,
                    "qty": executed_qty,
                    "latest_close": price,
                    "value": value,
                    "order_status": order_status,
                    "timeframe": timeframe,
                    "policy_action": str(policy["policy_action"]),
                    "policy_reason": str(policy["policy_reason"]),
                    "order_id": order.get("id"),
                    "submitted_status": submitted_status,
                    "final_status": final_status,
                    "filled_qty": executed_qty,
                    "filled_avg_price": filled_avg_price,
                }
                latest_bar_timestamps[symbol] = latest_bar_at or ""
                trades.append(trade_snapshot)
                self._publish_event(
                    event="agent_trade_decision",
                    agent_id=agent_id,
                    payload=trade_snapshot,
                )

            executed_at = _now_iso()
            run_record = {
                "executed_at": executed_at,
                "trades": trades,
                "spent": round(spent, 4),
                "earned": round(earned, 4),
                "net_pnl": round(earned - spent, 4),
            }
            updated_agent = self.store.append_run(agent_id=agent_id, run_record=run_record)
            updated_agent["last_run_at"] = executed_at
            updated_agent["updated_at"] = executed_at
            updated_agent["latest_bar_timestamps"] = latest_bar_timestamps
            self.store.save_agent(updated_agent)
            self._publish_event(
                event="agent_run_completed",
                agent_id=agent_id,
                payload=run_record,
            )
            return updated_agent
        except Exception as exc:
            self._publish_event(
                event="agent_run_failed",
                agent_id=agent_id,
                payload={"error": str(exc)},
            )
            raise

    def _publish_event(self, event: str, agent_id: str, payload: dict[str, Any]) -> None:
        if self.monitor_stream is None:
            return
        self.monitor_stream.publish(event=event, agent_id=agent_id, payload=payload)

    def get_live_positions(self) -> list[dict[str, Any]]:
        return self.trading_service.get_open_positions()

    def get_live_transactions(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.trading_service.get_fill_activities(limit=limit)

    def get_live_summary(self, period: str = "all") -> dict[str, Any]:
        account = self.trading_service.get_account_overview()
        equity = float(account.get("equity", 0.0) or 0.0)
        last_equity = float(account.get("last_equity", 0.0) or 0.0)
        day_change = float(account.get("day_change", equity - last_equity) or 0.0)
        day_change_pct = float(account.get("day_change_pct", 0.0) or 0.0)
        return {
            "period": period,
            "account_id": str(account.get("id", "")),
            "account_number": str(account.get("account_number", "")),
            "status": str(account.get("status", "unknown")),
            "buying_power": float(account.get("buying_power", 0.0) or 0.0),
            "cash": float(account.get("cash", 0.0) or 0.0),
            "equity": equity,
            "portfolio_value": float(account.get("portfolio_value", equity) or equity),
            "long_market_value": float(account.get("long_market_value", 0.0) or 0.0),
            "short_market_value": float(account.get("short_market_value", 0.0) or 0.0),
            "last_equity": last_equity,
            "day_change": day_change,
            "day_change_pct": day_change_pct,
        }

    def get_positions(self) -> list[dict[str, Any]]:
        positions_by_symbol: dict[str, dict[str, Any]] = {}
        for agent in self.list_agents():
            for run in agent.get("runs", []):
                for trade in run.get("trades", []):
                    symbol = str(trade.get("symbol", "")).upper()
                    if not symbol:
                        continue
                    qty = float(trade.get("qty", 0.0) or 0.0)
                    side = str(trade.get("side", "hold")).lower()
                    value = float(trade.get("value", 0.0) or 0.0)
                    price = float(trade.get("latest_close", 0.0) or 0.0)
                    row = positions_by_symbol.setdefault(
                        symbol,
                        {
                            "symbol": symbol,
                            "qty": 0.0,
                            "cost_basis": 0.0,
                            "realized_pnl": 0.0,
                            "latest_price": 0.0,
                            "agent_ids": [],
                        },
                    )
                    if side == "buy":
                        row["qty"] += qty
                        row["cost_basis"] += value
                    elif side == "sell":
                        current_qty = float(row["qty"])
                        if current_qty <= 0:
                            row["latest_price"] = price
                            continue
                        avg_cost = float(row["cost_basis"]) / current_qty if current_qty > 0 else 0.0
                        matched_qty = min(qty, current_qty)
                        row["qty"] = current_qty - matched_qty
                        row["cost_basis"] -= matched_qty * avg_cost
                        row["realized_pnl"] += (price - avg_cost) * matched_qty
                    row["latest_price"] = price
                    if agent["agent_id"] not in row["agent_ids"]:
                        row["agent_ids"].append(agent["agent_id"])

        positions: list[dict[str, Any]] = []
        for row in positions_by_symbol.values():
            qty = round(float(row["qty"]), 4)
            cost_basis = max(float(row["cost_basis"]), 0.0)
            avg_entry = round(cost_basis / qty, 4) if qty > 0 else 0.0
            market_value = round(qty * float(row["latest_price"]), 4)
            unrealized_pnl = round(market_value - cost_basis, 4)
            positions.append(
                {
                    "symbol": row["symbol"],
                    "qty": qty,
                    "avg_entry_price": avg_entry,
                    "market_value": market_value,
                    "unrealized_pnl": unrealized_pnl,
                    "realized_pnl": round(float(row["realized_pnl"]), 4),
                    "agent_ids": row["agent_ids"],
                }
            )
        return sorted(positions, key=lambda item: item["symbol"])

    def get_transactions(self, limit: int = 200, mode: Literal["executed", "decisions"] = "executed") -> list[dict[str, Any]]:
        transactions: list[dict[str, Any]] = []
        include_decisions = mode == "decisions"
        for agent in self.list_agents():
            agent_id = str(agent.get("agent_id", ""))
            agent_name = str(agent.get("name", ""))
            for run in agent.get("runs", []):
                executed_at = str(run.get("executed_at", ""))
                for trade in run.get("trades", []):
                    side = str(trade.get("side", "hold")).lower()
                    qty = float(trade.get("qty", 0.0) or 0.0)
                    if not include_decisions and (side not in {"buy", "sell"} or qty <= 0.0):
                        continue
                    price = float(trade.get("latest_close", 0.0) or 0.0)
                    value = float(trade.get("value", qty * price) or 0.0)
                    pnl = value if side == "sell" else (-value if side == "buy" else 0.0)
                    transactions.append(
                        {
                            "executed_at": executed_at,
                            "agent_id": agent_id,
                            "agent_name": agent_name,
                            "symbol": str(trade.get("symbol", "")).upper(),
                            "side": side,
                            "signal": str(trade.get("signal", "HOLD")).upper(),
                            "qty": qty,
                            "price": price,
                            "value": value,
                            "order_status": str(trade.get("order_status", "none")),
                            "timeframe": str(trade.get("timeframe", "")),
                            "policy_action": str(trade.get("policy_action")) if trade.get("policy_action") is not None else None,
                            "policy_reason": str(trade.get("policy_reason")) if trade.get("policy_reason") is not None else None,
                            "pnl": round(pnl, 4),
                        }
                    )
        transactions.sort(key=lambda item: item["executed_at"], reverse=True)
        normalized_limit = max(1, int(limit))
        return transactions[:normalized_limit]

    def get_summary(self, period: str = "all") -> dict[str, Any]:
        cutoff = _period_to_cutoff(period)
        spent = 0.0
        earned = 0.0
        run_count = 0
        trade_count = 0
        for agent in self.list_agents():
            for run in agent.get("runs", []):
                executed_at = _parse_iso_datetime(str(run.get("executed_at")))
                if cutoff and executed_at < cutoff:
                    continue
                run_count += 1
                trade_count += len(run.get("trades", []))
                spent += float(run.get("spent", 0.0) or 0.0)
                earned += float(run.get("earned", 0.0) or 0.0)

        deposited = float(self.settings.summary_starting_deposit)
        net_pnl = earned - spent
        return {
            "period": period,
            "deposited": round(deposited, 4),
            "spent": round(spent, 4),
            "earned": round(earned, 4),
            "net_pnl": round(net_pnl, 4),
            "balance": round(deposited + net_pnl, 4),
            "run_count": run_count,
            "trade_count": trade_count,
        }

    def _maybe_refresh_strategy(self, agent: dict[str, Any], now: datetime) -> dict[str, Any]:
        agent, refs_changed = self._sync_agent_ticker_research_ref(agent=agent)
        if refs_changed:
            agent["updated_at"] = now.isoformat()
        reasons = self._followup_reasons(agent=agent, now=now)
        if not reasons:
            if refs_changed:
                return self.store.save_agent(agent)
            return agent
        if not self._followup_refresh_allowed(agent=agent, now=now):
            if refs_changed:
                return self.store.save_agent(agent)
            return agent

        agent_id = str(agent.get("agent_id", ""))
        triggered_thought = self._build_followup_thought_summary(
            stage="triggered",
            reasons=reasons,
            decision="run_strategy_refresh",
            actions=["schedule_research_refresh", "rebuild_execution_plan"],
            next_steps=["attempt_strategy_refresh_now"],
        )
        self._publish_event(
            event="agent_followup_triggered",
            agent_id=agent_id,
            payload={
                "reasons": reasons,
                "thought_summary": triggered_thought,
            },
        )
        try:
            symbols = [str(symbol).upper() for symbol in agent.get("symbols", []) if str(symbol).strip()]
            refreshed = self.market_research_service.build_strategy(
                symbols=symbols,
                timeframe=str(agent.get("timeframe") or self.settings.bar_timeframe),
                risk_profile=str(agent.get("risk_profile") or "balanced"),
                research_scope="runtime",
                skill_ids=normalize_skill_ids(agent.get("skill_ids")),
                asset_class=self._agent_asset_class(agent),
            )
            refreshed_execution_plan = build_agent_execution_plan(
                strategy=refreshed.strategy,
                fallback_symbols=refreshed.symbols,
                default_interval_seconds=int(
                    agent.get("interval_seconds", self.settings.agent_run_interval_seconds)
                    or self.settings.agent_run_interval_seconds
                ),
            )
            timestamp = now.isoformat()
            agent["symbols"] = refreshed.symbols
            agent["ticker"] = refreshed.ticker
            agent["signal_context"] = refreshed.signal_context
            agent["strategy"] = refreshed.strategy
            agent["strategy_evaluation"] = refreshed.evaluation
            agent["execution_plan"] = refreshed_execution_plan
            agent["research_refs"] = refreshed.research_refs
            agent, _ = self._sync_agent_ticker_research_ref(agent=agent)
            agent["updated_at"] = timestamp
            agent["strategy_last_refreshed_at"] = timestamp
            updated = self.store.save_agent(agent)
            refreshed_thought = self._build_followup_thought_summary(
                stage="refreshed",
                reasons=reasons,
                decision="strategy_refreshed",
                actions=[
                    "update_signal_context",
                    "update_strategy_evaluation",
                    "update_execution_plan",
                ],
                next_steps=[
                    "resume_policy_enforced_runs",
                    "monitor_next_scheduler_cycle",
                ],
            )
            self._publish_event(
                event="agent_strategy_refreshed",
                agent_id=agent_id,
                payload={
                    "reasons": reasons,
                    "ticker": refreshed.ticker,
                    "symbols": refreshed.symbols,
                    "thought_summary": refreshed_thought,
                },
            )
            return updated
        except Exception as exc:
            failed_decision = "pause_agent_for_safety" if "drawdown_threshold" in reasons else "retry_on_next_cycle"
            failed_next_steps = (
                ["agent_auto_paused", "manual_review_required"]
                if "drawdown_threshold" in reasons
                else ["keep_agent_state", "retry_followup_on_future_cycle"]
            )
            failed_thought = self._build_followup_thought_summary(
                stage="failed",
                reasons=reasons,
                decision=failed_decision,
                actions=["record_refresh_error"],
                next_steps=failed_next_steps,
            )
            self._publish_event(
                event="agent_strategy_refresh_failed",
                agent_id=agent_id,
                payload={
                    "reasons": reasons,
                    "error": str(exc),
                    "thought_summary": failed_thought,
                },
            )
            if "drawdown_threshold" in reasons:
                agent["status"] = "paused"
                agent["updated_at"] = now.isoformat()
                paused = self.store.save_agent(agent)
                self._publish_event(
                    event="agent_status_changed",
                    agent_id=agent_id,
                    payload={"status": "paused"},
                )
                return paused
            return agent

    def _sync_agent_ticker_research_ref(self, agent: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        ticker = str(agent.get("ticker") or "").upper().strip()
        if not ticker:
            symbols = [str(symbol).upper().strip() for symbol in agent.get("symbols", []) if str(symbol).strip()]
            ticker = symbols[0] if symbols else ""
        if not ticker:
            return agent, False
        timeframe = str(agent.get("timeframe") or self.settings.bar_timeframe)
        runtime_ref = self.market_research_service.research_store.find_latest_record(
            ticker=ticker,
            timeframe=timeframe,
            scope="runtime",
        )
        generation_ref = self.market_research_service.research_store.find_latest_record(
            ticker=ticker,
            timeframe=timeframe,
            scope="generation",
        )
        chosen_ref = runtime_ref or generation_ref
        if not chosen_ref:
            return agent, False
        normalized_ref = {
            "created_at": str(chosen_ref.get("created_at", "")),
            "file_path": str(chosen_ref.get("file_path", "")),
            "strategy_fingerprint": str(chosen_ref.get("strategy_fingerprint", "")),
            "ticker": str(chosen_ref.get("ticker", "")).upper(),
            "timeframe": str(chosen_ref.get("timeframe", "")),
            "symbols": [str(symbol).upper() for symbol in chosen_ref.get("symbols", [])],
            "research_scope": str(chosen_ref.get("research_scope", "unknown")),
        }
        existing_refs = agent.get("research_refs", [])
        existing_primary = existing_refs[0] if isinstance(existing_refs, list) and existing_refs else {}
        if (
            isinstance(existing_primary, dict)
            and str(existing_primary.get("file_path", "")) == normalized_ref["file_path"]
            and str(existing_primary.get("strategy_fingerprint", "")) == normalized_ref["strategy_fingerprint"]
        ):
            return agent, False
        agent["research_refs"] = [normalized_ref]
        return agent, True

    def _build_followup_thought_summary(
        self,
        stage: str,
        reasons: list[str],
        decision: str,
        actions: list[str],
        next_steps: list[str],
    ) -> dict[str, Any]:
        reason_labels = [_followup_reason_label(reason) for reason in reasons]
        return {
            "stage": stage,
            "reasons": reasons,
            "reason_labels": reason_labels,
            "decision": decision,
            "actions": actions,
            "next_steps": next_steps,
        }

    def _followup_refresh_allowed(self, agent: dict[str, Any], now: datetime) -> bool:
        last_refreshed = _parse_optional_iso_datetime(agent.get("strategy_last_refreshed_at"))
        if last_refreshed is None:
            return True
        return (now - last_refreshed) >= timedelta(hours=_FOLLOWUP_REFRESH_COOLDOWN_HOURS)

    def _followup_reasons(self, agent: dict[str, Any], now: datetime) -> list[str]:
        reasons: list[str] = []
        latest_ref = _latest_research_created_at(agent.get("research_refs", []))
        if latest_ref is None or (now - latest_ref) >= timedelta(hours=_FOLLOWUP_STALE_HOURS):
            reasons.append("stale_research")

        evaluation = agent.get("strategy_evaluation", {})
        try:
            confidence = float(evaluation.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            threshold = float(evaluation.get("threshold", 0.7) or 0.7)
        except (TypeError, ValueError):
            threshold = 0.7
        recommendation = str(evaluation.get("recommendation", "not_recommended")).lower()
        if recommendation != "recommended" and confidence < threshold:
            reasons.append("low_confidence")

        recent_runs = list(agent.get("runs", []))[-_FOLLOWUP_HOLD_STREAK_RUNS:]
        if len(recent_runs) >= _FOLLOWUP_HOLD_STREAK_RUNS and all(_run_is_hold_only(run) for run in recent_runs):
            reasons.append("hold_streak")

        net_pnl = sum(float(run.get("net_pnl", 0.0) or 0.0) for run in agent.get("runs", []))
        drawdown_threshold = float(self.settings.summary_starting_deposit) * _FOLLOWUP_DRAWDOWN_RATIO
        if net_pnl <= -drawdown_threshold:
            reasons.append("drawdown_threshold")
        return reasons

    def _execution_plan_symbols(self, agent: dict[str, Any]) -> list[str]:
        execution_plan = agent.get("execution_plan", {})
        raw = execution_plan.get("symbols", []) if isinstance(execution_plan, dict) else []
        normalized: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                symbol = str(item).upper().strip()
                if symbol and symbol not in normalized:
                    normalized.append(symbol)
        return normalized

    def _evaluate_symbol_policy(
        self,
        agent: dict[str, Any],
        symbol: str,
        signal: str,
        position_qty: float,
    ) -> dict[str, Any]:
        normalized_symbol = str(symbol).upper().strip()
        normalized_signal = str(signal).upper().strip()
        allowed_symbols = set(self._execution_plan_symbols(agent) or [str(item).upper() for item in agent.get("symbols", [])])
        if normalized_symbol not in allowed_symbols:
            return {
                "allowed": False,
                "policy_action": "blocked_by_plan",
                "policy_reason": "symbol is not included in execution plan",
            }

        if normalized_signal == "BUY" and not self._plan_rule_allows(agent, "entry_rules", "buy"):
            return {
                "allowed": False,
                "policy_action": "blocked_by_plan",
                "policy_reason": "entry rules do not allow buy signals",
            }
        if normalized_signal == "SELL" and not self._plan_rule_allows(agent, "exit_rules", "sell"):
            return {
                "allowed": False,
                "policy_action": "blocked_by_plan",
                "policy_reason": "exit rules do not allow sell signals",
            }

        evaluation = agent.get("strategy_evaluation", {})
        try:
            confidence = float(evaluation.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            threshold = float(evaluation.get("threshold", 0.7) or 0.7)
        except (TypeError, ValueError):
            threshold = 0.7
        recommendation = str(evaluation.get("recommendation", "not_recommended")).lower()
        if normalized_signal == "BUY" and recommendation != "recommended" and confidence < threshold:
            return {
                "allowed": False,
                "policy_action": "blocked_by_confidence",
                "policy_reason": "strategy confidence is below threshold for new buy entries",
            }

        suggested_action = self._strategy_pick_action(agent=agent, symbol=normalized_symbol)
        if normalized_signal == "BUY" and suggested_action in {"reduce", "sell", "hold"}:
            return {
                "allowed": False,
                "policy_action": "blocked_by_strategy",
                "policy_reason": f"strategy suggested_action={suggested_action} does not allow buy entry",
            }
        if normalized_signal == "SELL" and position_qty <= 0:
            return {
                "allowed": False,
                "policy_action": "blocked_by_inventory",
                "policy_reason": "cannot sell without an existing long position",
            }
        return {
            "allowed": True,
            "policy_action": "policy_passed",
            "policy_reason": "strategy and execution policy checks passed",
        }

    def _plan_rule_allows(self, agent: dict[str, Any], key: str, expected_side: str) -> bool:
        execution_plan = agent.get("execution_plan", {})
        rules = execution_plan.get(key, []) if isinstance(execution_plan, dict) else []
        if not isinstance(rules, list) or not rules:
            return True
        expected = expected_side.lower()
        for rule in rules:
            if expected in str(rule).lower():
                return True
        return False

    def _strategy_pick_action(self, agent: dict[str, Any], symbol: str) -> str:
        strategy = agent.get("strategy", {})
        picks = strategy.get("picks", []) if isinstance(strategy, dict) else []
        if not isinstance(picks, list):
            return "unknown"
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            if str(pick.get("symbol", "")).upper().strip() != symbol:
                continue
            action = str(pick.get("suggested_action", "")).lower().strip()
            return action or "unknown"
        return "unknown"

    def _approved_qty_for_side(
        self,
        agent: dict[str, Any],
        symbol: str,
        side: str,
        latest_price: float,
        position_state: dict[str, dict[str, float]],
    ) -> float:
        normalized_side = str(side).lower().strip()
        base_qty = float(self.settings.order_qty)
        symbol_state = position_state.get(symbol, {})
        held_qty = max(float(symbol_state.get("qty", 0.0) or 0.0), 0.0)
        if normalized_side == "sell":
            return round(min(base_qty, held_qty), 6)
        if normalized_side != "buy":
            return 0.0
        if latest_price <= 0:
            return 0.0
        risk_profile = str(agent.get("risk_profile", "balanced")).lower().strip()
        exposure_limit = _RISK_EXPOSURE_LIMITS.get(risk_profile, _RISK_EXPOSURE_LIMITS["balanced"])
        max_notional = float(self.settings.summary_starting_deposit) * exposure_limit
        current_notional = held_qty * latest_price
        remaining_notional = max(max_notional - current_notional, 0.0)
        if remaining_notional <= 0.0:
            return 0.0
        max_qty = remaining_notional / latest_price
        return round(min(base_qty, max_qty), 6)

    def _build_agent_position_state(self, agent: dict[str, Any]) -> dict[str, dict[str, float]]:
        positions: dict[str, dict[str, float]] = {}
        for run in agent.get("runs", []):
            for trade in run.get("trades", []):
                symbol = str(trade.get("symbol", "")).upper().strip()
                if not symbol:
                    continue
                side = str(trade.get("side", "hold")).lower().strip()
                qty = float(trade.get("qty", 0.0) or 0.0)
                price = float(trade.get("latest_close", 0.0) or 0.0)
                if qty <= 0 or side not in {"buy", "sell"}:
                    continue
                self._apply_position_fill(
                    position_state=positions,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    price=price,
                )
        return positions

    def _apply_position_fill(
        self,
        position_state: dict[str, dict[str, float]],
        symbol: str,
        side: str,
        qty: float,
        price: float,
    ) -> None:
        if qty <= 0.0 or price <= 0.0:
            return
        row = position_state.setdefault(symbol, {"qty": 0.0, "cost_basis": 0.0, "realized_pnl": 0.0})
        if side == "buy":
            row["qty"] += qty
            row["cost_basis"] += qty * price
            return
        if side != "sell":
            return
        current_qty = float(row["qty"])
        if current_qty <= 0:
            return
        avg_cost = float(row["cost_basis"]) / current_qty if current_qty > 0 else 0.0
        matched_qty = min(qty, current_qty)
        row["qty"] = current_qty - matched_qty
        row["cost_basis"] -= matched_qty * avg_cost
        row["realized_pnl"] += (price - avg_cost) * matched_qty


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _period_to_cutoff(period: str) -> datetime | None:
    normalized = period.lower().strip()
    now = datetime.now(UTC)
    if normalized == "day":
        return now - timedelta(days=1)
    if normalized == "week":
        return now - timedelta(days=7)
    if normalized == "month":
        return now - timedelta(days=30)
    return None


def _parse_optional_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, str) and value.strip():
        return _parse_iso_datetime(value)
    return None


def _to_iso_optional(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _signal_to_side(signal: str) -> str:
    normalized = str(signal).upper().strip()
    if normalized == "BUY":
        return "buy"
    if normalized == "SELL":
        return "sell"
    return "hold"


def _latest_research_created_at(research_refs: Any) -> datetime | None:
    if not isinstance(research_refs, list):
        return None
    parsed_values: list[datetime] = []
    for item in research_refs:
        if not isinstance(item, dict):
            continue
        parsed = _parse_optional_iso_datetime(item.get("created_at"))
        if parsed is not None:
            parsed_values.append(parsed)
    if not parsed_values:
        return None
    return max(parsed_values)


def _run_is_hold_only(run: dict[str, Any]) -> bool:
    trades = run.get("trades", [])
    if not isinstance(trades, list) or not trades:
        return False
    return all(str(trade.get("side", "hold")).lower() == "hold" for trade in trades if isinstance(trade, dict))


def _timeframe_floor_interval_seconds(timeframe: str) -> int:
    normalized = str(timeframe).strip().lower()
    return _TIMEFRAME_MIN_INTERVAL_SECONDS.get(normalized, 5)


def _normalize_bar_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC).isoformat()
        return value.astimezone(UTC).isoformat()
    text = str(value).strip()
    return text or None


def _followup_reason_label(reason: str) -> str:
    mapping = {
        "stale_research": "Research snapshot is stale and requires refresh.",
        "low_confidence": "Strategy confidence is below the configured threshold.",
        "hold_streak": "Agent has repeated hold-only outcomes and needs reassessment.",
        "drawdown_threshold": "Net drawdown exceeded safety threshold.",
    }
    return mapping.get(reason, f"Follow-up trigger fired: {reason}")
