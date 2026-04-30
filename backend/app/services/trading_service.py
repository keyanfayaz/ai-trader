from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from app.clients.alpaca_client import AlpacaClient
from app.clients.openai_client import explain_signal
from app.services.signal_service import SignalResult, calculate_sma_signal
from app.settings import Settings


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class TradingRunResult:
    symbol: str
    timeframe: str
    bars: list[dict[str, Any]]
    signal_result: SignalResult
    order_response: dict[str, Any] | None
    openai_explanation: str | None


class TradingService:
    def __init__(
        self,
        settings: Settings,
        alpaca_client: AlpacaClient | None = None,
        alpaca_client_provider: Callable[[], AlpacaClient] | None = None,
        openai_client: Any | None = None,
    ) -> None:
        self.settings = settings
        self.alpaca_client = alpaca_client
        self.alpaca_client_provider = alpaca_client_provider
        self.openai_client = openai_client
        if self.alpaca_client is None and self.alpaca_client_provider is None:
            raise ValueError("TradingService requires alpaca_client or alpaca_client_provider.")

    def get_market_runtime(self) -> dict[str, Any]:
        clock = self._alpaca_client().get_market_clock()
        is_open = bool(clock.get("is_open", False))
        return {
            "is_market_open": is_open,
            "next_market_open": clock.get("next_open"),
            "next_market_close": clock.get("next_close"),
            "market_status_label": "market_open" if is_open else "market_closed",
        }

    def get_account_overview(self) -> dict[str, Any]:
        return self._alpaca_client().get_account_overview()

    def get_open_positions(self) -> list[dict[str, Any]]:
        return self._alpaca_client().get_open_positions()

    def get_fill_activities(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._alpaca_client().get_account_activities(activity_types=["FILL"], limit=limit)

    def get_market_data(
        self,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        chosen_symbol = self._resolve_symbol(symbol)
        chosen_timeframe = self._resolve_timeframe(timeframe)
        chosen_limit = limit or (self.settings.slow_sma_window + 5)
        bars = self._alpaca_client().get_bars(
            symbol=chosen_symbol,
            timeframe=chosen_timeframe,
            limit=chosen_limit,
        )
        LOGGER.info(
            "Fetched %s bars for symbol=%s timeframe=%s limit=%s",
            len(bars),
            chosen_symbol,
            chosen_timeframe,
            chosen_limit,
        )
        return bars

    def calculate_signal(self, bars: list[dict[str, Any]]) -> SignalResult:
        closes = [float(bar["c"]) for bar in bars]
        signal_result = calculate_sma_signal(
            closes=closes,
            fast_window=self.settings.fast_sma_window,
            slow_window=self.settings.slow_sma_window,
        )
        LOGGER.info(
            "Calculated signal=%s latest_close=%.4f fast_sma=%.4f slow_sma=%.4f",
            signal_result.signal,
            signal_result.latest_close,
            signal_result.fast_sma,
            signal_result.slow_sma,
        )
        return signal_result

    def buy(
        self,
        symbol: str,
        qty: float | None = None,
        order_type: str = "market",
        time_in_force: str = "day",
    ) -> dict[str, Any] | None:
        return self._submit_order(
            symbol=symbol,
            side="buy",
            qty=qty,
            order_type=order_type,
            time_in_force=time_in_force,
        )

    def sell(
        self,
        symbol: str,
        qty: float | None = None,
        order_type: str = "market",
        time_in_force: str = "day",
    ) -> dict[str, Any] | None:
        return self._submit_order(
            symbol=symbol,
            side="sell",
            qty=qty,
            order_type=order_type,
            time_in_force=time_in_force,
        )

    def execute_signal_order(
        self,
        symbol: str,
        signal: str,
        qty: float | None = None,
    ) -> dict[str, Any] | None:
        normalized_signal = signal.upper()
        if normalized_signal == "BUY":
            return self.buy(symbol=symbol, qty=qty)
        if normalized_signal == "SELL":
            return self.sell(symbol=symbol, qty=qty)
        LOGGER.info("Signal is %s. No order submitted.", normalized_signal)
        return None

    def run(
        self,
        symbol: str | None = None,
        timeframe: str | None = None,
        use_openai_explanation: bool = False,
    ) -> TradingRunResult:
        chosen_symbol = self._resolve_symbol(symbol)
        chosen_timeframe = self._resolve_timeframe(timeframe)

        bars = self.get_market_data(symbol=chosen_symbol, timeframe=chosen_timeframe)
        signal_result = self.calculate_signal(bars)
        order_response = self.execute_signal_order(
            symbol=chosen_symbol,
            signal=signal_result.signal,
        )

        explanation = self._build_signal_explanation(
            symbol=chosen_symbol,
            timeframe=chosen_timeframe,
            signal_result=signal_result,
            use_openai_explanation=use_openai_explanation,
        )
        self._log_run_summary(
            symbol=chosen_symbol,
            timeframe=chosen_timeframe,
            signal_result=signal_result,
            order_response=order_response,
            explanation=explanation,
        )

        return TradingRunResult(
            symbol=chosen_symbol,
            timeframe=chosen_timeframe,
            bars=bars,
            signal_result=signal_result,
            order_response=order_response,
            openai_explanation=explanation,
        )

    def _submit_order(
        self,
        symbol: str,
        side: str,
        qty: float | None,
        order_type: str,
        time_in_force: str,
    ) -> dict[str, Any]:
        normalized_symbol = self._resolve_symbol(symbol)
        chosen_qty = qty if qty is not None else self.settings.order_qty

        submitted_order = self._alpaca_client().submit_order(
            symbol=normalized_symbol,
            qty=chosen_qty,
            side=side,
            order_type=order_type,
            time_in_force=time_in_force,
        )
        final_order = self._resolve_final_order_state(submitted_order)
        lifecycle_order = {
            **final_order,
            "submitted_status": str(submitted_order.get("status", "")),
            "final_status": str(final_order.get("status", submitted_order.get("status", ""))),
            "filled_qty": float(final_order.get("filled_qty", 0.0) or 0.0),
            "filled_avg_price": final_order.get("filled_avg_price"),
        }
        LOGGER.info("Submitted order response=%s", lifecycle_order)
        return lifecycle_order

    def _build_signal_explanation(
        self,
        symbol: str,
        timeframe: str,
        signal_result: SignalResult,
        use_openai_explanation: bool,
    ) -> str | None:
        if not use_openai_explanation or self.openai_client is None:
            return None

        return explain_signal(
            client=self.openai_client,
            model=self.settings.openai_model,
            symbol=symbol,
            timeframe=timeframe,
            signal=signal_result.signal,
            fast_sma=signal_result.fast_sma,
            slow_sma=signal_result.slow_sma,
        )

    def _log_run_summary(
        self,
        symbol: str,
        timeframe: str,
        signal_result: SignalResult,
        order_response: dict[str, Any] | None,
        explanation: str | None,
    ) -> None:
        LOGGER.info(
            "Trading summary symbol=%s timeframe=%s signal=%s latest_close=%.4f fast_sma=%.4f slow_sma=%.4f order=%s",
            symbol,
            timeframe,
            signal_result.signal,
            signal_result.latest_close,
            signal_result.fast_sma,
            signal_result.slow_sma,
            order_response,
        )
        if explanation:
            LOGGER.info("Signal explanation=%s", explanation)

    def _resolve_symbol(self, symbol: str | None) -> str:
        return (symbol or self.settings.default_symbol).upper()

    def _resolve_timeframe(self, timeframe: str | None) -> str:
        return timeframe or self.settings.bar_timeframe

    def _alpaca_client(self) -> AlpacaClient:
        if self.alpaca_client_provider is not None:
            return self.alpaca_client_provider()
        if self.alpaca_client is None:
            raise RuntimeError("No Alpaca client configured.")
        return self.alpaca_client

    def _resolve_final_order_state(self, submitted_order: dict[str, Any]) -> dict[str, Any]:
        order_id = str(submitted_order.get("id", "")).strip()
        if not order_id or not hasattr(self._alpaca_client(), "get_order"):
            return submitted_order
        status = str(submitted_order.get("status", "")).lower()
        if _is_terminal_order_status(status):
            return submitted_order

        latest = submitted_order
        max_attempts = 3
        for _ in range(max_attempts):
            time.sleep(0.2)
            try:
                latest = self._alpaca_client().get_order(order_id)
            except Exception:
                break
            if _is_terminal_order_status(str(latest.get("status", "")).lower()):
                break
        return latest


def _is_terminal_order_status(status: str) -> bool:
    terminal_statuses = {
        "filled",
        "partially_filled",
        "canceled",
        "cancelled",
        "done_for_day",
        "expired",
        "replaced",
        "pending_replace",
        "rejected",
        "stopped",
        "suspended",
        "calculated",
    }
    return status in terminal_statuses
