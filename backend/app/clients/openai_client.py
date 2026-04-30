from __future__ import annotations

import json
from typing import Any

from openai import OpenAI


def build_openai_client(api_key: str) -> OpenAI:
    """Build the OpenAI SDK client."""
    return OpenAI(api_key=api_key)


def explain_signal(
    client: OpenAI,
    model: str,
    symbol: str,
    timeframe: str,
    signal: str,
    fast_sma: float,
    slow_sma: float,
) -> str:
    """Generate a short natural-language explanation of the latest signal."""
    try:
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a trading assistant. Explain technical signals clearly, "
                        "briefly, and do not provide financial guarantees."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Explain this SMA crossover signal in 2-3 sentences.\n"
                        f"Symbol: {symbol}\n"
                        f"Timeframe: {timeframe}\n"
                        f"Signal: {signal}\n"
                        f"Fast SMA: {fast_sma:.4f}\n"
                        f"Slow SMA: {slow_sma:.4f}"
                    ),
                },
            ],
        )
        return response.output_text.strip()
    except Exception as exc:  # SDK raises typed exceptions; we normalize upstream handling.
        raise RuntimeError("OpenAI explanation request failed.") from exc


def research_trading_strategy(
    client: OpenAI,
    model: str,
    symbols: list[str],
    timeframe: str,
    signal_context: list[dict[str, Any]],
    risk_profile: str,
) -> dict[str, Any]:
    """Generate market-research strategy suggestions using web-aware OpenAI responses."""
    prompt_payload = {
        "symbols": symbols,
        "timeframe": timeframe,
        "risk_profile": risk_profile,
        "signal_context": signal_context,
    }

    try:
        response = client.responses.create(
            model=model,
            tools=[{"type": "web_search_preview"}],
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a market research assistant for short-horizon trading setups. "
                        "Prioritize liquid, high value/volume stocks and integrate recent event catalysts "
                        "(earnings, guidance, product launches, M&A, regulatory, and macro shocks). "
                        "Use web context plus technical data to produce practical, concise strategy suggestions."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Analyze these stocks and provide a trading strategy recommendation.\n"
                        "Return STRICT JSON with keys: picks (list), market_outlook, strategy_notes, risk_warnings.\n"
                        "Each pick MUST include: symbol, signal, confidence (0-1), rationale, suggested_action, "
                        "liquidity_signal, notable_events, event_time_horizon, event_risk.\n"
                        "Rules:\n"
                        "- Prefer symbols with stronger liquidity/value context unless catalysts strongly justify otherwise.\n"
                        "- notable_events must be a list of concise strings with event + why it could move price.\n"
                        "- event_time_horizon should be near_term, medium_term, or unknown.\n"
                        "- event_risk should be low, medium, or high.\n"
                        "- Never provide guarantees.\n"
                        f"Input data: {json.dumps(prompt_payload, default=str)}"
                    ),
                },
            ],
        )
        return _parse_strategy_response(response.output_text)
    except Exception as exc:
        raise RuntimeError("OpenAI strategy research request failed.") from exc


def evaluate_strategy_signal(
    client: OpenAI,
    model: str,
    symbol: str,
    timeframe: str,
    risk_profile: str,
    signal_context: list[dict[str, Any]],
    strategy: dict[str, Any],
) -> dict[str, Any]:
    prompt_payload = {
        "symbol": symbol,
        "timeframe": timeframe,
        "risk_profile": risk_profile,
        "signal_context": signal_context,
        "strategy": strategy,
    }
    try:
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a strategy evaluator. Assess whether a generated trading setup is worth "
                        "capitalizing on. Weigh catalyst quality and event timing uncertainty alongside "
                        "technical context. Return concise, structured output and avoid guarantees."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Evaluate this proposed setup.\n"
                        "Return STRICT JSON with keys: confidence, decision_summary, key_reasons, risk_notes, high_level_plan.\n"
                        "Rules:\n"
                        "- confidence must be a number from 0.0 to 1.0.\n"
                        "- decision_summary must be one short paragraph.\n"
                        "- key_reasons, risk_notes, and high_level_plan must be short string lists.\n"
                        "- key_reasons should explicitly reference liquidity/value profile and catalyst strength when available.\n"
                        "- risk_notes should explicitly include event uncertainty risks when applicable.\n"
                        f"Input data: {json.dumps(prompt_payload, default=str)}"
                    ),
                },
            ],
        )
        return _parse_evaluator_response(response.output_text)
    except Exception as exc:
        raise RuntimeError("OpenAI strategy evaluation request failed.") from exc


def _parse_strategy_response(output_text: str) -> dict[str, Any]:
    cleaned = _strip_json_fence(output_text)
    if not cleaned:
        raise RuntimeError("OpenAI strategy research response was empty.")

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return {
            "picks": [],
            "market_outlook": "Unable to parse structured strategy output.",
            "strategy_notes": cleaned,
            "risk_warnings": ["Model output was not valid JSON; review manually."],
        }
    if not isinstance(parsed, dict):
        return {
            "picks": [],
            "market_outlook": "Model returned non-object output.",
            "strategy_notes": str(parsed),
            "risk_warnings": ["Model output did not match expected strategy object."],
        }
    return _normalize_strategy_response(parsed)


def _parse_evaluator_response(output_text: str) -> dict[str, Any]:
    cleaned = _strip_json_fence(output_text)
    if not cleaned:
        raise RuntimeError("OpenAI strategy evaluation response was empty.")
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenAI strategy evaluation response was not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("OpenAI strategy evaluation response must be a JSON object.")

    confidence_raw = parsed.get("confidence")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("OpenAI strategy evaluation confidence must be numeric.") from exc
    if confidence < 0.0 or confidence > 1.0:
        raise RuntimeError("OpenAI strategy evaluation confidence must be between 0 and 1.")

    decision_summary = str(parsed.get("decision_summary", "")).strip()
    if not decision_summary:
        raise RuntimeError("OpenAI strategy evaluation decision_summary is required.")

    key_reasons = _normalize_string_list(parsed.get("key_reasons"))
    risk_notes = _normalize_string_list(parsed.get("risk_notes"))
    high_level_plan = _normalize_string_list(parsed.get("high_level_plan"))
    if not key_reasons:
        raise RuntimeError("OpenAI strategy evaluation key_reasons must not be empty.")
    if not high_level_plan:
        raise RuntimeError("OpenAI strategy evaluation high_level_plan must not be empty.")

    return {
        "confidence": confidence,
        "decision_summary": decision_summary,
        "key_reasons": key_reasons,
        "risk_notes": risk_notes,
        "high_level_plan": high_level_plan,
    }


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


def _normalize_strategy_response(parsed: dict[str, Any]) -> dict[str, Any]:
    picks_value = parsed.get("picks")
    normalized_picks: list[dict[str, Any]] = []
    if isinstance(picks_value, list):
        for item in picks_value:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            signal = str(item.get("signal", "HOLD")).strip().upper()
            suggested_action = str(item.get("suggested_action", "hold")).strip().lower() or "hold"
            try:
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
            event_horizon = str(item.get("event_time_horizon", "unknown")).strip().lower() or "unknown"
            event_risk = str(item.get("event_risk", "unknown")).strip().lower() or "unknown"
            normalized_picks.append(
                {
                    "symbol": symbol,
                    "signal": signal,
                    "confidence": confidence,
                    "rationale": str(item.get("rationale", "")).strip(),
                    "suggested_action": suggested_action,
                    "liquidity_signal": str(item.get("liquidity_signal", "unknown")).strip() or "unknown",
                    "notable_events": _normalize_string_list(item.get("notable_events")),
                    "event_time_horizon": event_horizon,
                    "event_risk": event_risk,
                }
            )
    return {
        "picks": normalized_picks,
        "market_outlook": str(parsed.get("market_outlook", "")).strip(),
        "strategy_notes": str(parsed.get("strategy_notes", "")).strip(),
        "risk_warnings": _normalize_string_list(parsed.get("risk_warnings")),
    }


def _strip_json_fence(output_text: str) -> str:
    cleaned = output_text.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3:
            cleaned = "\n".join(lines[1:-1]).strip()
    return cleaned


def build_agent_execution_plan(
    strategy: dict[str, Any],
    fallback_symbols: list[str],
    default_interval_seconds: int,
) -> dict[str, Any]:
    """Normalize strategy output into a deterministic execution plan shape."""
    picks = strategy.get("picks")
    symbols: list[str] = []
    if isinstance(picks, list):
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            symbol = str(pick.get("symbol", "")).strip().upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    if not symbols:
        symbols = [symbol.upper() for symbol in fallback_symbols]
    return {
        "symbols": symbols,
        "interval_seconds": default_interval_seconds,
        "entry_rules": ["SMA crossover signal BUY"],
        "exit_rules": ["SMA crossover signal SELL"],
        "notes": "Derived from research strategy output; can be refined by future triggers.",
    }
