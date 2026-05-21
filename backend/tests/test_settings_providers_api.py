from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_provider_selection(api_client: TestClient):
    # The runtime-config file is shared across tests; keep selection isolated.
    api_client.patch(
        "/api/v1/settings/providers",
        json={"data_provider": "alpaca", "broker_provider": "alpaca"},
    )
    yield
    api_client.patch(
        "/api/v1/settings/providers",
        json={"data_provider": "alpaca", "broker_provider": "alpaca"},
    )


def test_get_providers_lists_capabilities(api_client: TestClient) -> None:
    res = api_client.get("/api/v1/settings/providers")
    assert res.status_code == 200
    body = res.json()
    assert body["data_provider"] == "alpaca"
    assert body["broker_provider"] == "alpaca"
    ids = {p["id"] for p in body["providers"]}
    assert ids == {"alpaca", "databento", "massive", "topstepx"}
    databento = next(p for p in body["providers"] if p["id"] == "databento")
    assert databento["market_data"] is True
    assert databento["trading"] is False
    assert "future" in databento["asset_classes"]


def test_patch_providers_round_trip(api_client: TestClient) -> None:
    res = api_client.patch(
        "/api/v1/settings/providers",
        json={"data_provider": "databento", "broker_provider": "topstepx"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["data_provider"] == "databento"
    assert body["broker_provider"] == "topstepx"

    after = api_client.get("/api/v1/settings/providers").json()
    assert after["data_provider"] == "databento"
    assert after["broker_provider"] == "topstepx"


def test_patch_rejects_view_only_broker(api_client: TestClient) -> None:
    res = api_client.patch(
        "/api/v1/settings/providers", json={"broker_provider": "databento"}
    )
    assert res.status_code == 400
    assert "view-only" in res.json()["detail"].lower()


def test_patch_rejects_unknown_provider(api_client: TestClient) -> None:
    res = api_client.patch(
        "/api/v1/settings/providers", json={"data_provider": "bogus"}
    )
    assert res.status_code == 400
