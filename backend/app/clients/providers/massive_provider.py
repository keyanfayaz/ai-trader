from __future__ import annotations

from typing import Any

import httpx

from app.clients.providers.base import AssetClass, ViewOnlyProviderError
from app.clients.providers.databento_provider import (
    _extract_symbols,
    _iter_rows,
    _normalize_bar,
)


_TIMEFRAME_TO_RESOLUTION = {
    "1min": "1/minute",
    "1minute": "1/minute",
    "5min": "5/minute",
    "15min": "15/minute",
    "1hour": "1/hour",
    "1day": "1/day",
    "1week": "1/week",
    "1month": "1/month",
}


class MassiveProvider:
    """Market-data-only provider backed by the Massive REST API.

    Trading operations raise ViewOnlyProviderError. The HTTP client is
    injectable so the adapter can be exercised without network access.
    """

    provider_id = "massive"
    base_url = "https://api.massive.com/v1"

    def __init__(self, api_key: str, http_client: httpx.Client | None = None) -> None:
        if not api_key:
            raise RuntimeError(
                "Massive is selected but MASSIVE_API_KEY is not configured."
            )
        self._api_key = api_key
        self._http = http_client or httpx.Client(timeout=30.0)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    # --- MarketDataProvider ---
    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        asset_class: AssetClass = "stock",
    ) -> list[dict[str, Any]]:
        resolution = _TIMEFRAME_TO_RESOLUTION.get(timeframe.strip().lower(), "1/day")
        market = "futures" if asset_class == "future" else "stocks"
        try:
            response = self._http.get(
                f"{self.base_url}/{market}/aggregates/{symbol.upper()}",
                params={"resolution": resolution, "limit": int(limit)},
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Massive market data request failed for {symbol}."
            ) from exc

        bars = [b for b in (_normalize_bar(r) for r in _iter_rows(rows)) if b is not None]
        if not bars:
            raise RuntimeError(
                f"Massive returned no bars for symbol '{symbol.upper()}'."
            )
        return bars[-int(limit):]

    def list_tradable_symbols(
        self,
        limit: int = 200,
        asset_class: AssetClass = "stock",
    ) -> list[str]:
        market = "futures" if asset_class == "future" else "stocks"
        try:
            response = self._http.get(
                f"{self.base_url}/{market}/tickers",
                params={"limit": int(limit), "active": "true"},
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Massive symbol list request failed.") from exc

        symbols = _extract_symbols(payload)[: int(limit)]
        if not symbols:
            raise RuntimeError("Massive returned no tradable symbols.")
        return symbols

    def get_market_clock(self, asset_class: AssetClass = "stock") -> dict[str, Any]:
        return {
            "is_open": True,
            "timestamp": None,
            "next_open": None,
            "next_close": None,
        }

    # --- BrokerProvider (unsupported) ---
    def submit_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ViewOnlyProviderError("Massive is a market-data-only provider; it cannot place orders.")

    def get_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ViewOnlyProviderError("Massive is a market-data-only provider.")

    def get_account_overview(self) -> dict[str, Any]:
        raise ViewOnlyProviderError("Massive is a market-data-only provider.")

    def get_open_positions(self) -> list[dict[str, Any]]:
        raise ViewOnlyProviderError("Massive is a market-data-only provider.")

    def get_account_activities(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise ViewOnlyProviderError("Massive is a market-data-only provider.")
