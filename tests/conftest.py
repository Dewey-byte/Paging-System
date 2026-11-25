"""Shared fixtures: an isolated AppContext per test (temporary data folder, simulated audio)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ADMIN_PASSWORD = "Admin1234"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def pump(seconds: float) -> None:
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_until(predicate, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.instance().processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def ctx(qapp, tmp_path):
    from core.app_context import AppContext
    context = AppContext(data_dir=tmp_path / "data", force_simulated_audio=True, enable_logging=False)
    context.startup()
    context.complete_first_run(school_name="Test High School", full_name="Ada Admin", username="admin",
                               password=ADMIN_PASSWORD, demo_content=True, use_local_output=False)
    context.auth.login("admin", ADMIN_PASSWORD)
    context.scheduler.stop()  # tests drive the scheduler explicitly with synthetic times
    yield context
    context.scheduler.stop()
    context.controller.stop_all("test teardown")
    pump(0.05)
    context.shutdown()
    from core import paths
    paths.set_data_dir(None)


@pytest.fixture
def make_user(ctx):
    def factory(role_name: str, username: str, extra: list[str] | None = None, password: str = "Passw0rd1"):
        role = next(r for r in ctx.users.list_roles() if r.name == role_name)
        return ctx.users.create_user(full_name=f"{role_name} User", username=username, email="", role_id=role.id,
                                     password=password, extra_permissions=extra or [], must_change_password=False)
    return factory


def login_as(ctx, username: str, password: str = "Passw0rd1"):
    ctx.auth.logout()
    return ctx.auth.login(username, password)
