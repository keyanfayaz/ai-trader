from __future__ import annotations

import logging
from threading import Event, Thread

from app.services.agent_management_service import AgentManagementService


LOGGER = logging.getLogger(__name__)


class AgentScheduler:
    """Background interval runner for active trading agents."""

    def __init__(self, agent_service: AgentManagementService, interval_seconds: int) -> None:
        self.agent_service = agent_service
        self.interval_seconds = interval_seconds
        self._thread: Thread | None = None
        self._stop_event = Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._run_loop, daemon=True, name="agent-scheduler")
        self._thread.start()
        LOGGER.info("Agent scheduler started (interval=%ss).", self.interval_seconds)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        LOGGER.info("Agent scheduler stopped.")

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                self.agent_service.run_active_agents_cycle()
            except Exception as exc:
                LOGGER.exception("Agent scheduler cycle failed: %s", exc)
            try:
                self.agent_service.run_mission_cycle()
            except Exception as exc:
                LOGGER.exception("Mission scheduler cycle failed: %s", exc)
