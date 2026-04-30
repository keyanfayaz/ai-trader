from __future__ import annotations

import pytest

from app.services.market_research_service import MarketResearchService


@pytest.mark.integration
def test_generate_buy_candidate_strategy_real_clients(
    real_market_research_service: MarketResearchService,
) -> None:
    result = real_market_research_service.generate_buy_candidate_strategy(
        risk_profile="balanced",
        max_candidates=80,
    )

    assert result.symbols
    assert result.ticker
    assert result.ticker in result.symbols
    assert isinstance(result.signal_context, list)
    assert len(result.signal_context) >= 1
    assert isinstance(result.strategy, dict)
    assert isinstance(result.strategy.get("picks", []), list)
    assert isinstance(result.evaluation, dict)
    assert result.evaluation.get("recommendation") in {"recommended", "not_recommended"}
    assert isinstance(result.evaluation.get("high_level_plan"), list)
    assert len(result.research_refs) == 1
    assert result.research_refs[0].get("file_path")
