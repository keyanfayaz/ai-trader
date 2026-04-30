from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.market_research_service import MarketResearchService
from app.settings import Settings


def test_build_strategy_uses_fallback_when_openai_missing(
    unit_settings: Settings,
    fake_alpaca_client: Any,
    fake_research_store: Any,
) -> None:
    service = MarketResearchService(
        settings=unit_settings,
        alpaca_client=fake_alpaca_client,
        research_store=fake_research_store,
        openai_client=None,
    )
    result = service.build_strategy(symbols=["aapl", "msft"])

    assert result.symbols == ["AAPL", "MSFT"]
    assert result.ticker == "AAPL"
    assert "picks" in result.strategy
    assert len(result.signal_context) >= 1
    assert "confidence" in result.evaluation
    assert result.evaluation["recommendation"] in {"recommended", "not_recommended"}
    assert len(result.research_refs) == 1
    assert len(result.research_thoughts) >= 1
    assert any(item.get("source") == "signal_service" for item in result.research_thoughts)


def test_build_strategy_applies_soft_gate_threshold(
    unit_settings: Settings,
    fake_alpaca_client: Any,
    fake_research_store: Any,
    fake_openai_client_factory: Callable[[list[str]], Any],
) -> None:
    fake_openai = fake_openai_client_factory(
        outputs=[
            """
            {
              "picks": [{"symbol": "AAPL", "signal": "BUY", "confidence": 0.80, "rationale": "Momentum improving", "suggested_action": "buy"}],
              "market_outlook": "Constructive",
              "strategy_notes": "Watch for continuation",
              "risk_warnings": ["Volatility can increase around headlines."]
            }
            """,
            """
            {
              "confidence": 0.65,
              "decision_summary": "The setup has potential but conviction is below preferred threshold.",
              "key_reasons": ["BUY trigger exists but trend confirmation is weak."],
              "risk_notes": ["Potential whipsaw in current regime."],
              "high_level_plan": ["Use smaller size initially."]
            }
            """,
        ]
    )
    service = MarketResearchService(
        settings=unit_settings,
        alpaca_client=fake_alpaca_client,
        research_store=fake_research_store,
        openai_client=fake_openai,
    )
    result = service.build_strategy(symbols=["aapl"])
    assert result.evaluation["confidence"] == 0.65
    assert result.evaluation["threshold"] == 0.7
    assert result.evaluation["recommendation"] == "not_recommended"
    assert isinstance(result.evaluation.get("high_level_plan"), list)
    assert len(result.evaluation.get("high_level_plan", [])) > 0
    assert len(result.research_refs) == 1
    assert len(result.research_thoughts) >= 1


def test_build_strategy_emits_progress_callbacks(
    unit_settings: Settings,
    fake_alpaca_client: Any,
    fake_research_store: Any,
) -> None:
    service = MarketResearchService(
        settings=unit_settings,
        alpaca_client=fake_alpaca_client,
        research_store=fake_research_store,
        openai_client=None,
    )
    progress: list[dict[str, Any]] = []
    service.build_strategy(
        symbols=["aapl"],
        progress_callback=lambda item: progress.append(item),
    )
    assert len(progress) >= 3
    assert any(str(item.get("source")) == "signal_service" for item in progress)
    assert any(str(item.get("stage")) == "evaluation" for item in progress)
