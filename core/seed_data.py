"""Default content created on first run (roles, zones, devices, alarm types, samples)."""
from __future__ import annotations

from datetime import date

from database.repositories import Repositories

from .permissions import DEFAULT_ROLES

LOCAL_DEVICE_NAME = "This Computer - Default Output"
SIM_DEVICE_NAME = "Simulated Zone Amplifier (8-ch)"

# name, description, color, map rect (x, y, w, h) on a 100 x 60 campus canvas
DEFAULT_ZONES = [
    ("Main Building", "Main hallway and lobby speakers", "#2563EB", (34, 4, 32, 16)),
    ("Administration Office", "Principal and registrar offices", "#0EA5E9", (3, 4, 27, 12)),
    ("Faculty Room", "Teachers' lounge and faculty offices", "#8B5CF6", (70, 4, 27, 12)),
    ("Classrooms", "All classroom ceiling speakers", "#16A34A", (34, 24, 32, 15)),
    ("Library", "Library and reading areas", "#F59E0B", (3, 20, 27, 13)),
    ("School Gymnasium", "Gym, covered court and locker rooms", "#EF4444", (70, 20, 27, 19)),
    ("Cafeteria", "Canteen and dining hall", "#EC4899", (3, 37, 27, 13)),
    ("School Grounds", "Outdoor horns: field, gates and parking", "#14B8A6", (34, 43, 63, 13)),
]

ANNOUNCEMENT_CATEGORIES = [
    "General Announcement", "Academic Announcement", "Faculty Announcement", "Student Announcement",
    "Event Announcement", "Class Suspension", "Administrative Announcement", "Emergency Announcement",
]

AUDIO_CATEGORIES = [
    "School Announcements", "Emergency Alarms", "School Hymns", "National Anthem", "Bell Sounds", "General Audio",
]

DEFAULT_ALARMS = [
    dict(code="FIRE", name="Fire Alarm", builtin_pattern="alarm_fire", color="#DC2626", icon="flame",
         duration_seconds=180, description="Temporal-3 evacuation tone for fire emergencies.",
         voice_message="Attention please. A fire alarm has been activated. Leave the building immediately "
                       "using the nearest exit and proceed to the assembly area."),
    dict(code="EARTHQUAKE", name="Earthquake Alarm", builtin_pattern="alarm_earthquake", color="#EA580C",
         icon="activity", duration_seconds=60, description="Rapid warble for earthquake drop-cover-hold.",
         voice_message="Earthquake alert. Drop, cover, and hold on. Stay away from windows until the "
                       "shaking stops."),
    dict(code="EVACUATION", name="Emergency Evacuation", builtin_pattern="alarm_evacuation", color="#B91C1C",
         icon="door", duration_seconds=180, description="Slow whoop signal ordering a full evacuation.",
         voice_message="Attention. This is an emergency evacuation. Please proceed calmly to the designated "
                       "evacuation area and follow your teacher's instructions."),
    dict(code="WEATHER", name="Weather Warning", builtin_pattern="alarm_weather", color="#D97706",
         icon="cloud_lightning", duration_seconds=90, description="Rising and falling wail for severe weather.",
         voice_message="Weather warning. Severe weather is approaching. Move indoors and stay away from "
                       "windows until further notice."),
    dict(code="SECURITY", name="Security Alert", builtin_pattern="alarm_security", color="#7C3AED",
         icon="shield_alert", duration_seconds=120, description="Hi-lo tone for lockdown and security threats.",
         voice_message="Security alert. Remain in your classrooms, lock the doors, and wait for further "
                       "instructions from school staff."),
    dict(code="SHOOTING", name="Shooting Incident", builtin_pattern="alarm_lockdown", color="#7F1D1D",
         icon="crosshair", duration_seconds=180,
         description="Lockdown yelp for an active shooter or shots fired on or near campus.",
         voice_message="Lockdown. Lockdown. A shooting incident has been reported. Get into the nearest room, lock "
                       "the door, turn off the lights, stay out of sight and remain silent. Do not open the door "
                       "until police or school officials release you."),
    dict(code="GENERAL", name="General Emergency", builtin_pattern="alarm_general", color="#BE123C",
         icon="alert_octagon", duration_seconds=120, description="Pulsed tone for any other emergency.",
         voice_message="Attention. An emergency has been declared. Please follow the instructions of "
                       "school staff."),
]

DEMO_ANNOUNCEMENTS = [
    dict(title="Morning Greeting", category="General Announcement",
         message="Good morning students and teachers. Welcome to another day of learning. Classes will begin "
                 "in fifteen minutes. Please proceed to your classrooms."),
    dict(title="Lunch Break", category="General Announcement",
         message="Attention please. It is now time for lunch break. Please proceed to the cafeteria in an "
                 "orderly manner."),
    dict(title="Faculty Meeting Reminder", category="Faculty Announcement",
         message="Attention all faculty members. There will be a faculty meeting at three o'clock this "
                 "afternoon in the faculty room."),
    dict(title="End of Classes", category="General Announcement",
         message="Classes have ended for today. Please make sure you have all your belongings. "
                 "Have a safe trip home."),
]


def ensure_roles(repos: Repositories) -> None:
    for name, spec in DEFAULT_ROLES.items():
        role = repos.roles.get_by_name(name)
        if role is None:
            repos.roles.create(name, spec["description"], spec["permissions"], is_system=True)
        elif name == "Administrator":
            # Administrators always receive newly introduced permissions.
            missing = set(spec["permissions"]) - set(role.permissions)
            if missing:
                repos.roles.update(role.id, permissions=role.permissions + sorted(missing))


def seed_base(repos: Repositories, map_local_output: bool = True) -> bool:
    """Create default devices, zones and alarm types. Returns True when seeding happened."""
    seeded = False
    if not repos.devices.all():
        repos.devices.create(name=LOCAL_DEVICE_NAME, device_type="local_output", transport="local",
                             system_device_name="", channels=2,
                             notes="Plays through this computer's default audio output. Connect a PA "
                                   "amplifier to the line-out to drive physical speakers.")
        repos.devices.create(name=SIM_DEVICE_NAME, device_type="simulated", transport="simulated", channels=8,
                             notes="Virtual 8-channel amplifier for testing zones without hardware. "
                                   "It produces no sound.")
        seeded = True
    if not repos.zones.all():
        local = repos.devices.get_by_name(LOCAL_DEVICE_NAME)
        sim = repos.devices.get_by_name(SIM_DEVICE_NAME)
        repos.zones.create(name="Entire Campus", description="Every enabled zone on campus", is_all_campus=True,
                           color="#172554", sort_order=0)
        for index, (name, description, color, rect) in enumerate(DEFAULT_ZONES, start=1):
            if index == 1 and map_local_output and local:
                device_id, channel = local.id, 0
            else:
                device_id, channel = (sim.id if sim else None), min(index - 1 if map_local_output else index, 8)
            repos.zones.create(name=name, description=description, device_id=device_id, output_channel=channel,
                               volume=80, color=color, map_x=rect[0], map_y=rect[1], map_w=rect[2], map_h=rect[3],
                               sort_order=index)
        seeded = True
    if ensure_system_alarms(repos):
        seeded = True
    return seeded


def ensure_system_alarms(repos: Repositories) -> int:
    """Create predefined alarm types that are missing (also adds ones introduced by updates).

    Existing alarms are never modified except for their position in the list,
    so administrators keep their customised sounds, messages and timings.
    Returns how many alarm types were created.
    """
    created = 0
    for order, spec in enumerate(DEFAULT_ALARMS):
        existing = repos.alarms.get_by_code(spec["code"])
        if existing is None:
            repos.alarms.create(sound_source="builtin", repeat_count=0, is_system=True, sort_order=order, **spec)
            created += 1
        elif existing.is_system and existing.sort_order != order:
            repos.alarms.update(existing.id, sort_order=order)
    return created


def seed_demo_content(repos: Repositories, user_id: int | None, include_schedules: bool = True) -> None:
    """Sample announcements and the example daily schedule from the specification."""
    if repos.announcements.all():
        return
    campus = next((z for z in repos.zones.all() if z.is_all_campus), None)
    ids = {}
    for spec in DEMO_ANNOUNCEMENTS:
        ids[spec["title"]] = repos.announcements.create(
            dict(source_type="tts", play_chime=1, repeat_count=1, is_approved=1, created_by=user_id, **spec),
            [campus.id] if campus else [])
    if not include_schedules or repos.schedules.all():
        return
    campus = next((z for z in repos.zones.all() if z.is_all_campus), None)
    zones = [campus.id] if campus else []
    bell = repos.audio_files.get_by_builtin_key("bell_class")
    dismissal = repos.audio_files.get_by_builtin_key("bell_dismissal") or bell
    today = date.today().isoformat()
    common = dict(start_date=today, recurrence="weekdays", days_of_week="", interval_weeks=1, created_by=user_id)
    plan = [
        dict(name="Morning School Announcement", content_type="announcement",
             announcement_id=ids.get("Morning Greeting"), time_of_day="07:30", duration_seconds=20, play_chime=1),
        dict(name="National Anthem", content_type="audio_file", audio_file_id=None, time_of_day="07:45",
             duration_seconds=120, is_enabled=0,
             notes="Upload the National Anthem to the Audio Library, assign it here, then enable this schedule."),
        dict(name="Start of Classes", content_type="audio_file", audio_file_id=bell.id if bell else None,
             time_of_day="08:00", duration_seconds=10, is_enabled=1 if bell else 0),
        dict(name="Lunch Break", content_type="announcement", announcement_id=ids.get("Lunch Break"),
             time_of_day="12:00", duration_seconds=15, play_chime=1),
        dict(name="End of Classes", content_type="audio_file", audio_file_id=dismissal.id if dismissal else None,
             time_of_day="16:30", duration_seconds=10, is_enabled=1 if dismissal else 0),
    ]
    for spec in plan:
        spec.setdefault("is_enabled", 1)
        repos.schedules.create({**common, **spec}, zones)
