from __future__ import annotations

from typing import Any

from app.clients.alpaca_client import AlpacaClient
from app.clients.providers.base import AssetClass, UnsupportedAssetClassError


class AlpacaProvider:
    """Adapter wrapping the existing AlpacaClient as a data + broker provider.

    Alpaca is currently wired for US equities only. Futures requests raise a
    clear error so the caller can pick a futures-capable data/broker provider.
    """

    provider_id = "alpaca"

    def __init__(self, client: AlpacaClient) -> None:
        self._client = client

    @staticmethod
    def _require_stock(asset_class: AssetClass) -> None:
        if asset_class != "stock":
            raise UnsupportedAssetClassError(
                "Alpaca supports stock instruments only. Select a futures-capable "
                "provider (e.g. Databento/Massive for data, TopStepX for execution)."
            )

    # --- MarketDataProvider ---
    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        asset_class: AssetClass = "stock",
    ) -> list[dict[str, Any]]:
        self._require_stock(asset_class)
        return self._client.get_bars(symbol=symbol, timeframe=timeframe, limit=limit)

    def list_tradable_symbols(
        self,
        limit: int = 200,
        asset_class: AssetClass = "stock",
    ) -> list[str]:
        self._require_stock(asset_class)
        return self._client.list_active_tradable_equity_symbols(limit=limit)

    def get_market_clock(self, asset_class: AssetClass = "stock") -> dict[str, Any]:
        return self._client.get_market_clock()

    # --- BrokerProvider ---
    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        time_in_force: str = "day",
        asset_class: AssetClass = "stock",
    ) -> dict[str, Any]:
        self._require_stock(asset_class)
        return self._client.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=order_type,
            time_in_force=time_in_force,
        )

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._client.get_order(order_id)

    def get_account_overview(self) -> dict[str, Any]:
        return self._client.get_account_overview()

    def get_open_positions(self) -> list[dict[str, Any]]:
        return self._client.get_open_positions()

    def get_account_activities(
        self,
        activity_types: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return self._client.get_account_activities(
            activity_types=activity_types, limit=limit
        )
