from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from queue import Empty, Queue
from threading import Lock
from typing import Any


@dataclass(slots=True)
class AgentMonitorEvent:
    event: str
    agent_id: str
    timestamp: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }


class AgentMonitorStream:
    """In-process pub/sub stream for agent monitoring events."""

    def __init__(self, max_queue_size: int = 200) -> None:
        self.max_queue_size = max_queue_size
        self._lock = Lock()
        self._all_subscribers: set[Queue[dict[str, Any]]] = set()
        self._agent_subscribers: dict[str, set[Queue[dict[str, Any]]]] = {}

    def subscribe(self, agent_id: str | None = None) -> Queue[dict[str, Any]]:
        queue: Queue[dict[str, Any]] = Queue(maxsize=self.max_queue_size)
        with self._lock:
            if agent_id:
                self._agent_subscribers.setdefault(agent_id, set()).add(queue)
            else:
                self._all_subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: Queue[dict[str, Any]], agent_id: str | None = None) -> None:
        with self._lock:
            if agent_id:
                subscribers = self._agent_subscribers.get(agent_id, set())
                subscribers.discard(queue)
                if not subscribers and agent_id in self._agent_subscribers:
                    del self._agent_subscribers[agent_id]
            else:
                self._all_subscribers.discard(queue)

    def publish(self, event: str, agent_id: str, payload: dict[str, Any] | None = None) -> None:
        event_payload = AgentMonitorEvent(
            event=event,
            agent_id=agent_id,
            timestamp=datetime.now(UTC).isoformat(),
            payload=payload or {},
        ).as_dict()
        with self._lock:
            targets = list(self._all_subscribers)
            targets.extend(self._agent_subscribers.get(agent_id, set()))
        for queue in targets:
            self._offer(queue, event_payload)

    def _offer(self, queue: Queue[dict[str, Any]], payload: dict[str, Any]) -> None:
        try:
            queue.put_nowait(payload)
        except Exception:
            try:
                queue.get_nowait()
            except Empty:
                pass
            try:
                queue.put_nowait(payload)
            except Exception:
                pass
