from __future__ import annotations

import json
from types import SimpleNamespace

from app.clients.openai_client import evaluate_strategy_signal, research_trading_strategy


class CapturingClient:
    def __init__(self, output_text: str) -> None:
        self.captured_inputs: list = []
        self.responses = self

    def create(self, *args, **kwargs):
        self.captured_inputs.append(kwargs.get("input"))
        return SimpleNamespace(output_text=self._output)

    _output = ""


def _strategy_client() -> CapturingClient:
    client = CapturingClient("")
    client._output = json.dumps(
        {"picks": [], "market_outlook": "", "strategy_notes": "", "risk_warnings": []}
    )
    return client


def test_research_without_skill_context_has_no_extra_system_message():
    client = _strategy_client()
    research_trading_strategy(
        client=client,
        model="gpt-test",
        symbols=["AAPL"],
        timeframe="1Day",
        signal_context=[],
        risk_profile="balanced",
    )
    messages = client.captured_inputs[0]
    system_messages = [m for m in messages if m["role"] == "system"]
    assert len(system_messages) == 1


def test_research_with_skill_context_inserts_system_message():
    client = _strategy_client()
    research_trading_strategy(
        client=client,
        model="gpt-test",
        symbols=["AAPL"],
        timeframe="1Day",
        signal_context=[],
        risk_profile="balanced",
        skill_context="SKILL BLOCK: only trade breakouts",
    )
    messages = client.captured_inputs[0]
    system_messages = [m for m in messages if m["role"] == "system"]
    assert len(system_messages) == 2
    assert any("SKILL BLOCK: only trade breakouts" in m["content"] for m in system_messages)


def test_evaluate_with_skill_context_inserts_system_message():
    client = CapturingClient("")
    client._output = json.dumps(
        {
            "confidence": 0.8,
            "decision_summary": "ok",
            "key_reasons": ["a"],
            "risk_notes": [],
            "high_level_plan": ["b"],
        }
    )
    evaluate_strategy_signal(
        client=client,
        model="gpt-test",
        symbol="AAPL",
        timeframe="1Day",
        risk_profile="balanced",
        signal_context=[],
        strategy={},
        skill_context="EVAL SKILL BLOCK",
    )
    messages = client.captured_inputs[0]
    system_messages = [m for m in messages if m["role"] == "system"]
    assert len(system_messages) == 2
    assert any("EVAL SKILL BLOCK" in m["content"] for m in system_messages)
