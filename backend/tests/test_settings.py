from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.settings import load_settings


class SettingsTests(unittest.TestCase):
    def test_load_settings_raises_when_openai_key_missing(self) -> None:
        env_content = "\n".join(
            [
                "ALPACA_PAPER_API_KEY=test_paper_key",
                "ALPACA_PAPER_SECRET_KEY=test_paper_secret",
                "ALPACA_PAPER_BASE_URL=https://paper-api.alpaca.markets",
                "ALPACA_REAL_API_KEY=test_real_key",
                "ALPACA_REAL_SECRET_KEY=test_real_secret",
                "ALPACA_REAL_BASE_URL=https://api.alpaca.markets",
                "DEFAULT_SYMBOL=SPY",
                "BAR_TIMEFRAME=1Day",
                "FAST_SMA_WINDOW=5",
                "SLOW_SMA_WINDOW=20",
                "ORDER_QTY=1",
                "LOG_LEVEL=INFO",
            ]
        )
        with tempfile.NamedTemporaryFile("w", delete=False) as env_file:
            env_file.write(env_content)
            env_path = Path(env_file.name)

        try:
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(
                    ValueError, "Missing required environment variable: OPENAI_API_KEY"
                ):
                    load_settings(env_path=env_path)
        finally:
            os.unlink(env_path)

    def test_load_settings_raises_when_paper_base_url_not_paper(self) -> None:
        env_content = "\n".join(
            [
                "ALPACA_PAPER_API_KEY=test_paper_key",
                "ALPACA_PAPER_SECRET_KEY=test_paper_secret",
                "ALPACA_PAPER_BASE_URL=https://api.alpaca.markets",
                "ALPACA_REAL_API_KEY=test_real_key",
                "ALPACA_REAL_SECRET_KEY=test_real_secret",
                "ALPACA_REAL_BASE_URL=https://api.alpaca.markets",
                "OPENAI_API_KEY=openai_key",
                "OPENAI_MODEL=gpt-5.4",
                "DEFAULT_SYMBOL=SPY",
                "BAR_TIMEFRAME=1Day",
                "FAST_SMA_WINDOW=5",
                "SLOW_SMA_WINDOW=20",
                "ORDER_QTY=1",
                "LOG_LEVEL=INFO",
            ]
        )
        with tempfile.NamedTemporaryFile("w", delete=False) as env_file:
            env_file.write(env_content)
            env_path = Path(env_file.name)

        try:
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "ALPACA_PAPER_BASE_URL must use the Alpaca paper endpoint"):
                    load_settings(env_path=env_path)
        finally:
            os.unlink(env_path)
