from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_runtime_config_store
from app.api.schemas import (
    ProviderCapabilityResponse,
    ProviderSelectionResponse,
    ProviderSelectionUpdateRequest,
    TradingModeResponse,
    TradingModeUpdateRequest,
)
from app.clients.providers.base import list_capabilities
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


def _provider_selection_response(
    runtime_store: RuntimeConfigStore,
) -> ProviderSelectionResponse:
    return ProviderSelectionResponse(
        data_provider=runtime_store.get_data_provider(),
        broker_provider=runtime_store.get_broker_provider(),
        providers=[
            ProviderCapabilityResponse(**caps.to_dict()) for caps in list_capabilities()
        ],
    )


@router.get("/providers", response_model=ProviderSelectionResponse)
def get_providers(
    runtime_store: RuntimeConfigStore = Depends(get_runtime_config_store),
) -> ProviderSelectionResponse:
    return _provider_selection_response(runtime_store)


@router.patch("/providers", response_model=ProviderSelectionResponse)
def update_providers(
    payload: ProviderSelectionUpdateRequest,
    runtime_store: RuntimeConfigStore = Depends(get_runtime_config_store),
) -> ProviderSelectionResponse:
    try:
        if payload.data_provider is not None:
            runtime_store.set_data_provider(payload.data_provider)
        if payload.broker_provider is not None:
            runtime_store.set_broker_provider(payload.broker_provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _provider_selection_response(runtime_store)
