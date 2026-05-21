from __future__ import annotations

import logging
from math import log10
from datetime import UTC, datetime
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from app.clients.alpaca_client import AlpacaClient
from app.clients.providers.alpaca_provider import AlpacaProvider
from app.clients.providers.base import AssetClass, MarketDataProvider
from app.clients.openai_client import (
    author_strategy_skill,
    build_execution_plan,
    evaluate_strategy_signal,
    expand_theme_to_basket,
    normalize_execution_plan,
    research_trading_strategy,
)
from app.repositories.markdown_research_store import MarkdownResearchStore, ResearchRecord
from app.services.signal_service import MarketSignalSummary, summarize_market_signal
from app.services.skill_context import build_skill_prompt_block
from app.services.strategy_dedup import build_strategy_fingerprint
from app.settings import Settings


LOGGER = logging.getLogger(__name__)


_HIGH_DOLLAR_VOLUME = 100_000_000.0
_MEDIUM_DOLLAR_VOLUME = 25_000_000.0
_HIGH_PRICE_LEVEL = 200.0
_MID_PRICE_LEVEL = 50.0
_GENERATION_SCOPE = "generation"
_RUNTIME_SCOPE = "runtime"


@dataclass(slots=True)
class MarketResearchResult:
    symbols: list[str]
    ticker: str
    timeframe: str
    risk_profile: str
    signal_context: list[dict[str, Any]]
    strategy: dict[str, Any]
    evaluation: dict[str, Any]
    research_refs: list[dict[str, Any]]
    research_thoughts: list[dict[str, Any]]
    quality_findings: dict[str, Any] | None = None


@dataclass(slots=True)
class MissionResearchResult:
    symbols: list[str]
    ticker: str
    timeframe: str
    risk_profile: str
    input_kind: str
    input_theme: str | None
    theme_summary: str
    basket: list[dict[str, Any]]
    signal_context: list[dict[str, Any]]
    strategy: dict[str, Any]
    evaluation: dict[str, Any]
    execution_plan: dict[str, Any]
    window_seconds: int
    window_rationale: str
    skill_markdown: str
    research_refs: list[dict[str, Any]]
    research_thoughts: list[dict[str, Any]]


class MarketResearchService:
    def __init__(
        self,
        settings: Settings,
        alpaca_client: AlpacaClient | None = None,
        research_store: MarkdownResearchStore | None = None,
        openai_client: Any | None = None,
        skill_store: Any | None = None,
        data_provider: MarketDataProvider | None = None,
        data_provider_factory: Callable[[], MarketDataProvider] | None = None,
    ) -> None:
        self.settings = settings
        self.alpaca_client = alpaca_client
        # Resolve the data provider lazily so a selected-but-uncredentialed
        # provider raises only when used (not at app/scheduler startup).
        self._data_provider_factory = data_provider_factory
        if data_provider is not None:
            self._data_provider: MarketDataProvider | None = data_provider
        elif alpaca_client is not None:
            self._data_provider = AlpacaProvider(alpaca_client)
        elif data_provider_factory is not None:
            self._data_provider = None
        else:
            raise ValueError(
                "MarketResearchService requires data_provider, "
                "data_provider_factory, or alpaca_client."
            )
        self.research_store = research_store
        self.openai_client = openai_client
        self.skill_store = skill_store

    @property
    def data_provider(self) -> MarketDataProvider:
        if self._data_provider is not None:
            return self._data_provider
        if self._data_provider_factory is None:
            raise RuntimeError("No market data provider configured.")
        return self._data_provider_factory()

    def build_strategy(
        self,
        symbols: list[str],
        timeframe: str | None = None,
        risk_profile: str = "balanced",
        research_scope: str = _GENERATION_SCOPE,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        skill_ids: list[str] | None = None,
        asset_class: AssetClass = "stock",
    ) -> MarketResearchResult:
        chosen_timeframe = timeframe or self.settings.bar_timeframe
        skill_context = build_skill_prompt_block(self.skill_store, skill_ids)
        normalized_symbols = [symbol.upper() for symbol in symbols]
        self._emit_progress(
            progress_callback,
            stage="start",
            message=f"Starting research for {len(normalized_symbols)} symbols.",
            source="market_research_service",
        )
        signal_context = self._collect_signal_context(
            symbols=normalized_symbols,
            timeframe=chosen_timeframe,
            progress_callback=progress_callback,
            asset_class=asset_class,
        )
        self._emit_progress(
            progress_callback,
            stage="strategy_generation",
            message="Building strategy output from signal context.",
            source="market_research_service",
        )
        strategy = self._build_strategy_output(
            symbols=normalized_symbols,
            timeframe=chosen_timeframe,
            risk_profile=risk_profile,
            signal_context=signal_context,
            skill_context=skill_context,
            asset_class=asset_class,
        )
        ticker = self._select_ticker(strategy=strategy, fallback_symbols=normalized_symbols)
        evaluation = self._build_strategy_evaluation(
            ticker=ticker,
            timeframe=chosen_timeframe,
            risk_profile=risk_profile,
            signal_context=signal_context,
            strategy=strategy,
            skill_context=skill_context,
        )
        self._emit_progress(
            progress_callback,
            stage="evaluation",
            message=f"Evaluated strategy for {ticker} with recommendation={evaluation.get('recommendation', 'unknown')}.",
            source="market_research_service",
        )
        research_ref = self._persist_research_record(
            source="build_strategy",
            symbols=normalized_symbols,
            ticker=ticker,
            timeframe=chosen_timeframe,
            risk_profile=risk_profile,
            signal_context=signal_context,
            strategy=strategy,
            evaluation=evaluation,
            research_scope=research_scope,
        )
        LOGGER.info(
            "Research strategy built for symbols=%s timeframe=%s risk_profile=%s",
            normalized_symbols,
            chosen_timeframe,
            risk_profile,
        )
        research_thoughts = self._build_research_thoughts(
            mode="manual_symbols",
            symbols=normalized_symbols,
            signal_context=signal_context,
            strategy=strategy,
            evaluation=evaluation,
            selected_symbol=ticker,
        )
        return MarketResearchResult(
            symbols=normalized_symbols,
            ticker=ticker,
            timeframe=chosen_timeframe,
            risk_profile=risk_profile,
            signal_context=signal_context,
            strategy=strategy,
            evaluation=evaluation,
            research_refs=[research_ref],
            research_thoughts=research_thoughts,
            quality_findings=None,
        )

    def build_mission(
        self,
        symbols: list[str] | None = None,
        theme: str | None = None,
        timeframe: str | None = None,
        risk_profile: str = "balanced",
        skill_ids: list[str] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        asset_class: AssetClass = "stock",
    ) -> MissionResearchResult:
        chosen_timeframe = timeframe or self.settings.bar_timeframe
        skill_context = build_skill_prompt_block(self.skill_store, skill_ids)
        normalized_theme = (theme or "").strip() or None

        basket: list[dict[str, Any]] = []
        theme_summary = ""
        if normalized_theme:
            input_kind = "theme"
            self._emit_progress(
                progress_callback,
                stage="theme_expansion",
                message=f"Expanding theme into a tradable basket: {normalized_theme}",
                source="market_research_service",
            )
            if self.openai_client is not None:
                expanded = expand_theme_to_basket(
                    client=self.openai_client,
                    model=self.settings.openai_model,
                    theme=normalized_theme,
                    risk_profile=risk_profile,
                    skill_context=skill_context,
                )
                basket = expanded["basket"]
                theme_summary = expanded["theme_summary"]
                resolved_symbols = [str(item["symbol"]).upper() for item in basket]
            else:
                resolved_symbols = [s.upper() for s in (symbols or [self.settings.default_symbol])]
                basket = [
                    {"symbol": s, "rationale": "Theme expansion unavailable; using fallback.", "score": 0.5}
                    for s in resolved_symbols
                ]
        elif symbols:
            input_kind = "ticker"
            resolved_symbols = [s.upper() for s in symbols]
            basket = [{"symbol": s, "rationale": "User-specified symbol.", "score": 1.0} for s in resolved_symbols]
        else:
            input_kind = "auto"
            resolved_symbols = [self.settings.default_symbol.upper()]
            basket = [{"symbol": resolved_symbols[0], "rationale": "Default symbol.", "score": 0.5}]

        self._emit_progress(
            progress_callback,
            stage="signal_scan",
            message=f"Collecting signal context for {len(resolved_symbols)} symbol(s).",
            source="signal_service",
        )
        signal_context = self._collect_signal_context(
            symbols=resolved_symbols,
            timeframe=chosen_timeframe,
            progress_callback=progress_callback,
            asset_class=asset_class,
        )
        active_symbols = [str(entry.get("symbol", "")).upper() for entry in signal_context if entry.get("symbol")]
        if not active_symbols:
            active_symbols = resolved_symbols

        strategy = self._build_strategy_output(
            symbols=active_symbols,
            timeframe=chosen_timeframe,
            risk_profile=risk_profile,
            signal_context=signal_context,
            skill_context=skill_context,
            asset_class=asset_class,
        )
        ticker = self._select_ticker(strategy=strategy, fallback_symbols=active_symbols)
        evaluation = self._build_strategy_evaluation(
            ticker=ticker,
            timeframe=chosen_timeframe,
            risk_profile=risk_profile,
            signal_context=signal_context,
            strategy=strategy,
            skill_context=skill_context,
        )

        self._emit_progress(
            progress_callback,
            stage="execution_plan",
            message="Designing the time-boxed execution plan.",
            source="market_research_service",
        )
        if self.openai_client is not None:
            execution_plan = build_execution_plan(
                client=self.openai_client,
                model=self.settings.openai_model,
                symbols=active_symbols,
                signal_context=signal_context,
                risk_profile=risk_profile,
                window_min_seconds=self.settings.mission_window_min_seconds,
                window_max_seconds=self.settings.mission_window_max_seconds,
                window_default_seconds=self.settings.mission_window_default_seconds,
                skill_context=skill_context,
            )
        else:
            execution_plan = normalize_execution_plan(
                parsed={},
                fallback_symbols=active_symbols,
                window_min_seconds=self.settings.mission_window_min_seconds,
                window_max_seconds=self.settings.mission_window_max_seconds,
                window_default_seconds=self.settings.mission_window_default_seconds,
            )

        skill_context_payload = {
            "input_kind": input_kind,
            "theme": normalized_theme,
            "symbols": active_symbols,
            "timeframe": chosen_timeframe,
            "risk_profile": risk_profile,
            "strategy": strategy,
            "evaluation": evaluation,
            "execution_plan": execution_plan,
        }
        if self.openai_client is not None:
            skill_markdown = author_strategy_skill(
                client=self.openai_client,
                model=self.settings.openai_model,
                context=skill_context_payload,
                skill_context=skill_context,
            )
        else:
            skill_markdown = _fallback_skill_markdown(
                ticker=ticker,
                theme=normalized_theme,
                risk_profile=risk_profile,
                execution_plan=execution_plan,
            )

        research_ref = self._persist_research_record(
            source="build_mission",
            symbols=active_symbols,
            ticker=ticker,
            timeframe=chosen_timeframe,
            risk_profile=risk_profile,
            signal_context=signal_context,
            strategy=strategy,
            evaluation=evaluation,
            research_scope=_GENERATION_SCOPE,
        )
        research_thoughts = self._build_research_thoughts(
            mode="mission",
            symbols=active_symbols,
            signal_context=signal_context,
            strategy=strategy,
            evaluation=evaluation,
            selected_symbol=ticker,
        )
        return MissionResearchResult(
            symbols=active_symbols,
            ticker=ticker,
            timeframe=chosen_timeframe,
            risk_profile=risk_profile,
            input_kind=input_kind,
            input_theme=normalized_theme,
            theme_summary=theme_summary,
            basket=basket,
            signal_context=signal_context,
            strategy=strategy,
            evaluation=evaluation,
            execution_plan=execution_plan,
            window_seconds=int(execution_plan.get("recommended_window_seconds", self.settings.mission_window_default_seconds)),
            window_rationale=str(execution_plan.get("window_rationale", "")),
            skill_markdown=skill_markdown,
            research_refs=[research_ref],
            research_thoughts=research_thoughts,
        )

    def generate_buy_candidate_strategy(
        self,
        timeframe: str | None = None,
        risk_profile: str = "balanced",
        max_candidates: int | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        skill_ids: list[str] | None = None,
        asset_class: AssetClass = "stock",
    ) -> MarketResearchResult:
        chosen_timeframe = timeframe or self.settings.bar_timeframe
        skill_context = build_skill_prompt_block(self.skill_store, skill_ids)
        scan_limit = int(max_candidates or self.settings.research_candidate_scan_limit)
        candidate_symbols = self.data_provider.list_tradable_symbols(
            limit=scan_limit, asset_class=asset_class
        )
        self._emit_progress(
            progress_callback,
            stage="start",
            message=f"Loaded {len(candidate_symbols)} tradable symbols. Building signal context.",
            source="market_research_service",
        )
        signal_context = self._collect_signal_context(
            symbols=candidate_symbols,
            timeframe=chosen_timeframe,
            progress_callback=progress_callback,
            asset_class=asset_class,
        )
        quality_context = self._quality_universe(signal_context=signal_context)
        quality_findings = self._build_quality_findings(
            signal_context=signal_context,
            quality_context=quality_context,
        )
        if not quality_context:
            fallback_pool = sorted(signal_context, key=self._candidate_rank_key, reverse=True)
            if not fallback_pool:
                raise RuntimeError("Market research failed: no fallback candidates were available.")
            selected_symbol = str(fallback_pool[0].get("symbol", "")).upper()
            if not selected_symbol:
                raise RuntimeError("Market research failed: fallback candidate symbol was empty.")
            self._emit_progress(
                progress_callback,
                stage="candidate_filtering",
                message=(
                    "Strict quality filters returned no candidates; using top fallback symbol while "
                    "including full quality findings for review."
                ),
                source="market_research_service",
            )
            selected_context = [
                entry for entry in signal_context if str(entry.get("symbol", "")).upper() == selected_symbol
            ]
            strategy = self._build_strategy_output(
                symbols=[selected_symbol],
                timeframe=chosen_timeframe,
                risk_profile=risk_profile,
                signal_context=selected_context,
                skill_context=skill_context,
                asset_class=asset_class,
            )
            risk_warnings = strategy.get("risk_warnings")
            if not isinstance(risk_warnings, list):
                risk_warnings = []
            strategy["risk_warnings"] = [
                "Strict quality filters did not yield qualifying symbols for this run.",
                "Treat this setup as low-conviction and review findings before creating an agent.",
                *[str(item) for item in risk_warnings if str(item).strip()],
            ]
            strategy_notes = str(strategy.get("strategy_notes", "")).strip()
            strategy["strategy_notes"] = (
                f"{strategy_notes} " if strategy_notes else ""
            ) + "Generated from fallback candidate because no strict-quality symbols passed."
            evaluation = self._build_strategy_evaluation(
                ticker=selected_symbol,
                timeframe=chosen_timeframe,
                risk_profile=risk_profile,
                signal_context=selected_context,
                strategy=strategy,
                skill_context=skill_context,
            )
            raw_confidence = float(evaluation.get("confidence", 0.0) or 0.0)
            evaluation["confidence"] = min(raw_confidence, 0.45)
            evaluation["recommendation"] = "not_recommended"
            existing_reasons = evaluation.get("key_reasons")
            if not isinstance(existing_reasons, list):
                existing_reasons = []
            evaluation["key_reasons"] = [
                "No symbols passed strict quality thresholds in this run.",
                *[str(item) for item in existing_reasons if str(item).strip()],
            ]
            existing_risks = evaluation.get("risk_notes")
            if not isinstance(existing_risks, list):
                existing_risks = []
            evaluation["risk_notes"] = [
                "Fallback candidate used after quality filter miss; setup may be less reliable.",
                *[str(item) for item in existing_risks if str(item).strip()],
            ]
            decision_summary = str(evaluation.get("decision_summary", "")).strip()
            evaluation["decision_summary"] = (
                "Strict quality filters yielded no qualifying symbols. "
                f"{decision_summary}".strip()
            )
            research_ref = self._persist_research_record(
                source="generate_buy_candidate_strategy",
                symbols=[selected_symbol],
                ticker=selected_symbol,
                timeframe=chosen_timeframe,
                risk_profile=risk_profile,
                signal_context=selected_context,
                strategy=strategy,
                evaluation=evaluation,
                research_scope=_GENERATION_SCOPE,
            )
            research_thoughts = self._build_research_thoughts(
                mode="auto_candidate",
                symbols=[str(entry.get("symbol", "")).upper() for entry in fallback_pool[:10]],
                signal_context=fallback_pool[:10],
                strategy=strategy,
                evaluation=evaluation,
                selected_symbol=selected_symbol,
                quality_findings=quality_findings,
            )
            return MarketResearchResult(
                symbols=[selected_symbol],
                ticker=selected_symbol,
                timeframe=chosen_timeframe,
                risk_profile=risk_profile,
                signal_context=selected_context,
                strategy=strategy,
                evaluation=evaluation,
                research_refs=[research_ref],
                research_thoughts=research_thoughts,
                quality_findings=quality_findings,
            )
        self._emit_progress(
            progress_callback,
            stage="candidate_filtering",
            message=(
                f"Applied strict quality filters to {len(signal_context)} symbols, "
                f"selected top {len(quality_context)} quality candidates."
            ),
            source="market_research_service",
        )
        recently_researched_symbols = self._recently_researched_symbols(
            symbols=[str(entry.get("symbol", "")).upper() for entry in quality_context],
            timeframe=chosen_timeframe,
            research_scope=_GENERATION_SCOPE,
        )
        fresh_context = [
            entry for entry in quality_context if str(entry.get("symbol", "")).upper() not in recently_researched_symbols
        ]
        candidate_pool = fresh_context or quality_context
        best_candidate = self._select_best_candidate(candidate_pool)
        selected_symbol = str(best_candidate.get("symbol", "")).upper()
        if not selected_symbol:
            raise RuntimeError("Market research failed: best candidate symbol was empty.")
        self._emit_progress(
            progress_callback,
            stage="candidate_selection",
            message=(
                f"Selected {selected_symbol} as best candidate for strategy generation "
                f"from strict top-{len(candidate_pool)} quality universe."
            ),
            source="market_research_service",
        )

        selected_context = [entry for entry in signal_context if str(entry.get("symbol", "")).upper() == selected_symbol]
        strategy = self._build_strategy_output(
            symbols=[selected_symbol],
            timeframe=chosen_timeframe,
            risk_profile=risk_profile,
            signal_context=selected_context,
            skill_context=skill_context,
        )
        evaluation = self._build_strategy_evaluation(
            ticker=selected_symbol,
            timeframe=chosen_timeframe,
            risk_profile=risk_profile,
            signal_context=selected_context,
            strategy=strategy,
            skill_context=skill_context,
        )
        self._emit_progress(
            progress_callback,
            stage="evaluation",
            message=(
                f"Evaluated candidate {selected_symbol} with recommendation="
                f"{evaluation.get('recommendation', 'unknown')}."
            ),
            source="market_research_service",
        )
        research_ref = self._persist_research_record(
            source="generate_buy_candidate_strategy",
            symbols=[selected_symbol],
            ticker=selected_symbol,
            timeframe=chosen_timeframe,
            risk_profile=risk_profile,
            signal_context=selected_context,
            strategy=strategy,
            evaluation=evaluation,
            research_scope=_GENERATION_SCOPE,
        )
        LOGGER.info(
            "Generated buy-candidate strategy for symbol=%s timeframe=%s signal=%s",
            selected_symbol,
            chosen_timeframe,
            best_candidate.get("signal", "unknown"),
        )
        research_thoughts = self._build_research_thoughts(
            mode="auto_candidate",
            symbols=[str(entry.get("symbol", "")).upper() for entry in quality_context],
            signal_context=quality_context,
            strategy=strategy,
            evaluation=evaluation,
            selected_symbol=selected_symbol,
            skipped_recent_symbols=sorted(recently_researched_symbols),
            quality_findings=quality_findings,
        )
        return MarketResearchResult(
            symbols=[selected_symbol],
            ticker=selected_symbol,
            timeframe=chosen_timeframe,
            risk_profile=risk_profile,
            signal_context=selected_context,
            strategy=strategy,
            evaluation=evaluation,
            research_refs=[research_ref],
            research_thoughts=research_thoughts,
            quality_findings=quality_findings,
        )

    def _recently_researched_symbols(
        self,
        symbols: list[str],
        timeframe: str,
        research_scope: str = _GENERATION_SCOPE,
    ) -> set[str]:
        recent_records = self.research_store.list_recent_records(
            symbols=symbols,
            timeframe=timeframe,
            scope=research_scope,
        )
        recent_symbols: set[str] = set()
        for record in recent_records:
            for symbol in record.get("symbols", []):
                normalized = str(symbol).upper().strip()
                if normalized:
                    recent_symbols.add(normalized)
        return recent_symbols

    def _persist_research_record(
        self,
        source: str,
        symbols: list[str],
        ticker: str,
        timeframe: str,
        risk_profile: str,
        signal_context: list[dict[str, Any]],
        strategy: dict[str, Any],
        evaluation: dict[str, Any],
        research_scope: str = _GENERATION_SCOPE,
    ) -> dict[str, Any]:
        timestamp = datetime.now(UTC).isoformat()
        fingerprint = build_strategy_fingerprint(
            symbols=symbols,
            ticker=ticker,
            timeframe=timeframe,
            risk_profile=risk_profile,
            strategy=strategy,
        )
        return self.research_store.append_record(
            ResearchRecord(
                created_at=timestamp,
                source=source,
                symbols=[symbol.upper() for symbol in symbols],
                ticker=ticker.upper(),
                timeframe=timeframe,
                risk_profile=risk_profile,
                strategy_fingerprint=fingerprint,
                signal_context=signal_context,
                strategy=strategy,
                evaluation=evaluation,
                research_scope=research_scope,
            )
        )

    def _collect_signal_context(
        self,
        symbols: list[str],
        timeframe: str,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        asset_class: AssetClass = "stock",
    ) -> list[dict[str, Any]]:
        contexts: list[dict[str, Any]] = []
        bar_limit = max(self.settings.slow_sma_window + 5, 40)
        for symbol in symbols:
            try:
                bars = self.data_provider.get_bars(
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=bar_limit,
                    asset_class=asset_class,
                )
                summary = summarize_market_signal(
                    bars=bars,
                    fast_window=self.settings.fast_sma_window,
                    slow_window=self.settings.slow_sma_window,
                )
                contexts.append(self._summary_to_context(symbol=symbol, summary=summary))
                self._emit_progress(
                    progress_callback,
                    stage="signal_scan",
                    message=(
                        f"{symbol}: signal={summary.signal_result.signal}, "
                        f"volume_ratio={summary.volume_ratio:.2f}, momentum={summary.momentum_percent:.2f}%"
                    ),
                    source="signal_service",
                )
            except Exception as exc:
                LOGGER.warning("Skipping symbol=%s during research: %s", symbol, exc)
        if not contexts:
            raise RuntimeError("Market research failed: no symbols produced valid signal context.")
        return contexts

    def _build_strategy_output(
        self,
        symbols: list[str],
        timeframe: str,
        risk_profile: str,
        signal_context: list[dict[str, Any]],
        skill_context: str | None = None,
        asset_class: AssetClass = "stock",
    ) -> dict[str, Any]:
        if self.openai_client is None:
            return self._fallback_strategy(signal_context)

        return research_trading_strategy(
            client=self.openai_client,
            model=self.settings.openai_model,
            symbols=symbols,
            timeframe=timeframe,
            risk_profile=risk_profile,
            signal_context=signal_context,
            skill_context=skill_context,
            asset_class=asset_class,
        )

    def _summary_to_context(
        self,
        symbol: str,
        summary: MarketSignalSummary,
    ) -> dict[str, Any]:
        latest_close = float(summary.signal_result.latest_close)
        latest_volume = float(summary.latest_volume)
        dollar_volume_estimate = max(latest_close, 0.0) * max(latest_volume, 0.0)
        return {
            "symbol": symbol,
            "signal": summary.signal_result.signal,
            "latest_close": latest_close,
            "fast_sma": summary.signal_result.fast_sma,
            "slow_sma": summary.signal_result.slow_sma,
            "previous_fast_sma": summary.signal_result.previous_fast_sma,
            "previous_slow_sma": summary.signal_result.previous_slow_sma,
            "latest_volume": latest_volume,
            "average_volume": summary.average_volume,
            "volume_ratio": summary.volume_ratio,
            "momentum_percent": summary.momentum_percent,
            "dollar_volume_estimate": dollar_volume_estimate,
            "liquidity_tier": self._liquidity_tier(dollar_volume_estimate),
            "price_tier": self._price_tier(latest_close),
            "predictability_score": self._predictability_score(
                volume_ratio=float(summary.volume_ratio),
                momentum_percent=float(summary.momentum_percent),
            ),
        }

    def _fallback_strategy(self, signal_context: list[dict[str, Any]]) -> dict[str, Any]:
        ranked = sorted(
            signal_context,
            key=lambda entry: (entry["volume_ratio"], entry["momentum_percent"]),
            reverse=True,
        )
        top_picks = []
        for entry in ranked[:3]:
            action = "hold"
            if entry["signal"] == "BUY":
                action = "buy"
            elif entry["signal"] == "SELL":
                action = "reduce"
            top_picks.append(
                {
                    "symbol": entry["symbol"],
                    "signal": entry["signal"],
                    "confidence": round(min(max(entry["volume_ratio"] / 2.0, 0.05), 0.95), 2),
                    "rationale": (
                        f"Signal={entry['signal']}, volume_ratio={entry['volume_ratio']:.2f}, "
                        f"momentum={entry['momentum_percent']:.2f}%."
                    ),
                    "suggested_action": action,
                    "liquidity_signal": str(entry.get("liquidity_tier", "unknown")),
                    "notable_events": [],
                    "event_time_horizon": "unknown",
                    "event_risk": "unknown",
                }
            )

        return {
            "picks": top_picks,
            "market_outlook": "Fallback strategy generated without OpenAI.",
            "strategy_notes": "Use as baseline only; no live web-news synthesis available.",
            "risk_warnings": [
                "This output is heuristic and should be validated before execution."
            ],
        }

    def _build_strategy_evaluation(
        self,
        ticker: str,
        timeframe: str,
        risk_profile: str,
        signal_context: list[dict[str, Any]],
        strategy: dict[str, Any],
        skill_context: str | None = None,
    ) -> dict[str, Any]:
        threshold = 0.70
        if self.openai_client is None:
            return self._fallback_evaluation(strategy=strategy, threshold=threshold)

        try:
            evaluated = evaluate_strategy_signal(
                client=self.openai_client,
                model=self.settings.openai_model,
                symbol=ticker,
                timeframe=timeframe,
                risk_profile=risk_profile,
                signal_context=signal_context,
                strategy=strategy,
                skill_context=skill_context,
            )
            confidence = float(evaluated.get("confidence", 0.0) or 0.0)
            recommendation = "recommended" if confidence >= threshold else "not_recommended"
            return {
                "confidence": confidence,
                "threshold": threshold,
                "recommendation": recommendation,
                "decision_summary": str(evaluated.get("decision_summary", "")).strip(),
                "key_reasons": evaluated.get("key_reasons", []),
                "risk_notes": evaluated.get("risk_notes", []),
                "high_level_plan": evaluated.get("high_level_plan", []),
            }
        except Exception as exc:
            LOGGER.warning("Strategy evaluation fallback used for symbol=%s: %s", ticker, exc)
            fallback = self._fallback_evaluation(strategy=strategy, threshold=threshold)
            fallback["risk_notes"] = [*fallback.get("risk_notes", []), "Evaluator parsing failed; fallback applied."]
            return fallback

    def _fallback_evaluation(self, strategy: dict[str, Any], threshold: float) -> dict[str, Any]:
        picks = strategy.get("picks")
        confidence = 0.5
        if isinstance(picks, list) and picks:
            first_pick = picks[0] if isinstance(picks[0], dict) else {}
            try:
                confidence = float(first_pick.get("confidence", confidence))
            except (TypeError, ValueError):
                confidence = 0.5
        recommendation = "recommended" if confidence >= threshold else "not_recommended"
        return {
            "confidence": confidence,
            "threshold": threshold,
            "recommendation": recommendation,
            "decision_summary": "Fallback evaluation was generated without evaluator model output.",
            "key_reasons": ["Technical context and strategy picks were used for baseline confidence."],
            "risk_notes": ["No second-stage evaluator output available."],
            "high_level_plan": [
                "Prioritize entries only when BUY signal remains valid.",
                "Use conservative position sizing until confidence improves.",
            ],
        }

    def _select_best_candidate(self, signal_context: list[dict[str, Any]]) -> dict[str, Any]:
        ranked = sorted(
            signal_context,
            key=self._candidate_rank_key,
            reverse=True,
        )
        if not ranked:
            raise RuntimeError("Market research failed: no ranked candidates were available.")
        return ranked[0]

    def _select_ticker(self, strategy: dict[str, Any], fallback_symbols: list[str]) -> str:
        picks = strategy.get("picks")
        if isinstance(picks, list):
            for pick in picks:
                if not isinstance(pick, dict):
                    continue
                symbol = str(pick.get("symbol", "")).upper().strip()
                if symbol:
                    return symbol
        if not fallback_symbols:
            raise RuntimeError("Market research failed: no ticker candidates were available.")
        return str(fallback_symbols[0]).upper().strip()

    def _build_research_thoughts(
        self,
        mode: str,
        symbols: list[str],
        signal_context: list[dict[str, Any]],
        strategy: dict[str, Any],
        evaluation: dict[str, Any],
        selected_symbol: str,
        skipped_recent_symbols: list[str] | None = None,
        quality_findings: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        thoughts: list[dict[str, Any]] = []
        thoughts.append(
            {
                "stage": "data_collection",
                "message": (
                    f"Collected signal context for {len(signal_context)} symbols "
                    f"(requested universe size: {len(symbols)})."
                ),
                "source": "signal_service",
            }
        )
        ranked = sorted(
            signal_context,
            key=self._candidate_rank_key,
            reverse=True,
        )
        for entry in ranked[:3]:
            symbol = str(entry.get("symbol", "")).upper()
            thoughts.append(
                {
                    "stage": "signal_assessment",
                    "message": (
                        f"{symbol}: signal={entry.get('signal', 'HOLD')}, "
                        f"liquidity={entry.get('liquidity_tier', 'unknown')}, "
                        f"dollar_volume=${float(entry.get('dollar_volume_estimate', 0.0) or 0.0):,.0f}, "
                        f"volume_ratio={float(entry.get('volume_ratio', 0.0) or 0.0):.2f}, "
                        f"momentum={float(entry.get('momentum_percent', 0.0) or 0.0):.2f}%."
                    ),
                    "source": "signal_service",
                }
            )
        if mode == "auto_candidate":
            if skipped_recent_symbols:
                thoughts.append(
                    {
                        "stage": "candidate_filtering",
                        "message": (
                            f"Excluded {len(skipped_recent_symbols)} recently researched symbols "
                            "when selecting a fresh candidate."
                        ),
                        "source": "market_research_service",
                    }
                )
            thoughts.append(
                {
                    "stage": "candidate_selection",
                    "message": f"Selected {selected_symbol} as the top candidate for strategy generation.",
                    "source": "market_research_service",
                }
            )
        else:
            thoughts.append(
                {
                    "stage": "candidate_selection",
                    "message": f"Built strategy from explicitly requested symbols: {', '.join(symbols)}.",
                    "source": "market_research_service",
                }
            )
        recommendation = str(evaluation.get("recommendation", "not_recommended"))
        confidence = float(evaluation.get("confidence", 0.0) or 0.0)
        picks = strategy.get("picks", []) if isinstance(strategy, dict) else []
        thoughts.append(
            {
                "stage": "strategy_output",
                "message": (
                    f"Generated {len(picks) if isinstance(picks, list) else 0} strategy picks; "
                    f"evaluation={recommendation} (confidence={confidence:.2f})."
                ),
                "source": "market_research_service",
            }
        )
        if quality_findings is not None:
            mode = str(quality_findings.get("mode", "strict_quality_passed"))
            quality_count = int(quality_findings.get("quality_universe_count", 0) or 0)
            scan_count = int(quality_findings.get("scan_count", 0) or 0)
            thoughts.append(
                {
                    "stage": "quality_findings",
                    "message": (
                        f"Quality findings mode={mode}; "
                        f"{quality_count} passed strict filters out of {scan_count} scanned symbols. "
                        "See the findings table for metric-level filter checks."
                    ),
                    "source": "market_research_service",
                }
            )
        return thoughts

    def _quality_universe(self, signal_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for entry in signal_context:
            if not self._quality_filter_failures(entry):
                filtered.append(entry)
        ranked = sorted(filtered, key=self._candidate_rank_key, reverse=True)
        top_limit = int(self.settings.research_quality_universe_limit)
        return ranked[:top_limit]

    def _build_quality_findings(
        self,
        signal_context: list[dict[str, Any]],
        quality_context: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ranked = sorted(signal_context, key=self._candidate_rank_key, reverse=True)
        rows: list[dict[str, Any]] = []
        for entry in ranked[:15]:
            failures = self._quality_filter_failures(entry)
            rows.append(
                {
                    "symbol": str(entry.get("symbol", "")).upper(),
                    "signal": str(entry.get("signal", "HOLD")).upper(),
                    "latest_close": float(entry.get("latest_close", 0.0) or 0.0),
                    "latest_volume": float(entry.get("latest_volume", 0.0) or 0.0),
                    "average_volume": float(entry.get("average_volume", 0.0) or 0.0),
                    "volume_ratio": float(entry.get("volume_ratio", 0.0) or 0.0),
                    "dollar_volume_estimate": float(entry.get("dollar_volume_estimate", 0.0) or 0.0),
                    "momentum_percent": float(entry.get("momentum_percent", 0.0) or 0.0),
                    "predictability_score": float(entry.get("predictability_score", 0.0) or 0.0),
                    "passed_quality_filters": len(failures) == 0,
                    "failed_checks": failures,
                }
            )
        mode = "strict_quality_passed" if quality_context else "strict_quality_fallback"
        return {
            "mode": mode,
            "scan_count": len(signal_context),
            "quality_universe_count": len(quality_context),
            "thresholds": {
                "min_price": float(self.settings.research_min_price),
                "min_dollar_volume": float(self.settings.research_min_dollar_volume),
                "min_average_volume": float(self.settings.research_min_average_volume),
                "min_volume_ratio": float(self.settings.research_min_volume_ratio),
                "max_volume_ratio": float(self.settings.research_max_volume_ratio),
                "quality_universe_limit": int(self.settings.research_quality_universe_limit),
            },
            "rows": rows,
        }

    def _quality_filter_failures(self, entry: dict[str, Any]) -> list[str]:
        failures: list[str] = []
        latest_close = float(entry.get("latest_close", 0.0) or 0.0)
        dollar_volume = float(entry.get("dollar_volume_estimate", 0.0) or 0.0)
        average_volume = float(entry.get("average_volume", 0.0) or 0.0)
        volume_ratio = float(entry.get("volume_ratio", 0.0) or 0.0)
        if latest_close < float(self.settings.research_min_price):
            failures.append("below_min_price")
        if dollar_volume < float(self.settings.research_min_dollar_volume):
            failures.append("below_min_dollar_volume")
        if average_volume < float(self.settings.research_min_average_volume):
            failures.append("below_min_average_volume")
        if volume_ratio < float(self.settings.research_min_volume_ratio):
            failures.append("below_min_volume_ratio")
        if volume_ratio > float(self.settings.research_max_volume_ratio):
            failures.append("above_max_volume_ratio")
        return failures

    def _candidate_rank_key(self, entry: dict[str, Any]) -> tuple[float, ...]:
        signal_bonus = 1.0 if str(entry.get("signal", "")).upper() == "BUY" else 0.0
        dollar_volume = float(entry.get("dollar_volume_estimate", 0.0) or 0.0)
        average_volume = float(entry.get("average_volume", 0.0) or 0.0)
        predictability = float(entry.get("predictability_score", 0.0) or 0.0)
        volume_ratio = float(entry.get("volume_ratio", 0.0) or 0.0)
        momentum_percent = float(entry.get("momentum_percent", 0.0) or 0.0)
        quality_score = (
            signal_bonus
            + 1.5 * log10(max(dollar_volume, 1.0))
            + 1.0 * log10(max(average_volume, 1.0))
            + 2.5 * predictability
        )
        return (
            quality_score,
            signal_bonus,
            dollar_volume,
            average_volume,
            1.0 / (1.0 + abs(volume_ratio - 1.0)),
            -abs(momentum_percent),
        )

    def _predictability_score(self, volume_ratio: float, momentum_percent: float) -> float:
        volume_stability = 1.0 / (1.0 + abs(volume_ratio - 1.0))
        momentum_stability = 1.0 / (1.0 + abs(momentum_percent) / 20.0)
        return max(0.0, min(1.0, (0.65 * volume_stability) + (0.35 * momentum_stability)))

    def _liquidity_tier(self, dollar_volume_estimate: float) -> str:
        if dollar_volume_estimate >= _HIGH_DOLLAR_VOLUME:
            return "high"
        if dollar_volume_estimate >= _MEDIUM_DOLLAR_VOLUME:
            return "medium"
        return "low"

    def _price_tier(self, latest_close: float) -> str:
        if latest_close >= _HIGH_PRICE_LEVEL:
            return "high"
        if latest_close >= _MID_PRICE_LEVEL:
            return "mid"
        return "low"

    def _emit_progress(
        self,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        stage: str,
        message: str,
        source: str,
    ) -> None:
        if progress_callback is None:
            return
        progress_callback(
            {
                "stage": stage,
                "message": message,
                "source": source,
            }
        )


def _fallback_skill_markdown(
    ticker: str,
    theme: str | None,
    risk_profile: str,
    execution_plan: dict[str, Any],
) -> str:
    legs = execution_plan.get("legs", []) if isinstance(execution_plan, dict) else []
    leg_lines = [
        f"- {leg.get('symbol')}: target {leg.get('target_pct')}% / stop {leg.get('stop_pct')}%"
        for leg in legs
        if isinstance(leg, dict)
    ]
    window = execution_plan.get("recommended_window_seconds", 1800)
    focus = theme or ticker
    return (
        "---\n"
        f"name: mission-{str(ticker or 'strategy').strip().lower()[:48] or 'strategy'}\n"
        f"description: Time-boxed momentum strategy for {focus} "
        f"({risk_profile} risk), generated as a fallback without model access.\n"
        "---\n\n"
        f"# Mission Strategy: {focus}\n\n"
        f"Holding window: ~{int(window) // 60} minutes. Always exit every leg by the "
        "window end; respect each leg's target and stop.\n\n"
        "## Legs\n\n" + ("\n".join(leg_lines) if leg_lines else "- (none)") + "\n\n"
        "## Risk\n\nFlatten everything at the window deadline. Buy first since the "
        "account starts flat.\n"
    )
