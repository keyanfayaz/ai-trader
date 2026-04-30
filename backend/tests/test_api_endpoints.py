from __future__ import annotations

from app.repositories.markdown_research_store import ResearchRecord
from fastapi.testclient import TestClient

from app.api.app import create_app


def test_health_endpoint(api_client: TestClient) -> None:
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_buy_endpoint(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/v1/trading/buy",
        json={"symbol": "AAPL", "qty": 2},
    )
    assert response.status_code == 200
    assert response.json()["order"]["side"] == "buy"


def test_research_strategy_endpoint(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/v1/research/strategy",
        json={"symbols": ["AAPL", "MSFT"], "risk_profile": "balanced"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["symbols"] == ["AAPL", "MSFT"]
    assert "picks" in body["strategy"]


def test_create_agent_endpoint(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/v1/agents",
        json={"symbols": ["AAPL", "MSFT"], "risk_profile": "balanced"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "paused"
    assert body["symbols"] == ["AAPL", "MSFT"]


def test_generate_agent_endpoint(api_client: TestClient) -> None:
    response = api_client.post("/api/v1/agents/generate", json={})
    assert response.status_code == 200
    body = response.json()
    assert "generated" in body
    assert body["generated"]["symbols"] == ["AAPL"]
    assert body["generated"]["ticker"] == "AAPL"
    assert "strategy" in body["generated"]
    assert "strategy_evaluation" in body["generated"]
    assert body["generated"]["strategy_evaluation"]["recommendation"] == "not_recommended"
    assert "research_thoughts" in body["generated"]


def test_create_agent_from_generated_endpoint(api_client: TestClient) -> None:
    generate_response = api_client.post("/api/v1/agents/generate", json={})
    generated = generate_response.json()["generated"]
    create_response = api_client.post("/api/v1/agents", json={"generated": generated})
    assert create_response.status_code == 200
    body = create_response.json()
    assert body["symbols"] == generated["symbols"]
    assert body["name"] == generated["name"]
    assert body["strategy_evaluation"]["recommendation"] == "not_recommended"
    assert "research_refs" in body


def test_delete_agent_endpoint(api_client: TestClient) -> None:
    create_response = api_client.post(
        "/api/v1/agents",
        json={"symbols": ["AAPL"], "risk_profile": "balanced"},
    )
    agent_id = create_response.json()["agent_id"]
    delete_response = api_client.delete(f"/api/v1/agents/{agent_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"agent_id": agent_id, "deleted": True}
    get_deleted = api_client.get(f"/api/v1/agents/{agent_id}")
    assert get_deleted.status_code == 404


def test_create_agent_conflict_endpoint(api_client: TestClient) -> None:
    generate_response = api_client.post("/api/v1/agents/generate", json={})
    generated = generate_response.json()["generated"]
    generated["name"] = "Duplicate draft"
    response = api_client.post("/api/v1/agents", json={"generated": generated})
    assert response.status_code == 409


def test_positions_and_summary_endpoints(api_client: TestClient) -> None:
    positions_response = api_client.get("/api/v1/agents/positions")
    summary_response = api_client.get("/api/v1/agents/summary?period=all")
    transactions_response = api_client.get("/api/v1/agents/transactions?limit=20")
    decisions_response = api_client.get("/api/v1/agents/transactions?limit=20&mode=decisions")
    assert positions_response.status_code == 200
    assert summary_response.status_code == 200
    assert transactions_response.status_code == 200
    assert decisions_response.status_code == 200
    assert "positions" in positions_response.json()
    assert "unrealized_pnl" in positions_response.json()["positions"][0]
    assert "portfolio_value" in summary_response.json()
    assert "transactions" in transactions_response.json()
    assert "transactions" in decisions_response.json()
    assert transactions_response.json()["transactions"][0]["side"] == "buy"
    assert decisions_response.json()["transactions"][0]["side"] in {"buy", "hold"}


def test_global_monitor_stream_emits_followup_thought_summary(api_client: TestClient) -> None:
    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/api/v1/agents/monitor/stream" in paths


def test_generate_stream_endpoint_registered(api_client: TestClient) -> None:
    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/api/v1/agents/generate/stream" in paths


def test_trading_mode_endpoint_round_trip(api_client: TestClient) -> None:
    get_response = api_client.get("/api/v1/settings/trading-mode")
    assert get_response.status_code == 200
    assert get_response.json()["trading_mode"] in {"paper", "real"}

    set_response = api_client.patch("/api/v1/settings/trading-mode", json={"trading_mode": "real"})
    assert set_response.status_code == 200
    assert set_response.json()["trading_mode"] == "real"

    get_after = api_client.get("/api/v1/settings/trading-mode")
    assert get_after.status_code == 200
    assert get_after.json()["trading_mode"] == "real"


def test_scheduler_state_toggle_removed() -> None:
    app = create_app()
    assert not hasattr(app.state, "enable_agent_scheduler")


def test_research_records_list_detail_and_update(api_client: TestClient, fake_research_store) -> None:
    saved = fake_research_store.append_record(
        ResearchRecord(
            created_at="2026-01-01T00:00:00+00:00",
            source="test",
            symbols=["AAPL"],
            ticker="AAPL",
            timeframe="1Day",
            risk_profile="balanced",
            strategy_fingerprint="fp-research",
            signal_context=[{"symbol": "AAPL", "signal": "BUY"}],
            strategy={"picks": [{"symbol": "AAPL"}]},
            evaluation={"confidence": 0.8},
        )
    )
    _ = saved
    list_response = api_client.get("/api/v1/research/records")
    assert list_response.status_code == 200
    body = list_response.json()
    assert len(body["folders"]) == 1
    record = body["folders"][0]["records"][0]
    detail_response = api_client.get(f"/api/v1/research/records/{record['id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["id"] == record["id"]
    assert detail["version"] == record["version"]
    update_response = api_client.patch(
        f"/api/v1/research/records/{record['id']}",
        json={"markdown": "## Updated\n\nEdited doc body.", "expected_version": detail["version"]},
    )
    assert update_response.status_code == 200
    updated = update_response.json()["record"]
    assert "## Updated" in updated["markdown"]
    assert updated["version"] != detail["version"]


def test_research_record_update_conflict(api_client: TestClient, fake_research_store) -> None:
    fake_research_store.append_record(
        ResearchRecord(
            created_at="2026-01-01T00:00:00+00:00",
            source="test",
            symbols=["MSFT"],
            ticker="MSFT",
            timeframe="1Day",
            risk_profile="balanced",
            strategy_fingerprint="fp-msft",
            signal_context=[{"symbol": "MSFT", "signal": "BUY"}],
            strategy={"picks": [{"symbol": "MSFT"}]},
            evaluation={"confidence": 0.75},
        )
    )
    list_response = api_client.get("/api/v1/research/records")
    record_id = list_response.json()["folders"][0]["records"][0]["id"]
    response = api_client.patch(
        f"/api/v1/research/records/{record_id}",
        json={"markdown": "stale update", "expected_version": "incorrect"},
    )
    assert response.status_code == 409


def test_research_record_delete_and_bulk_delete(api_client: TestClient, fake_research_store) -> None:
    first = fake_research_store.append_record(
        ResearchRecord(
            created_at="2026-01-01T00:00:00+00:00",
            source="test",
            symbols=["AAPL"],
            ticker="AAPL",
            timeframe="1Day",
            risk_profile="balanced",
            strategy_fingerprint="fp-del-aapl",
            signal_context=[{"symbol": "AAPL", "signal": "BUY"}],
            strategy={"picks": [{"symbol": "AAPL"}]},
            evaluation={"confidence": 0.8},
        )
    )
    second = fake_research_store.append_record(
        ResearchRecord(
            created_at="2026-01-01T01:00:00+00:00",
            source="test",
            symbols=["MSFT"],
            ticker="MSFT",
            timeframe="1Day",
            risk_profile="balanced",
            strategy_fingerprint="fp-del-msft",
            signal_context=[{"symbol": "MSFT", "signal": "BUY"}],
            strategy={"picks": [{"symbol": "MSFT"}]},
            evaluation={"confidence": 0.8},
        )
    )
    _ = (first, second)
    list_response = api_client.get("/api/v1/research/records")
    assert list_response.status_code == 200
    records = list_response.json()["folders"][0]["records"]
    first_id = records[0]["id"]
    second_id = records[1]["id"]

    delete_one = api_client.delete(f"/api/v1/research/records/{first_id}")
    assert delete_one.status_code == 200
    assert delete_one.json()["deleted"] is True

    delete_many = api_client.post("/api/v1/research/records/delete", json={"record_ids": [second_id, "missing"]})
    assert delete_many.status_code == 200
    body = delete_many.json()
    assert body["deleted_count"] == 1
    assert second_id in body["deleted_ids"]
    assert "missing" in body["missing_ids"]
