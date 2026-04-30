from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


class RuntimeConfigStore:
    """Simple file-backed store for runtime controls."""

    def __init__(self, file_path: str, default_trading_mode: str = "paper") -> None:
        self.file_path = Path(file_path)
        self.default_trading_mode = default_trading_mode
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

    def _read_payload_unlocked(self) -> dict[str, Any]:
        if not self.file_path.exists():
            return {"version": 1, "trading_mode": self.default_trading_mode}
        raw = self.file_path.read_text(encoding="utf-8").strip()
        if not raw:
            return {"version": 1, "trading_mode": self.default_trading_mode}
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return {"version": 1, "trading_mode": self.default_trading_mode}
        payload.setdefault("version", 1)
        payload.setdefault("trading_mode", self.default_trading_mode)
        return payload

    def _write_payload_unlocked(self, payload: dict[str, Any]) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
