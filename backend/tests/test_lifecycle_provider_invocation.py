"""End-to-end provider-invocation checks across research and execution phases.

Asserts the selected data provider is used during research AND the selected
broker is used during execution, for both stock and futures asset classes, and
that no path falls back to a hard-coded Alpaca client.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.clients.providers.base import ViewOnlyProviderError
from app.repositories.markdown_research_store import MarkdownResearchStore
from app.services.market_research_service import MarketResearchService
from app.services.trading_service import TradingService


def _bars(n: int = 40) -> list[dict[str, Any]]:
    return [
        {"t": i, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.0 + i * 0.1, "v": 100 + i}
        for i in range(n)
    ]


class SpyDataProvider:
    def __init__(self) -> None:
        self.get_bars_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []

    def get_bars(self, symbol, timeframe, limit, asset_class="stock"):
        self.get_bars_calls.append({"symbol": symbol, "asset_class": asset_class})
        return _bars(limit)

    def list_tradable_symbols(self, limit=200, asset_class="stock"):
        self.list_calls.append({"asset_class": asset_class, "limit": limit})
        return ["ESZ24"] if asset_class == "future" else ["AAPL"]

    def get_market_clock(self, asset_class="stock"):
        return {"is_open": True, "timestamp": None, "next_open": None, "next_close": None}


class SpyBroker:
    def __init__(self) -> None:
        self.orders: list[dict[str, Any]] = []

    def submit_order(self, symbol, qty, side, order_type="market", time_in_force="day", asset_class="stock"):
        self.orders.append({"symbol": symbol, "side": side, "asset_class": asset_class})
        return {"id": "o1", "symbol": symbol, "qty": qty, "side": side,
                "type": order_type, "time_in_force": time_in_force, "status": "filled",
                "filled_qty": qty, "filled_avg_price": 1.0}

    def get_order(self, order_id):
        return {"id": order_id, "status": "filled", "filled_qty": 1.0, "filled_avg_price": 1.0}

    def get_account_overview(self):
        return {"equity": 1000.0}

    def get_open_positions(self):
        return []

    def get_account_activities(self, activity_types=None, limit=200):
        return []


class ViewOnlyBroker(SpyBroker):
    def submit_order(self, *args, **kwargs):
        raise ViewOnlyProviderError("view only")


@pytest.fixture
def settings(settings_factory, tmp_path: Path):
    return settings_factory(
        agent_store_path=str(tmp_path / "agents.json"),
        runtime_config_path=str(tmp_path / "rc.json"),
        research_store_dir=str(tmp_path / "research"),
    )


@pytest.mark.parametrize("asset_class,symbol", [("stock", "AAPL"), ("future", "ESZ24")])
def test_trading_service_uses_data_and_broker(settings, asset_class, symbol):
    data = SpyDataProvider()
    broker = SpyBroker()
    svc = TradingService(
        settings=settings,
        broker_provider_provider=lambda: broker,
        data_provider_provider=lambda: data,
        openai_client=None,
    )

    bars = svc.get_market_data(symbol=symbol, timeframe="1Day", asset_class=asset_class)
    assert bars
    assert data.get_bars_calls[-1]["asset_class"] == asset_class

    order = svc.execute_signal_order(symbol=symbol, signal="BUY", qty=1, asset_class=asset_class)
    assert order is not None
    assert broker.orders[-1]["asset_class"] == asset_class


def test_view_only_broker_skips_order_without_raising(settings):
    svc = TradingService(
        settings=settings,
        broker_provider_provider=lambda: ViewOnlyBroker(),
        data_provider_provider=lambda: SpyDataProvider(),
        openai_client=None,
    )
    order = svc.execute_signal_order(symbol="ESZ24", signal="BUY", qty=1, asset_class="future")
    assert order is not None
    assert order["status"] == "skipped_view_only"
    assert order["skipped_reason"] == "broker_is_view_only"


@pytest.mark.parametrize("asset_class,symbol", [("stock", "AAPL"), ("future", "ESZ24")])
def test_research_uses_selected_data_provider(settings, tmp_path, asset_class, symbol):
    data = SpyDataProvider()
    research = MarketResearchService(
        settings=settings,
        data_provider=data,
        research_store=MarkdownResearchStore(
            base_dir=str(tmp_path / "research"), retention_days=14
        ),
        openai_client=None,
    )

    # Research from explicit symbols (build_strategy).
    result = research.build_strategy(symbols=[symbol], asset_class=asset_class)
    assert result.symbols == [symbol]
    assert any(c["asset_class"] == asset_class for c in data.get_bars_calls)

    # Universe scan path (generate_buy_candidate_strategy) hits list_tradable_symbols.
    research.generate_buy_candidate_strategy(asset_class=asset_class)
    assert data.list_calls and data.list_calls[-1]["asset_class"] == asset_class
