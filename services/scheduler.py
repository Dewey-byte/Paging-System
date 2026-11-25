"""Background scheduler that executes due scheduled broadcasts while the app runs."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)


class SchedulerService(QObject):
    """Checks every second for due schedules; waits politely when the system is busy.

    A due broadcast that cannot start because another broadcast is on air is
    retried until the grace period expires, then recorded as *missed*.
    """

    executed = Signal(int)
    queue_changed = Signal()

    def __init__(self, schedules, controller, settings, bus):
        super().__init__()
        self.schedules = schedules
        self.controller = controller
        self.settings = settings
        self.bus = bus
        self.pending: list[tuple[int, datetime]] = []
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.tick)
        self.running = False

    def start(self) -> None:
        self.running = True
        self._timer.start()

    def stop(self) -> None:
        self.running = False
        self._timer.stop()

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        if self.settings.get_bool("schedules.paused"):
            return
        grace = max(1, self.settings.get_int("schedules.grace_minutes"))
        try:
            for schedule, when in self.schedules.due(now, grace):
                key = (schedule.id, when)
                if key not in self.pending:
                    self.pending.append(key)
                    self.queue_changed.emit()
        except Exception:
            log.exception("Scheduler could not evaluate due schedules")
            return
        for key in list(self.pending):
            self._process(key, now, grace)

    def _process(self, key: tuple[int, datetime], now: datetime, grace: int) -> None:
        schedule_id, when = key
        try:
            schedule = self.schedules.get(schedule_id)
        except Exception:
            self._drop(key)
            return
        if not schedule.is_enabled:
            self._drop(key)
            return
        if now - when > timedelta(minutes=grace):
            active = self.controller.active
            reason = (f"System busy with '{active.title}' for longer than the {grace}-minute grace period"
                      if active else f"Could not start within the {grace}-minute grace period")
            self.schedules.mark_missed(schedule, when, reason)
            self.bus.toast("warning", "Scheduled broadcast missed", f"{schedule.name}: {reason}")
            self._drop(key)
            return
        decision = self.controller.evaluate(self.schedules.kind_for(schedule), self.schedules.priority_for(schedule),
                                            None, origin="scheduler")
        if not decision.allowed:
            return  # busy: keep waiting
        from core.errors import AppError, BroadcastRejected
        try:
            self.schedules.execute(schedule, when)
            self.bus.toast("info", "Scheduled broadcast started", f"{schedule.name} - {schedule.time_of_day}")
            self.executed.emit(schedule.id)
            self._drop(key)
        except BroadcastRejected as exc:
            if exc.code in ("busy", "emergency_active"):
                return
            self.schedules.mark_failed(schedule, when, exc.message)
            self.bus.toast("error", f"'{schedule.name}' could not play", exc.message)
            self._drop(key)
        except AppError as exc:
            self.schedules.mark_failed(schedule, when, exc.message)
            self.bus.toast("error", f"'{schedule.name}' could not play", exc.message)
            self._drop(key)
        except Exception as exc:
            log.exception("Scheduled broadcast %s failed", schedule.name)
            self.schedules.mark_failed(schedule, when, str(exc))
            self._drop(key)

    def _drop(self, key) -> None:
        if key in self.pending:
            self.pending.remove(key)
            self.queue_changed.emit()

    @property
    def waiting_count(self) -> int:
        return len(self.pending)
