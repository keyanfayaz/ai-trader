from __future__ import annotations

import hashlib
import json
from typing import Any


def build_strategy_fingerprint(
    symbols: list[str],
    ticker: str,
    timeframe: str,
    risk_profile: str,
    strategy: dict[str, Any],
) -> str:
    normalized_payload = {
        "symbols": sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()}),
        "ticker": str(ticker).upper().strip(),
        "timeframe": str(timeframe).strip().lower(),
        "risk_profile": str(risk_profile).strip().lower(),
        "picks": _normalized_picks(strategy.get("picks")),
        "market_outlook": str(strategy.get("market_outlook", "")).strip(),
        "strategy_notes": str(strategy.get("strategy_notes", "")).strip(),
    }
    canonical = json.dumps(normalized_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalized_picks(raw_picks: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_picks, list):
        return []
    normalized: list[dict[str, Any]] = []
    for pick in raw_picks:
        if not isinstance(pick, dict):
            continue
        symbol = str(pick.get("symbol", "")).upper().strip()
        signal = str(pick.get("signal", "")).upper().strip()
        action = str(pick.get("suggested_action", "")).lower().strip()
        confidence = _safe_float(pick.get("confidence"))
        rationale = str(pick.get("rationale", "")).strip()
        normalized.append(
            {
                "symbol": symbol,
                "signal": signal,
                "suggested_action": action,
                "confidence": confidence,
                "rationale": rationale,
            }
        )
    normalized.sort(key=lambda item: (item["symbol"], item["signal"], item["suggested_action"], item["confidence"]))
    return normalized


def _safe_float(value: Any) -> float:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return 0.0
