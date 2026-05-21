from __future__ import annotations

from typing import Any

import httpx

from app.clients.providers.base import AssetClass, ViewOnlyProviderError


_TIMEFRAME_TO_SCHEMA = {
    "1min": "ohlcv-1m",
    "1minute": "ohlcv-1m",
    "5min": "ohlcv-1m",
    "15min": "ohlcv-1m",
    "1hour": "ohlcv-1h",
    "1day": "ohlcv-1d",
    "1week": "ohlcv-1d",
    "1month": "ohlcv-1d",
}
_ASSET_CLASS_TO_DATASET = {
    "stock": "XNAS.ITCH",
    "future": "GLBX.MDP3",
}


class DatabentoProvider:
    """Market-data-only provider backed by the Databento HTTP API.

    Trading operations are unsupported (raises ViewOnlyProviderError). The HTTP
    client is injectable so the adapter can be exercised without network access.
    """

    provider_id = "databento"
    base_url = "https://hist.databento.com/v0"

    def __init__(self, api_key: str, http_client: httpx.Client | None = None) -> None:
        if not api_key:
            raise RuntimeError(
                "Databento is selected but DATABENTO_API_KEY is not configured."
            )
        self._api_key = api_key
        self._http = http_client or httpx.Client(timeout=30.0)

    # --- MarketDataProvider ---
    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        asset_class: AssetClass = "stock",
    ) -> list[dict[str, Any]]:
        schema = _TIMEFRAME_TO_SCHEMA.get(timeframe.strip().lower(), "ohlcv-1d")
        dataset = _ASSET_CLASS_TO_DATASET.get(asset_class, _ASSET_CLASS_TO_DATASET["stock"])
        try:
            response = self._http.get(
                f"{self.base_url}/timeseries.get_range",
                params={
                    "dataset": dataset,
                    "symbols": symbol.upper(),
                    "schema": schema,
                    "limit": int(limit),
                    "encoding": "json",
                },
                auth=(self._api_key, ""),
            )
            response.raise_for_status()
            rows = response.json()
        except Exception as exc:  # noqa: BLE001 - normalize to RuntimeError
            raise RuntimeError(
                f"Databento market data request failed for {symbol}."
            ) from exc

        bars = [_normalize_bar(row) for row in _iter_rows(rows)]
        bars = [bar for bar in bars if bar is not None]
        if not bars:
            raise RuntimeError(
                f"Databento returned no bars for symbol '{symbol.upper()}'."
            )
        return bars[-int(limit):]

    def list_tradable_symbols(
        self,
        limit: int = 200,
        asset_class: AssetClass = "stock",
    ) -> list[str]:
        dataset = _ASSET_CLASS_TO_DATASET.get(asset_class, _ASSET_CLASS_TO_DATASET["stock"])
        try:
            response = self._http.get(
                f"{self.base_url}/metadata.list_symbols",
                params={"dataset": dataset, "limit": int(limit)},
                auth=(self._api_key, ""),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Databento symbol list request failed.") from exc

        symbols = _extract_symbols(payload)[: int(limit)]
        if not symbols:
            raise RuntimeError("Databento returned no tradable symbols.")
        return symbols

    def get_market_clock(self, asset_class: AssetClass = "stock") -> dict[str, Any]:
        # View-only provider: report always-available so research can proceed;
        # execution is gated by the broker provider, not the data provider.
        return {
            "is_open": True,
            "timestamp": None,
            "next_open": None,
            "next_close": None,
        }

    # --- BrokerProvider (unsupported) ---
    def submit_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ViewOnlyProviderError("Databento is a market-data-only provider; it cannot place orders.")

    def get_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ViewOnlyProviderError("Databento is a market-data-only provider.")

    def get_account_overview(self) -> dict[str, Any]:
        raise ViewOnlyProviderError("Databento is a market-data-only provider.")

    def get_open_positions(self) -> list[dict[str, Any]]:
        raise ViewOnlyProviderError("Databento is a market-data-only provider.")

    def get_account_activities(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise ViewOnlyProviderError("Databento is a market-data-only provider.")


def _iter_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "result", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _normalize_bar(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return {
            "t": row.get("ts_event") or row.get("t") or row.get("timestamp"),
            "o": float(row.get("open", row.get("o", 0.0))),
            "h": float(row.get("high", row.get("h", 0.0))),
            "l": float(row.get("low", row.get("l", 0.0))),
            "c": float(row.get("close", row.get("c", 0.0))),
            "v": float(row.get("volume", row.get("v", 0.0))),
        }
    except (TypeError, ValueError):
        return None


def _extract_symbols(payload: Any) -> list[str]:
    raw: list[Any]
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, dict):
        raw = payload.get("symbols") or payload.get("result") or []
    else:
        raw = []
    seen: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            value = item.get("symbol") or item.get("raw_symbol")
        else:
            value = item
        symbol = str(value or "").upper().strip()
        if symbol and symbol not in seen:
            seen.append(symbol)
    return seen
