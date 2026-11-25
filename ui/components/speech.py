"""Reusable message editors: text-to-speech composer and in-app voice recorder.

Used by the announcement editor and the scheduled-broadcast editor so both
offer the same controls and behave identically.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget

from core.history_manager import format_duration
from services.audio_input import Recorder

from ..workers import run_async
from .base import InfoBanner, button, label
from .controls import LevelMeter, SliderField
from .dialogs import form_field, form_row


class TTSEditor(QWidget):
    """Message text, voice, speaking speed and speech volume."""

    changed = Signal()

    def __init__(self, ctx, message: str = "", voice: str = "", rate: int | None = None, volume: int | None = None,
                 placeholder: str = "Type the message exactly as it should be spoken...", parent=None):
        super().__init__(parent)
        self.setObjectName("Transparent")
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.message = QPlainTextEdit(message)
        self.message.setPlaceholderText(placeholder)
        self.message.setFixedHeight(110)
        self.char_count = label("", "caption")
        layout.addWidget(form_field("Message", self.message))
        layout.addWidget(self.char_count)
        self.voice = QComboBox()
        voices = ctx.tts.voices()
        self.voice.addItem("Default voice", "")
        for v in voices:
            self.voice.addItem(f"{v.label}  -  {v.language}", v.id)
        self.voice.setCurrentIndex(max(0, self.voice.findData(voice or "")))
        self.rate = SliderField(-10, 10, ctx.settings.get_int("tts.rate") if rate is None else rate,
                                formatter=lambda v: "Normal" if v == 0 else (f"+{v}" if v > 0 else str(v)))
        self.volume = SliderField(0, 100, ctx.settings.get_int("tts.volume") if volume is None else volume,
                                  icon_name="volume")
        layout.addLayout(form_row(form_field("Voice", self.voice), form_field("Speaking speed", self.rate)))
        layout.addWidget(form_field("Speech volume", self.volume))
        if not ctx.tts.available or not voices:
            layout.addWidget(InfoBanner(ctx.tts.error or "No text-to-speech voices are installed.", "warning"))
        self.message.textChanged.connect(self._text_changed)
        self.rate.valueChanged.connect(self._text_changed)
        self.voice.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self.volume.valueChanged.connect(lambda *_: self.changed.emit())
        self._update_count()

    # ---------------------------------------------------------------- values
    def text(self) -> str:
        return self.message.toPlainText()

    def voice_id(self) -> str:
        return self.voice.currentData() or ""

    def rate_value(self) -> int:
        return self.rate.value()

    def volume_value(self) -> int:
        return self.volume.value()

    def effective_voice(self) -> str:
        return self.voice_id() or self.ctx.settings.get("tts.voice") or self.ctx.tts.default_voice_id()

    # --------------------------------------------------------------- helpers
    def _text_changed(self, *args) -> None:
        self._update_count()
        self.changed.emit()

    def _update_count(self) -> None:
        text = self.text()
        seconds = self.ctx.tts.estimate_seconds(text, self.rate.value()) if text.strip() else 0
        self.char_count.setText(f"{len(text.split())} words · about {format_duration(seconds)} when spoken")

    def render_async(self, done, failed) -> None:
        """Synthesize the current text on a worker thread; ``done`` receives ``(samples, sample_rate)``."""
        text, voice, rate, volume = self.text().strip(), self.effective_voice(), self.rate_value(), self.volume_value()
        run_async(lambda: self.ctx.tts.load(text, voice, rate, volume), done, failed)


class VoiceRecorderPanel(QWidget):
    """Microphone picker, record/stop button, timer and level meter.

    After a take, :attr:`recording` holds ``(samples, sample_rate)``; the host
    dialog saves it to the audio library when the form is saved.
    """

    changed = Signal()
    error = Signal(str)

    MAX_SECONDS = 300

    def __init__(self, ctx, existing_name: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Transparent")
        self.ctx = ctx
        self.recorder: Recorder | None = None
        self.recording: tuple | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.device = QComboBox()
        default_in = ctx.registry.default_input()
        self.device.addItem(f"System default ({default_in.name})" if default_in else "System default", "")
        for d in ctx.registry.inputs():
            self.device.addItem(d.name, d.name)
        layout.addWidget(form_field("Microphone", self.device))
        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.button = button("Start recording", "record", "danger")
        self.button.clicked.connect(self.toggle)
        controls.addWidget(self.button)
        self.time = label("0:00", "h2")
        controls.addWidget(self.time)
        self.meter = LevelMeter(24)
        controls.addWidget(self.meter, 1)
        layout.addLayout(controls)
        self.status = label(f"Press Start recording and speak clearly. Up to {self.MAX_SECONDS // 60} minutes.",
                            "caption", wrap=True)
        if existing_name:
            self.status.setText(f"Current recording: {existing_name}. Record again to replace it.")
        layout.addWidget(self.status)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    @property
    def is_recording(self) -> bool:
        return self.recorder is not None and self.recorder.recording

    def toggle(self) -> None:
        if self.is_recording:
            self._finish()
            return
        active = self.ctx.controller.active
        if active is not None and active.kind == "live":
            self.error.emit("Live paging is using the microphone. Stop it before recording.")
            return
        self.recorder = Recorder(self.ctx.registry.find_input(self.device.currentData() or ""), self.MAX_SECONDS)
        try:
            self.recorder.start()
        except Exception as exc:  # noqa: BLE001
            self.recorder = None
            self.error.emit(f"Could not start recording: {exc}")
            return
        self.button.setText("Stop recording")
        self.status.setText("Recording... speak now.")
        self._timer.start(50)

    def _finish(self) -> None:
        data, rate = self.recorder.stop()
        self._timer.stop()
        self.meter.set_level(0)
        self.button.setText("Record again")
        if data.size < rate * 0.5:
            self.status.setText("The recording was too short. Try again.")
            self.recording = None
        else:
            self.recording = (data, rate)
            self.status.setText(f"Recorded {data.size / rate:.1f} seconds. Use Preview to listen, then Save.")
        self.changed.emit()

    def _tick(self) -> None:
        if self.recorder is None:
            return
        self.meter.set_level(self.recorder.level)
        elapsed = int(self.recorder.elapsed)
        self.time.setText(f"{elapsed // 60}:{elapsed % 60:02d}")
        if self.recorder.limit_reached:
            self._finish()

    def shutdown(self) -> None:
        """Release the microphone (call when the host dialog closes)."""
        if self.is_recording:
            self.recorder.stop()
        self._timer.stop()
