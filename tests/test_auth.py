"""Authentication, password policy, lockout and role-based access control."""
from __future__ import annotations

import pytest

from conftest import ADMIN_PASSWORD, login_as
from core.authentication import hash_password, verify_password
from core.errors import AuthError, PermissionDenied, ValidationError
from core.permissions import P


def test_password_hash_roundtrip():
    stored = hash_password("S3cret-pass")
    assert stored.startswith("scrypt$") and "S3cret" not in stored
    assert verify_password("S3cret-pass", stored)
    assert not verify_password("wrong", stored)
    assert hash_password("S3cret-pass") != stored  # random salt


def test_login_success_and_failure(ctx):
    ctx.auth.logout()
    with pytest.raises(AuthError):
        ctx.auth.login("admin", "nope")
    with pytest.raises(AuthError):
        ctx.auth.login("ghost", "whatever1")
    user = ctx.auth.login("ADMIN", ADMIN_PASSWORD)
    assert user.role_name == "Administrator"
    assert ctx.auth.current_user.last_login_at


def test_lockout_after_repeated_failures(ctx, make_user):
    make_user("Operator", "lockme")
    ctx.settings.set("security.lockout_attempts", 3)
    ctx.auth.logout()
    for _ in range(3):
        with pytest.raises(AuthError):
            ctx.auth.login("lockme", "badpass1")
    with pytest.raises(AuthError, match="locked"):
        ctx.auth.login("lockme", "Passw0rd1")


def test_inactive_account_cannot_sign_in(ctx, make_user):
    user = make_user("Operator", "sleepy")
    ctx.users.set_active(user.id, False)
    ctx.auth.logout()
    with pytest.raises(AuthError, match="deactivated"):
        ctx.auth.login("sleepy", "Passw0rd1")


def test_password_policy(ctx):
    with pytest.raises(ValidationError):
        ctx.auth.validate_password("short1")
    with pytest.raises(ValidationError):
        ctx.auth.validate_password("lettersonly")
    ctx.auth.validate_password("Good1234")


def test_change_password(ctx):
    with pytest.raises(ValidationError):
        ctx.auth.change_password("wrong", "NewPass99")
    ctx.auth.change_password(ADMIN_PASSWORD, "NewPass99")
    ctx.auth.logout()
    assert ctx.auth.login("admin", "NewPass99")


def test_last_admin_and_self_protection(ctx, make_user):
    me = ctx.auth.current_user
    with pytest.raises(ValidationError):
        ctx.users.set_active(me.id, False)
    with pytest.raises(ValidationError):
        ctx.users.delete_user(me.id)
    operator_role = next(r for r in ctx.users.list_roles() if r.name == "Operator")
    with pytest.raises(ValidationError):
        ctx.users.update_user(me.id, full_name=me.full_name, username=me.username, email="", role_id=operator_role.id,
                              extra_permissions=[], is_active=True)


def test_role_permissions_and_extra_grants(ctx, make_user):
    make_user("Operator", "op1")
    make_user("Operator", "op2", extra=[P.ALARMS_TRIGGER])
    personnel = make_user("School Personnel", "teacher")
    op1 = login_as(ctx, "op1")
    assert op1.has(P.PAGING_LIVE) and not op1.has(P.ALARMS_TRIGGER) and not op1.has(P.USERS_MANAGE)
    op2 = login_as(ctx, "op2")
    assert op2.has(P.ALARMS_TRIGGER)
    teacher = login_as(ctx, "teacher")
    assert teacher.has(P.ANNOUNCEMENTS_MANAGE) and not teacher.has(P.SCHEDULES_MANAGE)
    with pytest.raises(PermissionDenied):
        ctx.users.create_user(full_name="X", username="xx1", email="", role_id=personnel.role_id, password="Passw0rd1")


def test_duplicate_username_rejected(ctx, make_user):
    make_user("Operator", "dupe")
    with pytest.raises(ValidationError, match="taken"):
        make_user("Operator", "DUPE")


def test_activity_log_records_actions(ctx):
    actions = [e.action for e in ctx.activity.search(limit=50)]
    assert "Signed in" in actions and "Created initial administrator" in actions
