"""Application exception hierarchy.

Every exception carries a ``message`` that is safe to show to end users, so the
UI can display it directly in a toast or dialog.
"""
from __future__ import annotations


class AppError(Exception):
    title = "Something went wrong"

    def __init__(self, message: str, *, title: str | None = None):
        super().__init__(message)
        self.message = message
        if title:
            self.title = title


class ValidationError(AppError):
    title = "Please check your input"


class PermissionDenied(AppError):
    title = "Not authorized"


class NotFoundError(AppError):
    title = "Not found"


class AuthError(AppError):
    title = "Sign-in failed"


class ConflictError(AppError):
    """Raised when an operation would conflict with existing data (e.g. schedules)."""

    title = "Conflict detected"

    def __init__(self, message: str, conflicts: list | None = None, *, title: str | None = None):
        super().__init__(message, title=title)
        self.conflicts = conflicts or []


class BroadcastRejected(AppError):
    """A broadcast request was refused by the priority/permission rules."""

    title = "Broadcast not started"

    def __init__(self, message: str, code: str = "rejected", *, title: str | None = None):
        super().__init__(message, title=title)
        self.code = code


class AudioDeviceError(AppError):
    title = "Audio device problem"
