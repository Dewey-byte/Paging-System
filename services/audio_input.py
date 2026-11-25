"""Microphone capture: live paging source, level metering, recording, feedback guard."""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

from .audio_decoder import StreamResampler
from .audio_sources import AudioSource, LiveReader, SourceReader
from .device_detection import STREAMS, SystemDevice, sd

log = logging.getLogger(__name__)


def gain_db_from_slider(value: int) -> float:
    """Map a 0..100 slider to -24..+24 dB (50 = unity)."""
    return (max(0, min(100, int(value))) - 50) * 0.48


def db_to_linear(db: float) -> float:
    return float(10 ** (db / 20))


class HighPassFilter:
    """One-pole high-pass (~100 Hz) computed block-wise with numpy (no per-sample Python loop)."""

    def __init__(self, sample_rate: int, cutoff: float = 100.0, block: int = 512):
        self.a = float(np.exp(-2 * np.pi * cutoff / sample_rate))
        self.block = block
        self._x_prev = 0.0
        self._y_prev = 0.0
        k = np.arange(block, dtype=np.float64)
        self._pow = self.a ** k                    # a^k
        self._inv = self.a ** (-k)                 # a^-k

    def process(self, x: np.ndarray) -> np.ndarray:
        out = np.empty_like(x)
        for start in range(0, x.size, self.block):
            seg = x[start:start + self.block].astype(np.float64)
            n = seg.size
            diff = np.empty(n)
            diff[0] = seg[0] - self._x_prev
            diff[1:] = seg[1:] - seg[:-1]
            # y[k] = a^(k+1) * y_prev + sum_{j<=k} a^(k-j) * diff[j]
            acc = np.cumsum(diff * self._inv[:n]) * self._pow[:n]
            y = acc + self._y_prev * self._pow[:n] * self.a
            out[start:start + n] = y
            self._x_prev, self._y_prev = seg[-1], y[-1]
        return out.astype(np.float32)


class FeedbackGuard:
    """Detects sustained single-frequency howl (acoustic feedback) in the microphone signal."""

    def __init__(self, sample_rate: int, window: int = 4096):
        self.window = window
        self.sample_rate = sample_rate
        self._buf = np.zeros(0, dtype=np.float32)
        self._hann = np.hanning(window).astype(np.float32)
        self._hits = 0
        self._last_bin = -10
        self.events = 0
        self.last_frequency = 0.0

    def process(self, x: np.ndarray) -> bool:
        self._buf = np.concatenate([self._buf, x])
        triggered = False
        while self._buf.size >= self.window:
            frame, self._buf = self._buf[:self.window], self._buf[self.window:]
            rms = float(np.sqrt(np.mean(frame * frame)))
            if rms < 0.05:
                self._hits = max(0, self._hits - 1)
                continue
            spectrum = np.abs(np.fft.rfft(frame * self._hann))
            peak_bin = int(np.argmax(spectrum[4:])) + 4
            ratio_db = 20 * np.log10((spectrum[peak_bin] + 1e-9) / (np.median(spectrum) + 1e-9))
            if ratio_db > 38 and abs(peak_bin - self._last_bin) <= 1:
                self._hits += 1
            elif ratio_db > 38:
                self._hits = 1
            else:
                self._hits = max(0, self._hits - 1)
            self._last_bin = peak_bin
            if self._hits >= 5:
                self._hits = 0
                self.events += 1
                self.last_frequency = peak_bin * self.sample_rate / self.window
                triggered = True
        return triggered


class MicrophoneSource(AudioSource):
    """Live microphone feeding every output reader, with push-to-talk gate and safety processing."""

    is_live = True

    def __init__(self, device: SystemDevice | None, gain_db: float = 0.0, high_pass: bool = True,
                 feedback_guard: bool = True, gate_open: bool = True, name: str = "Live microphone"):
        self.device = device
        self.name = name
        self.sample_rate = device.samplerate if device else 48000
        self.gain_db = gain_db
        self.gate_open = gate_open
        self.level = 0.0
        self.error = ""
        self.feedback_events = 0
        self.feedback_reduction_db = 0.0
        self.high_pass = high_pass
        self.feedback_enabled = feedback_guard
        self._readers: list[LiveReader] = []
        self._lock = threading.Lock()
        self._stream = None
        self._gate = 1.0 if gate_open else 0.0
        self._hpf = HighPassFilter(self.sample_rate) if high_pass else None
        self._guard = FeedbackGuard(self.sample_rate) if feedback_guard else None
        self._running = False
        self.last_callback = 0.0

    # --------------------------------------------------------------- control
    def set_gain_db(self, db: float) -> None:
        self.gain_db = float(db)

    def set_gate(self, open_: bool) -> None:
        self.gate_open = bool(open_)

    @property
    def running(self) -> bool:
        return self._running

    def open_reader(self, sample_rate: int) -> SourceReader:
        reader = LiveReader(sample_rate)
        reader.resampler = StreamResampler(self.sample_rate, sample_rate)
        with self._lock:
            self._readers.append(reader)
        return reader

    def start(self) -> None:
        if self._running:
            return
        if sd is None:
            raise RuntimeError("The audio subsystem (PortAudio) is not available on this computer.")
        if self.device is None:
            raise RuntimeError("No microphone is connected. Plug in a microphone and press Refresh.")
        extra = None
        if self.device.host_api == "Windows WASAPI":
            try:
                extra = sd.WasapiSettings(auto_convert=True)
            except TypeError:
                extra = None
        self._stream = sd.InputStream(device=self.device.index, channels=1, samplerate=self.sample_rate,
                                      dtype="float32", latency="low", callback=self._callback,
                                      extra_settings=extra)
        STREAMS.acquire()
        try:
            self._stream.start()
        except Exception:
            STREAMS.release()
            self._stream.close(ignore_errors=True)
            self._stream = None
            raise
        self._running = True
        self.last_callback = time.monotonic()

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop(ignore_errors=True)
                self._stream.close(ignore_errors=True)
            except Exception:
                log.debug("Error closing microphone", exc_info=True)
            self._stream = None
            STREAMS.release()
        self._running = False
        self.level = 0.0
        with self._lock:
            for reader in self._readers:
                reader.close()
            self._readers.clear()

    # -------------------------------------------------------------- realtime
    def _callback(self, indata, frames, time_info, status) -> None:
        self.last_callback = time.monotonic()
        x = np.array(indata[:, 0], dtype=np.float32)
        if self._hpf is not None:
            x = self._hpf.process(x)
        x *= db_to_linear(self.gain_db - self.feedback_reduction_db)
        np.clip(x, -1.0, 1.0, out=x)
        self.level = float(np.max(np.abs(x))) if frames else 0.0
        if self._guard is not None and self.gate_open and self._guard.process(x):
            self.feedback_events += 1
            self.feedback_reduction_db = min(18.0, self.feedback_reduction_db + 6.0)
        target = 1.0 if self.gate_open else 0.0
        if self._gate != target or target == 0.0:
            step = frames / (0.02 * self.sample_rate)
            new_gate = self._gate + max(-step, min(step, target - self._gate))
            x *= np.linspace(self._gate, new_gate, frames, endpoint=False, dtype=np.float32)
            self._gate = new_gate
        with self._lock:
            for reader in self._readers:
                reader.push(reader.resampler.process(x))


class Recorder:
    """Records the microphone into memory (voice announcements, up to ``max_seconds``)."""

    def __init__(self, device: SystemDevice | None, max_seconds: int = 300):
        self.device = device
        self.max_seconds = max_seconds
        self.sample_rate = device.samplerate if device else 48000
        self.level = 0.0
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._stream = None
        self._lock = threading.Lock()
        self.recording = False
        self.limit_reached = False

    def start(self) -> None:
        if sd is None:
            raise RuntimeError("The audio subsystem (PortAudio) is not available on this computer.")
        if self.device is None:
            raise RuntimeError("No microphone is connected.")
        self._chunks, self._frames, self.limit_reached = [], 0, False
        self._stream = sd.InputStream(device=self.device.index, channels=1, samplerate=self.sample_rate,
                                      dtype="float32", callback=self._callback)
        STREAMS.acquire()
        try:
            self._stream.start()
        except Exception:
            STREAMS.release()
            self._stream.close(ignore_errors=True)
            self._stream = None
            raise
        self.recording = True

    def _callback(self, indata, frames, time_info, status) -> None:
        if not self.recording:
            return
        x = np.array(indata[:, 0], dtype=np.float32)
        self.level = float(np.max(np.abs(x))) if frames else 0.0
        with self._lock:
            self._chunks.append(x)
            self._frames += frames
            if self._frames >= self.max_seconds * self.sample_rate:
                self.recording = False
                self.limit_reached = True

    @property
    def elapsed(self) -> float:
        return self._frames / self.sample_rate

    def stop(self) -> tuple[np.ndarray, int]:
        self.recording = False
        if self._stream is not None:
            try:
                self._stream.stop(ignore_errors=True)
                self._stream.close(ignore_errors=True)
            finally:
                self._stream = None
                STREAMS.release()
        self.level = 0.0
        with self._lock:
            data = np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
        return data, self.sample_rate
