"""Output endpoints: real Windows sound devices and simulated zones.

Each broadcast opens one *session* per physical endpoint.  A session pulls
audio from its own reader, applies master x zone gain with click-free ramps,
soft-limits the signal and writes it to the device channels assigned to the
selected zones (multi-channel interfaces can drive one zone per channel).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from .audio_sources import AudioSource, SourceReader
from .device_detection import STREAMS, SystemDevice, sd

log = logging.getLogger(__name__)

LATENCY_PRESETS = {"low": "low", "balanced": 0.08, "safe": "high"}


class GainControl:
    """Shared master gain (0..1) read by every running session."""

    def __init__(self, value: float = 0.8):
        self.value = float(value)


@dataclass
class OutputTarget:
    key: str
    device_id: int
    device_name: str
    transport: str                       # "local" | "simulated" | "rtp"
    system_device: SystemDevice | None = None
    open_channels: int = 2
    channel_map: list[int] | None = None  # 0-based channels to drive; None = all channels
    gain: float = 1.0
    zone_ids: list[int] = field(default_factory=list)
    zone_names: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)

    @property
    def simulated(self) -> bool:
        return self.transport == "simulated"


def soft_limit(x: np.ndarray, threshold: float = 0.9) -> np.ndarray:
    magnitude = np.abs(x)
    over = magnitude > threshold
    if over.any():
        knee = 1.0 - threshold
        x[over] = np.sign(x[over]) * (threshold + knee * np.tanh((magnitude[over] - threshold) / knee))
    return x


class OutputSession:
    simulated = False

    def __init__(self, target: OutputTarget, source: AudioSource, master: GainControl, latency="balanced"):
        self.target = target
        self.source = source
        self.master = master
        self.latency = LATENCY_PRESETS.get(latency, latency)
        self.reader: SourceReader | None = None
        self.sample_rate = 48000
        self.level = 0.0
        self.error = ""
        self.started = False
        self.finished = False
        self.done = False
        self.frames_rendered = 0
        self.underflows = 0
        self.last_activity = 0.0
        self._gain = 0.0
        self._stop_requested = False

    # Rendering is shared by every endpoint type.
    def render(self, frames: int) -> np.ndarray:
        out = np.zeros(frames, dtype=np.float32)
        if not self.done and self.reader is not None:
            chunk = self.reader.read(frames)
            out[:chunk.size] = chunk
            if self.reader.finished:
                self.done = True
        target = 0.0 if self._stop_requested else max(0.0, self.master.value * self.target.gain)
        max_step = frames / (0.04 * self.sample_rate)
        delta = max(-max_step, min(max_step, target - self._gain))
        new_gain = self._gain + delta
        if new_gain != self._gain or new_gain != 1.0:
            out *= np.linspace(self._gain, new_gain, frames, endpoint=False, dtype=np.float32)
        self._gain = new_gain
        soft_limit(out)
        self.level = float(np.max(np.abs(out))) if frames else 0.0
        self.frames_rendered += frames
        if self._stop_requested and self._gain <= 1e-4:
            self.done = True
        return out

    @property
    def position_seconds(self) -> float:
        return self.frames_rendered / self.sample_rate if self.sample_rate else 0.0

    def request_stop(self) -> None:
        self._stop_requested = True

    def start(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def abort(self) -> None:
        self.finished = True

    def close(self) -> None:
        if self.reader is not None:
            self.reader.close()


class LocalOutputSession(OutputSession):
    """Plays through a Windows audio device (speakers, line-out to a PA amplifier, USB interface...)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stream = None
        self._acquired = False

    def start(self) -> None:
        device = self.target.system_device
        if sd is None:
            raise RuntimeError("The audio subsystem (PortAudio) is not available on this computer.")
        if device is None:
            raise RuntimeError(f"Output device for '{self.target.device_name}' is not connected.")
        self.sample_rate = device.samplerate
        channels = max(1, min(self.target.open_channels, device.max_output_channels))
        self.reader = self.source.open_reader(self.sample_rate)
        extra = None
        if device.host_api == "Windows WASAPI":
            try:
                extra = sd.WasapiSettings(auto_convert=True)
            except TypeError:
                extra = None
        self.stream = sd.OutputStream(device=device.index, samplerate=self.sample_rate, channels=channels,
                                      dtype="float32", latency=self.latency, callback=self._callback,
                                      finished_callback=self._on_finished, extra_settings=extra)
        STREAMS.acquire()
        self._acquired = True
        self.last_activity = time.monotonic()
        self.stream.start()
        self.started = True

    def _callback(self, outdata, frames, time_info, status) -> None:
        if status.output_underflow:
            self.underflows += 1
        try:
            mono = self.render(frames)
            mapping = self.target.channel_map
            if mapping is None:
                outdata[:] = mono[:, None]
            else:
                outdata.fill(0)
                for channel in mapping:
                    if channel < outdata.shape[1]:
                        outdata[:, channel] = mono
        except Exception as exc:  # never let an exception kill the audio thread silently
            self.error = f"Playback error: {exc}"
            outdata.fill(0)
            raise sd.CallbackAbort from exc
        self.last_activity = time.monotonic()
        if self.done:
            raise sd.CallbackStop

    def _on_finished(self) -> None:
        self.finished = True

    def abort(self) -> None:
        try:
            if self.stream is not None:
                self.stream.abort(ignore_errors=True)
        except Exception:
            pass
        self.finished = True

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.close(ignore_errors=True)
            except Exception:
                log.debug("Error closing stream", exc_info=True)
            self.stream = None
        if self._acquired:
            STREAMS.release()
            self._acquired = False
        super().close()


class SimulatedOutputSession(OutputSession):
    """Consumes audio in real time without producing sound (testing without hardware)."""

    simulated = True
    BLOCK = 960

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread: threading.Thread | None = None
        self._abort = False

    def start(self) -> None:
        self.sample_rate = 48000
        self.reader = self.source.open_reader(self.sample_rate)
        self._thread = threading.Thread(target=self._run, name=f"sim-output-{self.target.key}", daemon=True)
        self.last_activity = time.monotonic()
        self.started = True
        self._thread.start()

    def _run(self) -> None:
        period = self.BLOCK / self.sample_rate
        deadline = time.monotonic()
        try:
            while not self._abort:
                self.render(self.BLOCK)
                self.last_activity = time.monotonic()
                if self.done:
                    break
                deadline += period
                delay = deadline - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -0.5:
                    deadline = time.monotonic()
        except Exception as exc:
            self.error = f"Simulation error: {exc}"
        finally:
            self.level = 0.0
            self.finished = True

    def abort(self) -> None:
        self._abort = True

    def close(self) -> None:
        self._abort = True
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=0.5)
        super().close()
