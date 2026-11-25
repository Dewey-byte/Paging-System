"""Base class for module pages."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from core.errors import AppError, BroadcastRejected

from .base import EmptyState, PageHeader
from .dialogs import confirm, show_error


class BasePage(QWidget):
    title = ""
    subtitle = ""
    scrollable = True

    def __init__(self, ctx, window):
        super().__init__()
        self.ctx = ctx
        self.window_ = window
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body_widget = QWidget()
        body_widget.setObjectName("PageBody")
        self.body = QVBoxLayout(body_widget)
        self.body.setContentsMargins(28, 22, 28, 28)
        self.body.setSpacing(18)
        if self.scrollable:
            self.scroll = QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.scroll.setWidget(body_widget)
            outer.addWidget(self.scroll)
        else:
            outer.addWidget(body_widget)
        self.header = PageHeader(self.title, self.subtitle)
        self.body.addWidget(self.header)

    # ---------------------------------------------------------------- hooks
    def on_show(self) -> None:
        """Called every time the page becomes visible."""

    def on_hide(self) -> None:
        """Called when navigating away."""

    # -------------------------------------------------------------- helpers
    @property
    def user(self):
        return self.ctx.auth.current_user

    def can(self, permission: str) -> bool:
        return self.ctx.auth.has(permission)

    def toast(self, kind: str, title: str, message: str = "") -> None:
        self.window_.toasts.show(kind, title, message)

    def handle_error(self, exc: Exception, dialog: bool = False) -> None:
        if isinstance(exc, AppError):
            if dialog:
                show_error(self, exc)
            else:
                self.toast("error", exc.title, exc.message)
        else:
            show_error(self, exc)

    def guarded(self, fn, *args, success: str | None = None, **kwargs):
        """Run an action, show errors as toasts and an optional success toast."""
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.handle_error(exc)
            return None
        if success:
            self.toast("success", success)
        return result if result is not None else True

    def start_broadcast(self, starter, confirm_title: str = "Interrupt current broadcast?"):
        """Call ``starter(interrupt: bool)``; asks for confirmation when another broadcast must be interrupted."""
        try:
            return starter(False)
        except BroadcastRejected as exc:
            if exc.code == "confirm":
                if confirm(self, confirm_title, exc.message, "Interrupt and start", danger=True, icon_name="radio"):
                    try:
                        return starter(True)
                    except Exception as inner:  # noqa: BLE001
                        self.handle_error(inner, dialog=True)
                return None
            self.handle_error(exc, dialog=exc.code in ("no_devices", "audio_error", "content"))
            return None
        except Exception as exc:  # noqa: BLE001
            self.handle_error(exc, dialog=True)
            return None

    def restricted(self, message: str = "Your account does not have access to this module.") -> EmptyState:
        return EmptyState("lock", "Access restricted", message, tone="danger")
