"""Authentication: password hashing, login/logout, lockout and permission checks."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta

from database.database import now_str, parse_ts
from database.models import User
from database.repositories import Repositories

from .errors import AuthError, PermissionDenied, ValidationError
from .events import EventBus
from .permissions import PERMISSION_LABELS
from .settings_manager import SettingsManager

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r),
                                p=int(p), dklen=len(digest_hex) // 2)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


class AuthManager:
    def __init__(self, repos: Repositories, settings: SettingsManager, bus: EventBus):
        self._repos = repos
        self._settings = settings
        self._bus = bus
        self.current_user: User | None = None
        self.activity = None  # set by AppContext (ActivityLogger) to avoid a circular dependency

    # -------------------------------------------------------------- passwords
    def password_issues(self, password: str) -> list[str]:
        issues = []
        min_len = max(6, self._settings.get_int("security.min_password_length"))
        if len(password) < min_len:
            issues.append(f"at least {min_len} characters")
        if not re.search(r"[A-Za-z]", password):
            issues.append("at least one letter")
        if not re.search(r"\d", password):
            issues.append("at least one number")
        return issues

    def validate_password(self, password: str) -> None:
        issues = self.password_issues(password)
        if issues:
            raise ValidationError("Password must contain " + ", ".join(issues) + ".")

    # ------------------------------------------------------------------ login
    def has_users(self) -> bool:
        return self._repos.users.count() > 0

    def login(self, username: str, password: str) -> User:
        username = (username or "").strip()
        if not username or not password:
            raise AuthError("Enter your username and password.")
        user = self._repos.users.get_by_username(username)
        if user is None:
            self._log("Failed sign-in", f"Unknown username '{username}'", "warning")
            raise AuthError("Invalid username or password.")
        locked_until = parse_ts(user.locked_until)
        if locked_until and locked_until > datetime.now():
            minutes = max(1, int((locked_until - datetime.now()).total_seconds() // 60) + 1)
            raise AuthError(f"This account is temporarily locked after repeated failed attempts. "
                            f"Try again in {minutes} minute{'s' if minutes != 1 else ''}.")
        if not verify_password(password, user.password_hash):
            attempts = user.failed_attempts + 1
            limit = max(1, self._settings.get_int("security.lockout_attempts"))
            fields = {"failed_attempts": attempts}
            if attempts >= limit:
                minutes = max(1, self._settings.get_int("security.lockout_minutes"))
                fields["locked_until"] = (datetime.now() + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
                fields["failed_attempts"] = 0
                self._log("Account locked", f"'{user.username}' locked for {minutes} min after {attempts} "
                                            "failed attempts", "warning", user)
            self._repos.users.update(user.id, **fields)
            self._log("Failed sign-in", f"Wrong password for '{user.username}'", "warning", user)
            raise AuthError("Invalid username or password.")
        if not user.is_active:
            self._log("Blocked sign-in", f"Deactivated account '{user.username}'", "warning", user)
            raise AuthError("This account has been deactivated. Please contact your administrator.")
        self._repos.users.update(user.id, failed_attempts=0, locked_until=None, last_login_at=now_str())
        self.current_user = self._repos.users.get(user.id)
        self._log("Signed in", f"{self.current_user.full_name} ({self.current_user.role_name})", user=self.current_user)
        self._bus.session_changed.emit(self.current_user)
        return self.current_user

    def logout(self, reason: str = "") -> None:
        if self.current_user:
            self._log("Signed out", reason, user=self.current_user)
        self.current_user = None
        self._bus.session_changed.emit(None)

    def refresh_current_user(self) -> None:
        if self.current_user:
            self.current_user = self._repos.users.get(self.current_user.id)

    def verify_current_password(self, password: str) -> bool:
        return bool(self.current_user and verify_password(password, self.current_user.password_hash))

    def change_password(self, current: str, new: str) -> None:
        user = self.require_user()
        if not verify_password(current, user.password_hash):
            raise ValidationError("Your current password is incorrect.")
        if current == new:
            raise ValidationError("Choose a password different from the current one.")
        self.validate_password(new)
        self._repos.users.update(user.id, password_hash=hash_password(new), must_change_password=False)
        self.refresh_current_user()
        self._log("Changed password", user=user)

    # ------------------------------------------------------------ permissions
    def require_user(self) -> User:
        if self.current_user is None:
            raise PermissionDenied("Please sign in first.")
        return self.current_user

    def has(self, permission: str, user: User | None = None) -> bool:
        user = user or self.current_user
        return bool(user and user.has(permission))

    def require(self, permission: str, user: User | None = None) -> User:
        user = user or self.require_user()
        if not user.has(permission):
            label = PERMISSION_LABELS.get(permission, permission)
            raise PermissionDenied(f"Your account is not authorized to perform this action ({label}).")
        return user

    # ---------------------------------------------------------------- helpers
    def _log(self, action: str, details: str = "", level: str = "info", user: User | None = None) -> None:
        if self.activity is not None:
            self.activity.log(action, "Authentication", details, level, user=user, system=user is None)
