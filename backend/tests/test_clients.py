from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.clients.alpaca_client import AlpacaClient, _normalize_trading_base_url
from app.clients.openai_client import _parse_evaluator_response, explain_signal


class AlpacaClientTests(unittest.TestCase):
    def test_get_bars_raises_runtime_error_on_request_failure(self) -> None:
        mock_data_client = MagicMock()
        mock_data_client.get_stock_bars.side_effect = RuntimeError("network down")

        client = AlpacaClient(
            api_key="key",
            secret_key="secret",
            trading_base_url="https://paper-api.alpaca.markets",
            data_client=mock_data_client,
            trading_client=MagicMock(),
        )

        with self.assertRaisesRegex(RuntimeError, "Alpaca market data request failed"):
            client.get_bars(symbol="SPY", timeframe="1Day", limit=25)

    def test_normalize_trading_base_url_strips_v2_suffix(self) -> None:
        self.assertEqual(
            _normalize_trading_base_url("https://paper-api.alpaca.markets/v2"),
            "https://paper-api.alpaca.markets",
        )
        self.assertEqual(
            _normalize_trading_base_url("https://paper-api.alpaca.markets"),
            "https://paper-api.alpaca.markets",
        )


class OpenAIClientTests(unittest.TestCase):
    def test_explain_signal_raises_runtime_error_on_openai_failure(self) -> None:
        mock_client = MagicMock()
        mock_client.responses.create.side_effect = RuntimeError("boom")

        with self.assertRaisesRegex(RuntimeError, "OpenAI explanation request failed"):
            explain_signal(
                client=mock_client,
                model="gpt-5.4",
                symbol="SPY",
                timeframe="1Day",
                signal="BUY",
                fast_sma=101.0,
                slow_sma=100.0,
            )

    def test_parse_evaluator_response_requires_confidence_bounds(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "between 0 and 1"):
            _parse_evaluator_response(
                """
                {"confidence": 1.2, "decision_summary": "x", "key_reasons": ["a"], "risk_notes": ["b"], "high_level_plan": ["c"]}
                """
            )
