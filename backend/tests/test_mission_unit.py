from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.clients.openai_client import normalize_execution_plan


def test_normalize_plan_clamps_window_and_repairs_exits():
    plan = normalize_execution_plan(
        parsed={
            "recommended_window_seconds": 999_999,
            "legs": [{"symbol": "aapl", "weight": 2}],  # missing target/stop
        },
        fallback_symbols=["AAPL"],
        window_min_seconds=300,
        window_max_seconds=14_400,
        window_default_seconds=1_800,
    )
    assert plan["recommended_window_seconds"] == 14_400
    leg = plan["legs"][0]
    assert leg["symbol"] == "AAPL"
    assert leg["target_pct"] > 0
    assert leg["stop_pct"] > 0
    assert leg["weight"] == pytest.approx(1.0)  # single leg normalized


def test_normalize_plan_defaults_when_window_absent():
    plan = normalize_execution_plan(
        parsed={"legs": []},
        fallback_symbols=["MSFT"],
        window_min_seconds=300,
        window_max_seconds=14_400,
        window_default_seconds=1_800,
    )
    assert plan["recommended_window_seconds"] == 1_800
    assert plan["legs"][0]["symbol"] == "MSFT"


def test_mission_create_approve_reject(agent_management_service):
    svc = agent_management_service
    draft = svc.generate_mission_draft(theme="AI is hot", risk_profile="balanced")
    assert draft["input_kind"] == "theme"
    assert draft["skill_markdown"].startswith("---")

    record = svc.create_mission_agent(draft=draft)
    agent_id = record["agent_id"]
    assert record["mission_status"] == "awaiting_approval"
    assert record["status"] == "paused"

    approved = svc.approve_mission(agent_id, window_seconds_override=99_999_999)
    assert approved["mission_status"] == "armed"
    assert approved["status"] == "active"
    # Clamped to the configured max window.
    assert approved["strategy_window_seconds"] == svc.settings.mission_window_max_seconds

    rejected = svc.reject_mission(agent_id)
    assert rejected["mission_status"] == "rejected"
    assert rejected["status"] == "paused"


def test_mission_cycle_arms_and_buys_first(agent_management_service, fake_trading_service):
    svc = agent_management_service
    fake_trading_service.market_open = True
    fake_trading_service.latest_close = 50.0
    draft = svc.generate_mission_draft(symbols=["AAPL"], risk_profile="balanced")
    record = svc.create_mission_agent(draft=draft)
    svc.approve_mission(record["agent_id"])

    svc.run_mission_cycle()

    updated = svc.get_agent(record["agent_id"])
    assert updated["mission_status"] == "executing"
    assert updated["window_ends_at"]
    actions = [e["action"] for e in updated["decision_log"]]
    assert "window_started" in actions
    assert "position_opened" in actions
    assert fake_trading_service.signal_orders[0]["side"] == "buy"


def test_mission_time_exit_flattens_and_completes(
    agent_management_service, fake_trading_service
):
    svc = agent_management_service
    fake_trading_service.market_open = True
    draft = svc.generate_mission_draft(symbols=["AAPL"], risk_profile="balanced")
    record = svc.create_mission_agent(draft=draft)
    approved = svc.approve_mission(record["agent_id"])

    # Force the window to be already expired and simulate a held position.
    approved["mission_status"] = "executing"
    approved["window_started_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    approved["window_ends_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    svc.store.save_agent(approved)
    fake_trading_service.open_positions = [
        {"symbol": "AAPL", "qty": 3.0, "current_price": 51.0, "avg_entry_price": 50.0}
    ]

    svc.run_mission_cycle()

    final = svc.get_agent(record["agent_id"])
    assert final["mission_status"] == "completed"
    assert final["status"] == "paused"
    assert final["terminal_reason"] == "window_expired"
    sells = [o for o in fake_trading_service.signal_orders if o["side"] == "sell"]
    assert sells and sells[-1]["symbol"] == "AAPL"
    actions = [e["action"] for e in final["decision_log"]]
    assert "mission_completed" in actions
