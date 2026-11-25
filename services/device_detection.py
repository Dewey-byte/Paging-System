"""Detection of real audio hardware through PortAudio (sounddevice).

Only devices reported by the operating system are ever shown as *connected*.
Device indices change when hardware is plugged or unplugged, so configured
devices are stored by name + host API and re-resolved on every use.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)

try:  # PortAudio may be missing on unusual systems; the app then runs in simulation only.
    import sounddevice as sd
    SOUNDDEVICE_ERROR = ""
except Exception as exc:  # pragma: no cover - depends on the machine
    sd = None
    SOUNDDEVICE_ERROR = str(exc)

PREFERRED_HOST_APIS = ("Windows WASAPI", "Windows DirectSound", "MME")


@dataclass(frozen=True)
class SystemDevice:
    index: int
    name: str
    host_api: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float
    is_default_input: bool = False
    is_default_output: bool = False

    @property
    def samplerate(self) -> int:
        return int(self.default_samplerate or 48000)

    @property
    def label(self) -> str:
        return f"{self.name}"


class StreamTracker:
    """Counts open PortAudio streams so PortAudio is never re-initialised under them."""

    def __init__(self):
        self._lock = threading.Lock()
        self._count = 0

    def acquire(self) -> None:
        with self._lock:
            self._count += 1

    def release(self) -> None:
        with self._lock:
            self._count = max(0, self._count - 1)

    @property
    def count(self) -> int:
        return self._count


STREAMS = StreamTracker()


def _winmm_counts() -> tuple[int, int]:
    """Cheap device-count probe used to notice hot-plug events without re-initialising PortAudio."""
    if sys.platform != "win32":
        return (0, 0)
    try:
        winmm = ctypes.windll.winmm
        return int(winmm.waveOutGetNumDevs()), int(winmm.waveInGetNumDevs())
    except Exception:
        return (0, 0)


class DeviceRegistry:
    def __init__(self, preferred_host_api: str = "auto"):
        self.preferred = preferred_host_api or "auto"
        self.available = sd is not None
        self.error = SOUNDDEVICE_ERROR
        self._devices: list[SystemDevice] = []
        self._host_apis: list[str] = []
        self._active_api = ""
        self._counts = _winmm_counts()
        self.refresh(reinitialize=False)

    # ------------------------------------------------------------- scanning
    def refresh(self, reinitialize: bool = True) -> bool:
        """Re-scan hardware. Returns True when the device list changed."""
        if sd is None:
            return False
        before = self.signature()
        try:
            if reinitialize:
                if STREAMS.count > 0:
                    return False  # never pull PortAudio from under an open stream
                sd._terminate()
                sd._initialize()
            host_apis = sd.query_hostapis()
            devices = sd.query_devices()
        except Exception as exc:
            self.error = f"Audio subsystem error: {exc}"
            log.exception("Device scan failed")
            self._devices = []
            return before != self.signature()
        self.error = ""
        self._host_apis = [h["name"] for h in host_apis]
        api_index = self._choose_api(host_apis)
        self._active_api = host_apis[api_index]["name"] if api_index is not None else ""
        default_in = host_apis[api_index]["default_input_device"] if api_index is not None else -1
        default_out = host_apis[api_index]["default_output_device"] if api_index is not None else -1
        result = []
        for index, info in enumerate(devices):
            if api_index is not None and info["hostapi"] != api_index:
                continue
            result.append(SystemDevice(
                index=index, name=str(info["name"]).strip(), host_api=self._active_api,
                max_input_channels=int(info["max_input_channels"]),
                max_output_channels=int(info["max_output_channels"]),
                default_samplerate=float(info["default_samplerate"] or 48000),
                is_default_input=index == default_in, is_default_output=index == default_out))
        self._devices = result
        self._counts = _winmm_counts()
        return before != self.signature()

    def _choose_api(self, host_apis) -> int | None:
        names = [h["name"] for h in host_apis]
        if self.preferred != "auto" and self.preferred in names:
            return names.index(self.preferred)
        for name in PREFERRED_HOST_APIS:
            if name in names:
                return names.index(name)
        return 0 if names else None

    def hotplug_suspected(self) -> bool:
        """True when the OS device count changed since the last scan."""
        return _winmm_counts() != self._counts

    def set_preferred_host_api(self, name: str) -> None:
        self.preferred = name or "auto"
        self.refresh(reinitialize=False)

    def signature(self) -> tuple:
        return tuple((d.name, d.max_input_channels, d.max_output_channels) for d in self._devices)

    # -------------------------------------------------------------- queries
    @property
    def host_apis(self) -> list[str]:
        return list(self._host_apis)

    @property
    def active_host_api(self) -> str:
        return self._active_api

    def outputs(self) -> list[SystemDevice]:
        return [d for d in self._devices if d.max_output_channels > 0]

    def inputs(self) -> list[SystemDevice]:
        return [d for d in self._devices if d.max_input_channels > 0]

    def default_output(self) -> SystemDevice | None:
        outs = self.outputs()
        return next((d for d in outs if d.is_default_output), outs[0] if outs else None)

    def default_input(self) -> SystemDevice | None:
        ins = self.inputs()
        return next((d for d in ins if d.is_default_input), ins[0] if ins else None)

    def find_output(self, name: str) -> SystemDevice | None:
        return self.default_output() if not name else self._match(name, self.outputs())

    def find_input(self, name: str) -> SystemDevice | None:
        return self.default_input() if not name else self._match(name, self.inputs())

    @staticmethod
    def _match(name: str, candidates: list[SystemDevice]) -> SystemDevice | None:
        wanted = name.strip().lower()
        for device in candidates:
            if device.name.lower() == wanted:
                return device
        # MME truncates names to 31 characters; accept unambiguous prefix matches.
        if len(wanted) >= 12:
            prefixed = [d for d in candidates if d.name.lower().startswith(wanted) or wanted.startswith(d.name.lower())]
            if len(prefixed) == 1:
                return prefixed[0]
        return None
