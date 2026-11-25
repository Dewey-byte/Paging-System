"""Audio zones, output devices, hardware status and zone-to-endpoint routing."""
from __future__ import annotations

from dataclasses import dataclass

from database.models import AudioDevice, AudioZone
from database.repositories import Repositories
from services.audio_output import OutputTarget
from services.device_detection import DeviceRegistry
from services.network_audio import DRIVERS

from .activity_logger import ActivityLogger
from .authentication import AuthManager
from .errors import NotFoundError, ValidationError
from .events import EventBus
from .permissions import P

DEVICE_TYPES = {
    "local_output": "Local audio output",
    "pa_amplifier": "External PA amplifier",
    "network_adapter": "Network audio adapter",
    "ip_speaker": "IP speaker",
    "simulated": "Simulated device",
}

TRANSPORTS = {
    "local": "Windows sound device",
    "rtp": "RTP stream (G.711 mu-law)",
    "sip": "SIP paging (integration pending)",
    "http": "Vendor HTTP API (integration pending)",
    "simulated": "Simulation (no sound)",
}

# Which transports make sense for each device type.
TYPE_TRANSPORTS = {
    "local_output": ["local"],
    "pa_amplifier": ["local", "rtp"],
    "network_adapter": ["local", "rtp"],
    "ip_speaker": ["rtp", "sip", "http"],
    "simulated": ["simulated"],
}

DEVICE_STATUS_LABELS = {
    "connected": "Connected",
    "disconnected": "Disconnected",
    "simulated": "Simulated",
    "unverified": "Unverified",
    "reachable": "Reachable",
    "unreachable": "Unreachable",
    "unsupported": "Not supported",
    "disabled": "Disabled",
    "unknown": "Unknown",
}

# Zone states shown on maps and selectors.
ZS_BROADCASTING, ZS_READY, ZS_SIMULATED, ZS_UNVERIFIED, ZS_OFFLINE, ZS_DISABLED = (
    "broadcasting", "ready", "simulated", "unverified", "offline", "disabled")

ZONE_STATE_LABELS = {
    ZS_BROADCASTING: "Broadcasting", ZS_READY: "Ready", ZS_SIMULATED: "Simulated",
    ZS_UNVERIFIED: "Unverified", ZS_OFFLINE: "Offline", ZS_DISABLED: "Disabled",
}

PLAYABLE_STATUSES = {"connected", "simulated", "unverified", "reachable"}


@dataclass
class ZoneStatus:
    zone: AudioZone
    device: AudioDevice | None
    state: str
    detail: str

    @property
    def playable(self) -> bool:
        return self.state in (ZS_READY, ZS_SIMULATED, ZS_UNVERIFIED, ZS_BROADCASTING)


class ZoneManager:
    def __init__(self, repos: Repositories, registry: DeviceRegistry, auth: AuthManager,
                 activity: ActivityLogger, bus: EventBus):
        self._repos = repos
        self.registry = registry
        self._auth = auth
        self._activity = activity
        self._bus = bus
        self._live_status: dict[int, tuple[str, str]] = {}

    # ================================================================ zones
    def zones(self, include_disabled: bool = True, include_campus: bool = True) -> list[AudioZone]:
        items = self._repos.zones.all(include_disabled)
        return [z for z in items if include_campus or not z.is_all_campus]

    def physical_zones(self, include_disabled: bool = True) -> list[AudioZone]:
        return self.zones(include_disabled, include_campus=False)

    def campus_zone(self) -> AudioZone | None:
        return next((z for z in self._repos.zones.all() if z.is_all_campus), None)

    def get_zone(self, zone_id: int) -> AudioZone:
        zone = self._repos.zones.get(zone_id)
        if not zone:
            raise NotFoundError("That zone no longer exists.")
        return zone

    def expand_zone_ids(self, zone_ids) -> list[int]:
        """Resolve 'Entire Campus' and drop disabled/unknown zones."""
        zones = {z.id: z for z in self._repos.zones.all()}
        result: list[int] = []
        for zone_id in zone_ids or []:
            zone = zones.get(int(zone_id))
            if zone is None:
                continue
            if zone.is_all_campus:
                result.extend(z.id for z in zones.values() if not z.is_all_campus and z.is_enabled)
            elif zone.is_enabled:
                result.append(zone.id)
        return sorted(set(result))

    def describe_zones(self, zone_ids) -> str:
        zones = {z.id: z for z in self._repos.zones.all()}
        ids = [int(z) for z in zone_ids or [] if int(z) in zones]
        if any(zones[z].is_all_campus for z in ids):
            return "Entire Campus"
        physical = [z for z in zones.values() if not z.is_all_campus and z.is_enabled]
        if physical and {z.id for z in physical} <= set(ids):
            return "Entire Campus"
        return ", ".join(zones[z].name for z in ids) or "No zones"

    def _validate_zone(self, name: str, device_id: int | None, channel: int, volume: int, exclude: int | None):
        if not name.strip():
            raise ValidationError("Enter a zone name.")
        other = self._repos.zones.get_by_name(name.strip())
        if other and other.id != exclude:
            raise ValidationError(f"A zone named '{name.strip()}' already exists.")
        if not 0 <= int(volume) <= 100:
            raise ValidationError("Zone volume must be between 0 and 100.")
        if device_id:
            device = self._repos.devices.get(device_id)
            if not device:
                raise ValidationError("The selected output device no longer exists.")
            if channel and channel > device.channels:
                raise ValidationError(f"'{device.name}' only has {device.channels} output channel(s).")

    def create_zone(self, *, name: str, description: str = "", device_id: int | None = None,
                    output_channel: int = 0, volume: int = 80, is_enabled: bool = True,
                    color: str = "#2563EB") -> AudioZone:
        self._auth.require(P.ZONES_MANAGE)
        self._validate_zone(name, device_id, output_channel, volume, None)
        zone_id = self._repos.zones.create(name=name.strip(), description=description.strip(), device_id=device_id,
                                           output_channel=int(output_channel), volume=int(volume),
                                           is_enabled=is_enabled, color=color)
        self._activity.log("Created zone", "Audio Zones", name.strip())
        self._bus.data_changed.emit("zones")
        return self.get_zone(zone_id)

    def update_zone(self, zone_id: int, *, name: str, description: str, device_id: int | None, output_channel: int,
                    volume: int, is_enabled: bool, color: str) -> AudioZone:
        self._auth.require(P.ZONES_MANAGE)
        zone = self.get_zone(zone_id)
        if zone.is_all_campus:
            self._repos.zones.update(zone_id, description=description.strip(), color=color)
        else:
            self._validate_zone(name, device_id, output_channel, volume, zone_id)
            self._repos.zones.update(zone_id, name=name.strip(), description=description.strip(), device_id=device_id,
                                     output_channel=int(output_channel), volume=int(volume), is_enabled=is_enabled,
                                     color=color)
        self._activity.log("Updated zone", "Audio Zones", name.strip())
        self._bus.data_changed.emit("zones")
        return self.get_zone(zone_id)

    def delete_zone(self, zone_id: int) -> None:
        self._auth.require(P.ZONES_MANAGE)
        zone = self.get_zone(zone_id)
        if zone.is_all_campus:
            raise ValidationError("'Entire Campus' is a built-in group and cannot be deleted.")
        self._repos.zones.delete(zone_id)
        self._activity.log("Deleted zone", "Audio Zones", zone.name, "warning")
        self._bus.data_changed.emit("zones")

    def set_zone_enabled(self, zone_id: int, enabled: bool) -> None:
        self._auth.require(P.ZONES_MANAGE)
        zone = self.get_zone(zone_id)
        if zone.is_all_campus:
            raise ValidationError("'Entire Campus' is always available.")
        self._repos.zones.update(zone_id, is_enabled=enabled)
        self._activity.log("Enabled zone" if enabled else "Disabled zone", "Audio Zones", zone.name)
        self._bus.data_changed.emit("zones")

    def set_zone_volume(self, zone_id: int, volume: int, log: bool = True) -> None:
        self._auth.require(P.ZONES_MANAGE)
        volume = max(0, min(100, int(volume)))
        self._repos.zones.update(zone_id, volume=volume)
        if log:
            self._activity.log("Changed zone volume", "Audio Zones", f"{self.get_zone(zone_id).name}: {volume}%")
        self._bus.data_changed.emit("zones")

    def save_map_layout(self, rects: dict[int, tuple[float, float, float, float]]) -> None:
        self._auth.require(P.ZONES_MANAGE)
        for zone_id, (x, y, w, h) in rects.items():
            self._repos.zones.update(zone_id, map_x=round(x, 2), map_y=round(y, 2), map_w=round(w, 2),
                                     map_h=round(h, 2))
        self._activity.log("Rearranged campus map", "Audio Zones", f"{len(rects)} zones")
        self._bus.data_changed.emit("zones")

    # ============================================================== devices
    def devices(self) -> list[AudioDevice]:
        return self._repos.devices.all()

    def get_device(self, device_id: int) -> AudioDevice:
        device = self._repos.devices.get(device_id)
        if not device:
            raise NotFoundError("That device no longer exists.")
        return device

    def _validate_device(self, data: dict, exclude: int | None) -> dict:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("Enter a device name.")
        other = self._repos.devices.get_by_name(name)
        if other and other.id != exclude:
            raise ValidationError(f"A device named '{name}' already exists.")
        device_type, transport = data.get("device_type"), data.get("transport")
        if device_type not in DEVICE_TYPES:
            raise ValidationError("Select a device type.")
        if transport not in TYPE_TRANSPORTS[device_type]:
            raise ValidationError(f"{TRANSPORTS.get(transport, transport)} is not valid for "
                                  f"{DEVICE_TYPES[device_type].lower()} devices.")
        channels = int(data.get("channels") or 2)
        if not 1 <= channels <= 64:
            raise ValidationError("Channels must be between 1 and 64.")
        if transport in ("rtp", "sip", "http"):
            if not (data.get("address") or "").strip():
                raise ValidationError("Enter the device's IP address or multicast group.")
            if not 1 <= int(data.get("port") or 0) <= 65535:
                raise ValidationError("Enter a valid network port (1-65535).")
        return {
            "name": name, "device_type": device_type, "transport": transport,
            "system_device_name": (data.get("system_device_name") or "").strip() if transport == "local" else "",
            "channels": channels, "address": (data.get("address") or "").strip(),
            "port": int(data.get("port") or 0), "config": data.get("config") or {},
            "is_enabled": bool(data.get("is_enabled", True)), "notes": (data.get("notes") or "").strip(),
        }

    def create_device(self, **data) -> AudioDevice:
        self._auth.require(P.ZONES_MANAGE)
        clean = self._validate_device(data, None)
        device_id = self._repos.devices.create(**clean)
        self._activity.log("Added output device", "Audio Zones",
                           f"{clean['name']} ({DEVICE_TYPES[clean['device_type']]}, {TRANSPORTS[clean['transport']]})")
        self.refresh_status(reinitialize=False)
        self._bus.data_changed.emit("devices")
        return self.get_device(device_id)

    def update_device(self, device_id: int, **data) -> AudioDevice:
        self._auth.require(P.ZONES_MANAGE)
        self.get_device(device_id)
        clean = self._validate_device(data, device_id)
        self._repos.devices.update(device_id, **clean)
        self._activity.log("Updated output device", "Audio Zones", clean["name"])
        self.refresh_status(reinitialize=False)
        self._bus.data_changed.emit("devices")
        return self.get_device(device_id)

    def delete_device(self, device_id: int) -> None:
        self._auth.require(P.ZONES_MANAGE)
        device = self.get_device(device_id)
        self._repos.devices.delete(device_id)
        self._activity.log("Removed output device", "Audio Zones", device.name, "warning")
        self._bus.data_changed.emit("devices")
        self._bus.data_changed.emit("zones")

    def zones_using(self, device_id: int) -> list[AudioZone]:
        return [z for z in self._repos.zones.all() if z.device_id == device_id]

    # --------------------------------------------------------- live status
    def compute_device_status(self, device: AudioDevice) -> tuple[str, str]:
        if not device.is_enabled:
            return "disabled", "Device disabled by an administrator."
        if device.transport == "simulated":
            return "simulated", "Virtual device - no sound is produced."
        if device.transport == "local":
            if not self.registry.available:
                return "disconnected", f"Audio subsystem unavailable: {self.registry.error}"
            found = self.registry.find_output(device.system_device_name)
            if found is None:
                wanted = device.system_device_name or "system default output"
                return "disconnected", f"'{wanted}' is not detected on this computer."
            label = "System default: " if not device.system_device_name else ""
            return "connected", f"{label}{found.name} ({found.host_api}, {found.max_output_channels} ch, " \
                                f"{found.samplerate} Hz)"
        driver = DRIVERS.get(device.transport)
        if driver is None:
            return "unsupported", "Unknown integration."
        return driver.probe(device.address, device.port, device.config)

    def refresh_status(self, reinitialize: bool = True, probe_network: bool = True) -> bool:
        """Re-scan hardware and update every device's status. Returns True if hardware changed."""
        changed = self.registry.refresh(reinitialize=reinitialize) if reinitialize else False
        for device in self._repos.devices.all():
            if device.transport in ("rtp", "sip", "http") and not probe_network and device.id in self._live_status:
                continue
            status, detail = self.compute_device_status(device)
            self._live_status[device.id] = (status, detail)
            if status != device.status or detail != device.status_detail:
                self._repos.devices.set_status(device.id, status, detail, seen=status in ("connected", "reachable"))
            elif status in ("connected", "reachable"):
                self._repos.devices.set_status(device.id, status, detail, seen=True)
        self._bus.devices_refreshed.emit()
        return changed

    def mark_verified(self, device_id: int) -> None:
        device = self._repos.devices.get(device_id)
        if device:
            self._repos.devices.set_status(device_id, device.status, device.status_detail, seen=True, verified=True)

    def device_status(self, device: AudioDevice) -> tuple[str, str]:
        return self._live_status.get(device.id) or self.compute_device_status(device)

    def zone_statuses(self, broadcasting_zone_ids: set[int] | None = None) -> list[ZoneStatus]:
        broadcasting_zone_ids = broadcasting_zone_ids or set()
        devices = {d.id: d for d in self._repos.devices.all()}
        result = []
        for zone in self.physical_zones():
            device = devices.get(zone.device_id) if zone.device_id else None
            if not zone.is_enabled:
                state, detail = ZS_DISABLED, "Zone disabled"
            elif device is None:
                state, detail = ZS_OFFLINE, "No output device assigned"
            else:
                status, detail = self.device_status(device)
                if status == "connected":
                    state = ZS_READY
                elif status == "simulated":
                    state = ZS_SIMULATED
                elif status in ("unverified", "reachable"):
                    state = ZS_UNVERIFIED
                else:
                    state = ZS_OFFLINE
                channel = f" - channel {zone.output_channel}" if zone.output_channel else ""
                detail = f"{device.name}{channel}: {DEVICE_STATUS_LABELS.get(status, status)}"
            if zone.id in broadcasting_zone_ids:
                state = ZS_BROADCASTING
            result.append(ZoneStatus(zone, device, state, detail))
        return result

    def summary(self, broadcasting_zone_ids: set[int] | None = None) -> dict[str, int]:
        """Counts by *hardware* state (real / simulated / ...) plus how many zones are on air right now."""
        statuses = self.zone_statuses()
        on_air = broadcasting_zone_ids or set()
        count = lambda *states: sum(1 for s in statuses if s.state in states)  # noqa: E731
        return {
            "total": len(statuses),
            "enabled": sum(1 for s in statuses if s.zone.is_enabled),
            "ready": count(ZS_READY), "simulated": count(ZS_SIMULATED), "unverified": count(ZS_UNVERIFIED),
            "offline": count(ZS_OFFLINE), "disabled": count(ZS_DISABLED),
            "broadcasting": sum(1 for s in statuses if s.zone.id in on_air),
            "playable": sum(1 for s in statuses if s.playable),
        }

    # -------------------------------------------------------------- routing
    def device_target(self, device_id: int) -> OutputTarget:
        """Endpoint for a whole device (all channels), used by device tests."""
        from .errors import BroadcastRejected
        device = self.get_device(device_id)
        status, detail = self.compute_device_status(device)
        if status not in PLAYABLE_STATUSES:
            raise BroadcastRejected(f"'{device.name}' cannot play audio: {detail}", "no_devices")
        system_device = self.registry.find_output(device.system_device_name) if device.transport == "local" else None
        return OutputTarget(key=f"dev{device.id}", device_id=device.id, device_name=device.name,
                            transport=device.transport, system_device=system_device, open_channels=device.channels,
                            channel_map=None, gain=0.8, zone_ids=[], zone_names=[],
                            config={**device.config, "address": device.address, "port": device.port})

    def resolve_targets(self, zone_ids) -> tuple[list[OutputTarget], list[str]]:
        """Map selected zones to output endpoints. Returns (targets, warnings)."""
        expanded = self.expand_zone_ids(zone_ids)
        zones = {z.id: z for z in self._repos.zones.all()}
        devices = {d.id: d for d in self._repos.devices.all()}
        warnings: list[str] = []
        grouped: dict[int, list[AudioZone]] = {}
        for zone_id in expanded:
            zone = zones[zone_id]
            device = devices.get(zone.device_id) if zone.device_id else None
            if device is None:
                warnings.append(f"{zone.name}: no output device assigned")
                continue
            status, detail = self.compute_device_status(device)
            if status not in PLAYABLE_STATUSES:
                warnings.append(f"{zone.name}: {device.name} is {DEVICE_STATUS_LABELS.get(status, status).lower()}")
                continue
            grouped.setdefault(device.id, []).append(zone)
        targets = []
        for device_id, group in grouped.items():
            device = devices[device_id]
            system_device = self.registry.find_output(device.system_device_name) if device.transport == "local" else None
            if any(z.output_channel == 0 for z in group):
                channel_map, open_channels = None, device.channels
            else:
                channel_map = sorted({z.output_channel - 1 for z in group})
                open_channels = max(channel_map) + 1
            targets.append(OutputTarget(
                key=f"dev{device.id}", device_id=device.id, device_name=device.name, transport=device.transport,
                system_device=system_device, open_channels=max(open_channels, 1), channel_map=channel_map,
                gain=max(z.volume for z in group) / 100.0, zone_ids=[z.id for z in group],
                zone_names=[z.name for z in group],
                config={**device.config, "address": device.address, "port": device.port}))
        return targets, warnings
