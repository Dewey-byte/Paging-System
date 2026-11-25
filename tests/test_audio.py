"""Audio DSP, sources, decoding and the simulated output engine."""
from __future__ import annotations

import time

import numpy as np
import pytest

from services import sound_generator
from services.audio_decoder import AudioCache, StreamResampler, decode, probe, resample, write_wav
from services.audio_engine import AudioEngine
from services.audio_input import FeedbackGuard, HighPassFilter, gain_db_from_slider
from services.audio_output import OutputTarget, soft_limit
from services.audio_sources import (AudioSource, BufferSource, LiveReader, ProgramSource, RepeatSource, RingBuffer,
                                   SequenceSource, SilenceSource)
from services.device_detection import DeviceRegistry, SystemDevice
from services.network_audio import linear_to_ulaw, ulaw_to_linear


def test_builtin_sounds_are_valid():
    for key in sound_generator.BUILTIN_SOUNDS:
        data, rate = sound_generator.generate(key)
        assert rate == 44100 and data.dtype == np.float32 and data.size > rate
        assert 0.3 < float(np.max(np.abs(data))) <= 1.0, key


def test_resample_preserves_length_and_energy():
    t = np.arange(44100) / 44100
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    out = resample(tone, 44100, 48000)
    assert out.size == 48000
    assert abs(np.sqrt(np.mean(out[2000:-4000] ** 2)) - np.sqrt(0.125)) < 0.01


def test_stream_resampler_is_continuous():
    t = np.arange(48000) / 44100
    tone = np.sin(2 * np.pi * 300 * t).astype(np.float32)
    rs = StreamResampler(44100, 48000)
    out = np.concatenate([rs.process(tone[i:i + 441]) for i in range(0, tone.size, 441)])
    assert abs(out.size - tone.size * 48000 / 44100) < 3
    assert np.max(np.abs(np.diff(out))) < 0.06  # no discontinuities between chunks


def test_ring_buffer_keeps_newest_audio():
    rb = RingBuffer(1000)
    rb.write(np.arange(1500, dtype=np.float32))
    assert rb.available == 1000 and rb.dropped == 500
    assert rb.read(2).tolist() == [500.0, 501.0]


def test_live_reader_pads_and_bounds_latency():
    reader = LiveReader(48000, prebuffer_seconds=0.01)
    assert np.all(reader.read(256) == 0)  # not primed yet
    reader.push(np.ones(48000, dtype=np.float32))
    out = reader.read(480)
    assert out.size == 480 and out[0] == 1.0
    assert reader.buffer.available <= reader.target * 4 + 480


def test_sequence_and_repeat_durations():
    beep = BufferSource(np.ones(4800, dtype=np.float32), 48000, "beep")  # 0.1 s
    seq = SequenceSource([beep, SilenceSource(0.2), beep])
    assert seq.duration == pytest.approx(0.4, abs=1e-6)
    rep = RepeatSource(seq, count=3, gap_seconds=0.1)
    assert rep.duration == pytest.approx(1.4, abs=1e-6)
    reader = rep.open_reader(48000)
    total = 0
    while not reader.finished:
        total += reader.read(1000).size
    assert total == pytest.approx(48000 * 1.4, abs=2)
    capped = RepeatSource(seq, count=0, max_seconds=1.0).open_reader(48000)
    total = 0
    while not capped.finished:
        total += capped.read(997).size
    assert total == 48000


def test_mulaw_roundtrip():
    x = (0.8 * np.sin(np.linspace(0, 20, 1600))).astype(np.float32)
    y = ulaw_to_linear(linear_to_ulaw(x))
    assert y.size == x.size and np.max(np.abs(x - y)) < 0.03


def test_soft_limiter_and_filters():
    x = np.array([0.5, 0.95, -2.0, 3.0], dtype=np.float32)
    soft_limit(x)
    assert np.max(np.abs(x)) <= 1.0 and x[0] == 0.5
    hpf = HighPassFilter(48000)
    assert np.max(np.abs(hpf.process(np.full(48000, 0.5, dtype=np.float32))[-500:])) < 1e-3
    assert gain_db_from_slider(50) == 0 and gain_db_from_slider(100) == pytest.approx(24)


def test_feedback_guard_detects_sustained_howl():
    guard = FeedbackGuard(48000)
    t = np.arange(48000) / 48000
    howl = (0.6 * np.sin(2 * np.pi * 2500 * t)).astype(np.float32)
    assert any(guard.process(howl[i:i + 4096]) for i in range(0, howl.size, 4096))
    speechlike = FeedbackGuard(48000)
    noise = (np.random.default_rng(1).standard_normal(48000) * 0.2).astype(np.float32)
    assert not any(speechlike.process(noise[i:i + 4096]) for i in range(0, noise.size, 4096))


def test_wav_and_mp3_decode(tmp_path):
    data, rate = sound_generator.generate("chime_ding_dong")
    wav = tmp_path / "chime.wav"
    write_wav(wav, data, rate)
    info = probe(wav)
    assert info.sample_rate == 44100 and abs(info.duration - data.size / rate) < 0.01
    decoded, r2 = decode(wav)
    assert r2 == rate and decoded.size == data.size
    import soundfile as sf
    if "MP3" in sf.available_formats():
        mp3 = tmp_path / "chime.mp3"
        sf.write(str(mp3), data, rate, format="MP3")
        mp3_data, mp3_rate = AudioCache().load(mp3)
        assert mp3_rate == rate and abs(mp3_data.size - data.size) < rate * 0.1


def test_simulated_engine_plays_to_completion_and_maps_channels():
    engine = AudioEngine(DeviceRegistry(), force_simulated=True)
    engine.set_master_volume(100)
    src = BufferSource(np.full(9600, 0.5, dtype=np.float32), 48000, "test")  # 0.2 s
    targets = [OutputTarget("a", 1, "Sim A", "simulated", open_channels=8, channel_map=[2], gain=0.5),
               OutputTarget("b", 2, "Local forced", "local", open_channels=2, gain=1.0)]
    report = engine.start(src, targets)
    assert report.ok and report.simulated_count == 2 and report.real_count == 0
    start = time.monotonic()
    while engine.poll().active:
        time.sleep(0.01)
    elapsed = time.monotonic() - start
    assert 0.15 < elapsed < 1.0


def test_engine_stop_is_fast():
    engine = AudioEngine(DeviceRegistry(), force_simulated=True)
    src = RepeatSource(BufferSource(np.ones(4800, dtype=np.float32), 48000), count=0)
    engine.start(src, [OutputTarget("a", 1, "Sim", "simulated")])
    time.sleep(0.1)
    engine.stop()
    start = time.monotonic()
    while engine.poll().active:
        time.sleep(0.005)
    assert time.monotonic() - start < 0.5


def test_device_name_matching():
    devices = [SystemDevice(1, "Speakers (Realtek(R) Audio)", "MME", 0, 2, 48000),
               SystemDevice(2, "Headphones (Realtek(R) Audio)", "MME", 0, 2, 48000)]
    assert DeviceRegistry._match("speakers (realtek(r) audio)", devices).index == 1
    assert DeviceRegistry._match("Headphones (Realtek(R) Au", devices).index == 2  # MME-truncated name
    assert DeviceRegistry._match("USB Mixer", devices) is None


class _SilentLive(AudioSource):
    """Stand-in for the microphone: a live source that never receives audio."""
    is_live = True
    name = "fake mic"

    def open_reader(self, sample_rate):
        return LiveReader(sample_rate)


def test_program_source_plays_outro_after_finish():
    intro = BufferSource(np.full(480, 0.25, dtype=np.float32), 48000, "intro")
    outro = BufferSource(np.full(960, 0.5, dtype=np.float32), 48000, "outro")
    program = ProgramSource(_SilentLive(), intro, outro)
    reader = program.open_reader(48000)
    first = reader.read(480)
    assert np.all(first == 0.25)                      # intro plays first
    for _ in range(5):                                # live part is open-ended
        assert reader.read(256).size == 256 and not reader.finished
    program.finish()
    out = reader.read(4096)
    assert out.size == 960 and np.all(out == 0.5)     # mic closed, then exactly the outro
    assert reader.finished and reader.read(256).size == 0


def test_program_source_skips_outro_when_stopped_during_intro():
    intro = BufferSource(np.full(4800, 0.25, dtype=np.float32), 48000, "intro")
    outro = BufferSource(np.full(960, 0.5, dtype=np.float32), 48000, "outro")
    program = ProgramSource(_SilentLive(), intro, outro)
    reader = program.open_reader(48000)
    reader.read(100)
    program.finish()                                  # nobody spoke yet
    assert reader.read(4096).size == 0 and reader.finished


def test_lockdown_pattern_is_distinct_and_audible():
    data, rate = sound_generator.generate("alarm_lockdown")
    assert 6.0 < data.size / rate < 7.0
    assert "alarm_lockdown" in sound_generator.ALARM_PATTERNS
    other, _ = sound_generator.generate("alarm_security")
    n = min(data.size, other.size)
    assert abs(float(np.corrcoef(data[:n], other[:n])[0, 1])) < 0.5
