"""User and role administration with safeguards (last admin, self-lockout)."""
from __future__ import annotations

import re

from database.models import Role, User
from database.repositories import Repositories

from .activity_logger import ActivityLogger
from .authentication import AuthManager, hash_password
from .errors import NotFoundError, PermissionDenied, ValidationError
from .events import EventBus
from .permissions import ALL_PERMISSIONS, ROLE_ADMIN, P

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UserManager:
    def __init__(self, repos: Repositories, auth: AuthManager, activity: ActivityLogger, bus: EventBus):
        self._repos = repos
        self._auth = auth
        self._activity = activity
        self._bus = bus

    # ------------------------------------------------------------------ query
    def list_users(self) -> list[User]:
        return self._repos.users.all()

    def get(self, user_id: int) -> User:
        user = self._repos.users.get(user_id)
        if not user:
            raise NotFoundError("That user account no longer exists.")
        return user

    def list_roles(self) -> list[Role]:
        return self._repos.roles.all()

    # -------------------------------------------------------------- bootstrap
    def create_initial_admin(self, full_name: str, username: str, password: str, email: str = "") -> User:
        if self._repos.users.count() > 0:
            raise PermissionDenied("The administrator account has already been created.")
        self._validate_identity(full_name, username, email)
        self._auth.validate_password(password)
        role = self._repos.roles.get_by_name(ROLE_ADMIN)
        user_id = self._repos.users.create(username=username, full_name=full_name, email=email,
                                           password_hash=hash_password(password), role_id=role.id)
        user = self._repos.users.get(user_id)
        self._activity.log("Created initial administrator", "User Management", f"Username '{username}'", user=user)
        return user

    # ------------------------------------------------------------------- CRUD
    def create_user(self, *, full_name: str, username: str, email: str, role_id: int, password: str,
                    extra_permissions: list[str] | None = None, is_active: bool = True,
                    must_change_password: bool = True) -> User:
        self._auth.require(P.USERS_MANAGE)
        self._validate_identity(full_name, username, email)
        if self._repos.users.get_by_username(username):
            raise ValidationError(f"The username '{username}' is already taken.")
        self._auth.validate_password(password)
        role = self._repos.roles.get(role_id)
        if not role:
            raise ValidationError("Select a valid role.")
        user_id = self._repos.users.create(
            username=username, full_name=full_name, email=email, password_hash=hash_password(password),
            role_id=role_id, extra_permissions=self._clean_perms(extra_permissions), is_active=is_active,
            must_change_password=must_change_password)
        self._activity.log("Created user", "User Management", f"{full_name} ({username}) as {role.name}")
        self._bus.data_changed.emit("users")
        return self.get(user_id)

    def update_user(self, user_id: int, *, full_name: str, username: str, email: str, role_id: int,
                    extra_permissions: list[str] | None, is_active: bool) -> User:
        self._auth.require(P.USERS_MANAGE)
        user = self.get(user_id)
        self._validate_identity(full_name, username, email)
        other = self._repos.users.get_by_username(username)
        if other and other.id != user_id:
            raise ValidationError(f"The username '{username}' is already taken.")
        role = self._repos.roles.get(role_id)
        if not role:
            raise ValidationError("Select a valid role.")
        me = self._auth.current_user
        removing_admin = user.role_name == ROLE_ADMIN and (role.name != ROLE_ADMIN or not is_active)
        if me and me.id == user_id and (not is_active or removing_admin):
            raise ValidationError("You cannot deactivate your own account or remove your own administrator role.")
        if removing_admin and user.is_active and self._repos.users.count_active_with_role(ROLE_ADMIN) <= 1:
            raise ValidationError("At least one active Administrator account must remain.")
        self._repos.users.update(user_id, full_name=full_name.strip(), username=username.strip(),
                                 email=email.strip(), role_id=role_id,
                                 extra_permissions=self._clean_perms(extra_permissions), is_active=is_active)
        self._activity.log("Updated user", "User Management", f"{full_name} ({username}), role {role.name}, "
                                                              f"{'active' if is_active else 'inactive'}")
        self._after_change(user_id)
        return self.get(user_id)

    def set_active(self, user_id: int, active: bool) -> None:
        self._auth.require(P.USERS_MANAGE)
        user = self.get(user_id)
        me = self._auth.current_user
        if not active:
            if me and me.id == user_id:
                raise ValidationError("You cannot deactivate your own account.")
            if user.role_name == ROLE_ADMIN and self._repos.users.count_active_with_role(ROLE_ADMIN) <= 1:
                raise ValidationError("At least one active Administrator account must remain.")
        self._repos.users.update(user_id, is_active=active, failed_attempts=0, locked_until=None)
        self._activity.log("Activated user" if active else "Deactivated user", "User Management",
                           f"{user.full_name} ({user.username})", level="info" if active else "warning")
        self._after_change(user_id)

    def delete_user(self, user_id: int) -> None:
        self._auth.require(P.USERS_MANAGE)
        user = self.get(user_id)
        me = self._auth.current_user
        if me and me.id == user_id:
            raise ValidationError("You cannot delete your own account.")
        if user.role_name == ROLE_ADMIN and user.is_active and \
                self._repos.users.count_active_with_role(ROLE_ADMIN) <= 1:
            raise ValidationError("At least one active Administrator account must remain.")
        self._repos.users.delete(user_id)
        self._activity.log("Deleted user", "User Management", f"{user.full_name} ({user.username})", "warning")
        self._bus.data_changed.emit("users")

    def reset_password(self, user_id: int, new_password: str, must_change: bool = True) -> None:
        self._auth.require(P.USERS_MANAGE)
        user = self.get(user_id)
        self._auth.validate_password(new_password)
        self._repos.users.update(user_id, password_hash=hash_password(new_password),
                                 must_change_password=must_change, failed_attempts=0, locked_until=None)
        self._activity.log("Reset password", "User Management", f"{user.full_name} ({user.username})", "warning")
        self._after_change(user_id)

    def update_role(self, role_id: int, description: str, permissions: list[str]) -> None:
        self._auth.require(P.USERS_MANAGE)
        role = self._repos.roles.get(role_id)
        if not role:
            raise NotFoundError("That role no longer exists.")
        if role.name == ROLE_ADMIN:
            raise ValidationError("The Administrator role always has full access and cannot be changed.")
        self._repos.roles.update(role_id, description=description.strip(), permissions=self._clean_perms(permissions))
        self._activity.log("Updated role permissions", "User Management", f"{role.name}: {len(permissions)} permissions")
        self._auth.refresh_current_user()
        self._bus.data_changed.emit("roles")
        self._bus.data_changed.emit("users")

    # ---------------------------------------------------------------- helpers
    def _after_change(self, user_id: int) -> None:
        if self._auth.current_user and self._auth.current_user.id == user_id:
            self._auth.refresh_current_user()
        self._bus.data_changed.emit("users")

    @staticmethod
    def _clean_perms(perms: list[str] | None) -> list[str]:
        return sorted({p for p in (perms or []) if p in ALL_PERMISSIONS})

    @staticmethod
    def _validate_identity(full_name: str, username: str, email: str) -> None:
        if not (full_name or "").strip():
            raise ValidationError("Enter the person's full name.")
        if not _USERNAME_RE.match((username or "").strip()):
            raise ValidationError("Usernames must be 3-32 characters: letters, numbers, dot, dash or underscore.")
        if email and not _EMAIL_RE.match(email.strip()):
            raise ValidationError("Enter a valid email address or leave it empty.")
