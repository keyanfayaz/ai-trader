from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_trading_service
from app.api.schemas import MarketDataResponse, OrderRequest, OrderResponse
from app.services.trading_service import TradingService


router = APIRouter()


@router.post("/buy", response_model=OrderResponse)
def buy(
    payload: OrderRequest,
    trading_service: TradingService = Depends(get_trading_service),
) -> OrderResponse:
    order = trading_service.buy(
        symbol=payload.symbol,
        qty=payload.qty,
        order_type=payload.order_type,
        time_in_force=payload.time_in_force,
    )
    return OrderResponse(order=order or {})


@router.post("/sell", response_model=OrderResponse)
def sell(
    payload: OrderRequest,
    trading_service: TradingService = Depends(get_trading_service),
) -> OrderResponse:
    order = trading_service.sell(
        symbol=payload.symbol,
        qty=payload.qty,
        order_type=payload.order_type,
        time_in_force=payload.time_in_force,
    )
    return OrderResponse(order=order or {})


@router.get("/market-data", response_model=MarketDataResponse)
def market_data(
    symbol: str | None = Query(default=None),
    timeframe: str | None = Query(default=None),
    limit: int | None = Query(default=None, gt=1),
    trading_service: TradingService = Depends(get_trading_service),
) -> MarketDataResponse:
    bars = trading_service.get_market_data(symbol=symbol, timeframe=timeframe, limit=limit)
    signal_result = trading_service.calculate_signal(bars)
    return MarketDataResponse(
        symbol=(symbol or trading_service.settings.default_symbol).upper(),
        timeframe=timeframe or trading_service.settings.bar_timeframe,
        bars=bars,
        signal=signal_result.signal,
        fast_sma=signal_result.fast_sma,
        slow_sma=signal_result.slow_sma,
        latest_close=signal_result.latest_close,
    )
