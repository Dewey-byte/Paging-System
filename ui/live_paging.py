"""Live paging console: real-time microphone announcements to selected zones."""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QApplication, QBoxLayout, QComboBox, QHBoxLayout, QLineEdit,
                               QPlainTextEdit, QSizePolicy, QVBoxLayout, QWidget)

from core.history_manager import format_duration
from core.permissions import P
from services.audio_input import MicrophoneSource, gain_db_from_slider

from .components.base import (Badge, Card, ElidedLabel, IconBubble, InfoBanner, SectionHeader, StatusDot, button,
                              divider, label)
from .components.controls import LevelMeter, SegmentedControl, SliderField, ToggleSwitch
from .components.page import BasePage
from .components.zone_selector import ZoneSelector
from .icons import pixmap
from .theme import theme


class TalkButton(QAbstractButton):
    """Large circular push-to-talk button with a live level ring."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(196, 196)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.state = "idle"     # idle | muted | talking | open | closing
        self.level = 0.0
        theme.changed.connect(self.update)

    def set_state(self, state: str, level: float = 0.0) -> None:
        self.state = state
        self.level = level
        self.setEnabled(state in ("muted", "talking"))
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(196, 196)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        outer = self.width() / 2 - 6
        if self.state == "talking":
            fill, ring = theme.c("danger"), theme.c("danger")
        elif self.state == "open":
            fill, ring = theme.c("success"), theme.c("success")
        elif self.state == "muted":
            fill, ring = theme.c("accent"), theme.c("accent")
        elif self.state == "closing":
            fill, ring = theme.c("warning"), theme.c("warning")
        else:
            fill, ring = theme.c("border_strong"), theme.c("border")
        painter.setPen(QPen(theme.c("track"), 8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(c, outer, outer)
        if self.state in ("talking", "open") and self.level > 0.01:
            pen = QPen(ring, 8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            span = int(360 * 16 * min(1.0, self.level))
            painter.drawArc(QRectF(c.x() - outer, c.y() - outer, outer * 2, outer * 2), 90 * 16, -span)
        halo = QColor(fill)
        halo.setAlpha(40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(c, outer - 12, outer - 12)
        painter.setBrush(fill if not self.isDown() else fill.darker(115))
        painter.drawEllipse(c, outer - 24, outer - 24)
        glyph = "mic" if self.state not in ("idle", "closing") else "mic_off"
        painter.drawPixmap(QPointF(c.x() - 22, c.y() - 34), pixmap(glyph, "#FFFFFF", 44, 2.2))
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(10)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        text = {"idle": "NOT ON AIR", "muted": "HOLD TO TALK", "talking": "TALKING", "open": "MIC LIVE",
                "closing": "MIC CLOSED"}[self.state]
        painter.drawText(QRectF(c.x() - 70, c.y() + 16, 140, 24), Qt.AlignmentFlag.AlignCenter, text)


class LivePagingPage(BasePage):
    title = "Live Paging"
    subtitle = "Speak live to selected zones through the connected microphone."

    def __init__(self, ctx, window):
        super().__init__(ctx, window)
        self.monitor: MicrophoneSource | None = None
        self._space_down = False
        self._warned_feedback = 0

        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.columns.setSpacing(16)
        self.body.addLayout(self.columns)
        left = QVBoxLayout()
        left.setSpacing(16)
        right = QVBoxLayout()
        right.setSpacing(16)
        self.columns.addLayout(left, 3)
        self.columns.addLayout(right, 2)

        # ------------------------------------------------------ console card
        console = Card(padding=24, spacing=14)
        top = QHBoxLayout()
        self.state_dot = StatusDot("neutral", 12)
        top.addWidget(self.state_dot)
        self.state_label = label("READY", "h3")
        top.addWidget(self.state_label)
        top.addStretch(1)
        self.mode = SegmentedControl([("ptt", "Push-to-talk", "radio"), ("open", "Open microphone", "mic")])
        self.mode.set_value("ptt" if ctx.settings.get_bool("paging.push_to_talk") else "open")
        self.mode.changed.connect(self._mode_changed)
        top.addWidget(self.mode)
        console.body.addLayout(top)

        center = QHBoxLayout()
        center.setSpacing(28)
        center.addStretch(1)
        self.talk = TalkButton()
        self.talk.pressed.connect(lambda: self._set_talking(True))
        self.talk.released.connect(lambda: self._set_talking(False))
        center.addWidget(self.talk)
        info = QVBoxLayout()
        info.setSpacing(4)
        info.addStretch(1)
        info.addWidget(label("BROADCAST TIME", "overline"))
        self.timer_label = label("00:00")
        self.timer_label.setStyleSheet("font-size: 34pt; font-weight: 700;")
        info.addWidget(self.timer_label)
        self.target_label = label("No zones selected", "muted", wrap=True)
        self.target_label.setMaximumWidth(260)
        info.addWidget(self.target_label)
        self.limit_label = label("", "caption", wrap=True)
        info.addWidget(self.limit_label)
        info.addStretch(1)
        center.addLayout(info)
        center.addStretch(1)
        console.body.addLayout(center)
        self.hint = label("", "caption", wrap=True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        console.body.addWidget(self.hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        self.start_button = button("START BROADCAST", "radio", "success", "xl")
        self.start_button.clicked.connect(self.start_live)
        self.stop_button = button("STOP", "stop", "danger", "xl")
        self.stop_button.clicked.connect(self.stop_broadcast)
        buttons.addWidget(self.start_button, 3)
        buttons.addWidget(self.stop_button, 2)
        console.body.addLayout(buttons)
        left.addWidget(console)

        zones_card = Card(padding=20, spacing=12)
        zones_card.body.addWidget(SectionHeader("Target zones", "Choose where your voice will be heard", "speaker"))
        self.selector = ZoneSelector(ctx, min_tile_width=180)
        self.selector.set_all(True)
        self.selector.changed.connect(self._selection_changed)
        zones_card.body.addWidget(self.selector)
        left.addWidget(zones_card)
        left.addStretch(1)

        # ------------------------------------------------------- mic card
        mic_card = Card(padding=20, spacing=12)
        head = SectionHeader("Microphone", "Input device and level", "mic")
        self.refresh_button = head.add_action(button("", "refresh", "ghost", tooltip="Re-scan audio devices"))
        self.refresh_button.clicked.connect(self._refresh_devices)
        mic_card.body.addWidget(head)
        self.mic_combo = QComboBox()
        self.mic_combo.currentIndexChanged.connect(self._mic_changed)
        mic_card.body.addWidget(self.mic_combo)
        meter_row = QHBoxLayout()
        meter_row.setSpacing(10)
        self.mic_meter = LevelMeter(32)
        self.mic_meter.setFixedHeight(16)
        meter_row.addWidget(self.mic_meter, 1)
        self.mic_check = button("Mic check", "headphones", None, "sm",
                                tooltip="Show the microphone level without broadcasting")
        self.mic_check.setCheckable(True)
        self.mic_check.toggled.connect(self._toggle_monitor)
        meter_row.addWidget(self.mic_check)
        mic_card.body.addLayout(meter_row)
        mic_card.body.addWidget(label("Microphone gain", "field"))
        self.gain = SliderField(0, 100, ctx.settings.get_int("audio.mic_gain"), icon_name="sliders",
                                formatter=lambda v: f"{gain_db_from_slider(v):+.0f} dB")
        self.gain.valueChanged.connect(self._gain_changed)
        mic_card.body.addWidget(self.gain)
        self.feedback_banner = InfoBanner("", "warning")
        self.feedback_banner.hide()
        mic_card.body.addWidget(self.feedback_banner)
        mic_card.body.addWidget(divider())
        self.chime_toggles: dict[str, ToggleSwitch] = {}
        for key, text, tip in (("audio.chime_before", "Intro chime before paging", "Alert listeners with a chime "
                                                                                   "before your microphone opens"),
                               ("audio.chime_after", "Outro chime after paging", "Play a closing chime when you "
                                                                                 "press STOP"),
                               ("audio.feedback_guard", "Feedback guard", "Lower the gain automatically if howling "
                                                                          "(acoustic feedback) is detected"),
                               ("audio.high_pass", "Low-cut filter", "Reduce rumble, handling and wind noise")):
            row = QHBoxLayout()
            row.addWidget(label(text), 1)
            toggle = ToggleSwitch(ctx.settings.get_bool(key))
            toggle.setToolTip(tip)
            toggle.toggled.connect(lambda checked, k=key: self.ctx.settings.set(k, checked))
            row.addWidget(toggle)
            mic_card.body.addLayout(row)
            self.chime_toggles[key] = toggle
        right.addWidget(mic_card)

        # ----------------------------------------------------- output card
        out_card = Card(padding=20, spacing=12)
        out_card.body.addWidget(SectionHeader("Output", "Master volume and audio path", "volume"))
        out_card.body.addWidget(label("Master output volume", "field"))
        self.master = SliderField(0, 100, ctx.settings.get_int("audio.master_volume"), icon_name="volume")
        self.master.valueChanged.connect(self._master_changed)
        out_card.body.addWidget(self.master)
        self.out_meter = LevelMeter(32)
        self.out_meter.setFixedHeight(10)
        out_card.body.addWidget(self.out_meter)
        out_card.body.addWidget(label("AUDIO WILL BE SENT TO", "overline"))
        self.routes_box = QVBoxLayout()
        self.routes_box.setSpacing(6)
        out_card.body.addLayout(self.routes_box)
        self.route_warning = InfoBanner("", "warning")
        self.route_warning.hide()
        out_card.body.addWidget(self.route_warning)
        right.addWidget(out_card)

        tips = InfoBanner("<b>Tips:</b> speak 10-20 cm from the microphone in a steady voice. If this computer's "
                          "speakers are part of the selection, use headphones or push-to-talk to avoid feedback.",
                          "info", "info")
        right.addWidget(tips)
        right.addStretch(1)

        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(50)
        self._ui_timer.timeout.connect(self._tick)
        ctx.bus.broadcast_state_changed.connect(self._state_changed)
        ctx.bus.settings_changed.connect(self._setting_changed)
        ctx.bus.devices_refreshed.connect(self._populate_mics)
        ctx.bus.data_changed.connect(lambda name: self.selector.refresh() if name in ("zones", "devices") else None)
        self._populate_mics()
        self._selection_changed()
        self._state_changed()

    # ------------------------------------------------------------- lifecycle
    def on_show(self) -> None:
        self.selector.refresh()
        self._populate_mics()
        self._selection_changed()
        self._state_changed()
        self._ui_timer.start()
        QApplication.instance().installEventFilter(self)

    def on_hide(self) -> None:
        self._ui_timer.stop()
        self.mic_check.setChecked(False)
        QApplication.instance().removeEventFilter(self)
        if self._space_down:
            self._space_down = False
            self._set_talking(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        wide = self.width() >= 1100
        self.columns.setDirection(QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)

    # ---------------------------------------------------------------- devices
    def _populate_mics(self) -> None:
        current = self.mic_combo.currentData() if self.mic_combo.count() else None
        preferred = current if current is not None else (self.ctx.settings.get("audio.default_input") or "")
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        default = self.ctx.registry.default_input()
        self.mic_combo.addItem(f"System default ({default.name})" if default else "System default (none detected)", "")
        for device in self.ctx.registry.inputs():
            self.mic_combo.addItem(device.name, device.name)
        index = self.mic_combo.findData(preferred)
        self.mic_combo.setCurrentIndex(max(0, index))
        self.mic_combo.blockSignals(False)
        if not self.ctx.registry.inputs():
            self.hint.setText("No microphone detected. Connect a microphone and press refresh.")

    def _refresh_devices(self) -> None:
        if self.monitor is not None:
            self.mic_check.setChecked(False)
        changed = self.ctx.refresh_devices()
        self._populate_mics()
        self.selector.refresh()
        self._selection_changed()
        self.toast("success" if self.ctx.registry.available else "error", "Audio devices refreshed",
                   f"{len(self.ctx.registry.inputs())} microphone(s), {len(self.ctx.registry.outputs())} output(s) "
                   f"detected{' - changes found' if changed else ''}.")

    def _selected_device(self):
        return self.ctx.registry.find_input(self.mic_combo.currentData() or "")

    def _mic_changed(self) -> None:
        if self.monitor is not None:
            self._toggle_monitor(False)
            self._toggle_monitor(True)
        self._selection_changed()

    # ------------------------------------------------------------- monitor
    def _toggle_monitor(self, on: bool) -> None:
        if on and self.ctx.controller.active is None:
            device = self._selected_device()
            if device is None:
                self.toast("error", "No microphone", "No microphone is connected.")
                self.mic_check.setChecked(False)
                return
            try:
                self.monitor = MicrophoneSource(device, gain_db_from_slider(self.gain.value()),
                                                high_pass=self.ctx.settings.get_bool("audio.high_pass"),
                                                feedback_guard=False, gate_open=False, name="Mic check")
                self.monitor.start()
            except Exception as exc:  # noqa: BLE001
                self.monitor = None
                self.mic_check.blockSignals(True)
                self.mic_check.setChecked(False)
                self.mic_check.blockSignals(False)
                self.toast("error", "Microphone could not be opened", str(exc))
        else:
            if self.monitor is not None:
                self.monitor.stop()
                self.monitor = None
            if self.mic_check.isChecked() and not on:
                self.mic_check.blockSignals(True)
                self.mic_check.setChecked(False)
                self.mic_check.blockSignals(False)

    # ------------------------------------------------------------ settings
    def _setting_changed(self, key: str) -> None:
        """Keep the toggles in sync when the same option is changed on the Settings page."""
        toggle = self.chime_toggles.get(key)
        if toggle is not None and toggle.isChecked() != self.ctx.settings.get_bool(key):
            toggle.blockSignals(True)
            toggle.setChecked(self.ctx.settings.get_bool(key))
            toggle.blockSignals(False)
            toggle.update()
        if key in ("audio.chime_before", "audio.chime_after"):
            self._state_changed()

    def _gain_changed(self, value: int) -> None:
        self.ctx.settings.set("audio.mic_gain", value, quiet=True)
        self.ctx.controller.set_mic_gain(value)
        if self.monitor is not None:
            self.monitor.set_gain_db(gain_db_from_slider(value))

    def _master_changed(self, value: int) -> None:
        self.ctx.settings.set("audio.master_volume", value)

    def _mode_changed(self, mode: str) -> None:
        ptt = mode == "ptt"
        self.ctx.settings.set("paging.push_to_talk", ptt, quiet=True)
        active = self.ctx.controller.active
        if active is not None and active.kind == "live":
            self.ctx.controller.set_talking(not ptt)
        self._state_changed()

    # ----------------------------------------------------------- selection
    def _selection_changed(self) -> None:
        zone_ids = self.selector.selected_ids()
        self.target_label.setText(self.ctx.zones.describe_zones(zone_ids) if zone_ids else "No zones selected")
        while self.routes_box.count():
            item = self.routes_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        targets, warnings = self.ctx.zones.resolve_targets(zone_ids) if zone_ids else ([], [])
        local_speakers = False
        for target in targets:
            row = QWidget()
            row.setObjectName("Transparent")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            sim = target.transport == "simulated"
            tone = "purple" if sim else ("warning" if target.transport == "rtp" else "success")
            layout.addWidget(IconBubble("cpu" if sim else ("globe" if target.transport == "rtp" else "speaker"), tone,
                                        30, 15))
            texts = QVBoxLayout()
            texts.setSpacing(0)
            texts.addWidget(ElidedLabel(target.device_name, "h3"))
            channel = "all channels" if target.channel_map is None else \
                "channel " + ", ".join(str(c + 1) for c in target.channel_map)
            texts.addWidget(ElidedLabel(f"{len(target.zone_ids)} zone(s) · {channel}", "caption"))
            layout.addLayout(texts, 1)
            layout.addWidget(Badge("Simulated" if sim else ("Unverified" if target.transport == "rtp" else "Detected"),
                                   tone, small=True))
            self.routes_box.addWidget(row)
            if target.transport == "local" and target.system_device and "speaker" in target.system_device.name.lower():
                local_speakers = True
        if not targets:
            self.routes_box.addWidget(label("No playable outputs for the current selection.", "muted", wrap=True))
        messages = list(warnings)
        mic = self._selected_device()
        if local_speakers and mic and "headset" not in mic.name.lower() and "headphone" not in mic.name.lower():
            messages.append("This computer's speakers are included - keep the microphone away from them to "
                            "prevent feedback.")
        self.route_warning.set_text("<br>".join(messages[:4]), "warning")
        self.route_warning.setVisible(bool(messages))

    # ------------------------------------------------------------ broadcast
    def start_live(self) -> None:
        if not self.can(P.PAGING_LIVE):
            self.toast("error", "Not authorized", "Your account cannot make live announcements.")
            return
        zones = self.selector.selected_ids()
        if not zones:
            self.toast("warning", "Select zones", "Choose at least one zone or Entire Campus.")
            return
        device = self._selected_device()
        if device is None:
            self.toast("error", "No microphone", "Connect a microphone, then press refresh.")
            return
        self._toggle_monitor(False)
        ptt = self.mode.value() == "ptt"
        active = self.start_broadcast(lambda interrupt: self.ctx.controller.start_live(
            zones, device, self.user, ptt, interrupt), "Interrupt the current broadcast?")
        if active is not None:
            self._warned_feedback = 0
            self.toast("success", "You are on air", "Hold the talk button (or Space) to speak." if ptt else
                       "Your microphone is live.")

    def stop_broadcast(self) -> None:
        active = self.ctx.controller.active
        if active is None:
            return
        if active.is_emergency:
            self.window_.stop_broadcast()
            return
        if self._space_down:
            self._space_down = False
        self.guarded(self.ctx.controller.stop, self.user, "Live page ended by operator" if active.kind == "live"
                     else "Stopped from the live paging console")

    def _set_talking(self, talking: bool) -> None:
        active = self.ctx.controller.active
        if active is not None and active.kind == "live" and not active.ending and self.mode.value() == "ptt":
            self.ctx.controller.set_talking(talking)
            self._state_changed()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and self.isVisible():
            if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
                focus = QApplication.focusWidget()
                if isinstance(focus, (QLineEdit, QPlainTextEdit)):
                    return False
                active = self.ctx.controller.active
                if active is not None and active.kind == "live" and self.mode.value() == "ptt":
                    down = event.type() == QEvent.Type.KeyPress
                    if down != self._space_down:
                        self._space_down = down
                        self._set_talking(down)
                    return True
        return False

    # ------------------------------------------------------------- refresh
    def _state_changed(self, *args) -> None:
        active = self.ctx.controller.active
        live = active is not None and active.kind == "live"
        ptt = self.mode.value() == "ptt"
        mic = self.ctx.controller.mic
        closing = live and active.ending
        if closing:
            state = "closing"
            self.state_label.setText("ENDING - OUTRO CHIME")
            self.state_dot.set_tone("warning", True)
            self.hint.setText("Microphone closed. The outro chime is playing - press STOP again to end immediately.")
        elif live:
            talking = mic is not None and mic.gate_open
            if ptt:
                state = "talking" if talking else "muted"
            else:
                state = "open"
            self.state_label.setText("ON AIR - TALKING" if talking else ("ON AIR - MIC MUTED" if ptt else "ON AIR"))
            self.state_dot.set_tone("danger" if talking else "warning", True)
            ending = " The outro chime plays when you press STOP." if self.ctx.settings.get_bool("audio.chime_after") \
                else ""
            self.hint.setText(("Hold the button or press and hold the Space bar to talk. Release to mute." if ptt
                               else "Your microphone is live to the selected zones. Press STOP when finished.") +
                              ending)
        else:
            state = "idle"
            if active is not None:
                self.state_label.setText(f"BUSY - {active.kind_label.upper()}")
                self.state_dot.set_tone("danger" if active.is_emergency else "accent", True)
                self.hint.setText(f"'{active.title}' is on air. Starting live paging will ask to interrupt it."
                                  if not active.is_emergency else
                                  "An emergency alarm is active. Live paging cannot interrupt emergency alarms.")
            else:
                self.state_label.setText("READY")
                self.state_dot.set_tone("success", False)
                intro = self.ctx.settings.get_bool("audio.chime_before")
                outro = self.ctx.settings.get_bool("audio.chime_after")
                chimes = {(True, True): " A chime plays before your microphone opens and after you press STOP.",
                          (True, False): " A chime plays before your microphone opens.",
                          (False, True): " A closing chime plays after you press STOP.",
                          (False, False): ""}[(intro, outro)]
                self.hint.setText("Select zones, then press START BROADCAST." + chimes)
        self.talk.set_state(state, self.talk.level)
        emergency = active is not None and active.is_emergency
        self.start_button.setEnabled(not live and not emergency and self.can(P.PAGING_LIVE))
        self.stop_button.setEnabled(active is not None and (not emergency or self.can(P.ALARMS_TRIGGER)))
        self.selector.setEnabled(not live)
        self.mic_combo.setEnabled(not live)
        self.mic_check.setEnabled(not live)
        self.selector.refresh()
        if not live:
            self.timer_label.setText("00:00")
            self.limit_label.setText("")

    def _tick(self) -> None:
        controller = self.ctx.controller
        active = controller.active
        level = controller.mic_level if controller.mic is not None else (self.monitor.level if self.monitor else 0.0)
        self.mic_meter.set_level(level)
        self.out_meter.set_level(controller.output_level if active is not None else 0.0)
        if active is not None and active.kind == "live":
            self.talk.level = LevelMeter.to_meter(level)
            self.talk.update()
            self.timer_label.setText(format_duration(active.elapsed) if active.elapsed >= 60 else
                                     f"00:{int(active.elapsed):02d}")
            limit = active.request.max_seconds
            if limit:
                remaining = limit - active.elapsed
                self.limit_label.setText(f"Auto-stops in {format_duration(remaining)} (safety limit)"
                                         if remaining < 120 else f"Safety limit: {int(limit // 60)} min")
            mic = controller.mic
            if mic is not None and mic.feedback_events > self._warned_feedback:
                self._warned_feedback = mic.feedback_events
                self.feedback_banner.set_text(f"Possible feedback detected near {mic._guard.last_frequency:.0f} Hz - "
                                              f"microphone gain reduced by {mic.feedback_reduction_db:.0f} dB. Move "
                                              "the microphone away from the speakers.", "warning")
                self.feedback_banner.show()
                self.toast("warning", "Feedback detected", "Microphone gain was reduced automatically.")
        elif active is None and self.feedback_banner.isVisible() and self._warned_feedback == 0:
            self.feedback_banner.hide()
