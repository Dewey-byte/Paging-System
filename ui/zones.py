"""Audio zone management: zones, output devices, campus map and detected hardware."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLineEdit, QPlainTextEdit, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from core.permissions import P
from core.zone_manager import (DEVICE_STATUS_LABELS, DEVICE_TYPES, TRANSPORTS, TYPE_TRANSPORTS, ZONE_STATE_LABELS,
                               ZS_BROADCASTING)
from services.device_detection import sd

from .components.base import Card, EmptyState, InfoBanner, SectionHeader, button, label
from .components.campus_map import STATE_TONES, CampusMap, legend_row
from .components.controls import ColorSwatches, SliderField, ToggleSwitch
from .components.dialogs import Dialog, confirm
from .components.page import BasePage
from .components.tables import Column, DataTable

DEVICE_STATUS_TONES = {"connected": "success", "disconnected": "danger", "simulated": "purple", "unverified": "warning",
                       "reachable": "info", "unreachable": "danger", "unsupported": "neutral", "disabled": "neutral",
                       "unknown": "neutral"}
DEVICE_ICONS = {"local_output": "speaker", "pa_amplifier": "volume", "network_adapter": "server", "ip_speaker": "globe",
                "simulated": "cpu"}


def _actions(*buttons) -> QWidget:
    holder = QWidget()
    holder.setObjectName("Transparent")
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(4, 0, 4, 0)
    layout.setSpacing(4)
    for btn in buttons:
        if btn is not None:
            layout.addWidget(btn)
    layout.addStretch(1)
    return holder


class ZoneDialog(Dialog):
    def __init__(self, page, zone=None):
        super().__init__(page, "Edit zone" if zone else "Add zone",
                         "A zone is a paging area. Map it to a configured output device and channel.", "speaker",
                         width=560)
        self.page = page
        self.ctx = page.ctx
        self.zone = zone
        campus = bool(zone and zone.is_all_campus)
        self.name = QLineEdit(zone.name if zone else "")
        self.name.setPlaceholderText("e.g. Science Building")
        self.name.setEnabled(not campus)
        self.description = QLineEdit(zone.description if zone else "")
        self.description.setPlaceholderText("Optional - where the speakers are")
        self.body.addWidget(self.field("Zone name", self.name))
        self.body.addWidget(self.field("Description", self.description))
        if not campus:
            self.device = QComboBox()
            self.device.addItem("Not assigned (zone stays offline)", None)
            for device in self.ctx.zones.devices():
                status, _ = self.ctx.zones.device_status(device)
                self.device.addItem(f"{device.name}  -  {DEVICE_STATUS_LABELS.get(status, status)}", device.id)
            self.device.setCurrentIndex(max(0, self.device.findData(zone.device_id if zone else None)))
            self.channel = QComboBox()
            self.device.currentIndexChanged.connect(self._fill_channels)
            self._fill_channels()
            if zone:
                self.channel.setCurrentIndex(max(0, self.channel.findData(zone.output_channel)))
            self.body.addLayout(self.row(self.field("Output device", self.device),
                                         self.field("Output channel", self.channel,
                                                    "Use separate channels of a multi-channel interface to drive "
                                                    "different amplifier zones."), stretch=[3, 2]))
            self.volume = SliderField(0, 100, zone.volume if zone else 80, icon_name="volume")
            self.body.addWidget(self.field("Zone volume", self.volume))
            enabled_row = QHBoxLayout()
            enabled_row.addWidget(label("Zone enabled"), 1)
            self.enabled = ToggleSwitch(zone.is_enabled if zone else True)
            enabled_row.addWidget(self.enabled)
            self.body.addLayout(enabled_row)
        self.color = ColorSwatches(zone.color if zone else "#2563EB")
        self.body.addWidget(self.field("Map color", self.color))
        self.add_cancel()
        self.add_button("Save zone", "primary", self._save, "check", default=True)

    def _fill_channels(self) -> None:
        self.channel.clear()
        self.channel.addItem("All channels", 0)
        device_id = self.device.currentData()
        if device_id:
            device = self.ctx.zones.get_device(device_id)
            names = {1: "Left", 2: "Right"} if device.channels == 2 else {}
            for ch in range(1, device.channels + 1):
                self.channel.addItem(f"Channel {ch}" + (f" ({names[ch]})" if ch in names else ""), ch)

    def _save(self) -> None:
        try:
            if self.zone and self.zone.is_all_campus:
                self.ctx.zones.update_zone(self.zone.id, name=self.zone.name, description=self.description.text(),
                                           device_id=None, output_channel=0, volume=self.zone.volume,
                                           is_enabled=True, color=self.color.value())
            elif self.zone:
                self.ctx.zones.update_zone(self.zone.id, name=self.name.text(), description=self.description.text(),
                                           device_id=self.device.currentData(), output_channel=self.channel.currentData(),
                                           volume=self.volume.value(), is_enabled=self.enabled.isChecked(),
                                           color=self.color.value())
            else:
                self.ctx.zones.create_zone(name=self.name.text(), description=self.description.text(),
                                           device_id=self.device.currentData(),
                                           output_channel=self.channel.currentData(), volume=self.volume.value(),
                                           is_enabled=self.enabled.isChecked(), color=self.color.value())
        except Exception as exc:  # noqa: BLE001
            self.show_error(exc)
            return
        self.accept()


TYPE_HELP = {
    "local_output": "A Windows sound device on this computer: built-in speakers, line-out, USB audio interface or "
                    "HDMI output.",
    "pa_amplifier": "An external PA amplifier fed from one of this computer's outputs (line-out or USB interface). "
                    "Multi-channel interfaces can drive several amplifier zones, one per channel.",
    "network_adapter": "A network audio adapter. Dante/AES67 virtual soundcards appear as Windows sound devices; "
                       "RTP receivers can be fed a G.711 stream.",
    "ip_speaker": "Network IP speaker. This version streams standard RTP (G.711 mu-law) only. SIP paging and vendor "
                  "HTTP APIs need the manufacturer's protocol and will show as 'Not supported' until a driver is "
                  "added. Compatibility is not guaranteed - check your speaker's documentation.",
    "simulated": "A virtual device for testing zones without hardware. It plays silently and is always shown as "
                 "Simulated, never as connected.",
}


class DeviceDialog(Dialog):
    def __init__(self, page, device=None):
        super().__init__(page, "Edit output device" if device else "Add output device",
                         "Configure the hardware that physically plays a zone's audio.", "server", width=600,
                         scroll=True)
        self.ctx = page.ctx
        self.device = device
        self.name = QLineEdit(device.name if device else "")
        self.name.setPlaceholderText("e.g. Gym amplifier (USB interface ch 1-2)")
        self.body.addWidget(self.field("Device name", self.name))
        self.type = QComboBox()
        for key, text in DEVICE_TYPES.items():
            self.type.addItem(text, key)
        self.transport = QComboBox()
        self.body.addLayout(self.row(self.field("Device type", self.type), self.field("Connection", self.transport)))
        self.help = InfoBanner("", "info")
        self.body.addWidget(self.help)

        self.local_box = QWidget()
        self.local_box.setObjectName("Transparent")
        local = QVBoxLayout(self.local_box)
        local.setContentsMargins(0, 0, 0, 0)
        local.setSpacing(12)
        self.system_device = QComboBox()
        refresh = button("", "refresh", None, tooltip="Re-scan hardware")
        refresh.clicked.connect(self._refresh_outputs)
        sys_row = QHBoxLayout()
        sys_row.setSpacing(8)
        sys_row.addWidget(self.system_device, 1)
        sys_row.addWidget(refresh)
        holder = QWidget()
        holder.setObjectName("Transparent")
        holder.setLayout(sys_row)
        local.addWidget(self.field("Windows output device", holder,
                                   "Only devices detected by Windows are listed. Unplugged devices show as "
                                   "Disconnected."))
        self.local_box_layout = local
        self.body.addWidget(self.local_box)

        self.net_box = QWidget()
        self.net_box.setObjectName("Transparent")
        net = QVBoxLayout(self.net_box)
        net.setContentsMargins(0, 0, 0, 0)
        net.setSpacing(12)
        self.address = QLineEdit(device.address if device else "")
        self.address.setPlaceholderText("e.g. 239.0.0.10 (multicast) or 192.168.1.50")
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setValue(device.port if device and device.port else 5004)
        net.addLayout(self.row(self.field("IP address / multicast group", self.address), self.field("Port", self.port),
                               stretch=[3, 1]))
        self.ttl = QSpinBox()
        self.ttl.setRange(1, 255)
        self.ttl.setValue(int((device.config or {}).get("ttl", 16)) if device else 16)
        self.health = QSpinBox()
        self.health.setRange(0, 65535)
        self.health.setValue(int((device.config or {}).get("health_port", 0)) if device else 0)
        self.health.setSpecialValueText("Not checked")
        net.addLayout(self.row(self.field("Multicast TTL", self.ttl),
                               self.field("Health-check TCP port", self.health,
                                          "Optional. Used only to confirm the host responds (e.g. its web port).")))
        self.body.addWidget(self.net_box)

        self.channels = QSpinBox()
        self.channels.setRange(1, 64)
        self.channels.setValue(device.channels if device else 2)
        self.enabled = ToggleSwitch(device.is_enabled if device else True)
        row = QHBoxLayout()
        row.addWidget(self.field("Output channels", self.channels), 1)
        en = QVBoxLayout()
        en.addWidget(label("Enabled", "field"))
        en.addWidget(self.enabled)
        row.addLayout(en)
        self.body.addLayout(row)
        self.notes = QPlainTextEdit(device.notes if device else "")
        self.notes.setPlaceholderText("Location, cabling, amplifier model...")
        self.notes.setFixedHeight(70)
        self.body.addWidget(self.field("Notes", self.notes))

        self.type.currentIndexChanged.connect(self._type_changed)
        self.transport.currentIndexChanged.connect(self._transport_changed)
        if device:
            self.type.setCurrentIndex(self.type.findData(device.device_type))
        self._type_changed()
        if device:
            self.transport.setCurrentIndex(max(0, self.transport.findData(device.transport)))
        self._refresh_outputs(initial=True)
        self.add_cancel()
        self.add_button("Save device", "primary", self._save, "check", default=True)

    def _refresh_outputs(self, initial: bool = False) -> None:
        if not initial:
            self.ctx.refresh_devices()
        wanted = self.system_device.currentData() if self.system_device.count() else (
            self.device.system_device_name if self.device else "")
        self.system_device.clear()
        default = self.ctx.registry.default_output()
        self.system_device.addItem(f"System default output ({default.name if default else 'none detected'})", "")
        names = set()
        for out in self.ctx.registry.outputs():
            names.add(out.name)
            self.system_device.addItem(f"{out.name}  ({out.max_output_channels} ch, {out.samplerate} Hz)", out.name)
        if wanted and wanted not in names:
            self.system_device.addItem(f"{wanted}  (NOT DETECTED)", wanted)
        self.system_device.setCurrentIndex(max(0, self.system_device.findData(wanted or "")))

    def _type_changed(self) -> None:
        key = self.type.currentData()
        current = self.transport.currentData()
        self.transport.blockSignals(True)
        self.transport.clear()
        for transport in TYPE_TRANSPORTS[key]:
            self.transport.addItem(TRANSPORTS[transport], transport)
        index = self.transport.findData(current)
        self.transport.setCurrentIndex(max(0, index))
        self.transport.blockSignals(False)
        self.help.set_text(TYPE_HELP[key], "warning" if key == "ip_speaker" else "info")
        self._transport_changed()

    def _transport_changed(self) -> None:
        transport = self.transport.currentData()
        self.local_box.setVisible(transport == "local")
        self.net_box.setVisible(transport in ("rtp", "sip", "http"))
        if transport == "simulated" and self.channels.value() == 2 and not self.device:
            self.channels.setValue(8)
        if transport in ("sip", "http"):
            self.help.set_text(TRANSPORTS[transport] + ": this integration is not implemented in this version. The "
                               "device can be documented now and will show 'Not supported' until a driver for your "
                               "hardware is added.", "warning")

    def _save(self) -> None:
        data = dict(name=self.name.text(), device_type=self.type.currentData(), transport=self.transport.currentData(),
                    system_device_name=self.system_device.currentData() or "", channels=self.channels.value(),
                    address=self.address.text(), port=self.port.value(),
                    config={"ttl": self.ttl.value(), "health_port": self.health.value(), "codec": "pcmu"},
                    is_enabled=self.enabled.isChecked(), notes=self.notes.toPlainText())
        try:
            if self.device:
                self.ctx.zones.update_device(self.device.id, **data)
            else:
                self.ctx.zones.create_device(**data)
        except Exception as exc:  # noqa: BLE001
            self.show_error(exc)
            return
        self.accept()


class ZonesPage(BasePage):
    title = "Audio Zones"
    subtitle = "Divide the campus into paging zones and connect each one to real hardware."

    def __init__(self, ctx, window):
        super().__init__(ctx, window)
        manage = self.can(P.ZONES_MANAGE)
        self.add_zone_btn = self.header.add_action(button("Add zone", "plus", "primary"))
        self.add_zone_btn.clicked.connect(self.add_zone)
        self.add_zone_btn.setVisible(manage)
        self.add_device_btn = self.header.add_action(button("Add device", "server", None))
        self.add_device_btn.clicked.connect(self.add_device)
        self.add_device_btn.setVisible(manage)

        self.body.addWidget(InfoBanner(
            "<b>Selecting a zone does not connect speakers by itself.</b> Each zone plays through the output device "
            "and channel mapped to it below. Devices are shown as <b>Connected</b> only when Windows actually detects "
            "them; simulated devices are always labelled <b>Simulated</b>.", "info"))

        self.tabs = QTabWidget()
        self.body.addWidget(self.tabs, 1)

        # zones tab
        zones_tab = Card(padding=16, spacing=10)
        self.zone_table = DataTable([
            Column("Zone", lambda z: z.zone.name, kind="title", width=250,
                   subtitle=lambda z: z.zone.description or "—", icon=lambda z: ("speaker", z.zone.color)),
            Column("Output device", lambda z: (z.device.name + (f"  ·  ch {z.zone.output_channel}"
                                                               if z.zone.output_channel else "  ·  all channels"))
                   if z.device else "Not assigned", stretch=True),
            Column("Status", lambda z: "On air" if z.state == ZS_BROADCASTING else ZONE_STATE_LABELS[z.state],
                   kind="badge", width=130, tone=lambda z: STATE_TONES.get(z.state, "neutral"), tooltip=lambda z: z.detail),
            Column("Volume", width=190, kind="actions"),
            Column("Enabled", width=90, kind="actions"),
            Column("", width=170, kind="actions"),
        ], row_height=56)
        self._fix_action_columns(self.zone_table)
        self.zone_table.set_actions(self._zone_widgets)
        self.zone_table.row_activated.connect(lambda z: self.edit_zone(z.zone) if manage else None)
        self.zone_table.setMinimumHeight(420)
        zones_tab.body.addWidget(self.zone_table)
        self.tabs.addTab(zones_tab, "Zones")

        # devices tab
        devices_tab = Card(padding=16, spacing=10)
        bar = QHBoxLayout()
        bar.addWidget(label("Configured output devices. Status is checked against the hardware Windows reports.",
                            "muted"), 1)
        refresh = button("Refresh hardware", "refresh", None, "sm")
        refresh.clicked.connect(self.refresh_hardware)
        bar.addWidget(refresh)
        devices_tab.body.addLayout(bar)
        self.device_table = DataTable([
            Column("Device", lambda d: d.name, kind="title", width=280,
                   subtitle=lambda d: DEVICE_TYPES.get(d.device_type, d.device_type),
                   icon=lambda d: (DEVICE_ICONS.get(d.device_type, "speaker"),
                                   "purple" if d.transport == "simulated" else "accent")),
            Column("Connection", self._connection_text, stretch=True, tooltip=lambda d: self._status(d)[1]),
            Column("Ch", lambda d: d.channels, width=50, align="center"),
            Column("Status", lambda d: DEVICE_STATUS_LABELS.get(self._status(d)[0], self._status(d)[0]), kind="badge",
                   width=140, tone=lambda d: DEVICE_STATUS_TONES.get(self._status(d)[0], "neutral"),
                   tooltip=lambda d: self._status(d)[1]),
            Column("Zones", lambda d: len(self.ctx.zones.zones_using(d.id)), width=70, align="center"),
            Column("Last verified", lambda d: (d.last_verified_at or "Never")[:16], width=130, kind="muted"),
            Column("", width=150, kind="actions"),
        ], row_height=56)
        self._fix_action_columns(self.device_table)
        self.device_table.set_actions(self._device_widgets)
        self.device_table.row_activated.connect(lambda d: self.edit_device(d) if manage else None)
        self.device_table.setMinimumHeight(380)
        devices_tab.body.addWidget(self.device_table)
        self.tabs.addTab(devices_tab, "Output devices")

        # map tab
        map_tab = Card(padding=16, spacing=10)
        head = SectionHeader("Campus map", "Drag zones to match your campus layout; drag the corner to resize."
                             if manage else "Live zone overview", "map")
        map_tab.body.addWidget(head)
        self.map = CampusMap(editable=manage)
        self.map.setMinimumHeight(460)
        self.map.layout_changed.connect(self._save_layout)
        map_tab.body.addWidget(self.map, 1)
        map_tab.body.addWidget(legend_row())
        self.tabs.addTab(map_tab, "Campus map")

        # hardware tab
        hw_tab = Card(padding=16, spacing=12)
        hw_head = SectionHeader("Detected hardware", "Devices reported by Windows audio right now", "cpu")
        self.api_combo = QComboBox()
        self.api_combo.setMinimumWidth(200)
        self.api_combo.setToolTip("Windows audio driver interface used for all devices")
        self.api_combo.currentIndexChanged.connect(self._api_changed)
        hw_head.add_action(self.api_combo)
        rescan = hw_head.add_action(button("Re-scan", "refresh", None, "sm"))
        rescan.clicked.connect(self.refresh_hardware)
        hw_tab.body.addWidget(hw_head)
        self.hw_info = label("", "caption", wrap=True)
        hw_tab.body.addWidget(self.hw_info)
        hw_tab.body.addWidget(label("OUTPUT DEVICES", "overline"))
        self.out_table = DataTable([
            Column("Name", lambda d: d.name, kind="bold", stretch=True),
            Column("Channels", lambda d: d.max_output_channels, width=90, align="center"),
            Column("Sample rate", lambda d: f"{d.samplerate} Hz", width=120),
            Column("Default", lambda d: "Default" if d.is_default_output else "", kind="badge", width=110,
                   tone=lambda d: "accent"),
        ], row_height=40)
        self.out_table.setMinimumHeight(200)
        hw_tab.body.addWidget(self.out_table)
        hw_tab.body.addWidget(label("INPUT DEVICES (MICROPHONES)", "overline"))
        self.in_table = DataTable([
            Column("Name", lambda d: d.name, kind="bold", stretch=True),
            Column("Channels", lambda d: d.max_input_channels, width=90, align="center"),
            Column("Sample rate", lambda d: f"{d.samplerate} Hz", width=120),
            Column("Default", lambda d: "Default" if d.is_default_input else "", kind="badge", width=110,
                   tone=lambda d: "accent"),
        ], row_height=40)
        self.in_table.setMinimumHeight(180)
        hw_tab.body.addWidget(self.in_table)
        self.tabs.addTab(hw_tab, "Detected hardware")

        ctx.bus.data_changed.connect(lambda name: self.refresh() if name in ("zones", "devices") and self.isVisible()
                                     else None)
        ctx.bus.devices_refreshed.connect(lambda: self.refresh() if self.isVisible() else None)
        ctx.bus.broadcast_state_changed.connect(lambda: self.refresh() if self.isVisible() else None)

    @staticmethod
    def _fix_action_columns(table: DataTable) -> None:
        from PySide6.QtWidgets import QHeaderView
        for i, column in enumerate(table.columns):
            if column.kind == "actions":
                table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
                table.setColumnWidth(i, column.width or 120)

    # ----------------------------------------------------------------- data
    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        ctx = self.ctx
        statuses = ctx.zones.zone_statuses(ctx.controller.active_zone_ids())
        campus = ctx.zones.campus_zone()
        self.zone_table.set_rows(statuses)
        self.device_table.set_rows(ctx.zones.devices())
        self.map.set_statuses(statuses)
        self.out_table.set_rows(ctx.registry.outputs())
        self.in_table.set_rows(ctx.registry.inputs())
        self.api_combo.blockSignals(True)
        self.api_combo.clear()
        self.api_combo.addItem("Automatic (recommended)", "auto")
        for name in ctx.registry.host_apis:
            if name in ("Windows WASAPI", "Windows DirectSound", "MME"):
                self.api_combo.addItem(name, name)
        self.api_combo.setCurrentIndex(max(0, self.api_combo.findData(ctx.settings.get("audio.host_api") or "auto")))
        self.api_combo.blockSignals(False)
        version = sd.get_portaudio_version()[1] if sd is not None else "unavailable"
        self.hw_info.setText(f"Active interface: {ctx.registry.active_host_api or 'none'}  ·  "
                             f"{len(ctx.registry.outputs())} output(s), {len(ctx.registry.inputs())} input(s)  ·  "
                             f"{version}" + (f"  ·  {ctx.registry.error}" if ctx.registry.error else ""))
        self.tabs.setTabText(0, f"Zones ({len(statuses)})")
        self.tabs.setTabText(1, f"Output devices ({len(self.device_table.rows)})")

    def _status(self, device) -> tuple[str, str]:
        return self.ctx.zones.device_status(device)

    @staticmethod
    def _connection_text(device) -> str:
        if device.transport == "local":
            return f"Windows device: {device.system_device_name or 'System default output'}"
        if device.transport == "simulated":
            return "Simulation - no audio is produced"
        return f"{TRANSPORTS[device.transport]}  ·  {device.address}:{device.port}"

    # ------------------------------------------------------------ widgets
    def _zone_widgets(self, status) -> QWidget:
        return _ZoneRowWidgets(self, status, self.can(P.ZONES_MANAGE))

    def _device_widgets(self, device) -> QWidget:
        manage = self.can(P.ZONES_MANAGE)
        test = button("Test", "play", "subtle", "sm", tooltip="Play a test tone on every channel of this device")
        test.clicked.connect(lambda: self.test_device(device))
        test.setEnabled(manage)
        edit = button("", "edit", "ghost", tooltip="Edit device") if manage else None
        if edit:
            edit.clicked.connect(lambda: self.edit_device(device))
        delete = button("", "trash", "ghost", tooltip="Remove device", icon_color="danger") if manage else None
        if delete:
            delete.clicked.connect(lambda: self.delete_device(device))
        return _actions(test, edit, delete)

    # ------------------------------------------------------------ actions
    def add_zone(self) -> None:
        if ZoneDialog(self).exec() == QDialog.DialogCode.Accepted:
            self.toast("success", "Zone saved")

    def edit_zone(self, zone) -> None:
        if ZoneDialog(self, zone).exec() == QDialog.DialogCode.Accepted:
            self.toast("success", "Zone updated", zone.name)

    def delete_zone(self, zone) -> None:
        if confirm(self, "Delete zone?", f"<b>{zone.name}</b> will be removed from announcements and schedules that "
                                         "target it.", "Delete zone", danger=True, icon_name="trash"):
            self.guarded(self.ctx.zones.delete_zone, zone.id, success="Zone deleted")

    def test_zone(self, zone) -> None:
        if self.start_broadcast(lambda interrupt: self.ctx.controller.test_zone(zone.id, self.user, interrupt)):
            self.toast("info", "Testing zone", f"Playing a test tone in {zone.name}.")

    def add_device(self) -> None:
        if DeviceDialog(self).exec() == QDialog.DialogCode.Accepted:
            self.toast("success", "Device saved")

    def edit_device(self, device) -> None:
        if DeviceDialog(self, device).exec() == QDialog.DialogCode.Accepted:
            self.toast("success", "Device updated", device.name)

    def delete_device(self, device) -> None:
        zones = self.ctx.zones.zones_using(device.id)
        detail = (f"{len(zones)} zone(s) use this device and will become unassigned: "
                  f"{', '.join(z.name for z in zones)}") if zones else ""
        if confirm(self, "Remove device?", f"<b>{device.name}</b> will be removed from the configuration.",
                   "Remove device", danger=True, icon_name="trash", detail=detail):
            self.guarded(self.ctx.zones.delete_device, device.id, success="Device removed")

    def test_device(self, device) -> None:
        if self.start_broadcast(lambda interrupt: self.ctx.controller.test_device(device.id, self.user, interrupt)):
            self.toast("info", "Testing device", f"Playing a test tone on {device.name}.")

    def refresh_hardware(self) -> None:
        changed = self.ctx.refresh_devices()
        self.refresh()
        self.toast("success", "Hardware re-scanned", f"{len(self.ctx.registry.outputs())} output(s) and "
                   f"{len(self.ctx.registry.inputs())} input(s) detected" + (" - changes found." if changed else "."))

    def _api_changed(self) -> None:
        value = self.api_combo.currentData()
        if value and value != (self.ctx.settings.get("audio.host_api") or "auto"):
            self.ctx.settings.set("audio.host_api", value)
            self.refresh()
            self.toast("info", "Audio interface changed", f"Now using {self.ctx.registry.active_host_api}. "
                       "Re-check device mappings if names differ.")

    def _save_layout(self, rects: dict) -> None:
        self.guarded(self.ctx.zones.save_map_layout, rects)


class _ZoneRowWidgets(QWidget):
    """Installs the volume slider, enabled toggle and action buttons into a zone row."""

    def __init__(self, page: ZonesPage, status, manage: bool):
        super().__init__()
        self.setObjectName("Transparent")
        table = page.zone_table
        zone = status.zone
        row = table.rows.index(status)
        volume = SliderField(0, 100, zone.volume, suffix="%")
        volume.setEnabled(manage)
        volume.slider.sliderReleased.connect(lambda: page.guarded(page.ctx.zones.set_zone_volume, zone.id,
                                                                  volume.value()))
        volume_holder = _actions(volume)
        volume_holder.layout().setContentsMargins(8, 0, 12, 0)
        table.setIndexWidget(table.model_.index(row, 3), volume_holder)
        toggle = ToggleSwitch(zone.is_enabled)
        toggle.setEnabled(manage)
        toggle.toggled.connect(lambda checked: page.guarded(page.ctx.zones.set_zone_enabled, zone.id, checked))
        table.setIndexWidget(table.model_.index(row, 4), _actions(toggle))
        test = button("Test", "play", "subtle", "sm", tooltip="Play a test tone in this zone")
        test.setEnabled(manage and status.playable)
        test.clicked.connect(lambda: page.test_zone(zone))
        edit = button("", "edit", "ghost", tooltip="Edit zone") if manage else None
        if edit:
            edit.clicked.connect(lambda: page.edit_zone(zone))
        delete = button("", "trash", "ghost", tooltip="Delete zone", icon_color="danger") if manage else None
        if delete:
            delete.clicked.connect(lambda: page.delete_zone(zone))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_actions(test, edit, delete))
