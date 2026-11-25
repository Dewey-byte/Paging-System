"""Audit trail of user and system actions (``activity_logs`` table)."""
from __future__ import annotations

import logging
from typing import Callable

from database.models import User
from database.repositories import ActivityRepository

log = logging.getLogger(__name__)


class ActivityLogger:
    def __init__(self, repo: ActivityRepository, current_user: Callable[[], User | None]):
        self._repo = repo
        self._current_user = current_user

    def log(self, action: str, module: str = "", details: str = "", level: str = "info",
            user: User | None = None, system: bool = False) -> None:
        actor = None if system else (user or self._current_user())
        try:
            self._repo.add(user_id=actor.id if actor else None,
                           username=actor.username if actor else "system",
                           action=action, module=module, details=details, level=level)
        except Exception:  # never let auditing break the calling operation
            log.exception("Could not write activity log entry: %s", action)
        getattr(log, "warning" if level != "info" else "info")("[%s] %s %s", module, action, details)

    def search(self, **filters):
        return self._repo.search(**filters)

    def modules(self) -> list[str]:
        return self._repo.modules()
