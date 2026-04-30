from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


class JsonAgentStore:
    """Simple file-backed store for agent state and run history."""

    def __init__(self, file_path: str) -> None:
        self.file_path = Path(file_path)
        self._lock = Lock()

    def list_agents(self) -> list[dict[str, Any]]:
        payload = self._read_payload()
        agents = payload.get("agents", {})
        return [record for record in agents.values()]

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        payload = self._read_payload()
        agents = payload.get("agents", {})
        return agents.get(agent_id)

    def save_agent(self, agent_record: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(agent_record.get("agent_id", "")).strip()
        if not agent_id:
            raise ValueError("agent_record must include agent_id.")

        with self._lock:
            payload = self._read_payload_unlocked()
            payload.setdefault("agents", {})[agent_id] = agent_record
            self._write_payload_unlocked(payload)
        return agent_record

    def append_run(self, agent_id: str, run_record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            payload = self._read_payload_unlocked()
            agent = payload.setdefault("agents", {}).get(agent_id)
            if agent is None:
                raise KeyError(f"Agent {agent_id} was not found.")
            runs = agent.setdefault("runs", [])
            runs.append(run_record)
            # Keep the store lean while still preserving enough history for summary screens.
            if len(runs) > 500:
                agent["runs"] = runs[-500:]
            payload["agents"][agent_id] = agent
            self._write_payload_unlocked(payload)
            return agent

    def delete_agent(self, agent_id: str) -> dict[str, Any]:
        with self._lock:
            payload = self._read_payload_unlocked()
            agents = payload.setdefault("agents", {})
            removed = agents.pop(agent_id, None)
            if removed is None:
                raise KeyError(f"Agent {agent_id} was not found.")
            self._write_payload_unlocked(payload)
            return removed

    def _read_payload(self) -> dict[str, Any]:
        with self._lock:
            return self._read_payload_unlocked()

    def _read_payload_unlocked(self) -> dict[str, Any]:
        if not self.file_path.exists():
            return {"version": 1, "agents": {}}
        raw = self.file_path.read_text(encoding="utf-8").strip()
        if not raw:
            return {"version": 1, "agents": {}}
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return {"version": 1, "agents": {}}
        payload.setdefault("version", 1)
        payload.setdefault("agents", {})
        agents = payload.get("agents", {})
        if isinstance(agents, dict):
            migrated = False
            for record in agents.values():
                if not isinstance(record, dict):
                    continue
                if "dry_run" in record:
                    record.pop("dry_run", None)
                    migrated = True
            if migrated:
                self._write_payload_unlocked(payload)
        return payload

    def _write_payload_unlocked(self, payload: dict[str, Any]) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
