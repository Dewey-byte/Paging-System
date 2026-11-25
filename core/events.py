"""Application-wide event bus.

Managers emit events here; UI pages subscribe to refresh themselves.  Signals
emitted from worker threads are delivered on the GUI thread automatically
because the bus lives in the main thread.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    # Broadcast lifecycle
    broadcast_started = Signal(object)          # ActiveBroadcast
    broadcast_ended = Signal(object)            # ActiveBroadcast (with final status)
    broadcast_state_changed = Signal()
    emergency_changed = Signal(bool)

    # Data changes: "announcements", "schedules", "zones", "devices", "library",
    # "users", "roles", "history", "alarms", "settings"
    data_changed = Signal(str)
    settings_changed = Signal(str)

    # User-facing notifications: kind ("success" | "info" | "warning" | "error"), title, message
    notify = Signal(str, str, str)

    devices_refreshed = Signal()
    session_changed = Signal(object)            # current User or None

    def toast(self, kind: str, title: str, message: str = "") -> None:
        self.notify.emit(kind, title, message)
