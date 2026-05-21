from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


AssetClass = Literal["stock", "future"]

VALID_ASSET_CLASSES: tuple[str, ...] = ("stock", "future")


class ViewOnlyProviderError(RuntimeError):
    """Raised when a trading operation is attempted on a market-data-only provider."""


class UnsupportedAssetClassError(RuntimeError):
    """Raised when a provider is asked for an asset class it does not support."""


@runtime_checkable
class MarketDataProvider(Protocol):
    """Read-only market data surface. All data providers implement this."""

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        asset_class: AssetClass = "stock",
    ) -> list[dict[str, Any]]: ...

    def list_tradable_symbols(
        self,
        limit: int = 200,
        asset_class: AssetClass = "stock",
    ) -> list[str]: ...

    def get_market_clock(self, asset_class: AssetClass = "stock") -> dict[str, Any]: ...


@runtime_checkable
class BrokerProvider(Protocol):
    """Trade-execution surface. Only trade-capable providers implement this."""

    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        time_in_force: str = "day",
        asset_class: AssetClass = "stock",
    ) -> dict[str, Any]: ...

    def get_order(self, order_id: str) -> dict[str, Any]: ...

    def get_account_overview(self) -> dict[str, Any]: ...

    def get_open_positions(self) -> list[dict[str, Any]]: ...

    def get_account_activities(
        self,
        activity_types: list[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]: ...


@dataclass(slots=True, frozen=True)
class ProviderCapabilities:
    id: str
    label: str
    market_data: bool
    trading: bool
    asset_classes: tuple[str, ...]
    supports_paper: bool
    description: str = ""

    def supports_asset_class(self, asset_class: str) -> bool:
        return asset_class in self.asset_classes

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "market_data": self.market_data,
            "trading": self.trading,
            "asset_classes": list(self.asset_classes),
            "supports_paper": self.supports_paper,
            "description": self.description,
        }


PROVIDER_REGISTRY: dict[str, ProviderCapabilities] = {
    "alpaca": ProviderCapabilities(
        id="alpaca",
        label="Alpaca",
        market_data=True,
        trading=True,
        asset_classes=("stock",),
        supports_paper=True,
        description="US equities market data and brokerage. Supports paper and live trading.",
    ),
    "databento": ProviderCapabilities(
        id="databento",
        label="Databento",
        market_data=True,
        trading=False,
        asset_classes=("stock", "future"),
        supports_paper=False,
        description="Market data only (view-only). Stocks and futures. No order execution.",
    ),
    "massive": ProviderCapabilities(
        id="massive",
        label="Massive",
        market_data=True,
        trading=False,
        asset_classes=("stock", "future"),
        supports_paper=False,
        description="Market data only (view-only). Stocks and futures. No order execution.",
    ),
    "topstepx": ProviderCapabilities(
        id="topstepx",
        label="TopStepX",
        market_data=True,
        trading=True,
        asset_classes=("future",),
        supports_paper=False,
        description="Futures market data and brokerage (ProjectX Gateway API). Prop-firm accounts; real-only.",
    ),
}

DEFAULT_DATA_PROVIDER = "alpaca"
DEFAULT_BROKER_PROVIDER = "alpaca"


def list_capabilities() -> list[ProviderCapabilities]:
    return list(PROVIDER_REGISTRY.values())


def get_capabilities(provider_id: str) -> ProviderCapabilities:
    key = (provider_id or "").strip().lower()
    if key not in PROVIDER_REGISTRY:
        raise ValueError(
            f"Unknown provider '{provider_id}'. Valid providers: {sorted(PROVIDER_REGISTRY)}."
        )
    return PROVIDER_REGISTRY[key]
