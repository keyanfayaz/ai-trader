from __future__ import annotations

import json
from typing import Any

from openai import OpenAI


def build_openai_client(api_key: str) -> OpenAI:
    """Build the OpenAI SDK client."""
    return OpenAI(api_key=api_key)


def _skill_messages(skill_context: str | None) -> list[dict[str, str]]:
    """Return an extra system message carrying bound-skill instructions, if any."""
    if not skill_context or not skill_context.strip():
        return []
    return [{"role": "system", "content": skill_context.strip()}]


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
    skill_context: str | None = None,
    asset_class: str = "stock",
) -> dict[str, Any]:
    """Generate market-research strategy suggestions using web-aware OpenAI responses."""
    prompt_payload = {
        "symbols": symbols,
        "timeframe": timeframe,
        "risk_profile": risk_profile,
        "asset_class": asset_class,
        "signal_context": signal_context,
    }
    if asset_class == "future":
        instrument_guidance = (
            "You are a market research assistant for short-horizon FUTURES trading setups. "
            "Prioritize liquid front-month contracts and integrate open interest, contract "
            "roll/expiry timing, and macro catalysts. "
        )
    else:
        instrument_guidance = (
            "You are a market research assistant for short-horizon trading setups. "
            "Prioritize liquid, high value/volume stocks and integrate recent event catalysts "
            "(earnings, guidance, product launches, M&A, regulatory, and macro shocks). "
        )

    try:
        response = client.responses.create(
            model=model,
            tools=[{"type": "web_search_preview"}],
            input=[
                {
                    "role": "system",
                    "content": (
                        instrument_guidance
                        + "Use web context plus technical data to produce practical, concise strategy suggestions."
                    ),
                },
                *_skill_messages(skill_context),
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
    skill_context: str | None = None,
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
                *_skill_messages(skill_context),
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


# --------------------------------------------------------------------------- #
# Mission engine LLM calls (theme expansion, structured plan, checkpoint, skill)
# --------------------------------------------------------------------------- #


def _parse_json_object(output_text: str) -> dict[str, Any]:
    cleaned = _strip_json_fence(output_text)
    if not cleaned:
        raise RuntimeError("OpenAI response was empty.")
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenAI response was not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("OpenAI response must be a JSON object.")
    return parsed


def expand_theme_to_basket(
    client: OpenAI,
    model: str,
    theme: str,
    risk_profile: str,
    skill_context: str | None = None,
) -> dict[str, Any]:
    """Turn a free-text theme into a small ranked basket of liquid, tradable symbols."""
    try:
        response = client.responses.create(
            model=model,
            tools=[{"type": "web_search_preview"}],
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are an equity research assistant. Convert an investment theme into a "
                        "small basket of liquid, high-volume, optionable US equities that are the "
                        "best current expressions of that theme. Use recent web context."
                    ),
                },
                *_skill_messages(skill_context),
                {
                    "role": "user",
                    "content": (
                        f"Theme: {theme}\nRisk profile: {risk_profile}\n"
                        "Return STRICT JSON: {\"theme_summary\": str, \"basket\": ["
                        "{\"symbol\": str, \"rationale\": str, \"score\": number 0-1}]}.\n"
                        "Rules: 3-5 symbols, most liquid names only, no guarantees, "
                        "uppercase tickers, ordered by conviction."
                    ),
                },
            ],
        )
        parsed = _parse_json_object(response.output_text)
    except Exception as exc:
        raise RuntimeError("OpenAI theme expansion request failed.") from exc

    basket_raw = parsed.get("basket")
    basket: list[dict[str, Any]] = []
    if isinstance(basket_raw, list):
        for item in basket_raw:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            try:
                score = float(item.get("score", 0.0))
            except (TypeError, ValueError):
                score = 0.0
            basket.append(
                {
                    "symbol": symbol,
                    "rationale": str(item.get("rationale", "")).strip(),
                    "score": max(0.0, min(1.0, score)),
                }
            )
            if len(basket) >= 5:
                break
    if not basket:
        raise RuntimeError("OpenAI theme expansion returned no usable symbols.")
    return {
        "theme_summary": str(parsed.get("theme_summary", "")).strip(),
        "basket": basket,
    }


def build_execution_plan(
    client: OpenAI,
    model: str,
    symbols: list[str],
    signal_context: list[dict[str, Any]],
    risk_profile: str,
    window_min_seconds: int,
    window_max_seconds: int,
    window_default_seconds: int,
    skill_context: str | None = None,
) -> dict[str, Any]:
    """Produce a structured, time-boxed execution plan with an agent-chosen window."""
    prompt_payload = {
        "symbols": symbols,
        "risk_profile": risk_profile,
        "signal_context": signal_context,
        "window_bounds_seconds": {"min": window_min_seconds, "max": window_max_seconds},
    }
    try:
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are an execution strategist for short-horizon, momentum-driven "
                        "equity trades. You design a single time-boxed strategy with an explicit "
                        "exit for every position. The position is flat now and must be bought "
                        "first. Choose a holding window appropriate to the setup."
                    ),
                },
                *_skill_messages(skill_context),
                {
                    "role": "user",
                    "content": (
                        "Build the execution plan. Return STRICT JSON with keys: "
                        "recommended_window_seconds (int, within the provided bounds), "
                        "window_rationale (str), plan_notes (str), and legs (list). "
                        "Each leg MUST include: symbol, entry {type: 'market'|'limit', "
                        "ref_price (number|null), condition (str)}, target_pct (>0), "
                        "stop_pct (>0), weight (0-1). Every leg MUST have a target and a stop. "
                        "Pick the window to fit the setup (fast momentum = shorter, "
                        "catalyst = longer). Never guarantee outcomes.\n"
                        f"Input: {json.dumps(prompt_payload, default=str)}"
                    ),
                },
            ],
        )
        parsed = _parse_json_object(response.output_text)
    except Exception as exc:
        raise RuntimeError("OpenAI execution plan request failed.") from exc

    return normalize_execution_plan(
        parsed=parsed,
        fallback_symbols=symbols,
        window_min_seconds=window_min_seconds,
        window_max_seconds=window_max_seconds,
        window_default_seconds=window_default_seconds,
    )


def normalize_execution_plan(
    parsed: dict[str, Any],
    fallback_symbols: list[str],
    window_min_seconds: int,
    window_max_seconds: int,
    window_default_seconds: int,
) -> dict[str, Any]:
    """Validate/repair the structured plan: clamp window, require target+stop per leg."""
    try:
        window = int(float(parsed.get("recommended_window_seconds", window_default_seconds)))
    except (TypeError, ValueError):
        window = window_default_seconds
    window = max(window_min_seconds, min(window_max_seconds, window))

    legs_raw = parsed.get("legs")
    legs: list[dict[str, Any]] = []
    if isinstance(legs_raw, list):
        for item in legs_raw:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            try:
                target_pct = abs(float(item.get("target_pct", 0.0)))
            except (TypeError, ValueError):
                target_pct = 0.0
            try:
                stop_pct = abs(float(item.get("stop_pct", 0.0)))
            except (TypeError, ValueError):
                stop_pct = 0.0
            # Repair missing exits with conservative defaults so a leg always has both.
            if target_pct <= 0.0:
                target_pct = 1.5
            if stop_pct <= 0.0:
                stop_pct = 1.0
            try:
                weight = float(item.get("weight", 0.0))
            except (TypeError, ValueError):
                weight = 0.0
            entry_raw = item.get("entry") if isinstance(item.get("entry"), dict) else {}
            entry_type = str(entry_raw.get("type", "market")).strip().lower()
            if entry_type not in {"market", "limit"}:
                entry_type = "market"
            ref_price_raw = entry_raw.get("ref_price")
            try:
                ref_price = float(ref_price_raw) if ref_price_raw is not None else None
            except (TypeError, ValueError):
                ref_price = None
            legs.append(
                {
                    "symbol": symbol,
                    "entry": {
                        "type": entry_type,
                        "ref_price": ref_price,
                        "condition": str(entry_raw.get("condition", "")).strip(),
                    },
                    "target_pct": target_pct,
                    "stop_pct": stop_pct,
                    "weight": max(0.0, weight),
                }
            )

    if not legs:
        legs = [
            {
                "symbol": str(symbol).upper(),
                "entry": {"type": "market", "ref_price": None, "condition": "immediate"},
                "target_pct": 1.5,
                "stop_pct": 1.0,
                "weight": 1.0,
            }
            for symbol in fallback_symbols
        ]

    total_weight = sum(leg["weight"] for leg in legs)
    if total_weight <= 0.0:
        even = 1.0 / len(legs)
        for leg in legs:
            leg["weight"] = even
    else:
        for leg in legs:
            leg["weight"] = leg["weight"] / total_weight

    return {
        "recommended_window_seconds": window,
        "window_rationale": str(parsed.get("window_rationale", "")).strip(),
        "plan_notes": str(parsed.get("plan_notes", "")).strip(),
        "legs": legs,
    }


def evaluate_execution_checkpoint(
    client: OpenAI,
    model: str,
    state: dict[str, Any],
    skill_context: str | None = None,
) -> dict[str, Any]:
    """Mid-window checkpoint: continue, tighten stop, take profit, or abort."""
    try:
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a trade supervisor monitoring an in-flight, time-boxed "
                        "strategy. Decide whether to continue, tighten the stop, take profit "
                        "now, or abort and flatten. Be decisive and risk-aware."
                    ),
                },
                *_skill_messages(skill_context),
                {
                    "role": "user",
                    "content": (
                        "Return STRICT JSON: {\"decision\": "
                        "\"continue\"|\"tighten_stop\"|\"take_profit\"|\"abort\", "
                        "\"stop_pct\": number|null, \"target_pct\": number|null, "
                        "\"rationale\": str}. Only include stop_pct/target_pct when adjusting.\n"
                        f"State: {json.dumps(state, default=str)}"
                    ),
                },
            ],
        )
        parsed = _parse_json_object(response.output_text)
    except Exception as exc:
        raise RuntimeError("OpenAI checkpoint request failed.") from exc

    decision = str(parsed.get("decision", "continue")).strip().lower()
    if decision not in {"continue", "tighten_stop", "take_profit", "abort"}:
        decision = "continue"

    def _opt_float(value: Any) -> float | None:
        try:
            return abs(float(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "decision": decision,
        "stop_pct": _opt_float(parsed.get("stop_pct")),
        "target_pct": _opt_float(parsed.get("target_pct")),
        "rationale": str(parsed.get("rationale", "")).strip(),
    }


def author_strategy_skill(
    client: OpenAI,
    model: str,
    context: dict[str, Any],
    skill_context: str | None = None,
) -> str:
    """Codify the developed strategy as a SKILL.md document (Agent Skills spec)."""
    try:
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You write reusable trading-strategy skills following the Agent Skills "
                        "spec. Output ONLY a SKILL.md document: YAML frontmatter with 'name' "
                        "(lowercase-hyphen, <=64 chars) and 'description' (when to use, <=1024 "
                        "chars), then a markdown body covering thesis, entry/exit rules, the "
                        "window rationale, sizing, and risk notes. No code fences around the file."
                    ),
                },
                *_skill_messages(skill_context),
                {
                    "role": "user",
                    "content": (
                        "Write the SKILL.md capturing this developed strategy so a future agent "
                        "can reuse it.\n"
                        f"Strategy context: {json.dumps(context, default=str)}"
                    ),
                },
            ],
        )
        text = _strip_json_fence(response.output_text).strip()
    except Exception as exc:
        raise RuntimeError("OpenAI skill authoring request failed.") from exc
    if not text:
        raise RuntimeError("OpenAI skill authoring returned empty output.")
    return text
