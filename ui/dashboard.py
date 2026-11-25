"""Dashboard: today's statistics, live system status, quick actions, campus map and activity."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from core.history_manager import STATUS_LABELS, TYPE_LABELS, format_duration
from core.permissions import P
from core.schedule_manager import CONTENT_LABELS

from .components.base import (Badge, Card, ElidedLabel, EmptyState, IconBubble, SectionHeader, StatCard, button,
                              clear_layout, divider, label)
from .components.campus_map import CampusMap, legend_row
from .components.charts import BarChart, Sparkline
from .components.flow_layout import ResponsiveGrid
from .components.page import BasePage
from .theme import theme

TYPE_STYLE = {
    "live": ("mic", "accent"), "announcement": ("megaphone", "info"), "alarm": ("siren", "danger"),
    "scheduled": ("calendar_clock", "purple"), "audio": ("music", "success"), "test": ("speaker", "neutral"),
}
STATUS_TONES = {"completed": "success", "cancelled": "warning", "interrupted": "warning", "failed": "danger",
                "missed": "danger", "in_progress": "accent"}


def time_ago(ts: str) -> str:
    try:
        then = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts
    delta = datetime.now() - then
    if delta.total_seconds() < 60:
        return "just now"
    if delta.total_seconds() < 3600:
        return f"{int(delta.total_seconds() // 60)} min ago"
    if then.date() == date.today():
        return then.strftime("%I:%M %p").lstrip("0")
    if then.date() == date.today() - timedelta(days=1):
        return "Yesterday " + then.strftime("%I:%M %p").lstrip("0")
    return then.strftime("%b %d, %I:%M %p").replace(" 0", " ")


def countdown(when: datetime) -> str:
    seconds = int((when - datetime.now()).total_seconds())
    if seconds < 60:
        return "now"
    minutes = seconds // 60
    if minutes < 60:
        return f"in {minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"in {hours}h {minutes:02d}m"
    return f"in {hours // 24}d"


class QuickAction(Card):
    clicked = Signal()

    def __init__(self, title: str, caption: str, icon_name: str, tone: str, enabled: bool = True):
        super().__init__(padding=16, hoverable=enabled, spacing=10)
        self._enabled = enabled
        row = QHBoxLayout()
        row.setSpacing(12)
        self.bubble = IconBubble(icon_name if enabled else "lock", tone if enabled else "neutral", 46, 22)
        row.addWidget(self.bubble)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(label(title, "h3", wrap=True))
        cap = label(caption if enabled else "Not permitted for your role", "caption", wrap=True)
        texts.addWidget(cap)
        row.addLayout(texts, 1)
        self.body.addLayout(row)
        if not enabled:
            self.setToolTip("Your account does not have permission for this action.")

    def mouseReleaseEvent(self, event):
        if self._enabled and event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ActivityRow(QWidget):
    def __init__(self, record):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(12)
        icon_name, tone = TYPE_STYLE.get(record.broadcast_type, ("radio", "neutral"))
        layout.addWidget(IconBubble(icon_name, tone, 36, 17))
        texts = QVBoxLayout()
        texts.setSpacing(1)
        texts.addWidget(ElidedLabel(record.title, "h3"))
        duration = format_duration(record.duration_seconds) if record.status != "in_progress" else "on air"
        texts.addWidget(ElidedLabel(f"{TYPE_LABELS.get(record.broadcast_type, record.broadcast_type)}  ·  "
                                    f"{record.zone_names}  ·  {duration}", "caption"))
        layout.addLayout(texts, 1)
        right = QVBoxLayout()
        right.setSpacing(3)
        badge = Badge(STATUS_LABELS.get(record.status, record.status), STATUS_TONES.get(record.status, "neutral"),
                      dot=True, small=True)
        right.addWidget(badge, alignment=Qt.AlignmentFlag.AlignRight)
        when = label(time_ago(record.started_at), "caption")
        when.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(when)
        layout.addLayout(right)


class UpcomingRow(QWidget):
    def __init__(self, when: datetime, schedule):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(12)
        block = QVBoxLayout()
        block.setSpacing(0)
        clock = label(when.strftime("%I:%M").lstrip("0"), "h3")
        clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ampm = label(when.strftime("%p") if when.date() == date.today() else when.strftime("%a"), "caption")
        ampm.setAlignment(Qt.AlignmentFlag.AlignCenter)
        block.addWidget(clock)
        block.addWidget(ampm)
        holder = QWidget()
        holder.setFixedWidth(58)
        holder.setLayout(block)
        holder.setObjectName("TimeBlock")
        holder.setStyleSheet(f"QWidget#TimeBlock {{ background: {theme.hex('surface_alt')}; border-radius: 10px; }}")
        layout.addWidget(holder)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        texts.addWidget(ElidedLabel(schedule.name, "h3"))
        content = CONTENT_LABELS.get(schedule.content_type, "")
        texts.addWidget(ElidedLabel(f"{content}: {schedule.content_name or '—'}", "caption"))
        layout.addLayout(texts, 1)
        layout.addWidget(Badge(countdown(when), "danger" if schedule.content_type == "alarm" else "accent", small=True))


class DashboardPage(BasePage):
    title = "Dashboard"
    subtitle = "Live overview of the campus paging system."

    def __init__(self, ctx, window):
        super().__init__(ctx, window)
        self.header.title.setText(self._greeting())
        self.manage_zones = self.header.add_action(button("Manage zones", "speaker", None))
        self.manage_zones.clicked.connect(lambda: self.window_.navigate("zones"))
        self.manage_zones.setVisible(self.can(P.ZONES_VIEW))
        self.live_button = self.header.add_action(button("Start live paging", "mic", "primary"))
        self.live_button.clicked.connect(lambda: self.window_.navigate("live"))
        self.live_button.setVisible(self.can(P.PAGING_LIVE))

        # --- stat cards
        stats_host = QWidget()
        stats_host.setObjectName("Transparent")
        stats = ResponsiveGrid(stats_host, min_width=230, spacing=14, max_columns=4)
        self.stat_total = StatCard("Announcements today", "megaphone", "accent")
        self.stat_completed = StatCard("Completed broadcasts", "check_circle", "success")
        self.stat_upcoming = StatCard("Upcoming today", "calendar_clock", "purple")
        self.stat_zones = StatCard("Active audio zones", "speaker", "info")
        self.spark_total = Sparkline("accent")
        self.stat_total.body.addWidget(self.spark_total)
        for card in (self.stat_total, self.stat_completed, self.stat_upcoming, self.stat_zones):
            card.body.addStretch(1)
            stats.addWidget(card)
        self.body.addWidget(stats_host)

        # --- status + quick actions
        mid_host = QWidget()
        mid_host.setObjectName("Transparent")
        mid = ResponsiveGrid(mid_host, min_width=430, spacing=14, max_columns=2)
        self.status_card = Card(padding=20, spacing=14)
        self.status_card.body.addWidget(SectionHeader("Paging system status", "Current broadcast and audio path",
                                                      "radio"))
        hero = QHBoxLayout()
        hero.setSpacing(14)
        self.status_bubble = IconBubble("check_circle", "success", 56, 28)
        hero.addWidget(self.status_bubble)
        hero_text = QVBoxLayout()
        hero_text.setSpacing(2)
        self.status_title = label("Ready to broadcast", "h2")
        self.status_detail = label("", "muted", wrap=True)
        hero_text.addWidget(self.status_title)
        hero_text.addWidget(self.status_detail)
        hero.addLayout(hero_text, 1)
        self.status_stop = button("Stop", "stop", "danger")
        self.status_stop.clicked.connect(self.window_.stop_broadcast)
        hero.addWidget(self.status_stop)
        self.status_card.body.addLayout(hero)
        self.status_card.body.addWidget(divider())
        facts = QGridLayout()
        facts.setHorizontalSpacing(18)
        facts.setVerticalSpacing(12)
        self.fact_widgets = {}
        for i, (key, caption, icon_name) in enumerate([("output", "Default output", "speaker"),
                                                        ("mic", "Default microphone", "mic"),
                                                        ("next", "Next scheduled", "clock"),
                                                        ("last", "Last broadcast", "history")]):
            cell = QHBoxLayout()
            cell.setSpacing(10)
            cell.addWidget(IconBubble(icon_name, "neutral", 32, 16))
            texts = QVBoxLayout()
            texts.setSpacing(0)
            texts.addWidget(label(caption.upper(), "overline"))
            value = label("—", wrap=True)
            value.setStyleSheet("font-weight: 600;")
            texts.addWidget(value)
            cell.addLayout(texts, 1)
            facts.addLayout(cell, i // 2, i % 2)
            self.fact_widgets[key] = value
        self.status_card.body.addLayout(facts)
        self.status_card.body.addStretch(1)
        mid.addWidget(self.status_card)

        actions_card = Card(padding=20, spacing=14)
        actions_card.body.addWidget(SectionHeader("Quick actions", "Common tasks, one click away", "zap"))
        grid = QGridLayout()
        grid.setSpacing(12)
        specs = [
            ("Start live announcement", "Talk to selected zones now", "mic", "accent", P.PAGING_LIVE,
             lambda: self.window_.navigate("live")),
            ("Play emergency alarm", "Fire, earthquake, evacuation...", "siren", "danger", P.ALARMS_TRIGGER,
             lambda: self.window_.navigate("alarms")),
            ("Create announcement", "Text-to-speech, audio or voice", "megaphone", "info", P.ANNOUNCEMENTS_MANAGE,
             self._new_announcement),
            ("Schedule broadcast", "Bells and recurring messages", "calendar_clock", "purple", P.SCHEDULES_MANAGE,
             self._new_schedule),
        ]
        for i, (title, caption, icon_name, tone, perm, action) in enumerate(specs):
            tile = QuickAction(title, caption, icon_name, tone, self.can(perm))
            tile.clicked.connect(action)
            grid.addWidget(tile, i // 2, i % 2)
        actions_card.body.addLayout(grid)
        mid.addWidget(actions_card)
        self.body.addWidget(mid_host)

        # --- campus map
        map_card = Card(padding=20, spacing=12)
        header = SectionHeader("Campus audio zones", "Live state of every paging zone and its output device", "map")
        self.zone_summary = Badge("", "neutral")
        header.add_action(self.zone_summary)
        map_card.body.addWidget(header)
        self.map = CampusMap()
        self.map.setMinimumHeight(330)
        self.map.zone_clicked.connect(lambda _: self.window_.navigate("zones") if self.can(P.ZONES_VIEW) else None)
        map_card.body.addWidget(self.map)
        map_card.body.addWidget(legend_row())
        self.body.addWidget(map_card)

        # --- activity, upcoming, chart
        bottom_host = QWidget()
        bottom_host.setObjectName("Transparent")
        bottom = ResponsiveGrid(bottom_host, min_width=340, spacing=14, max_columns=3)
        self.activity_card = Card(padding=20, spacing=6)
        head = SectionHeader("Recent activity", "", "history")
        if self.can(P.HISTORY_VIEW):
            view_all = head.add_action(button("View all", None, "ghost", "sm"))
            view_all.clicked.connect(lambda: self.window_.navigate("history"))
        self.activity_card.body.addWidget(head)
        self.activity_list = QVBoxLayout()
        self.activity_list.setSpacing(0)
        self.activity_card.body.addLayout(self.activity_list)
        self.activity_card.body.addStretch(1)
        bottom.addWidget(self.activity_card)

        self.upcoming_card = Card(padding=20, spacing=6)
        head = SectionHeader("Upcoming", "", "calendar_clock")
        if self.can(P.SCHEDULES_VIEW):
            view_all = head.add_action(button("Calendar", None, "ghost", "sm"))
            view_all.clicked.connect(lambda: self.window_.navigate("schedules"))
        self.upcoming_card.body.addWidget(head)
        self.upcoming_list = QVBoxLayout()
        self.upcoming_list.setSpacing(0)
        self.upcoming_card.body.addLayout(self.upcoming_list)
        self.upcoming_card.body.addStretch(1)
        bottom.addWidget(self.upcoming_card)

        chart_card = Card(padding=20, spacing=10)
        chart_card.body.addWidget(SectionHeader("Broadcasts this week", "All broadcast types, last 7 days",
                                                "trend_up"))
        self.chart = BarChart()
        self.chart.setMinimumHeight(220)
        chart_card.body.addWidget(self.chart, 1)
        bottom.addWidget(chart_card)
        self.body.addWidget(bottom_host)
        self.body.addStretch(1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        bus = ctx.bus
        bus.broadcast_state_changed.connect(self._refresh_if_visible)
        bus.devices_refreshed.connect(self._refresh_if_visible)
        bus.data_changed.connect(lambda name: self._refresh_if_visible()
                                 if name in ("history", "schedules", "zones", "devices") else None)
        self._ticks = 0

    # ------------------------------------------------------------------ data
    def _greeting(self) -> str:
        hour = datetime.now().hour
        part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        user = self.user
        first = user.full_name.split()[0] if user and user.full_name else ""
        return f"Good {part}, {first}" if first else f"Good {part}"

    def on_show(self) -> None:
        self.refresh()
        self._timer.start(1000)

    def on_hide(self) -> None:
        self._timer.stop()

    def _refresh_if_visible(self, *args) -> None:
        if self.isVisible():
            self.refresh()

    def _tick(self) -> None:
        self._ticks += 1
        self._update_status()
        if self._ticks % 15 == 0:
            self.refresh()

    def refresh(self) -> None:
        ctx = self.ctx
        self.header.title.setText(self._greeting())
        today = ctx.history.stats_for_day()
        counts = ctx.history.daily_counts(7)
        self.stat_total.set_value(today.get("total", 0), f"{today.get('alarms', 0)} emergency alarm(s) today")
        self.spark_total.set_values([c for _, c in counts])
        completed = today.get("completed", 0)
        stopped = today.get("cancelled", 0) + today.get("interrupted", 0)
        self.stat_completed.set_value(completed, f"{stopped} stopped · {today.get('failed', 0) + today.get('missed', 0)}"
                                                 f" failed/missed", f"{format_duration(today.get('airtime', 0))} airtime",
                                      "success")
        end_of_day = datetime.combine(date.today(), datetime.max.time())
        upcoming_today = [u for u in ctx.schedules.upcoming(200, 1) if u[0] <= end_of_day]
        nxt = ctx.schedules.upcoming(1, 7)
        self.stat_upcoming.set_value(len(upcoming_today),
                                     f"Next: {nxt[0][1].name} at {nxt[0][0].strftime('%I:%M %p').lstrip('0')}"
                                     if nxt else "No upcoming broadcasts",
                                     "Paused" if ctx.settings.get_bool("schedules.paused") else "",
                                     "warning")
        summary = ctx.zones.summary(ctx.controller.active_zone_ids())
        self.stat_zones.set_value(f"{summary['enabled']}/{summary['total']}",
                                  f"{summary['ready']} real · {summary['simulated']} simulated"
                                  f" · {summary['offline']} offline",
                                  "All online" if not summary["offline"] else f"{summary['offline']} offline",
                                  "success" if not summary["offline"] else "danger")
        self.map.set_statuses(ctx.zones.zone_statuses(ctx.controller.active_zone_ids()))
        self.zone_summary.set(f"{summary['playable']} of {summary['total']} zones available",
                              "success" if not summary["offline"] else "warning")

        clear_layout(self.activity_list)
        records = ctx.history.recent(6)
        if records:
            for i, record in enumerate(records):
                if i:
                    self.activity_list.addWidget(divider())
                self.activity_list.addWidget(ActivityRow(record))
        else:
            self.activity_list.addWidget(EmptyState("radio", "No broadcasts yet",
                                                    "Live pages, announcements and alarms will appear here."))
        clear_layout(self.upcoming_list)
        upcoming = ctx.schedules.upcoming(5, 7)
        if upcoming:
            for i, (when, schedule) in enumerate(upcoming):
                if i:
                    self.upcoming_list.addWidget(divider())
                self.upcoming_list.addWidget(UpcomingRow(when, schedule))
        else:
            self.upcoming_list.addWidget(EmptyState("calendar", "Nothing scheduled",
                                                    "Create a schedule for bells and recurring announcements."))
        items = []
        for day, count in counts:
            items.append((day.strftime("%a"), count, f"{day.strftime('%A, %b %d')}: {count} broadcast(s)"))
        self.chart.set_data(items)
        self._update_status()

    def _update_status(self) -> None:
        ctx = self.ctx
        active = ctx.controller.active
        if active is not None:
            emergency = active.is_emergency
            self.status_bubble.set_icon("siren" if emergency else ("mic" if active.kind == "live" else "radio"),
                                        "danger" if emergency else "accent")
            self.status_title.setText(("EMERGENCY: " if emergency else "On air: ") + active.title)
            self.status_detail.setText(f"{active.kind_label} to {active.zone_names} · {format_duration(active.elapsed)}"
                                       f" · by {active.initiated_by}")
            self.status_stop.setVisible(True)
            self.status_stop.setEnabled(not emergency or self.can(P.ALARMS_TRIGGER))
        else:
            ok = ctx.registry.available
            self.status_bubble.set_icon("check_circle" if ok else "x_circle", "success" if ok else "danger")
            self.status_title.setText("Idle - ready to broadcast" if ok else "Audio subsystem unavailable")
            waiting = ctx.scheduler.waiting_count
            self.status_detail.setText(f"{waiting} scheduled broadcast(s) waiting for the line" if waiting else
                                       "No broadcast in progress. Select a quick action to begin.")
            self.status_stop.setVisible(False)
        out = ctx.registry.default_output()
        mic = ctx.registry.find_input(ctx.settings.get("audio.default_input") or "")
        self.fact_widgets["output"].setText(out.name if out else "No output detected")
        self.fact_widgets["mic"].setText(mic.name if mic else "No microphone detected")
        nxt = ctx.schedules.upcoming(1, 7)
        self.fact_widgets["next"].setText(f"{nxt[0][1].name} · {countdown(nxt[0][0])}" if nxt else "Nothing scheduled")
        last = ctx.history.recent(1)
        self.fact_widgets["last"].setText(f"{last[0].title} · {time_ago(last[0].started_at)}" if last else "None yet")

    # --------------------------------------------------------------- actions
    def _new_announcement(self) -> None:
        page = self.window_.page("announcements")
        if page is not None and hasattr(page, "new_announcement"):
            page.new_announcement()

    def _new_schedule(self) -> None:
        page = self.window_.page("schedules")
        if page is not None and hasattr(page, "new_schedule"):
            page.new_schedule()
