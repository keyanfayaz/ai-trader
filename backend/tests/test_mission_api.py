from __future__ import annotations

from fastapi.testclient import TestClient


def test_generate_and_create_mission(api_client: TestClient):
    gen = api_client.post(
        "/api/v1/agents/mission/generate",
        json={"theme": "AI is hot", "risk_profile": "balanced"},
    )
    assert gen.status_code == 200
    draft = gen.json()["draft"]
    assert draft["input_kind"] == "theme"
    assert draft["execution_plan"]["legs"]

    created = api_client.post("/api/v1/agents/mission", json={"draft": draft})
    assert created.status_code == 200
    body = created.json()
    assert body["mission_status"] == "awaiting_approval"


def test_approve_and_reject_mission(api_client: TestClient):
    approved = api_client.post(
        "/api/v1/agents/agent-1/approve", json={"window_seconds": 600}
    )
    assert approved.status_code == 200
    assert approved.json()["mission_status"] == "armed"
    assert approved.json()["strategy_window_seconds"] == 600

    rejected = api_client.post("/api/v1/agents/agent-1/reject")
    assert rejected.status_code == 200
    assert rejected.json()["mission_status"] == "rejected"


def test_approve_unknown_agent_404(api_client: TestClient):
    resp = api_client.post("/api/v1/agents/nope/approve", json={})
    assert resp.status_code == 404
