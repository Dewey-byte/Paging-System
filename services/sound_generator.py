"""Procedurally synthesised chimes, bells and emergency alarm signals.

Generating the built-in sounds avoids shipping third-party recordings.  The
alarm patterns follow common evacuation-signal conventions (temporal-3,
slow whoop, hi-lo, wail) but are not certified life-safety signals.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from .audio_decoder import normalize, write_wav

SR = 44100


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(SR * seconds)) / SR


def _fade(signal: np.ndarray, fade_in: float = 0.005, fade_out: float = 0.01) -> np.ndarray:
    out = signal.copy()
    n_in, n_out = int(SR * fade_in), int(SR * fade_out)
    if n_in:
        out[:n_in] *= np.linspace(0, 1, n_in)
    if n_out:
        out[-n_out:] *= np.linspace(1, 0, n_out)
    return out


def _bell_note(freq: float, seconds: float, decay: float = 1.4, brightness: float = 1.0) -> np.ndarray:
    t = _t(seconds)
    partials = [(1.0, 1.0, 1.0), (2.0, 0.42 * brightness, 1.6), (3.0, 0.18 * brightness, 2.2),
                (4.2, 0.10 * brightness, 3.0), (5.4, 0.05 * brightness, 3.6)]
    out = np.zeros_like(t)
    for ratio, amp, dk in partials:
        out += amp * np.sin(2 * np.pi * freq * ratio * t) * np.exp(-t * dk * decay)
    return _fade(out, 0.004, 0.05)


def _sequence(notes: list[tuple[float, float]], spacing: float, ring: float, decay: float = 1.4) -> np.ndarray:
    total = spacing * (len(notes) - 1) + ring
    out = np.zeros(int(SR * total) + 1)
    for i, (freq, amp) in enumerate(notes):
        note = _bell_note(freq, ring, decay) * amp
        start = int(SR * spacing * i)
        out[start:start + note.size] += note[: out.size - start]
    return out


def _sweep_phase(freq_curve: np.ndarray) -> np.ndarray:
    return 2 * np.pi * np.cumsum(freq_curve) / SR


def _horn(phase: np.ndarray) -> np.ndarray:
    """Band-limited, horn-like timbre (odd harmonics) from a phase track."""
    wave = sum(np.sin(k * phase) / k for k in (1, 3, 5, 7))
    return np.tanh(1.6 * wave) / np.tanh(1.6)


# ----------------------------------------------------------------- chimes/bells
def chime_pre() -> np.ndarray:
    return _sequence([(392.00, 1.0), (523.25, 0.95), (659.25, 0.9), (783.99, 0.95)], 0.42, 1.9)


def chime_post() -> np.ndarray:
    return _sequence([(783.99, 0.95), (659.25, 0.9), (523.25, 0.95), (392.00, 1.0)], 0.42, 1.9)


def chime_ding_dong() -> np.ndarray:
    return _sequence([(659.25, 1.0), (523.25, 1.0)], 0.65, 2.2, decay=1.1)


def bell_class() -> np.ndarray:
    """Electric school bell: a metallic gong struck ~18 times per second."""
    seconds = 4.0
    t = _t(seconds)
    base = 1180.0
    tone = sum(a * np.sin(2 * np.pi * base * r * t) for r, a in ((1.0, 1.0), (2.76, 0.5), (5.40, 0.25), (8.93, 0.12)))
    strike = 0.55 + 0.45 * np.abs(np.sin(np.pi * 18 * t)) ** 0.6
    return _fade(tone * strike, 0.01, 0.35)


def bell_dismissal() -> np.ndarray:
    """Westminster-style quarter chime (traditional, public-domain melody)."""
    gs4, fs4, e4, b3 = 415.30, 369.99, 329.63, 246.94
    notes = [gs4, fs4, e4, b3, e4, gs4, fs4, b3]
    out = _sequence([(f, 1.0) for f in notes], 0.62, 2.4, decay=0.9)
    return out


def test_tone() -> np.ndarray:
    t = _t(1.6)
    tone = 0.8 * np.sin(2 * np.pi * 1000 * t)
    tone[int(SR * 0.7):int(SR * 0.9)] = 0
    return _fade(tone, 0.01, 0.05)


# ------------------------------------------------------------------- alarms
def alarm_fire() -> np.ndarray:
    """Temporal-3: three 0.5 s pulses, 0.5 s apart, then 1.5 s silence (two cycles)."""
    cycle = np.zeros(int(SR * 4.0))
    pulse = _fade(_horn(_sweep_phase(np.full(int(SR * 0.5), 950.0))), 0.01, 0.02)
    for i in range(3):
        start = int(SR * i * 1.0)
        cycle[start:start + pulse.size] = pulse
    return np.concatenate([cycle, cycle])


def alarm_earthquake() -> np.ndarray:
    t = _t(6.0)
    freq = 900 + 320 * np.sin(2 * np.pi * 7 * t)
    return _fade(_horn(_sweep_phase(freq)), 0.02, 0.05)


def alarm_evacuation() -> np.ndarray:
    """Slow whoop: 500 -> 1200 Hz rising sweep over 3.5 s, then 0.5 s pause (two cycles)."""
    sweep_t = _t(3.5)
    freq = 500 * (1200 / 500) ** (sweep_t / 3.5)
    whoop = _fade(_horn(_sweep_phase(freq)), 0.02, 0.03)
    cycle = np.concatenate([whoop, np.zeros(int(SR * 0.5))])
    return np.concatenate([cycle, cycle])


def alarm_weather() -> np.ndarray:
    rise = np.linspace(320, 1000, int(SR * 2.4))
    hold = np.full(int(SR * 1.2), 1000.0)
    fall = np.linspace(1000, 320, int(SR * 2.4))
    freq = np.concatenate([rise, hold, fall])
    return _fade(np.sin(_sweep_phase(freq)) * 0.7 + 0.3 * np.sin(2 * _sweep_phase(freq)), 0.05, 0.1)


def alarm_security() -> np.ndarray:
    seg = int(SR * 0.5)
    freq = np.concatenate([np.full(seg, 960.0), np.full(seg, 770.0)] * 4)
    return _fade(_horn(_sweep_phase(freq)), 0.01, 0.03)


def alarm_lockdown() -> np.ndarray:
    """Lockdown yelp: rapid 700 -> 1600 Hz sweeps (4 per second) in 2.5 s bursts with 0.75 s gaps.

    Deliberately unlike the evacuation signals so that nobody mistakes a
    shelter-in-place lockdown (e.g. a shooting incident) for an order to leave.
    """
    burst_t = _t(2.5)
    sweep = (burst_t * 4.0) % 1.0
    freq = 700 + 900 * sweep ** 0.8
    burst = _fade(_horn(_sweep_phase(freq)), 0.01, 0.04)
    cycle = np.concatenate([burst, np.zeros(int(SR * 0.75))])
    return np.concatenate([cycle, cycle])


def alarm_general() -> np.ndarray:
    pulse = _fade(_horn(_sweep_phase(np.full(int(SR * 0.2), 1050.0))), 0.005, 0.01)
    gap = np.zeros(int(SR * 0.1))
    burst = np.concatenate([np.concatenate([pulse, gap]) for _ in range(10)])
    return np.concatenate([burst, np.zeros(int(SR * 1.0)), burst])


BUILTIN_SOUNDS: dict[str, tuple[str, str, Callable[[], np.ndarray], float]] = {
    # key: (display name, category, generator, peak dBFS)
    "chime_pre": ("Pre-announcement Chime", "Bell Sounds", chime_pre, -3.0),
    "chime_post": ("Post-announcement Chime", "Bell Sounds", chime_post, -3.0),
    "chime_ding_dong": ("Ding-Dong Chime", "Bell Sounds", chime_ding_dong, -3.0),
    "bell_class": ("Class Bell", "Bell Sounds", bell_class, -2.0),
    "bell_dismissal": ("Dismissal Melody", "Bell Sounds", bell_dismissal, -3.0),
    "alarm_fire": ("Fire Alarm (Temporal-3)", "Emergency Alarms", alarm_fire, -1.0),
    "alarm_earthquake": ("Earthquake Warble", "Emergency Alarms", alarm_earthquake, -1.0),
    "alarm_evacuation": ("Evacuation Slow Whoop", "Emergency Alarms", alarm_evacuation, -1.0),
    "alarm_weather": ("Weather Wail", "Emergency Alarms", alarm_weather, -1.0),
    "alarm_security": ("Security Hi-Lo", "Emergency Alarms", alarm_security, -1.0),
    "alarm_lockdown": ("Lockdown Yelp", "Emergency Alarms", alarm_lockdown, -1.0),
    "alarm_general": ("General Emergency Pulse", "Emergency Alarms", alarm_general, -1.0),
    "test_tone": ("Zone Test Tone", "General Audio", test_tone, -6.0),
}

ALARM_PATTERNS = {key: spec[0] for key, spec in BUILTIN_SOUNDS.items() if key.startswith("alarm_")}
CHIME_SOUNDS = {key: spec[0] for key, spec in BUILTIN_SOUNDS.items() if key.startswith(("chime_", "bell_"))}


def generate(key: str) -> tuple[np.ndarray, int]:
    if key not in BUILTIN_SOUNDS:
        raise KeyError(f"Unknown built-in sound: {key}")
    _, _, generator, peak = BUILTIN_SOUNDS[key]
    return normalize(generator().astype(np.float32), peak), SR


def write_builtin(key: str, folder: Path) -> Path:
    data, rate = generate(key)
    path = Path(folder) / f"{key}.wav"
    write_wav(path, data, rate)
    return path
