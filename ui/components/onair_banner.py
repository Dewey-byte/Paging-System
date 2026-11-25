"""Global on-air / emergency banner shown on every page while audio is broadcasting."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from core.history_manager import format_duration

from ..icons import icon, pixmap
from ..theme import theme
from .base import StatusDot


class OnAirBanner(QFrame):
    stop_requested = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("OnAirBanner")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 10, 16, 10)
        layout.setSpacing(14)
        self.dot = StatusDot("danger", 12, pulse=True)
        layout.addWidget(self.dot)
        self.glyph = QLabel()
        self.glyph.setFixedSize(26, 26)
        layout.addWidget(self.glyph)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        self.title = QLabel("")
        self.detail = QLabel("")
        texts.addWidget(self.title)
        texts.addWidget(self.detail)
        layout.addLayout(texts, 1)
        self.elapsed = QLabel("")
        layout.addWidget(self.elapsed)
        self.stop_button = QPushButton("  STOP")
        self.stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_button.setMinimumHeight(38)
        self.stop_button.clicked.connect(self.stop_requested)
        layout.addWidget(self.stop_button)
        self._flash = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.hide()
        ctx.bus.broadcast_state_changed.connect(self.refresh)
        theme.changed.connect(self.refresh)

    def refresh(self) -> None:
        active = self.ctx.controller.active
        if active is None:
            self._timer.stop()
            self.hide()
            return
        emergency = active.is_emergency
        self._emergency = emergency
        if emergency:
            self.title.setText(f"EMERGENCY ALARM ACTIVE - {active.title.upper()}")
            self.glyph.setPixmap(pixmap("siren", "#FFFFFF", 26))
        else:
            if active.ending:
                label = "ENDING - OUTRO CHIME PLAYING"
            else:
                label = "LIVE MICROPHONE ON AIR" if active.kind == "live" else f"ON AIR - {active.title}"
            self.title.setText(label)
            self.glyph.setPixmap(pixmap("mic" if active.kind == "live" else "radio", "#FFFFFF", 24))
        sim = "  |  simulated outputs only" if active.simulated_only else ""
        self.detail.setText(f"{active.kind_label} to {active.zone_names}  |  started by {active.initiated_by} at "
                            f"{active.started_at.strftime('%I:%M %p').lstrip('0')}{sim}")
        can_stop = not emergency or self.ctx.auth.has("alarms.trigger")
        self.stop_button.setEnabled(can_stop)
        self.stop_button.setText("  STOP ALARM" if emergency else "  STOP BROADCAST")
        self.stop_button.setToolTip("" if can_stop else "Only users authorized to trigger alarms can stop them")
        self.dot.set_tone("danger" if emergency else "success", True)
        self._apply_style()
        self.show()
        self._tick()
        self._timer.start(500)

    def _apply_style(self) -> None:
        emergency = getattr(self, "_emergency", False)
        if emergency:
            bg = "#B91C1C" if self._flash else "#DC2626"
        else:
            bg = "#172554" if not theme.is_dark else "#16264A"
        stop_bg = "#FFFFFF"
        stop_fg = "#B91C1C" if emergency else "#172554"
        self.setStyleSheet(
            f"QFrame#OnAirBanner {{ background: {bg}; border: none; }}"
            "QLabel { color: #FFFFFF; background: transparent; }"
            f"QPushButton {{ background: {stop_bg}; color: {stop_fg}; border: none; border-radius: 9px;"
            " padding: 8px 18px; font-weight: 800; }"
            "QPushButton:hover { background: #F1F5F9; }"
            "QPushButton:disabled { background: rgba(255,255,255,0.35); color: rgba(255,255,255,0.8); }")
        self.title.setStyleSheet("font-weight: 800; font-size: 10.5pt; color: white; background: transparent;")
        self.detail.setStyleSheet("font-size: 9pt; color: rgba(255,255,255,0.82); background: transparent;")
        self.elapsed.setStyleSheet("font-size: 15pt; font-weight: 700; color: white; background: transparent;"
                                   " font-family: 'Segoe UI';")
        self.stop_button.setIcon(icon("stop", stop_fg, 14))

    def _tick(self) -> None:
        active = self.ctx.controller.active
        if active is None:
            self.refresh()
            return
        self.elapsed.setText(format_duration(active.elapsed))
        if active.is_emergency:
            self._flash = not self._flash
            self._apply_style()
