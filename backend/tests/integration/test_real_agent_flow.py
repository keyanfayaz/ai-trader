from __future__ import annotations

import pytest

from app.services.agent_management_service import AgentManagementService


@pytest.mark.integration
def test_generated_candidate_agent_can_run_with_real_clients(
    real_agent_management_service: AgentManagementService,
) -> None:
    generated = real_agent_management_service.generate_agent_draft(
        symbols=None,
        timeframe=None,
        risk_profile="balanced",
        interval_seconds=60,
    )

    assert generated["symbols"]
    assert generated["ticker"] in generated["symbols"]
    assert isinstance(generated["strategy"], dict)
    assert isinstance(generated.get("strategy_evaluation", {}), dict)
    assert generated["strategy_evaluation"].get("recommendation") in {"recommended", "not_recommended"}

    created = real_agent_management_service.create_agent(
        symbols=None,
        timeframe=None,
        risk_profile=None,
        interval_seconds=None,
        generated=generated,
    )
    agent_id = created["agent_id"]
    real_agent_management_service.activate_agent(agent_id)
    updated = real_agent_management_service.run_agent_once(agent_id)

    assert updated["last_run_at"] is not None
    assert len(updated.get("runs", [])) >= 1
    latest_run = updated["runs"][-1]
    assert latest_run.get("executed_at")
    assert isinstance(latest_run.get("trades", []), list)
    assert len(latest_run["trades"]) >= 1

    first_trade = latest_run["trades"][0]
    assert first_trade.get("symbol")
    assert first_trade.get("side") in {"buy", "sell", "hold"}
    assert "latest_close" in first_trade
    assert "policy_action" in first_trade
