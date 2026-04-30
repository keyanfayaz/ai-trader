from __future__ import annotations

import logging
from math import log10
from datetime import UTC, datetime
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from app.clients.alpaca_client import AlpacaClient
from app.clients.openai_client import evaluate_strategy_signal, research_trading_strategy
from app.repositories.markdown_research_store import MarkdownResearchStore, ResearchRecord
from app.services.signal_service import MarketSignalSummary, summarize_market_signal
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


class MarketResearchService:
    def __init__(
        self,
        settings: Settings,
        alpaca_client: AlpacaClient,
        research_store: MarkdownResearchStore,
        openai_client: Any | None = None,
    ) -> None:
        self.settings = settings
        self.alpaca_client = alpaca_client
        self.research_store = research_store
        self.openai_client = openai_client

    def build_strategy(
        self,
        symbols: list[str],
        timeframe: str | None = None,
        risk_profile: str = "balanced",
        research_scope: str = _GENERATION_SCOPE,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> MarketResearchResult:
        chosen_timeframe = timeframe or self.settings.bar_timeframe
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
        )
        ticker = self._select_ticker(strategy=strategy, fallback_symbols=normalized_symbols)
        evaluation = self._build_strategy_evaluation(
            ticker=ticker,
            timeframe=chosen_timeframe,
            risk_profile=risk_profile,
            signal_context=signal_context,
            strategy=strategy,
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

    def generate_buy_candidate_strategy(
        self,
        timeframe: str | None = None,
        risk_profile: str = "balanced",
        max_candidates: int | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> MarketResearchResult:
        chosen_timeframe = timeframe or self.settings.bar_timeframe
        scan_limit = int(max_candidates or self.settings.research_candidate_scan_limit)
        candidate_symbols = self.alpaca_client.list_active_tradable_equity_symbols(limit=scan_limit)
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
        )
        evaluation = self._build_strategy_evaluation(
            ticker=selected_symbol,
            timeframe=chosen_timeframe,
            risk_profile=risk_profile,
            signal_context=selected_context,
            strategy=strategy,
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
    ) -> list[dict[str, Any]]:
        contexts: list[dict[str, Any]] = []
        bar_limit = max(self.settings.slow_sma_window + 5, 40)
        for symbol in symbols:
            try:
                bars = self.alpaca_client.get_bars(
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=bar_limit,
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
