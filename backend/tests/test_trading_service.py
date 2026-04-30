from __future__ import annotations

from typing import Any

from app.services.trading_service import TradingService
from app.settings import Settings


def test_get_market_data_fetches_bars(unit_settings: Settings, fake_alpaca_client: Any) -> None:
    service = TradingService(
        settings=unit_settings,
        alpaca_client=fake_alpaca_client,
        openai_client=None,
    )
    bars = service.get_market_data(symbol="msft")
    assert len(bars) > 0
    assert fake_alpaca_client.last_bars_symbol == "MSFT"


def test_buy_submits_order(unit_settings: Settings, fake_alpaca_client: Any) -> None:
    service = TradingService(
        settings=unit_settings,
        alpaca_client=fake_alpaca_client,
        openai_client=None,
    )
    order = service.buy(symbol="aapl")
    assert order["symbol"] == "AAPL"
    assert order["side"] == "buy"
    assert order["status"] == "accepted"


def test_sell_live_submits_order(unit_settings: Settings, fake_alpaca_client: Any) -> None:
    service = TradingService(
        settings=unit_settings,
        alpaca_client=fake_alpaca_client,
        openai_client=None,
    )
    order = service.sell(symbol="aapl", qty=1.5)
    assert order["symbol"] == "AAPL"
    assert order["side"] == "sell"
    assert fake_alpaca_client.last_order_side == "sell"


def test_run_produces_standard_result_shape(unit_settings: Settings, fake_alpaca_client: Any) -> None:
    service = TradingService(
        settings=unit_settings,
        alpaca_client=fake_alpaca_client,
        openai_client=None,
    )
    result = service.run(use_openai_explanation=False)
    assert result.symbol == "AAPL"
    assert result.timeframe == "1Day"
    assert result.signal_result.signal in {"BUY", "SELL", "HOLD"}
    if result.signal_result.signal in {"BUY", "SELL"}:
        assert result.order_response is not None


def test_execute_signal_order_honors_validated_qty(unit_settings: Settings, fake_alpaca_client: Any) -> None:
    service = TradingService(
        settings=unit_settings,
        alpaca_client=fake_alpaca_client,
        openai_client=None,
    )
    order = service.execute_signal_order(
        symbol="aapl",
        signal="BUY",
        qty=1.25,
    )
    assert order is not None
    assert order["side"] == "buy"
    assert order["qty"] == 1.25


def test_live_order_tracks_submitted_and_final_status(unit_settings: Settings) -> None:
    class PollingAlpacaClient:
        def __init__(self) -> None:
            self._status_calls = 0

        def get_bars(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, float | int]]:
            _ = symbol
            _ = timeframe
            _ = limit
            return []

        def submit_order(self, symbol: str, qty: float, side: str, order_type: str, time_in_force: str) -> dict[str, Any]:
            return {
                "id": "ord-live-1",
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "type": order_type,
                "time_in_force": time_in_force,
                "status": "accepted",
                "filled_qty": 0.0,
                "filled_avg_price": None,
            }

        def get_order(self, order_id: str) -> dict[str, Any]:
            _ = order_id
            self._status_calls += 1
            if self._status_calls == 1:
                return {
                    "id": "ord-live-1",
                    "symbol": "AAPL",
                    "qty": 2.0,
                    "side": "buy",
                    "status": "partially_filled",
                    "filled_qty": 1.0,
                    "filled_avg_price": 101.25,
                }
            return {
                "id": "ord-live-1",
                "symbol": "AAPL",
                "qty": 2.0,
                "side": "buy",
                "status": "filled",
                "filled_qty": 2.0,
                "filled_avg_price": 101.5,
            }

        def get_market_clock(self) -> dict[str, Any]:
            return {"is_open": True, "next_open": None, "next_close": None}

    service = TradingService(
        settings=unit_settings,
        alpaca_client=PollingAlpacaClient(),
        openai_client=None,
    )
    order = service.buy(symbol="aapl", qty=2.0)
    assert order["submitted_status"] == "accepted"
    assert order["final_status"] in {"partially_filled", "filled"}
    assert order["filled_qty"] > 0.0
