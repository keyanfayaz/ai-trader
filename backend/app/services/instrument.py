"""Asset-class instrument helpers.

Stocks pass through unchanged. Futures get light normalization plus a small
contract-multiplier table used for display/quantity sizing. Signal logic
itself is asset-agnostic and is not touched here.
"""

from __future__ import annotations

import re

# Point value (USD per 1.0 price move) for common futures roots. Used only
# for display and notional sizing; execution sizing stays contract-count based.
FUTURES_MULTIPLIERS: dict[str, float] = {
    "ES": 50.0,    # E-mini S&P 500
    "MES": 5.0,    # Micro E-mini S&P 500
    "NQ": 20.0,    # E-mini Nasdaq 100
    "MNQ": 2.0,    # Micro E-mini Nasdaq 100
    "YM": 5.0,     # E-mini Dow
    "RTY": 50.0,   # E-mini Russell 2000
    "CL": 1000.0,  # Crude Oil
    "MCL": 100.0,  # Micro Crude Oil
    "GC": 100.0,   # Gold
    "MGC": 10.0,   # Micro Gold
    "SI": 5000.0,  # Silver
    "ZB": 1000.0,  # 30Y T-Bond
    "ZN": 1000.0,  # 10Y T-Note
    "6E": 125000.0,  # Euro FX
}

_MONTH_CODES = set("FGHJKMNQUVXZ")
# Root + month code + 1-2 digit year, e.g. ESZ24 / MNQH2025 (year may be 1-4 digits).
_CONTRACT_RE = re.compile(r"^([A-Z0-9]{1,4})([FGHJKMNQUVXZ])(\d{1,4})$")

VALID_ASSET_CLASSES = ("stock", "future")


def normalize_asset_class(value: str | None) -> str:
    normalized = str(value or "stock").strip().lower()
    return normalized if normalized in VALID_ASSET_CLASSES else "stock"


def normalize_symbol(symbol: str, asset_class: str = "stock") -> str:
    """Uppercase + trim. Futures keep their full contract code if present."""
    return str(symbol or "").strip().upper()


def futures_root(symbol: str) -> str:
    """Return the futures root (e.g. ``ESZ24`` -> ``ES``)."""
    s = normalize_symbol(symbol)
    match = _CONTRACT_RE.match(s)
    if match:
        return match.group(1)
    return s


def is_valid_symbol(symbol: str, asset_class: str = "stock") -> bool:
    s = normalize_symbol(symbol)
    if not s:
        return False
    if normalize_asset_class(asset_class) == "future":
        # Either a bare root (ES) or a full contract code (ESZ24).
        return bool(_CONTRACT_RE.match(s)) or s.isalnum()
    return bool(re.match(r"^[A-Z][A-Z0-9.\-]{0,9}$", s))


def contract_multiplier(symbol: str, asset_class: str = "stock") -> float:
    """Point value for sizing/display. Stocks are always 1.0."""
    if normalize_asset_class(asset_class) != "future":
        return 1.0
    return FUTURES_MULTIPLIERS.get(futures_root(symbol), 1.0)
