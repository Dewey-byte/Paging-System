"""Integration layer for network audio endpoints (IP speakers, network audio adapters).

Network paging hardware speaks many vendor protocols.  This module defines a
driver interface so a manufacturer's documented protocol (SIP paging, HTTP
API, proprietary multicast...) can be added without touching the rest of the
system.  Shipped drivers:

* ``RtpStreamDriver`` - standards-based RTP (RFC 3550) carrying G.711 mu-law
  audio (payload type 0) to a unicast or multicast address.  Many IP paging
  devices can *listen* to such a stream, but compatibility depends on the
  device configuration; delivery cannot be verified from this computer.
* ``SipPagingDriver`` / ``HttpApiDriver`` - placeholders that report the
  integration as pending instead of pretending to work.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import struct
import threading
import time
from abc import ABC, abstractmethod

import numpy as np

from .audio_output import OutputSession

STATUS_UNVERIFIED = "unverified"
STATUS_REACHABLE = "reachable"
STATUS_UNREACHABLE = "unreachable"
STATUS_UNSUPPORTED = "unsupported"


def linear_to_ulaw(samples: np.ndarray) -> bytes:
    """Vectorised ITU-T G.711 mu-law encoder."""
    pcm = np.clip(np.asarray(samples, dtype=np.float32) * 32767.0, -32768, 32767).astype(np.int32)
    sign = np.where(pcm < 0, 0x80, 0x00)
    magnitude = np.minimum(np.abs(pcm), 32635) + 0x84
    exponent = np.clip(np.floor(np.log2(magnitude)).astype(np.int32) - 7, 0, 7)
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa) & 0xFF).astype(np.uint8).tobytes()


def ulaw_to_linear(data: bytes) -> np.ndarray:
    u = ~np.frombuffer(data, dtype=np.uint8).astype(np.int32) & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    magnitude = ((mantissa << 3) + 0x84) << exponent
    value = magnitude - 0x84
    return (np.where(sign, -value, value) / 32768.0).astype(np.float32)


class NetworkAudioDriver(ABC):
    transport = ""
    label = ""
    streams_audio = False

    @abstractmethod
    def probe(self, address: str, port: int, config: dict) -> tuple[str, str]:
        """Return (status, detail).  Never claims audio delivery that cannot be verified."""

    def create_session(self, target, source, master) -> OutputSession:
        raise NotImplementedError(f"{self.label} does not support audio streaming yet.")


class RtpStreamDriver(NetworkAudioDriver):
    transport = "rtp"
    label = "RTP stream (G.711 mu-law)"
    streams_audio = True

    def probe(self, address: str, port: int, config: dict) -> tuple[str, str]:
        if not address or not port:
            return STATUS_UNSUPPORTED, "Address and port are required."
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return STATUS_UNVERIFIED, "Hostname configured; reachability not checked."
        if ip.is_multicast:
            return STATUS_UNVERIFIED, "Multicast stream - delivery cannot be verified from this computer."
        health_port = int(config.get("health_port") or 0)
        if not health_port:
            return STATUS_UNVERIFIED, "Unicast stream - add a health-check port to verify the host responds."
        try:
            with socket.create_connection((address, health_port), timeout=0.6):
                return STATUS_REACHABLE, f"Host answered on TCP port {health_port} (audio not verified)."
        except OSError as exc:
            return STATUS_UNREACHABLE, f"No answer on TCP port {health_port}: {exc.strerror or exc}"

    def create_session(self, target, source, master) -> OutputSession:
        return RtpOutputSession(target, source, master)


class SipPagingDriver(NetworkAudioDriver):
    transport = "sip"
    label = "SIP paging (integration pending)"

    def probe(self, address: str, port: int, config: dict) -> tuple[str, str]:
        return STATUS_UNSUPPORTED, ("SIP paging needs the speaker manufacturer's SIP/registration details; "
                                    "this integration is not implemented yet.")


class HttpApiDriver(NetworkAudioDriver):
    transport = "http"
    label = "Vendor HTTP API (integration pending)"

    def probe(self, address: str, port: int, config: dict) -> tuple[str, str]:
        return STATUS_UNSUPPORTED, ("Vendor HTTP control requires the manufacturer's documented API; "
                                    "add a driver for your hardware model.")


DRIVERS: dict[str, NetworkAudioDriver] = {d.transport: d for d in (RtpStreamDriver(), SipPagingDriver(),
                                                                    HttpApiDriver())}


class RtpOutputSession(OutputSession):
    """Sends 20 ms G.711 mu-law RTP packets in real time."""

    PACKET = 160  # 20 ms at 8 kHz

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thread: threading.Thread | None = None
        self._abort = False
        self._sock: socket.socket | None = None

    def start(self) -> None:
        address = self.target.config.get("address", "")
        port = int(self.target.config.get("port") or 0)
        if not address or not port:
            raise RuntimeError(f"'{self.target.device_name}' has no destination address/port configured.")
        self.sample_rate = 8000
        self.reader = self.source.open_reader(self.sample_rate)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ttl = int(self.target.config.get("ttl") or 16)
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", max(1, min(ttl, 255))))
        self._dest = (address, port)
        self._ssrc = struct.unpack("!I", os.urandom(4))[0]
        self._thread = threading.Thread(target=self._run, name=f"rtp-{self.target.key}", daemon=True)
        self.last_activity = time.monotonic()
        self.started = True
        self._thread.start()

    def _run(self) -> None:
        seq = int.from_bytes(os.urandom(2), "big")
        timestamp = int.from_bytes(os.urandom(4), "big")
        period = self.PACKET / self.sample_rate
        deadline = time.monotonic()
        first = True
        try:
            while not self._abort:
                payload = linear_to_ulaw(self.render(self.PACKET))
                header = struct.pack("!BBHII", 0x80, (0x80 if first else 0x00) | 0, seq & 0xFFFF,
                                     timestamp & 0xFFFFFFFF, self._ssrc)
                try:
                    self._sock.sendto(header + payload, self._dest)
                except OSError as exc:
                    self.error = f"Network send failed: {exc}"
                    break
                first = False
                seq += 1
                timestamp += self.PACKET
                self.last_activity = time.monotonic()
                if self.done:
                    break
                deadline += period
                delay = deadline - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -0.5:
                    deadline = time.monotonic()
        finally:
            self.finished = True

    def abort(self) -> None:
        self._abort = True

    def close(self) -> None:
        self._abort = True
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=0.5)
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        super().close()
