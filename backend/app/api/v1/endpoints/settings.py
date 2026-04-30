from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_runtime_config_store
from app.api.schemas import TradingModeResponse, TradingModeUpdateRequest
from app.repositories.runtime_config_store import RuntimeConfigStore


router = APIRouter()


@router.get("/trading-mode", response_model=TradingModeResponse)
def get_trading_mode(
    runtime_store: RuntimeConfigStore = Depends(get_runtime_config_store),
) -> TradingModeResponse:
    return TradingModeResponse(trading_mode=runtime_store.get_trading_mode())


@router.patch("/trading-mode", response_model=TradingModeResponse)
def update_trading_mode(
    payload: TradingModeUpdateRequest,
    runtime_store: RuntimeConfigStore = Depends(get_runtime_config_store),
) -> TradingModeResponse:
    try:
        mode = runtime_store.set_trading_mode(payload.trading_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TradingModeResponse(trading_mode=mode)
