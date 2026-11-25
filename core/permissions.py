"""Permission catalogue and default role definitions (role-based access control)."""
from __future__ import annotations

from dataclasses import dataclass


class P:
    DASHBOARD_VIEW = "dashboard.view"
    PAGING_LIVE = "paging.live"
    ANNOUNCEMENTS_VIEW = "announcements.view"
    ANNOUNCEMENTS_MANAGE = "announcements.manage"
    ANNOUNCEMENTS_BROADCAST = "announcements.broadcast"
    ANNOUNCEMENTS_APPROVE = "announcements.approve"
    ALARMS_VIEW = "alarms.view"
    ALARMS_TRIGGER = "alarms.trigger"
    ALARMS_MANAGE = "alarms.manage"
    EMERGENCY_OVERRIDE = "emergency.override"
    SCHEDULES_VIEW = "schedules.view"
    SCHEDULES_MANAGE = "schedules.manage"
    ZONES_VIEW = "zones.view"
    ZONES_MANAGE = "zones.manage"
    HISTORY_VIEW = "history.view"
    HISTORY_EXPORT = "history.export"
    LIBRARY_VIEW = "library.view"
    LIBRARY_MANAGE = "library.manage"
    USERS_MANAGE = "users.manage"
    SETTINGS_MANAGE = "settings.manage"


@dataclass(frozen=True)
class PermissionInfo:
    key: str
    label: str
    group: str
    description: str


PERMISSIONS: list[PermissionInfo] = [
    PermissionInfo(P.DASHBOARD_VIEW, "View dashboard", "General", "Open the dashboard overview."),
    PermissionInfo(P.PAGING_LIVE, "Live paging", "Broadcasting", "Make real-time microphone announcements."),
    PermissionInfo(P.ANNOUNCEMENTS_VIEW, "View announcements", "Broadcasting", "See saved announcements."),
    PermissionInfo(P.ANNOUNCEMENTS_MANAGE, "Create & edit announcements", "Broadcasting",
                   "Create, record, edit and delete announcements."),
    PermissionInfo(P.ANNOUNCEMENTS_BROADCAST, "Play announcements", "Broadcasting",
                   "Broadcast approved announcements to zones."),
    PermissionInfo(P.ANNOUNCEMENTS_APPROVE, "Approve announcements", "Broadcasting",
                   "Approve announcements so operators can play them."),
    PermissionInfo(P.ALARMS_VIEW, "View alarms", "Emergency", "See alarm types and the alarm log."),
    PermissionInfo(P.ALARMS_TRIGGER, "Trigger emergency alarms", "Emergency",
                   "Activate and stop emergency alarms."),
    PermissionInfo(P.EMERGENCY_OVERRIDE, "Emergency override", "Emergency",
                   "Interrupt ongoing regular broadcasts with an emergency alarm."),
    PermissionInfo(P.ALARMS_MANAGE, "Configure alarms", "Emergency",
                   "Edit alarm sounds, durations and custom alarm types."),
    PermissionInfo(P.SCHEDULES_VIEW, "View schedules", "Automation", "See scheduled broadcasts."),
    PermissionInfo(P.SCHEDULES_MANAGE, "Manage schedules", "Automation",
                   "Create, edit and delete scheduled broadcasts."),
    PermissionInfo(P.ZONES_VIEW, "View audio zones", "Audio system", "See zones and device status."),
    PermissionInfo(P.ZONES_MANAGE, "Manage zones & devices", "Audio system",
                   "Add zones, configure output devices and hardware."),
    PermissionInfo(P.LIBRARY_VIEW, "View audio library", "Audio system", "Browse and preview audio files."),
    PermissionInfo(P.LIBRARY_MANAGE, "Manage audio library", "Audio system",
                   "Upload, rename, categorize and delete audio files."),
    PermissionInfo(P.HISTORY_VIEW, "View broadcast history", "Administration", "Browse broadcast records."),
    PermissionInfo(P.HISTORY_EXPORT, "Export reports", "Administration", "Export history to CSV or PDF."),
    PermissionInfo(P.USERS_MANAGE, "Manage users", "Administration",
                   "Create accounts, assign roles and view activity logs."),
    PermissionInfo(P.SETTINGS_MANAGE, "System settings", "Administration",
                   "Change system configuration, backup and restore."),
]

ALL_PERMISSIONS: list[str] = [p.key for p in PERMISSIONS]
PERMISSION_LABELS: dict[str, str] = {p.key: p.label for p in PERMISSIONS}

ROLE_ADMIN = "Administrator"
ROLE_PERSONNEL = "School Personnel"
ROLE_OPERATOR = "Operator"

DEFAULT_ROLES: dict[str, dict] = {
    ROLE_ADMIN: {
        "description": "Full access to every module, users, devices and system settings.",
        "permissions": list(ALL_PERMISSIONS),
    },
    ROLE_PERSONNEL: {
        "description": "Creates announcements, performs authorized live paging and views history.",
        "permissions": [
            P.DASHBOARD_VIEW, P.PAGING_LIVE,
            P.ANNOUNCEMENTS_VIEW, P.ANNOUNCEMENTS_MANAGE, P.ANNOUNCEMENTS_BROADCAST,
            P.ALARMS_VIEW, P.SCHEDULES_VIEW, P.ZONES_VIEW,
            P.LIBRARY_VIEW, P.LIBRARY_MANAGE, P.HISTORY_VIEW,
        ],
    },
    ROLE_OPERATOR: {
        "description": "Operates live paging and plays approved announcements.",
        "permissions": [
            P.DASHBOARD_VIEW, P.PAGING_LIVE,
            P.ANNOUNCEMENTS_VIEW, P.ANNOUNCEMENTS_BROADCAST,
            P.ALARMS_VIEW, P.SCHEDULES_VIEW, P.ZONES_VIEW, P.LIBRARY_VIEW,
        ],
    },
}

# Permissions an administrator can grant to an individual account on top of its
# role ("schedule announcements if permitted", "trigger alarms if authorized").
GRANTABLE_PERMISSIONS: list[str] = [
    P.SCHEDULES_MANAGE, P.ALARMS_TRIGGER, P.EMERGENCY_OVERRIDE, P.ANNOUNCEMENTS_MANAGE,
    P.ANNOUNCEMENTS_APPROVE, P.LIBRARY_MANAGE, P.HISTORY_VIEW, P.HISTORY_EXPORT,
    P.ZONES_MANAGE, P.ALARMS_MANAGE,
]
