"""Audio file decoding, probing, resampling and WAV writing."""
from __future__ import annotations

import threading
import wave
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:
    import soundfile as sf
except Exception:  # pragma: no cover - soundfile ships wheels for Windows
    sf = None

SUPPORTED_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg", ".aiff", ".aif")
FILE_DIALOG_FILTER = "Audio files (*.wav *.mp3 *.flac *.ogg *.aiff *.aif)"


class DecodeError(Exception):
    pass


@dataclass
class AudioInfo:
    duration: float
    sample_rate: int
    channels: int
    format: str
    frames: int


def probe(path: str | Path) -> AudioInfo:
    path = Path(path)
    if not path.exists():
        raise DecodeError(f"File not found: {path.name}")
    if sf is not None:
        try:
            info = sf.info(str(path))
            frames = int(info.frames)
            duration = frames / info.samplerate if info.samplerate else 0.0
            return AudioInfo(duration, int(info.samplerate), int(info.channels), str(info.format), frames)
        except Exception as exc:
            if path.suffix.lower() != ".wav":
                raise DecodeError(f"Unsupported or damaged audio file ({exc}).") from exc
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            return AudioInfo(frames / rate if rate else 0.0, rate, handle.getnchannels(), "WAV", frames)
    except (wave.Error, EOFError, OSError) as exc:
        raise DecodeError(f"Unsupported or damaged audio file ({exc}).") from exc


def to_mono(data: np.ndarray) -> np.ndarray:
    if data.ndim == 2:
        data = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    return np.ascontiguousarray(data, dtype=np.float32)


def decode(path: str | Path) -> tuple[np.ndarray, int]:
    """Decode an audio file into mono float32 samples in [-1, 1]."""
    path = Path(path)
    if not path.exists():
        raise DecodeError(f"Audio file is missing: {path.name}")
    if sf is not None:
        try:
            data, rate = sf.read(str(path), dtype="float32", always_2d=True)
            return to_mono(data), int(rate)
        except Exception as exc:
            if path.suffix.lower() != ".wav":
                raise DecodeError(f"Could not decode {path.name}: {exc}") from exc
    return _decode_wav(path)


def _decode_wav(path: Path) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(path), "rb") as handle:
            width, channels, rate = handle.getsampwidth(), handle.getnchannels(), handle.getframerate()
            raw = handle.readframes(handle.getnframes())
    except (wave.Error, EOFError, OSError) as exc:
        raise DecodeError(f"Could not decode {path.name}: {exc}") from exc
    if width == 1:
        data = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    elif width == 2:
        data = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        ints = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16))
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        data = ints.astype(np.float32) / 8388608
    elif width == 4:
        data = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648
    else:
        raise DecodeError(f"Unsupported WAV sample width: {width * 8} bit")
    return to_mono(data.reshape(-1, channels)), rate


def resample(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """High-quality offline resampling (FFT based) for whole buffers."""
    data = np.asarray(data, dtype=np.float32)
    if src_rate == dst_rate or data.size == 0:
        return data
    n_out = int(round(data.size * dst_rate / src_rate))
    if data.size > (1 << 23):  # very long files: linear interpolation keeps memory bounded
        positions = np.arange(n_out, dtype=np.float64) * (src_rate / dst_rate)
        return np.interp(positions, np.arange(data.size), data).astype(np.float32)
    pad = min(data.size, int(src_rate * 0.05))
    padded = np.concatenate([data, np.zeros(pad, dtype=np.float32)])
    n_padded_out = int(round(padded.size * dst_rate / src_rate))
    spectrum = np.fft.rfft(padded)
    out_bins = n_padded_out // 2 + 1
    resized = np.zeros(out_bins, dtype=np.complex128)
    keep = min(spectrum.size, out_bins)
    resized[:keep] = spectrum[:keep]
    result = np.fft.irfft(resized, n_padded_out) * (n_padded_out / padded.size)
    return result[:n_out].astype(np.float32)


class StreamResampler:
    """Stateful linear-interpolation resampler for live (chunked) audio."""

    def __init__(self, src_rate: int, dst_rate: int):
        self.src_rate, self.dst_rate = int(src_rate), int(dst_rate)
        self.step = self.src_rate / self.dst_rate
        self._pos = 1.0
        self._last = np.float32(0.0)

    def process(self, chunk: np.ndarray) -> np.ndarray:
        if self.src_rate == self.dst_rate or chunk.size == 0:
            return chunk
        buf = np.empty(chunk.size + 1, dtype=np.float32)
        buf[0] = self._last
        buf[1:] = chunk
        limit = buf.size - 1
        if self._pos > limit:
            self._pos -= chunk.size
            self._last = chunk[-1]
            return np.zeros(0, dtype=np.float32)
        count = int((limit - self._pos) // self.step) + 1
        positions = self._pos + self.step * np.arange(count)
        idx = positions.astype(np.int64)
        frac = (positions - idx).astype(np.float32)
        nxt = np.minimum(idx + 1, limit)
        out = buf[idx] * (1.0 - frac) + buf[nxt] * frac
        self._pos = self._pos + self.step * count - chunk.size
        self._last = chunk[-1]
        return out.astype(np.float32)


def normalize(data: np.ndarray, peak_db: float = -1.0) -> np.ndarray:
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak <= 1e-6:
        return data.astype(np.float32)
    return (data * (10 ** (peak_db / 20) / peak)).astype(np.float32)


def write_wav(path: str | Path, data: np.ndarray, rate: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(np.asarray(data, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(rate))
        handle.writeframes(pcm.tobytes())


class AudioCache:
    """Small LRU cache of decoded files keyed by path + modification time."""

    def __init__(self, max_bytes: int = 256 * 1024 * 1024):
        self._items: OrderedDict[tuple, tuple[np.ndarray, int]] = OrderedDict()
        self._bytes = 0
        self._max = max_bytes
        self._lock = threading.Lock()

    def load(self, path: str | Path) -> tuple[np.ndarray, int]:
        path = Path(path)
        try:
            key = (str(path.resolve()), path.stat().st_mtime_ns)
        except OSError as exc:
            raise DecodeError(f"Audio file is missing: {path.name}") from exc
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        data, rate = decode(path)
        with self._lock:
            self._items[key] = (data, rate)
            self._bytes += data.nbytes
            while self._bytes > self._max and len(self._items) > 1:
                _, (old, _) = self._items.popitem(last=False)
                self._bytes -= old.nbytes
        return data, rate
