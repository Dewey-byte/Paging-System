"""Top bar: school identity, live system/audio status, clock and profile menu."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from ..branding import school_logo
from ..icons import icon
from ..theme import theme
from .base import Avatar, StatusDot, button


class StatusChip(QFrame):
    def __init__(self, icon_name: str, parent=None):
        super().__init__(parent)
        self._tone = "success"
        self._icon_name = icon_name
        self.setFixedHeight(44)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 14, 4)
        layout.setSpacing(8)
        self.dot = StatusDot("success", 8)
        layout.addWidget(self.dot)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.caption = QLabel("")
        self.caption.setProperty("role", "overline")
        self.value = QLabel("")
        self.value.setStyleSheet("font-weight: 600; font-size: 9pt; background: transparent;")
        texts.addWidget(self.caption)
        texts.addWidget(self.value)
        layout.addLayout(texts)
        theme.changed.connect(self._restyle)
        self._restyle()

    def set(self, caption: str, value: str, tone: str, pulse: bool = False, tooltip: str = "") -> None:
        self.caption.setText(caption.upper())
        self.value.setText(value)
        self.setToolTip(tooltip)
        if tone != self._tone:
            self._tone = tone
            self._restyle()
        self.dot.set_tone(tone, pulse)

    def _restyle(self) -> None:
        bg, fg, _ = theme.tone(self._tone)
        self.setStyleSheet(f"StatusChip {{ background: {bg.name()}; border-radius: 10px; }}"
                           f"QLabel {{ background: transparent; }}")
        self.value.setStyleSheet(f"font-weight: 600; font-size: 9pt; color: {fg.name()}; background: transparent;")


class TopBar(QWidget):
    toggle_sidebar = Signal()
    toggle_theme = Signal()
    change_password = Signal()
    logout = Signal()
    open_page = Signal(str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("TopBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(68)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 20, 0)
        layout.setSpacing(12)

        self.menu_button = button("", "sidebar", "ghost", tooltip="Collapse / expand navigation")
        self.menu_button.clicked.connect(self.toggle_sidebar)
        layout.addWidget(self.menu_button)

        self.logo = QLabel()
        self.logo.setFixedSize(40, 40)
        layout.addWidget(self.logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addStretch(1)
        self.school = QLabel("")
        self.school.setObjectName("TopSchool")
        self.tagline = QLabel("")
        self.tagline.setProperty("role", "caption")
        names.addWidget(self.school)
        names.addWidget(self.tagline)
        names.addStretch(1)
        layout.addLayout(names)
        layout.addStretch(1)

        self.system_chip = StatusChip("radio")
        self.system_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.system_chip.mousePressEvent = lambda e: self.open_page.emit("live")
        layout.addWidget(self.system_chip)
        self.audio_chip = StatusChip("speaker")
        self.audio_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.audio_chip.mousePressEvent = lambda e: self.open_page.emit("zones")
        layout.addWidget(self.audio_chip)

        clock_box = QVBoxLayout()
        clock_box.setSpacing(0)
        clock_box.addStretch(1)
        self.clock = QLabel("")
        self.clock.setObjectName("TopClock")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.date = QLabel("")
        self.date.setObjectName("TopDate")
        self.date.setAlignment(Qt.AlignmentFlag.AlignRight)
        clock_box.addWidget(self.clock)
        clock_box.addWidget(self.date)
        clock_box.addStretch(1)
        layout.addSpacing(6)
        layout.addLayout(clock_box)

        self.theme_button = button("", "moon", "ghost", tooltip="Switch light / dark theme")
        self.theme_button.clicked.connect(self.toggle_theme)
        layout.addWidget(self.theme_button)

        self.profile = QPushButton()
        self.profile.setObjectName("ProfileButton")
        self.profile.setCursor(Qt.CursorShape.PointingHandCursor)
        profile_layout = QHBoxLayout(self.profile)
        profile_layout.setContentsMargins(6, 2, 6, 2)
        profile_layout.setSpacing(8)
        self.avatar = Avatar("", 32)
        profile_layout.addWidget(self.avatar)
        who = QVBoxLayout()
        who.setSpacing(0)
        self.user_name = QLabel("")
        self.user_name.setStyleSheet("font-weight: 600; background: transparent;")
        self.user_role = QLabel("")
        self.user_role.setProperty("role", "caption")
        who.addWidget(self.user_name)
        who.addWidget(self.user_role)
        profile_layout.addLayout(who)
        self.chevron = QLabel()
        profile_layout.addWidget(self.chevron)
        self.profile.setMinimumSize(QSize(170, 48))
        self.profile.clicked.connect(self._show_menu)
        layout.addWidget(self.profile)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        theme.changed.connect(self._restyle)
        ctx.bus.broadcast_state_changed.connect(self.refresh_status)
        ctx.bus.devices_refreshed.connect(self.refresh_status)
        ctx.bus.data_changed.connect(lambda name: self.refresh_status() if name in ("zones", "devices") else None)
        self._restyle()
        self.refresh_identity()
        self._tick()
        self.refresh_status()

    def shutdown(self) -> None:
        self._timer.stop()

    def _restyle(self) -> None:
        self.theme_button.setIcon(icon("sun" if theme.is_dark else "moon", theme.hex("text_secondary"), 18))
        self.chevron.setPixmap(icon("chevron_down", theme.hex("text_muted"), 14).pixmap(14, 14))

    def refresh_identity(self) -> None:
        settings = self.ctx.settings
        self.school.setText(settings.school_name)
        self.tagline.setText(settings.get("school.tagline") or "")
        self.logo.setPixmap(school_logo(settings.get("school.logo_path") or "", 40))
        user = self.ctx.auth.current_user
        if user:
            self.user_name.setText(user.display_name)
            self.user_role.setText(user.role_name)
            self.avatar.set_initials(user.initials)

    def _tick(self) -> None:
        now = datetime.now()
        self.clock.setText(now.strftime("%I:%M:%S %p").lstrip("0"))
        self.date.setText(now.strftime("%A, %B %d, %Y"))
        if now.second % 2 == 0:
            self.refresh_status()

    def refresh_status(self) -> None:
        controller = self.ctx.controller
        active = controller.active
        if active is not None and active.is_emergency:
            self.system_chip.set("System status", f"EMERGENCY: {active.title}", "danger", True,
                                 "An emergency alarm is active")
        elif active is not None:
            self.system_chip.set("System status", f"On air: {active.title}", "accent", True,
                                 f"{active.kind_label} to {active.zone_names}")
        elif not self.ctx.registry.available:
            self.system_chip.set("System status", "Audio unavailable", "danger", False, self.ctx.registry.error)
        elif self.ctx.settings.get_bool("schedules.paused"):
            self.system_chip.set("System status", "Online - schedules paused", "warning", False,
                                 "Automatic scheduled broadcasts are paused")
        else:
            self.system_chip.set("System status", "Online - ready", "success", False,
                                 "Paging system is idle and ready")
        summary = self.ctx.zones.summary(controller.active_zone_ids())
        real = summary["ready"]
        default = self.ctx.registry.default_output()
        detail = (f"{summary['ready']} zone(s) on detected hardware, {summary['simulated']} simulated, "
                  f"{summary['unverified']} network (unverified), {summary['offline']} offline.\n"
                  f"System default output: {default.name if default else 'none detected'}")
        if summary["offline"] and not (real or summary["simulated"]):
            self.audio_chip.set("Audio output", "No outputs available", "danger", False, detail)
        elif summary["offline"]:
            self.audio_chip.set("Audio output", f"{summary['playable']}/{summary['total']} zones - "
                                f"{summary['offline']} offline", "warning", False, detail)
        elif real == 0 and summary["simulated"]:
            self.audio_chip.set("Audio output", f"Simulation only ({summary['simulated']} zones)", "purple", False,
                                detail)
        elif summary["simulated"]:
            self.audio_chip.set("Audio output", f"{real} real · {summary['simulated']} simulated zones",
                                "success", False, detail)
        else:
            self.audio_chip.set("Audio output", f"{summary['playable']}/{summary['total']} zones ready",
                                "success", False, detail)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction(icon("key", theme.hex("text_secondary"), 16), "Change password", self.change_password)
        menu.addAction(icon("sun" if theme.is_dark else "moon", theme.hex("text_secondary"), 16),
                       "Light theme" if theme.is_dark else "Dark theme", self.toggle_theme)
        menu.addSeparator()
        menu.addAction(icon("logout", theme.hex("danger"), 16), "Sign out", self.logout)
        menu.exec(self.profile.mapToGlobal(self.profile.rect().bottomLeft()))
