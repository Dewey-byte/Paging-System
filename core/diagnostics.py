"""Self-test of every subsystem (``main.py --self-test``) for deployment checks and support."""
from __future__ import annotations

import json
import platform
import tempfile
import time
import traceback
from pathlib import Path


def _check(results: list, name: str, fn) -> None:
    started = time.perf_counter()
    try:
        detail = fn()
        results.append({"check": name, "ok": True, "detail": detail,
                        "ms": round((time.perf_counter() - started) * 1000)})
    except Exception as exc:  # noqa: BLE001 - every failure is reported, never raised
        results.append({"check": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}",
                        "trace": traceback.format_exc(limit=4),
                        "ms": round((time.perf_counter() - started) * 1000)})


def run_self_test(work_dir: str | Path | None = None) -> dict:
    work = Path(work_dir or tempfile.mkdtemp(prefix="sps_selftest_"))
    work.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    def database():
        from database.database import Database
        from database.migrations import migrate
        db = Database(work / "selftest.db")
        version = migrate(db)
        status = db.integrity_check()
        db.close()
        return f"schema v{version}, integrity {status}"

    def audio_devices():
        from services.device_detection import DeviceRegistry, sd
        if sd is None:
            raise RuntimeError("sounddevice/PortAudio not available")
        reg = DeviceRegistry()
        out, mic = reg.default_output(), reg.default_input()
        return (f"{sd.get_portaudio_version()[1]}; {reg.active_host_api}; {len(reg.outputs())} outputs, "
                f"{len(reg.inputs())} inputs; default out: {out.name if out else 'none'}; "
                f"default mic: {mic.name if mic else 'none'}")

    def decoder():
        import numpy as np
        import soundfile as sf
        from services.audio_decoder import decode, write_wav
        tone = (0.3 * np.sin(np.linspace(0, 880 * np.pi, 22050))).astype(np.float32)
        write_wav(work / "tone.wav", tone, 22050)
        data, rate = decode(work / "tone.wav")
        mp3 = "MP3" in sf.available_formats()
        if mp3:
            sf.write(str(work / "tone.mp3"), tone, 22050, format="MP3")
            decode(work / "tone.mp3")
        return f"libsndfile {sf.__libsndfile_version__}; WAV ok ({data.size} samples @ {rate}); MP3 {'ok' if mp3 else 'unsupported'}"

    def sounds():
        from services import sound_generator
        for key in sound_generator.BUILTIN_SOUNDS:
            sound_generator.generate(key)
        return f"{len(sound_generator.BUILTIN_SOUNDS)} built-in sounds synthesized"

    def tts():
        from services.text_to_speech import TTSService
        service = TTSService(work / "tts")
        if not service.available:
            raise RuntimeError(service.error)
        voices = service.voices()
        path = service.synthesize("Self test.", service.default_voice_id())
        return f"{len(voices)} voice(s): {', '.join(v.label for v in voices[:4])}; rendered {path.stat().st_size} bytes"

    def pdf():
        from services.reporting import export_history_pdf
        from database.models import BroadcastRecord
        record = BroadcastRecord(1, "Self test", "test", "Diagnostics", 5, "manual", None, "System", "Test zone", [],
                                 "2026-01-01 08:00:00", "2026-01-01 08:00:05", 5.0, "completed", "", "System", "", None,
                                 "")
        path = export_history_pdf([record], work / "selftest.pdf", "Self test", "none", {"total": 1}, {}, {})
        return f"PDF {path.stat().st_size} bytes"

    def engine():
        import numpy as np
        from services.audio_engine import AudioEngine
        from services.audio_output import OutputTarget
        from services.audio_sources import BufferSource
        from services.device_detection import DeviceRegistry
        eng = AudioEngine(DeviceRegistry(), force_simulated=True)
        eng.start(BufferSource(np.zeros(4800, dtype=np.float32), 48000), [OutputTarget("sim", 0, "Sim", "simulated")])
        deadline = time.monotonic() + 3
        while eng.poll().active and time.monotonic() < deadline:
            time.sleep(0.01)
        if eng.active:
            raise RuntimeError("simulated playback did not finish")
        return "simulated 0.1 s broadcast completed"

    for name, fn in (("database", database), ("audio devices", audio_devices), ("decoder", decoder),
                     ("built-in sounds", sounds), ("text-to-speech", tts), ("pdf reports", pdf),
                     ("audio engine", engine)):
        _check(results, name, fn)
    from . import paths
    report = {"app": paths.APP_NAME, "version": paths.APP_VERSION, "frozen": paths.is_frozen(),
              "python": platform.python_version(), "os": f"{platform.system()} {platform.release()}",
              "ok": all(r["ok"] for r in results), "checks": results}
    (work / "self_test.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
