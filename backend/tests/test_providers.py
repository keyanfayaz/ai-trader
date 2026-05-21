from __future__ import annotations

import pytest

from app.clients.providers.base import (
    PROVIDER_REGISTRY,
    ViewOnlyProviderError,
    get_capabilities,
    list_capabilities,
)
from app.clients.providers.databento_provider import DatabentoProvider
from app.clients.providers.massive_provider import MassiveProvider
from app.clients.providers.topstepx_provider import TopStepXProvider
from app.repositories.runtime_config_store import RuntimeConfigStore


def test_registry_capabilities():
    assert set(PROVIDER_REGISTRY) == {"alpaca", "databento", "massive", "topstepx"}
    alpaca = get_capabilities("alpaca")
    assert alpaca.market_data and alpaca.trading and alpaca.asset_classes == ("stock",)
    databento = get_capabilities("databento")
    assert databento.market_data and not databento.trading
    assert "future" in databento.asset_classes
    topstepx = get_capabilities("topstepx")
    assert topstepx.trading and topstepx.asset_classes == ("future",)
    assert not topstepx.supports_paper
    assert len(list_capabilities()) == 4


def test_view_only_providers_cannot_trade():
    db = DatabentoProvider(api_key="x", http_client=object())
    ms = MassiveProvider(api_key="x", http_client=object())
    for provider in (db, ms):
        with pytest.raises(ViewOnlyProviderError):
            provider.submit_order(symbol="ES", qty=1, side="buy")
        with pytest.raises(ViewOnlyProviderError):
            provider.get_account_overview()


def test_missing_credentials_raise():
    with pytest.raises(RuntimeError):
        DatabentoProvider(api_key="")
    with pytest.raises(RuntimeError):
        TopStepXProvider(api_key="", username="")


def test_runtime_store_validates_selection(tmp_path):
    store = RuntimeConfigStore(file_path=str(tmp_path / "rc.json"))
    assert store.get_data_provider() == "alpaca"
    assert store.get_broker_provider() == "alpaca"

    assert store.set_data_provider("databento") == "databento"
    assert store.get_data_provider() == "databento"

    # View-only provider cannot be a broker.
    with pytest.raises(ValueError):
        store.set_broker_provider("databento")
    # Unknown provider rejected.
    with pytest.raises(ValueError):
        store.set_data_provider("nope")

    assert store.set_broker_provider("topstepx") == "topstepx"
    assert store.get_broker_provider() == "topstepx"
