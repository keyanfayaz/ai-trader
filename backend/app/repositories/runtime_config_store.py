from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from app.clients.providers.base import PROVIDER_REGISTRY


class RuntimeConfigStore:
    """Simple file-backed store for runtime controls."""

    def __init__(
        self,
        file_path: str,
        default_trading_mode: str = "paper",
        default_data_provider: str = "alpaca",
        default_broker_provider: str = "alpaca",
    ) -> None:
        self.file_path = Path(file_path)
        self.default_trading_mode = default_trading_mode
        self.default_data_provider = default_data_provider
        self.default_broker_provider = default_broker_provider
        self._lock = Lock()

    def get_trading_mode(self) -> str:
        with self._lock:
            payload = self._read_payload_unlocked()
            mode = str(payload.get("trading_mode", self.default_trading_mode)).strip().lower()
            return mode if mode in {"paper", "real"} else self.default_trading_mode

    def set_trading_mode(self, trading_mode: str) -> str:
        normalized = str(trading_mode).strip().lower()
        if normalized not in {"paper", "real"}:
            raise ValueError("trading_mode must be either 'paper' or 'real'.")
        with self._lock:
            payload = self._read_payload_unlocked()
            payload["trading_mode"] = normalized
            self._write_payload_unlocked(payload)
            return normalized

    def get_data_provider(self) -> str:
        with self._lock:
            payload = self._read_payload_unlocked()
            value = str(payload.get("data_provider", self.default_data_provider)).strip().lower()
            caps = PROVIDER_REGISTRY.get(value)
            if caps is None or not caps.market_data:
                return self.default_data_provider
            return value

    def set_data_provider(self, provider_id: str) -> str:
        normalized = str(provider_id).strip().lower()
        caps = PROVIDER_REGISTRY.get(normalized)
        if caps is None:
            raise ValueError(f"Unknown provider '{provider_id}'.")
        if not caps.market_data:
            raise ValueError(f"Provider '{normalized}' does not offer market data.")
        with self._lock:
            payload = self._read_payload_unlocked()
            payload["data_provider"] = normalized
            self._write_payload_unlocked(payload)
            return normalized

    def get_broker_provider(self) -> str:
        with self._lock:
            payload = self._read_payload_unlocked()
            value = str(payload.get("broker_provider", self.default_broker_provider)).strip().lower()
            caps = PROVIDER_REGISTRY.get(value)
            if caps is None or not caps.trading:
                return self.default_broker_provider
            return value

    def set_broker_provider(self, provider_id: str) -> str:
        normalized = str(provider_id).strip().lower()
        caps = PROVIDER_REGISTRY.get(normalized)
        if caps is None:
            raise ValueError(f"Unknown provider '{provider_id}'.")
        if not caps.trading:
            raise ValueError(
                f"Provider '{normalized}' is view-only and cannot be used as a broker."
            )
        with self._lock:
            payload = self._read_payload_unlocked()
            payload["broker_provider"] = normalized
            self._write_payload_unlocked(payload)
            return normalized

    def _defaults(self) -> dict[str, Any]:
        return {
            "version": 1,
            "trading_mode": self.default_trading_mode,
            "data_provider": self.default_data_provider,
            "broker_provider": self.default_broker_provider,
        }

    def _read_payload_unlocked(self) -> dict[str, Any]:
        if not self.file_path.exists():
            return self._defaults()
        raw = self.file_path.read_text(encoding="utf-8").strip()
        if not raw:
            return self._defaults()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return self._defaults()
        payload.setdefault("version", 1)
        payload.setdefault("trading_mode", self.default_trading_mode)
        payload.setdefault("data_provider", self.default_data_provider)
        payload.setdefault("broker_provider", self.default_broker_provider)
        return payload

    def _write_payload_unlocked(self, payload: dict[str, Any]) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
