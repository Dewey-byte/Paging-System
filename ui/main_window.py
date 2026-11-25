"""Main application window: sidebar navigation, top bar, on-air banner and page stack."""
from __future__ import annotations

import importlib
import logging

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QGraphicsOpacityEffect, QHBoxLayout, QMainWindow, QMenu,
                               QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from core import paths
from core.errors import AppError
from core.permissions import P

from .branding import app_icon
from .components.base import EmptyState
from .components.dialogs import confirm, show_error
from .components.onair_banner import OnAirBanner
from .components.sidebar import Sidebar
from .components.toast import ToastManager
from .components.topbar import TopBar
from .theme import theme

log = logging.getLogger(__name__)

# key, label, icon, permission, section, module, class
PAGES = [
    ("dashboard", "Dashboard", "dashboard", P.DASHBOARD_VIEW, "Overview", "ui.dashboard", "DashboardPage"),
    ("live", "Live Paging", "mic", P.PAGING_LIVE, "Broadcast", "ui.live_paging", "LivePagingPage"),
    ("announcements", "Announcements", "megaphone", P.ANNOUNCEMENTS_VIEW, "Broadcast", "ui.announcements",
     "AnnouncementsPage"),
    ("alarms", "Alarm Management", "siren", P.ALARMS_VIEW, "Broadcast", "ui.alarm_management", "AlarmPage"),
    ("schedules", "Scheduled Broadcasts", "calendar_clock", P.SCHEDULES_VIEW, "Broadcast", "ui.schedules",
     "SchedulesPage"),
    ("zones", "Audio Zones", "speaker", P.ZONES_VIEW, "System", "ui.zones", "ZonesPage"),
    ("history", "Broadcast History", "history", P.HISTORY_VIEW, "System", "ui.history", "HistoryPage"),
    ("library", "Audio Library", "music", P.LIBRARY_VIEW, "System", "ui.audio_library", "AudioLibraryPage"),
    ("users", "User Management", "users", P.USERS_MANAGE, "Administration", "ui.user_management", "UsersPage"),
    ("settings", "System Settings", "settings", P.SETTINGS_MANAGE, "Administration", "ui.settings", "SettingsPage"),
]


class MainWindow(QMainWindow):
    logout_requested = Signal(str)
    exit_requested = Signal()

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(f"{ctx.settings.school_name} - {paths.APP_NAME}")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1040, 680)
        self.resize(1440, 900)
        self._allow_close = False
        self.pages: dict[str, QWidget] = {}
        self.current_key: str | None = None
        user = ctx.auth.current_user
        self.allowed = [p for p in PAGES if user and user.has(p[3])]

        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.sidebar = Sidebar([(k, label, ic, sec) for k, label, ic, _, sec, _, _ in self.allowed], user)
        self.sidebar.navigate.connect(self.navigate)
        self.sidebar.logout.connect(self.request_logout)
        h.addWidget(self.sidebar)

        right = QWidget()
        right.setObjectName("ContentArea")
        v = QVBoxLayout(right)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.topbar = TopBar(ctx)
        self.topbar.toggle_sidebar.connect(lambda: self._set_collapsed(not self.sidebar.collapsed, manual=True))
        self.topbar.toggle_theme.connect(self.toggle_theme)
        self.topbar.logout.connect(self.request_logout)
        self.topbar.change_password.connect(self.change_password)
        self.topbar.open_page.connect(self.navigate)
        v.addWidget(self.topbar)
        self.banner = OnAirBanner(ctx)
        self.banner.stop_requested.connect(self.stop_broadcast)
        v.addWidget(self.banner)
        self.stack = QStackedWidget()
        self.stack.setObjectName("Pages")
        v.addWidget(self.stack, 1)
        h.addWidget(right, 1)

        self.toasts = ToastManager(root, top_offset=80)
        ctx.bus.notify.connect(self.toasts.show)
        ctx.bus.emergency_changed.connect(self._on_emergency)
        ctx.bus.settings_changed.connect(self._on_setting)
        self._manual_collapse = False
        self._setup_tray()
        self._setup_shortcuts()
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._check_idle)
        self._idle_timer.start(30_000)
        self._last_input = 0.0
        QApplication.instance().installEventFilter(self)
        self._reset_idle()
        if self.allowed:
            self.navigate(self.allowed[0][0])
        else:
            empty = EmptyState("lock", "No modules available",
                               "Your account has no permissions assigned. Contact your administrator.", tone="danger")
            self.stack.addWidget(empty)

    # ---------------------------------------------------------- navigation
    def navigate(self, key: str) -> None:
        spec = next((p for p in self.allowed if p[0] == key), None)
        if spec is None:
            self.toasts.show("warning", "Not available", "Your account cannot open that module.")
            return
        if self.current_key and self.current_key in self.pages and self.current_key != key:
            try:
                self.pages[self.current_key].on_hide()
            except Exception:
                log.exception("on_hide failed")
        page = self.pages.get(key)
        if page is None:
            try:
                module = importlib.import_module(spec[5])
                page = getattr(module, spec[6])(self.ctx, self)
            except Exception as exc:
                log.exception("Could not open page %s", key)
                page = EmptyState("alert", f"{spec[1]} could not be opened", str(exc), tone="danger")
            self.pages[key] = page
            self.stack.addWidget(page)
        self.current_key = key
        self.sidebar.set_active(key)
        self.stack.setCurrentWidget(page)
        self._fade_in(page)
        if hasattr(page, "on_show"):
            try:
                page.on_show()
            except Exception as exc:  # keep the app usable even if a refresh fails
                log.exception("on_show failed for %s", key)
                self.toasts.show("error", "Could not refresh page", str(exc))

    def _fade_in(self, widget: QWidget) -> None:
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(170)
        anim.setStartValue(0.35)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: widget.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def page(self, key: str):
        if key not in self.pages:
            self.navigate(key)
        return self.pages.get(key)

    # --------------------------------------------------------------- shell
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._manual_collapse:
            self._set_collapsed(self.width() < 1240)

    def _set_collapsed(self, collapsed: bool, manual: bool = False) -> None:
        if manual:
            self._manual_collapse = True
        self.sidebar.set_collapsed(collapsed)

    def toggle_theme(self) -> None:
        new = "light" if theme.is_dark else "dark"
        self.ctx.settings.set("app.theme", new)

    def _on_setting(self, key: str) -> None:
        if key == "app.theme":
            theme.apply(self.ctx.settings.get("app.theme"))
        if key.startswith("school."):
            self.topbar.refresh_identity()
            self.setWindowTitle(f"{self.ctx.settings.school_name} - {paths.APP_NAME}")

    def _on_emergency(self, active: bool) -> None:
        if active:
            self.showNormal() if self.isMinimized() else None
            self.show()
            self.raise_()

    def stop_broadcast(self) -> None:
        active = self.ctx.controller.active
        if active is None:
            return
        if active.is_emergency:
            if not confirm(self, "Stop emergency alarm?",
                           f"<b>{active.title}</b> will be cancelled on all zones. Only stop the alarm when the "
                           "emergency is over or it was activated by mistake.", "Stop alarm", danger=True,
                           icon_name="siren"):
                return
        try:
            self.ctx.controller.stop(self.ctx.auth.current_user, "Stopped from the on-air banner")
            self.toasts.show("success", "Broadcast stopped", active.title)
        except AppError as exc:
            show_error(self, exc)

    def change_password(self) -> None:
        from .login_window import ChangePasswordDialog
        ChangePasswordDialog(self.ctx, self).exec()

    def request_logout(self, reason: str = "") -> None:
        active = self.ctx.controller.active
        if active is not None and active.kind == "live":
            if not confirm(self, "Sign out?", "Your live page is still on air. Signing out will stop it.",
                           "Stop && sign out", danger=True):
                return
            self.ctx.controller.stop(self.ctx.auth.current_user, "Operator signed out")
        self.logout_requested.emit(reason or "Signed out")

    # --------------------------------------------------------------- idle
    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.Wheel):
            self._reset_idle()
        return False

    def _reset_idle(self) -> None:
        import time
        self._last_input = time.monotonic()

    def _check_idle(self) -> None:
        import time
        minutes = self.ctx.settings.get_int("app.auto_logout_minutes")
        if minutes <= 0 or self.ctx.controller.active is not None:
            return
        if time.monotonic() - self._last_input > minutes * 60:
            self.logout_requested.emit(f"Signed out automatically after {minutes} minutes of inactivity")

    # ---------------------------------------------------------- shortcuts
    def _setup_shortcuts(self) -> None:
        for index, spec in enumerate(self.allowed[:9]):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda k=spec[0]: self.navigate(k))

    # ---------------------------------------------------------------- tray
    def _setup_tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(f"{paths.APP_NAME} - {self.ctx.settings.school_name}")
        menu = QMenu()
        menu.addAction("Open School Paging System", self.restore_from_tray)
        menu.addAction("Stop current broadcast", self.stop_broadcast)
        menu.addSeparator()
        menu.addAction("Exit", self.request_exit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore_from_tray()
                                    if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def request_exit(self) -> None:
        active = self.ctx.controller.active
        message = "Scheduled broadcasts will not play while the application is closed."
        if active is not None:
            message = f"<b>{active.title}</b> is on air and will be stopped. " + message
        if confirm(self, "Exit School Paging System?", message, "Exit", danger=active is not None, icon_name="power"):
            self._allow_close = True
            self.exit_requested.emit()

    def closeEvent(self, event):
        if self._allow_close:
            if self.tray:
                self.tray.hide()
            QApplication.instance().removeEventFilter(self)
            event.accept()
            return
        if self.ctx.settings.get_bool("app.close_to_tray") and self.tray is not None:
            event.ignore()
            self.hide()
            self.tray.showMessage(paths.APP_NAME, "Still running in the background so scheduled broadcasts and "
                                  "alarms keep working. Right-click the tray icon to exit.",
                                  QSystemTrayIcon.MessageIcon.Information, 4000)
            return
        event.ignore()
        self.request_exit()

    def force_close(self) -> None:
        self._allow_close = True
        self._idle_timer.stop()
        self.topbar.shutdown()
        self.banner._timer.stop()
        for page in self.pages.values():
            if hasattr(page, "on_hide"):
                try:
                    page.on_hide()
                except Exception:
                    pass
        self.close()
