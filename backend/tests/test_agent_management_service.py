from __future__ import annotations

from queue import Empty

import pytest

from app.repositories.json_agent_store import JsonAgentStore
from app.services.agent_management_service import AgentManagementService
from app.services.agent_monitor_stream import AgentMonitorStream
from app.settings import Settings


def test_create_and_retrieve_agent(agent_management_service: AgentManagementService) -> None:
    created = agent_management_service.create_agent(
        symbols=["aapl", "msft"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=90,
        name="Morning Agent",
    )
    fetched = agent_management_service.get_agent(created["agent_id"])
    assert fetched["name"] == "Morning Agent"
    assert fetched["symbols"] == ["AAPL", "MSFT"]


def test_activate_pause_and_run_cycle(agent_management_service: AgentManagementService) -> None:
    created = agent_management_service.create_agent(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=60,
    )
    active = agent_management_service.activate_agent(created["agent_id"])
    assert active["status"] == "active"
    agent_management_service.run_active_agents_cycle()
    updated = agent_management_service.get_agent(created["agent_id"])
    assert updated["last_run_at"] is not None
    assert len(updated["runs"]) >= 1
    paused = agent_management_service.pause_agent(created["agent_id"])
    assert paused["status"] == "paused"


def test_activate_runs_initial_bootstrap_buy_once(agent_management_service: AgentManagementService) -> None:
    created = agent_management_service.create_agent(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=60,
    )
    first_activation = agent_management_service.activate_agent(created["agent_id"])
    assert first_activation["status"] == "active"
    assert len(first_activation["runs"]) == 1
    first_trade = first_activation["runs"][0]["trades"][0]
    assert first_trade["symbol"] == "AAPL"
    assert first_trade["signal"] == "BUY"
    assert first_trade["side"] == "buy"
    assert first_trade["qty"] == 0.0
    assert first_trade["order_status"] == "accepted"
    assert first_trade["policy_action"] == "activation_bootstrap_executed"
    assert first_activation.get("activation_bootstrap_attempted_at")

    agent_management_service.pause_agent(created["agent_id"])
    second_activation = agent_management_service.activate_agent(created["agent_id"])
    assert second_activation["status"] == "active"
    assert len(second_activation["runs"]) == 1


def test_activation_bootstrap_blocked_does_not_create_holdings(agent_management_service: AgentManagementService) -> None:
    agent_management_service.settings.summary_starting_deposit = 0.0
    created = agent_management_service.create_agent(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="conservative",
        interval_seconds=60,
    )
    active = agent_management_service.activate_agent(created["agent_id"])
    trade = active["runs"][0]["trades"][0]
    assert trade["signal"] == "BUY"
    assert trade["side"] == "hold"
    assert trade["qty"] == 0.0
    assert trade["order_status"] == "risk_blocked"
    assert trade["policy_action"] == "activation_bootstrap_skipped"
    positions = agent_management_service.get_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL"
    assert positions[0]["qty"] == 0.0


def test_delete_agent_stops_active_and_removes_record(agent_management_service: AgentManagementService) -> None:
    created = agent_management_service.create_agent(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=60,
    )
    agent_management_service.activate_agent(created["agent_id"])
    removed = agent_management_service.delete_agent(created["agent_id"])
    assert removed["agent_id"] == created["agent_id"]
    with pytest.raises(KeyError):
        agent_management_service.get_agent(created["agent_id"])


def test_summary_and_positions(agent_management_service: AgentManagementService) -> None:
    created = agent_management_service.create_agent(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=60,
    )
    agent_management_service.activate_agent(created["agent_id"])
    agent_management_service.run_agent_once(created["agent_id"])
    positions = agent_management_service.get_positions()
    summary = agent_management_service.get_summary(period="all")
    assert len(positions) >= 1
    assert summary["deposited"] == 100.0
    assert summary["trade_count"] >= 1


def test_duplicate_strategy_is_rejected(
    agent_management_service: AgentManagementService,
) -> None:
    first = agent_management_service.create_agent(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=60,
    )
    generated = agent_management_service.generate_agent_draft(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=60,
    )
    with pytest.raises(Exception):
        agent_management_service.create_agent(
            symbols=None,
            timeframe=None,
            risk_profile=None,
            interval_seconds=None,
            generated=generated,
        )
    assert first["agent_id"]


def test_get_transactions_flattens_runs_sorted_limited_and_mode_filtered(
    agent_management_service: AgentManagementService,
) -> None:
    first = agent_management_service.create_agent(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=60,
        name="First Agent",
    )
    second = agent_management_service.create_agent(
        symbols=["msft"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=60,
        name="Second Agent",
    )

    first["runs"] = [
        {
            "executed_at": "2026-03-08T10:00:00+00:00",
            "spent": 10.0,
            "earned": 0.0,
            "net_pnl": -10.0,
            "trades": [
                {
                    "symbol": "AAPL",
                    "signal": "BUY",
                    "side": "buy",
                    "qty": 1.0,
                    "latest_close": 10.0,
                    "value": 10.0,
                    "order_status": "filled",
                    "timeframe": "1Day",
                }
            ],
        }
    ]
    second["runs"] = [
        {
            "executed_at": "2026-03-08T11:00:00+00:00",
            "spent": 0.0,
            "earned": 12.0,
            "net_pnl": 12.0,
            "trades": [
                {
                    "symbol": "MSFT",
                    "signal": "SELL",
                    "side": "sell",
                    "qty": 1.0,
                    "latest_close": 12.0,
                    "value": 12.0,
                    "order_status": "filled",
                    "timeframe": "1Day",
                },
                {
                    "symbol": "MSFT",
                    "signal": "HOLD",
                    "side": "hold",
                    "qty": 0.0,
                    "latest_close": 12.0,
                    "value": 0.0,
                    "order_status": "none",
                    "timeframe": "1Day",
                },
            ],
        }
    ]
    agent_management_service.store.save_agent(first)
    agent_management_service.store.save_agent(second)

    transactions = agent_management_service.get_transactions(limit=10)
    assert len(transactions) == 2
    assert transactions[0]["agent_name"] == "Second Agent"
    assert transactions[0]["symbol"] == "MSFT"
    assert transactions[0]["side"] == "sell"
    assert transactions[0]["pnl"] == 12.0
    assert transactions[1]["agent_name"] == "First Agent"
    assert transactions[1]["side"] == "buy"
    assert transactions[1]["pnl"] == -10.0

    decisions = agent_management_service.get_transactions(limit=10, mode="decisions")
    assert len(decisions) == 3
    assert decisions[0]["symbol"] == "MSFT"
    assert decisions[0]["side"] == "sell"
    assert decisions[1]["symbol"] == "MSFT"
    assert decisions[1]["side"] == "hold"
    assert decisions[1]["pnl"] == 0.0
    assert decisions[2]["symbol"] == "AAPL"
    assert decisions[2]["side"] == "buy"

    limited = agent_management_service.get_transactions(limit=1, mode="decisions")
    assert len(limited) == 1
    assert limited[0]["symbol"] == "MSFT"


def test_policy_blocks_buy_when_confidence_below_threshold(
    agent_management_service: AgentManagementService,
) -> None:
    created = agent_management_service.create_agent(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=60,
    )
    created["strategy_evaluation"] = {
        "confidence": 0.2,
        "threshold": 0.7,
        "recommendation": "not_recommended",
        "decision_summary": "Low confidence setup",
        "key_reasons": ["Insufficient confirmation"],
        "risk_notes": ["High whipsaw risk"],
        "high_level_plan": ["Wait for better setup"],
    }
    agent_management_service.store.save_agent(created)
    agent_management_service.activate_agent(created["agent_id"])

    updated = agent_management_service.run_agent_once(created["agent_id"])
    trade = updated["runs"][-1]["trades"][0]
    assert trade["side"] == "hold"
    assert trade["order_status"] == "policy_blocked"
    assert trade["policy_action"] == "blocked_by_confidence"


def test_risk_guardrail_limits_position_size_for_buy(
    agent_management_service: AgentManagementService,
) -> None:
    agent_management_service.settings.order_qty = 10.0
    created = agent_management_service.create_agent(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="conservative",
        interval_seconds=60,
    )
    created["strategy_evaluation"]["confidence"] = 0.9
    created["strategy_evaluation"]["recommendation"] = "recommended"
    agent_management_service.store.save_agent(created)
    agent_management_service.activate_agent(created["agent_id"])

    updated = agent_management_service.run_agent_once(created["agent_id"])
    trade = updated["runs"][-1]["trades"][0]
    assert trade["side"] == "buy"
    assert trade["qty"] == 0.0
    assert trade["order_status"] == "accepted"
    assert trade["policy_action"] == "policy_passed"


def test_no_new_bar_skips_trade_execution(
    agent_management_service: AgentManagementService,
) -> None:
    created = agent_management_service.create_agent(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=60,
    )
    agent_management_service.activate_agent(created["agent_id"])
    first = agent_management_service.run_agent_once(created["agent_id"])
    assert first["runs"][-1]["trades"][0]["order_status"] in {"accepted", "none", "policy_blocked", "risk_blocked"}

    second = agent_management_service.run_agent_once(created["agent_id"])
    trade = second["runs"][-1]["trades"][0]
    assert trade["order_status"] == "no_new_bar"
    assert trade["policy_action"] == "skip_no_new_bar"


def test_live_pending_order_does_not_mutate_positions(
    agent_management_service: AgentManagementService,
) -> None:
    created = agent_management_service.create_agent(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=60,
    )
    created["strategy_evaluation"]["confidence"] = 0.9
    created["strategy_evaluation"]["recommendation"] = "recommended"
    agent_management_service.store.save_agent(created)
    agent_management_service.activate_agent(created["agent_id"])

    updated = agent_management_service.run_agent_once(created["agent_id"])
    trade = updated["runs"][-1]["trades"][0]
    assert trade["side"] == "buy"
    assert trade["qty"] == 0.0
    assert trade["order_status"] == "accepted"

    positions = agent_management_service.get_positions()
    assert len(positions) == 1
    assert positions[0]["qty"] == 0.0


def test_followup_trigger_refreshes_strategy(
    agent_management_service: AgentManagementService,
) -> None:
    created = agent_management_service.create_agent(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=60,
    )
    created["strategy"] = {"picks": [{"symbol": "AAPL", "suggested_action": "hold"}]}
    created["strategy_evaluation"] = {
        "confidence": 0.2,
        "threshold": 0.7,
        "recommendation": "not_recommended",
        "decision_summary": "Low confidence",
        "key_reasons": ["Weak setup"],
        "risk_notes": [],
        "high_level_plan": ["Wait"],
    }
    created["research_refs"] = [
        {
            "created_at": "2020-01-01T00:00:00+00:00",
            "file_path": "/tmp/stale.md",
            "strategy_fingerprint": "stale",
            "ticker": "AAPL",
            "timeframe": "1Day",
            "symbols": ["AAPL"],
        }
    ]
    created["runs"] = [
        {
            "executed_at": "2026-03-01T00:00:00+00:00",
            "trades": [{"symbol": "AAPL", "side": "hold"}],
            "spent": 0.0,
            "earned": 0.0,
            "net_pnl": 0.0,
        }
        for _ in range(5)
    ]
    agent_management_service.store.save_agent(created)
    agent_management_service.activate_agent(created["agent_id"])

    agent_management_service.run_active_agents_cycle()
    refreshed = agent_management_service.get_agent(created["agent_id"])
    assert refreshed.get("strategy_last_refreshed_at")
    assert refreshed["strategy"].get("picks")


def test_positions_include_paused_agents_and_realized_unrealized_pnl(
    agent_management_service: AgentManagementService,
) -> None:
    created = agent_management_service.create_agent(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=60,
    )
    created["status"] = "paused"
    created["runs"] = [
        {
            "executed_at": "2026-03-08T10:00:00+00:00",
            "spent": 20.0,
            "earned": 0.0,
            "net_pnl": -20.0,
            "trades": [
                {
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 2.0,
                    "latest_close": 10.0,
                    "value": 20.0,
                }
            ],
        },
        {
            "executed_at": "2026-03-08T11:00:00+00:00",
            "spent": 0.0,
            "earned": 12.0,
            "net_pnl": 12.0,
            "trades": [
                {
                    "symbol": "AAPL",
                    "side": "sell",
                    "qty": 1.0,
                    "latest_close": 12.0,
                    "value": 12.0,
                }
            ],
        },
    ]
    agent_management_service.store.save_agent(created)

    positions = agent_management_service.get_positions()
    assert len(positions) == 1
    row = positions[0]
    assert row["symbol"] == "AAPL"
    assert row["qty"] == 1.0
    assert row["realized_pnl"] == 2.0
    assert row["unrealized_pnl"] == 2.0


def test_followup_events_include_safe_thought_summary(
    unit_settings: Settings,
    fake_research_service,
    fake_trading_service,
) -> None:
    monitor_stream = AgentMonitorStream()
    service = AgentManagementService(
        settings=unit_settings,
        store=JsonAgentStore(file_path=unit_settings.agent_store_path),
        market_research_service=fake_research_service,
        trading_service=fake_trading_service,
        monitor_stream=monitor_stream,
    )
    queue = monitor_stream.subscribe(agent_id=None)
    created = service.create_agent(
        symbols=["aapl"],
        timeframe="1Day",
        risk_profile="balanced",
        interval_seconds=60,
    )
    created["strategy_evaluation"] = {
        "confidence": 0.2,
        "threshold": 0.7,
        "recommendation": "not_recommended",
        "decision_summary": "Low confidence",
        "key_reasons": ["Weak setup"],
        "risk_notes": [],
        "high_level_plan": ["Wait"],
    }
    created["research_refs"] = [
        {
            "created_at": "2020-01-01T00:00:00+00:00",
            "file_path": "/tmp/stale.md",
            "strategy_fingerprint": "stale",
            "ticker": "AAPL",
            "timeframe": "1Day",
            "symbols": ["AAPL"],
        }
    ]
    created["runs"] = [
        {
            "executed_at": "2026-03-01T00:00:00+00:00",
            "trades": [{"symbol": "AAPL", "side": "hold"}],
            "spent": 0.0,
            "earned": 0.0,
            "net_pnl": 0.0,
        }
        for _ in range(5)
    ]
    service.store.save_agent(created)
    service.activate_agent(created["agent_id"])
    service.run_active_agents_cycle()

    events = []
    while True:
        try:
            events.append(queue.get_nowait())
        except Empty:
            break
    thought_events = [
        event
        for event in events
        if event.get("event") in {"agent_followup_triggered", "agent_strategy_refreshed", "agent_strategy_refresh_failed"}
    ]
    assert len(thought_events) >= 1
    for event in thought_events:
        payload = event.get("payload", {})
        assert isinstance(payload.get("thought_summary"), dict)
        summary = payload["thought_summary"]
        assert "stage" in summary
        assert "decision" in summary
        assert isinstance(summary.get("actions", []), list)
        assert isinstance(summary.get("next_steps", []), list)
        assert "raw_reasoning" not in summary
        assert "chain_of_thought" not in summary
