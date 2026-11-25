"""Emergency priority, conflict prevention, permissions, routing and history recording."""
from __future__ import annotations

import pytest

from conftest import login_as, pump, wait_until
from core.errors import BroadcastRejected, PermissionDenied
from core.paging_manager import BroadcastRequest, Priority
from core.permissions import P
from services.audio_sources import BufferSource, RepeatSource
import numpy as np


def _endless():
    return RepeatSource(BufferSource(np.zeros(4800, dtype=np.float32), 48000, "loop"), count=0)


def _campus(ctx):
    return [ctx.zones.campus_zone().id]


def test_emergency_interrupts_announcement_and_is_logged(ctx):
    ann = ctx.announcements.list()[0]
    ctx.announcements.broadcast(ann.id, _campus(ctx))
    fire = ctx.alarms.alarm_types()[0]
    with pytest.raises(BroadcastRejected) as info:
        ctx.alarms.trigger(fire.id, None)
    assert info.value.code == "confirm"
    ctx.alarms.trigger(fire.id, None, interrupt=True)
    assert ctx.controller.emergency_active
    interrupted = ctx.history.search(broadcast_type="announcement")[0]
    assert interrupted.status == "interrupted" and "Emergency" in interrupted.status_detail
    ctx.alarms.stop_active()
    record = ctx.history.search(broadcast_type="alarm")[0]
    assert record.status == "cancelled" and record.ended_by_name == "Ada Admin"
    levels = {e.action: e.level for e in ctx.activity.search(module="Broadcasting")}
    assert levels["Emergency alarm activated"] == "critical" and levels["Emergency alarm cancelled"] == "critical"


def test_regular_broadcasts_cannot_interrupt_emergency(ctx):
    ctx.alarms.trigger(ctx.alarms.alarm_types()[0].id, None)
    ann = ctx.announcements.list()[0]
    for interrupt in (False, True):
        with pytest.raises(BroadcastRejected) as info:
            ctx.announcements.broadcast(ann.id, _campus(ctx), interrupt=interrupt)
        assert info.value.code == "emergency_active"
    decision = ctx.controller.evaluate("live", Priority.LIVE, ctx.auth.current_user)
    assert not decision.allowed


def test_equal_priority_is_rejected_and_higher_needs_confirmation(ctx):
    anns = ctx.announcements.list()
    ctx.announcements.broadcast(anns[0].id, _campus(ctx))
    with pytest.raises(BroadcastRejected) as info:
        ctx.announcements.broadcast(anns[1].id, _campus(ctx))
    assert info.value.code == "busy"
    decision = ctx.controller.evaluate("live", Priority.LIVE, ctx.auth.current_user)
    assert decision.allowed and decision.needs_confirmation


def test_emergency_override_permission_required(ctx, make_user):
    make_user("Operator", "alarmop", extra=[P.ALARMS_TRIGGER])
    ctx.announcements.broadcast(ctx.announcements.list()[0].id, _campus(ctx))
    login_as(ctx, "alarmop")
    with pytest.raises(BroadcastRejected) as info:
        ctx.alarms.trigger(ctx.alarms.alarm_types()[0].id, None, interrupt=True)
    assert info.value.code == "override"


def test_unauthorized_users_cannot_page_or_trigger(ctx, make_user):
    make_user("Operator", "op_user")
    user = login_as(ctx, "op_user")
    with pytest.raises(PermissionDenied):
        ctx.alarms.trigger(ctx.alarms.alarm_types()[0].id, None)
    role = next(r for r in ctx.users.list_roles() if r.name == "Operator")
    login_as(ctx, "admin", "Admin1234")
    ctx.users.update_role(role.id, role.description, [p for p in role.permissions if p != P.PAGING_LIVE])
    user = login_as(ctx, "op_user")
    decision = ctx.controller.evaluate("live", Priority.LIVE, user)
    assert not decision.allowed and decision.code == "permission"


def test_only_alarm_triggerers_can_stop_alarm(ctx, make_user):
    op = make_user("Operator", "viewer")
    ctx.alarms.trigger(ctx.alarms.alarm_types()[0].id, None)
    with pytest.raises(PermissionDenied):
        ctx.controller.stop(op)
    ctx.controller.stop(ctx.auth.current_user)
    assert not ctx.controller.is_active


def test_every_broadcast_recorded_including_failures(ctx):
    orphan = ctx.zones.create_zone(name="No Speaker Zone", device_id=None)
    with pytest.raises(BroadcastRejected) as info:
        ctx.announcements.broadcast(ctx.announcements.list()[0].id, [orphan.id])
    assert info.value.code == "no_devices"
    failed = ctx.history.recent(1)[0]
    assert failed.status == "failed" and "No Speaker Zone" in failed.status_detail


def test_completed_broadcast_has_duration(ctx):
    short = BufferSource(np.zeros(9600, dtype=np.float32), 48000, "short")
    ctx.controller.start(BroadcastRequest(kind="audio", title="Short clip", zone_ids=_campus(ctx), source=short,
                                          priority=Priority.ANNOUNCEMENT, user=ctx.auth.current_user))
    assert wait_until(lambda: not ctx.controller.is_active, 3)
    record = ctx.history.recent(1)[0]
    assert record.status == "completed" and 0.1 <= record.duration_seconds < 1.5


def test_live_safety_limit_auto_stops(ctx):
    ctx.controller.start(BroadcastRequest(kind="audio", title="Long", zone_ids=_campus(ctx), source=_endless(),
                                          priority=Priority.ANNOUNCEMENT, user=ctx.auth.current_user,
                                          max_seconds=0.3))
    assert wait_until(lambda: not ctx.controller.is_active, 3)
    assert "safety limit" in ctx.history.recent(1)[0].status_detail


def test_zone_routing(ctx):
    zones = ctx.zones
    expanded = zones.expand_zone_ids(_campus(ctx))
    physical = [z for z in zones.physical_zones() if z.is_enabled]
    assert expanded == sorted(z.id for z in physical)
    library = next(z for z in physical if z.name == "Library")
    zones.set_zone_enabled(library.id, False)
    assert library.id not in zones.expand_zone_ids(_campus(ctx))
    targets, warnings = zones.resolve_targets(_campus(ctx))
    sim = next(t for t in targets if t.transport == "simulated")
    assert sim.channel_map == sorted({z.output_channel - 1 for z in zones.physical_zones()
                                      if z.is_enabled and z.device_id == sim.device_id})
    assert zones.describe_zones(_campus(ctx)) == "Entire Campus"


def test_simulated_devices_never_reported_connected(ctx):
    for device in ctx.zones.devices():
        status, _ = ctx.zones.compute_device_status(device)
        if device.transport == "simulated":
            assert status == "simulated"
    net = ctx.zones.create_device(name="Hallway IP speaker", device_type="ip_speaker", transport="sip",
                                  address="192.0.2.10", port=5060)
    assert ctx.zones.compute_device_status(net)[0] == "unsupported"
    rtp = ctx.zones.create_device(name="Multicast group", device_type="ip_speaker", transport="rtp",
                                  address="239.1.2.3", port=5004)
    assert ctx.zones.compute_device_status(rtp)[0] == "unverified"


def test_zone_and_device_tests_are_low_priority(ctx):
    zone = ctx.zones.physical_zones()[1]
    ctx.controller.test_zone(zone.id, ctx.auth.current_user)
    assert ctx.controller.active.request.priority == Priority.TEST
    ctx.announcements.broadcast(ctx.announcements.list()[0].id, _campus(ctx), interrupt=True)
    assert ctx.controller.active.kind == "announcement"
