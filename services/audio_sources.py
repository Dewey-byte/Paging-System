"""Audio sources and per-output readers.

A *source* describes what is broadcast (a decoded file, a TTS rendering, a
synthesised alarm, the live microphone...).  Every output endpoint opens its
own *reader* at the endpoint's sample rate, so devices with different clocks
and rates can play the same broadcast independently and stay glitch-free.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod

import numpy as np

from .audio_decoder import resample


class SourceReader(ABC):
    sample_rate: int = 48000

    @abstractmethod
    def read(self, frames: int) -> np.ndarray:
        """Return up to ``frames`` mono float32 samples (fewer only when the data ended)."""

    @property
    @abstractmethod
    def finished(self) -> bool:
        """True once no more audio will ever be produced."""

    def activate(self) -> None:
        """Called when the reader becomes current inside a sequence."""

    def close(self) -> None:
        pass


class AudioSource(ABC):
    name = ""
    is_live = False

    @abstractmethod
    def open_reader(self, sample_rate: int) -> SourceReader:
        ...

    def start(self) -> None:
        """Open hardware needed by the source (e.g. the microphone)."""

    def stop(self) -> None:
        pass

    @property
    def duration(self) -> float | None:
        """Length in seconds, or None for live / open-ended sources."""
        return None


# ------------------------------------------------------------------ buffers
class ArrayReader(SourceReader):
    def __init__(self, data: np.ndarray, sample_rate: int):
        self.data = data
        self.sample_rate = sample_rate
        self.pos = 0

    def read(self, frames: int) -> np.ndarray:
        chunk = self.data[self.pos:self.pos + frames]
        self.pos += chunk.size
        return chunk

    @property
    def finished(self) -> bool:
        return self.pos >= self.data.size

    @property
    def progress(self) -> float:
        return self.pos / self.data.size if self.data.size else 1.0


class BufferSource(AudioSource):
    """A fully decoded mono buffer (file, TTS output, generated sound)."""

    def __init__(self, data: np.ndarray, sample_rate: int, name: str = ""):
        self.data = np.ascontiguousarray(data, dtype=np.float32)
        self.sample_rate = int(sample_rate)
        self.name = name
        self._cache: dict[int, np.ndarray] = {self.sample_rate: self.data}
        self._lock = threading.Lock()

    def resampled(self, rate: int) -> np.ndarray:
        with self._lock:
            if rate not in self._cache:
                self._cache[rate] = resample(self.data, self.sample_rate, rate)
            return self._cache[rate]

    def open_reader(self, sample_rate: int) -> SourceReader:
        return ArrayReader(self.resampled(int(sample_rate)), int(sample_rate))

    @property
    def duration(self) -> float:
        return self.data.size / self.sample_rate


class SilenceSource(AudioSource):
    def __init__(self, seconds: float):
        self.seconds = max(0.0, seconds)
        self.name = "silence"

    def open_reader(self, sample_rate: int) -> SourceReader:
        return ArrayReader(np.zeros(int(self.seconds * sample_rate), dtype=np.float32), sample_rate)

    @property
    def duration(self) -> float:
        return self.seconds


# --------------------------------------------------------------- composites
class SequenceReader(SourceReader):
    def __init__(self, readers: list[SourceReader], sample_rate: int):
        self.readers = readers
        self.sample_rate = sample_rate
        self.index = 0
        self._activated = -1

    def read(self, frames: int) -> np.ndarray:
        parts, needed = [], frames
        while needed > 0 and self.index < len(self.readers):
            reader = self.readers[self.index]
            if self._activated != self.index:
                reader.activate()
                self._activated = self.index
            chunk = reader.read(needed)
            if chunk.size:
                parts.append(chunk)
                needed -= chunk.size
            if reader.finished:
                self.index += 1
            elif chunk.size == 0:
                break
        if not parts:
            return np.zeros(0, dtype=np.float32)
        return parts[0] if len(parts) == 1 else np.concatenate(parts)

    @property
    def finished(self) -> bool:
        return self.index >= len(self.readers)

    def close(self) -> None:
        for reader in self.readers:
            reader.close()


class SequenceSource(AudioSource):
    """Plays several sources back to back (e.g. chime -> message -> chime)."""

    def __init__(self, parts: list[AudioSource], name: str = ""):
        self.parts = [p for p in parts if p is not None]
        self.name = name or " + ".join(p.name for p in self.parts if p.name)
        self.is_live = any(p.is_live for p in self.parts)

    def open_reader(self, sample_rate: int) -> SourceReader:
        return SequenceReader([p.open_reader(sample_rate) for p in self.parts], sample_rate)

    def start(self) -> None:
        for part in self.parts:
            part.start()

    def stop(self) -> None:
        for part in self.parts:
            part.stop()

    @property
    def duration(self) -> float | None:
        total = 0.0
        for part in self.parts:
            if part.duration is None:
                return None
            total += part.duration
        return total


class ProgramReader(SourceReader):
    """Reader for :class:`ProgramSource`: intro -> live part -> (after ``finish``) outro."""

    INTRO, LIVE, OUTRO, DONE = range(4)

    def __init__(self, owner: "ProgramSource", intro: SourceReader | None, live: SourceReader,
                 outro: SourceReader | None, sample_rate: int):
        self.owner = owner
        self.readers = (intro, live, outro)
        self.sample_rate = sample_rate
        self.stage = self.INTRO if intro is not None else self.LIVE
        self._activated = -1

    def _advance_from(self, stage: int) -> None:
        if stage == self.INTRO:
            self.stage = self.LIVE
        elif stage == self.LIVE:
            self.stage = self.OUTRO if self.readers[2] is not None else self.DONE
        else:
            self.stage = self.DONE

    def read(self, frames: int) -> np.ndarray:
        parts, needed = [], frames
        while needed > 0 and self.stage != self.DONE:
            if self.owner.ending and self.stage in (self.INTRO, self.LIVE):
                # Nobody spoke yet (still in the intro) -> end without a closing chime.
                spoke = self.stage == self.LIVE
                self.readers[1].close()
                self.stage = self.OUTRO if spoke and self.readers[2] is not None else self.DONE
                continue
            reader = self.readers[self.stage]
            if self._activated != self.stage:
                reader.activate()
                self._activated = self.stage
            chunk = reader.read(needed)
            if chunk.size:
                parts.append(chunk)
                needed -= chunk.size
            if reader.finished:
                self._advance_from(self.stage)
            elif chunk.size == 0:
                break
        if not parts:
            return np.zeros(0, dtype=np.float32)
        return parts[0] if len(parts) == 1 else np.concatenate(parts)

    @property
    def finished(self) -> bool:
        return self.stage == self.DONE

    @property
    def in_outro(self) -> bool:
        return self.stage == self.OUTRO

    def close(self) -> None:
        for reader in self.readers:
            if reader is not None:
                reader.close()


class ProgramSource(AudioSource):
    """An open-ended live broadcast framed by chimes.

    Plays ``intro`` once, then the live part until :meth:`finish` is called,
    then ``outro`` once and ends.  Used by live paging so that pressing STOP
    closes the microphone and plays the closing chime instead of cutting off.
    """

    def __init__(self, live: AudioSource, intro: AudioSource | None = None, outro: AudioSource | None = None,
                 name: str = ""):
        self.live = live
        self.intro = intro
        self.outro = outro
        self.name = name or live.name
        self.is_live = True
        self.ending = False

    @property
    def has_outro(self) -> bool:
        return self.outro is not None

    def finish(self) -> None:
        """Close the live part; every output plays the outro (if any) and then finishes."""
        self.ending = True

    def open_reader(self, sample_rate: int) -> SourceReader:
        return ProgramReader(self, self.intro.open_reader(sample_rate) if self.intro else None,
                             self.live.open_reader(sample_rate),
                             self.outro.open_reader(sample_rate) if self.outro else None, sample_rate)

    def start(self) -> None:
        self.live.start()

    def stop(self) -> None:
        self.live.stop()

    @property
    def duration(self) -> float | None:
        return None


class RepeatReader(SourceReader):
    def __init__(self, factory, sample_rate: int, count: int, gap_frames: int, max_frames: int):
        self._factory = factory
        self.sample_rate = sample_rate
        self.count = count            # 0 = unlimited
        self.gap_frames = gap_frames
        self.max_frames = max_frames  # 0 = unlimited
        self.done_cycles = 0
        self.produced = 0
        self._current = factory()
        self._gap_left = 0
        self._finished = False

    def read(self, frames: int) -> np.ndarray:
        if self._finished:
            return np.zeros(0, dtype=np.float32)
        if self.max_frames:
            frames = min(frames, self.max_frames - self.produced)
        parts, needed = [], frames
        while needed > 0 and not self._finished:
            if self._gap_left > 0:
                n = min(needed, self._gap_left)
                parts.append(np.zeros(n, dtype=np.float32))
                self._gap_left -= n
                needed -= n
                if self._gap_left == 0:
                    self._current = self._factory()
                continue
            chunk = self._current.read(needed)
            if chunk.size:
                parts.append(chunk)
                needed -= chunk.size
            if self._current.finished:
                self.done_cycles += 1
                if self.count and self.done_cycles >= self.count:
                    self._finished = True
                elif self.gap_frames:
                    self._gap_left = self.gap_frames
                else:
                    self._current = self._factory()
            elif chunk.size == 0:
                break
        out = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
        self.produced += out.size
        if self.max_frames and self.produced >= self.max_frames:
            self._finished = True
        return out

    @property
    def finished(self) -> bool:
        return self._finished


class RepeatSource(AudioSource):
    """Repeat ``inner`` ``count`` times (0 = forever), optionally capped at ``max_seconds``."""

    def __init__(self, inner: AudioSource, count: int = 1, gap_seconds: float = 0.0, max_seconds: float = 0.0,
                 name: str = ""):
        self.inner = inner
        self.count = max(0, int(count))
        self.gap_seconds = max(0.0, gap_seconds)
        self.max_seconds = max(0.0, max_seconds)
        self.name = name or inner.name

    def open_reader(self, sample_rate: int) -> SourceReader:
        return RepeatReader(lambda: self.inner.open_reader(sample_rate), sample_rate, self.count,
                            int(self.gap_seconds * sample_rate), int(self.max_seconds * sample_rate))

    @property
    def duration(self) -> float | None:
        if self.max_seconds and not self.count:
            return self.max_seconds
        if not self.count or self.inner.duration is None:
            return self.max_seconds or None
        total = self.count * self.inner.duration + (self.count - 1) * self.gap_seconds
        return min(total, self.max_seconds) if self.max_seconds else total


# --------------------------------------------------------------- live audio
class RingBuffer:
    """Thread-safe bounded FIFO of float32 samples; keeps the newest audio on overflow."""

    def __init__(self, capacity: int):
        self.capacity = max(256, int(capacity))
        self._buf = np.zeros(self.capacity, dtype=np.float32)
        self._start = 0
        self._size = 0
        self._lock = threading.Lock()
        self.dropped = 0

    def write(self, data: np.ndarray) -> None:
        if data.size == 0:
            return
        with self._lock:
            if data.size >= self.capacity:
                self.dropped += self._size + data.size - self.capacity
                data = data[-self.capacity:]
                self._buf[:] = data
                self._start, self._size = 0, self.capacity
                return
            overflow = self._size + data.size - self.capacity
            if overflow > 0:
                self._start = (self._start + overflow) % self.capacity
                self._size -= overflow
                self.dropped += overflow
            end = (self._start + self._size) % self.capacity
            first = min(data.size, self.capacity - end)
            self._buf[end:end + first] = data[:first]
            if first < data.size:
                self._buf[:data.size - first] = data[first:]
            self._size += data.size

    def read(self, frames: int) -> np.ndarray:
        with self._lock:
            n = min(frames, self._size)
            if n == 0:
                return np.zeros(0, dtype=np.float32)
            first = min(n, self.capacity - self._start)
            out = np.empty(n, dtype=np.float32)
            out[:first] = self._buf[self._start:self._start + first]
            if first < n:
                out[first:] = self._buf[:n - first]
            self._start = (self._start + n) % self.capacity
            self._size -= n
            return out

    def discard(self, frames: int) -> None:
        with self._lock:
            n = min(frames, self._size)
            self._start = (self._start + n) % self.capacity
            self._size -= n

    def clear(self) -> None:
        with self._lock:
            self._start = self._size = 0

    @property
    def available(self) -> int:
        return self._size


class LiveReader(SourceReader):
    """Reader fed by a live source; pads underflows with silence and bounds latency."""

    def __init__(self, sample_rate: int, prebuffer_seconds: float = 0.04, capacity_seconds: float = 1.0):
        self.sample_rate = sample_rate
        self.buffer = RingBuffer(int(sample_rate * capacity_seconds))
        self.target = int(sample_rate * prebuffer_seconds)
        self._primed = False
        self._closed = False
        self.resampler = None

    def push(self, data: np.ndarray) -> None:
        if not self._closed:
            self.buffer.write(data)

    def read(self, frames: int) -> np.ndarray:
        if self._closed:
            return np.zeros(0, dtype=np.float32)
        excess = self.buffer.available - (self.target * 4 + frames)
        if excess > 0:  # clock drift: drop the oldest audio to keep latency bounded
            self.buffer.discard(excess)
        if not self._primed:
            if self.buffer.available < self.target:
                return np.zeros(frames, dtype=np.float32)
            self._primed = True
        data = self.buffer.read(frames)
        if data.size < frames:
            self._primed = False
            data = np.concatenate([data, np.zeros(frames - data.size, dtype=np.float32)])
        return data

    def activate(self) -> None:
        self.buffer.clear()
        self._primed = False

    @property
    def finished(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True
