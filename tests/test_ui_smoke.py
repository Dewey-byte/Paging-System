"""UI smoke tests: every page and key dialog builds and refreshes for each role (offscreen)."""
from __future__ import annotations

import pytest

from conftest import login_as, pump


@pytest.fixture
def window(ctx):
    from ui.main_window import MainWindow
    win = MainWindow(ctx)
    win.resize(1440, 900)
    win.show()
    pump(0.05)
    yield win
    win.force_close()
    win.deleteLater()
    pump(0.05)


def test_admin_can_open_every_page(ctx, window):
    from ui.main_window import PAGES
    assert [p[0] for p in window.allowed] == [p[0] for p in PAGES]
    for key, *_ in PAGES:
        window.navigate(key)
        pump(0.02)
        page = window.pages[key]
        assert page.__class__.__name__ != "EmptyState", f"{key} failed to build"


def test_operator_sees_limited_navigation(ctx, make_user):
    make_user("Operator", "operator")
    login_as(ctx, "operator")
    from ui.main_window import MainWindow
    win = MainWindow(ctx)
    keys = [p[0] for p in win.allowed]
    assert "live" in keys and "users" not in keys and "settings" not in keys and "history" not in keys
    win.navigate("alarms")
    pump(0.02)
    win.force_close()
    win.deleteLater()
    pump(0.05)


def test_dialogs_build(ctx, window):
    from ui.alarm_management import ActivateDialog, AlarmConfigDialog
    from ui.announcements import AnnouncementDialog
    from ui.schedules import ScheduleDialog
    from ui.user_management import UserDialog
    from ui.zones import DeviceDialog, ZoneDialog
    dialogs = [
        AnnouncementDialog(window.page("announcements")),
        AnnouncementDialog(window.page("announcements"), ctx.announcements.list()[0]),
        ActivateDialog(window.page("alarms"), ctx.alarms.alarm_types()[0]),
        AlarmConfigDialog(window.page("alarms"), ctx.alarms.alarm_types()[0]),
        ScheduleDialog(window.page("schedules"), ctx.schedules.schedules()[0]),
        ScheduleDialog(window.page("schedules")),
        ZoneDialog(window.page("zones")),
        DeviceDialog(window.page("zones"), ctx.zones.devices()[0]),
        UserDialog(window.page("users")),
    ]
    for dialog in dialogs:
        dialog.show()
        pump(0.01)
        dialog.close()


def test_emergency_banner_and_theme_switch(ctx, window):
    from ui.theme import theme
    ctx.alarms.trigger(ctx.alarms.alarm_types()[0].id, None)
    pump(0.1)
    assert window.banner.isVisible() and "EMERGENCY" in window.banner.title.text()
    ctx.controller.stop(ctx.auth.current_user)
    pump(0.1)
    assert not window.banner.isVisible()
    window.toggle_theme()
    assert theme.is_dark
    window.toggle_theme()
    assert not theme.is_dark


def test_login_window_first_run_and_sign_in(qapp, tmp_path):
    from core.app_context import AppContext
    from ui.login_window import LoginWindow
    ctx = AppContext(data_dir=tmp_path / "fresh", force_simulated_audio=True, enable_logging=False)
    ctx.startup()
    win = LoginWindow(ctx)
    assert win.stack.currentWidget() is win.setup_form
    win.s_school.setText("Fresh School")
    win.s_name.setText("First Admin")
    win.s_user.setText("boss")
    win.s_pass.setText("Boss12345")
    win.s_confirm.setText("Boss12345")
    signed = []
    win.logged_in.connect(signed.append)
    win._create_admin()
    assert signed and signed[0].username == "boss"
    assert ctx.settings.school_name == "Fresh School"
    win.close()
    ctx.shutdown()
    from core import paths
    paths.set_data_dir(None)


def test_pages_refresh_on_state_changes_without_errors(ctx, window, monkeypatch):
    """Regression: every page must survive broadcast/emergency state changes while it is visible."""
    import sys
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append(exc[1]))
    alarm = ctx.alarms.alarm_types()[0]
    for key in ("alarms", "announcements", "dashboard", "live", "zones", "schedules", "history", "library", "users",
                "settings"):
        window.navigate(key)
        pump(0.02)
        ctx.alarms.trigger(alarm.id, None, interrupt=True)
        pump(0.08)
        ctx.controller.stop(ctx.auth.current_user)
        pump(0.08)
        ctx.bus.data_changed.emit("history")
        pump(0.02)
    assert not errors, errors


def test_schedule_dialog_offers_tts_and_recording(ctx, window):
    from datetime import date

    from ui.schedules import ScheduleDialog
    page = window.page("schedules")
    dialog = ScheduleDialog(page)
    dialog.show()
    for kind in ("tts", "recording", "audio_file", "alarm", "announcement"):
        dialog.content_type.set_value(kind)
        dialog._fill_content()
        pump(0.01)
        assert dialog.tts_editor.isVisible() == (kind == "tts")
        assert dialog.recorder_panel.isVisible() == (kind == "recording")
        assert dialog.chime_box.isVisible() == (kind != "alarm")
    dialog.content_type.set_value("tts")
    dialog._fill_content()
    assert dialog.chime.isChecked() and dialog.outro_chime.isChecked()   # spoken content defaults to chimes
    dialog.name.setText("Dismissal reminder")
    dialog.tts_editor.message.setPlainText("Classes are dismissed. Walk, do not run.")
    data = dialog._data()
    assert data["content_type"] == "tts" and data["tts_message"].startswith("Classes")
    dialog.close()

    saved = ctx.schedules.save(dict(data, time_of_day="16:40", start_date=date.today().isoformat()),
                               [ctx.zones.campus_zone().id])
    edit = ScheduleDialog(page, saved)
    edit.show()
    pump(0.01)
    assert edit.content_type.value() == "tts" and edit.tts_editor.text().startswith("Classes")
    edit.close()


def test_live_page_shows_closing_state_during_outro(ctx, window):
    import numpy as np

    from core.paging_manager import BroadcastRequest, Priority
    from services.audio_sources import AudioSource, BufferSource, LiveReader, ProgramSource

    class FakeMic(AudioSource):
        is_live = True

        def open_reader(self, sample_rate):
            return LiveReader(sample_rate)

    window.navigate("live")
    pump(0.02)
    page = window.page("live")
    outro = BufferSource(np.zeros(48000 * 5, dtype=np.float32), 48000, "outro")
    ctx.controller.start(BroadcastRequest(kind="live", title="Live Paging", zone_ids=[ctx.zones.campus_zone().id],
                                          source=ProgramSource(FakeMic(), None, outro), priority=Priority.LIVE,
                                          user=ctx.auth.current_user))
    pump(0.05)
    page.stop_broadcast()
    pump(0.05)
    assert page.state_label.text() == "ENDING - OUTRO CHIME" and page.talk.state == "closing"
    assert page.stop_button.isEnabled() and "OUTRO" in window.banner.title.text()
    page.stop_broadcast()
    pump(0.05)
    assert not ctx.controller.is_active and page.state_label.text() == "READY"
