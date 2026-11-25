"""User and role administration plus the activity (audit) log."""
from __future__ import annotations

import secrets
import string
from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QFileDialog, QGridLayout, QHBoxLayout,
                               QLineEdit, QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget)

from core.permissions import GRANTABLE_PERMISSIONS, PERMISSIONS, ROLE_ADMIN, P
from database.database import parse_ts
from services.reporting import export_activity_csv

from .components.base import Badge, Card, InfoBanner, SectionHeader, button, label
from .components.controls import SearchBox, ToggleSwitch
from .components.dialogs import Dialog, confirm
from .components.page import BasePage
from .components.tables import Column, DataTable

ROLE_TONES = {"Administrator": "danger", "School Personnel": "accent", "Operator": "purple"}
LEVEL_TONES = {"info": "info", "warning": "warning", "critical": "danger"}


def _user_status(user) -> tuple[str, str]:
    locked = parse_ts(user.locked_until)
    from datetime import datetime
    if not user.is_active:
        return "Inactive", "neutral"
    if locked and locked > datetime.now():
        return "Locked", "warning"
    return "Active", "success"


def _temp_password() -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(10))
        if any(c.isdigit() for c in value) and any(c.isalpha() for c in value):
            return value


class UserDialog(Dialog):
    def __init__(self, page, user=None):
        super().__init__(page, "Edit user" if user else "Add user",
                         "Accounts sign in to the console. Permissions come from the role plus any extras below.",
                         "user_plus" if not user else "user", width=640, scroll=True)
        self.ctx = page.ctx
        self.user = user
        self.setMinimumHeight(640)
        self.full_name = QLineEdit(user.full_name if user else "")
        self.username = QLineEdit(user.username if user else "")
        self.email = QLineEdit(user.email if user else "")
        self.email.setPlaceholderText("Optional")
        self.role = QComboBox()
        for role in self.ctx.users.list_roles():
            self.role.addItem(role.name, role.id)
        if user:
            self.role.setCurrentIndex(max(0, self.role.findData(user.role_id)))
        else:
            self.role.setCurrentIndex(max(0, self.role.findText("School Personnel")))
        self.body.addLayout(self.row(self.field("Full name", self.full_name), self.field("Username", self.username)))
        self.body.addLayout(self.row(self.field("Email", self.email), self.field("Role", self.role)))
        if not user:
            self.password = QLineEdit()
            self.password.setEchoMode(QLineEdit.EchoMode.Password)
            self.confirm_pw = QLineEdit()
            self.confirm_pw.setEchoMode(QLineEdit.EchoMode.Password)
            self.body.addLayout(self.row(self.field("Password", self.password),
                                         self.field("Confirm password", self.confirm_pw)))
            gen = button("Generate temporary password", "key", "ghost", "sm")
            gen.clicked.connect(self._generate)
            self.generated = label("", "mono")
            self.generated.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row = QHBoxLayout()
            row.addWidget(gen)
            row.addWidget(self.generated, 1)
            self.body.addLayout(row)
            self.must_change = QCheckBox("Require a new password at first sign-in")
            self.must_change.setChecked(True)
            self.body.addWidget(self.must_change)
        active_row = QHBoxLayout()
        active_row.addWidget(label("Account active"), 1)
        self.active = ToggleSwitch(user.is_active if user else True)
        active_row.addWidget(self.active)
        self.body.addLayout(active_row)

        self.body.addWidget(label("ADDITIONAL PERMISSIONS", "overline"))
        self.body.addWidget(label("Grant specific abilities on top of the role (e.g. let an operator trigger alarms "
                                  "or let personnel manage schedules).", "caption", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        self.extra_boxes: dict[str, QCheckBox] = {}
        info = {p.key: p for p in PERMISSIONS}
        for i, key in enumerate(GRANTABLE_PERMISSIONS):
            box = QCheckBox(info[key].label)
            box.setToolTip(info[key].description)
            box.setChecked(bool(user and key in user.extra_permissions))
            self.extra_boxes[key] = box
            grid.addWidget(box, i // 2, i % 2)
        self.body.addLayout(grid)
        self.role.currentIndexChanged.connect(self._role_changed)
        self._role_changed()
        self.add_cancel()
        self.add_button("Save user", "primary", self._save, "check", default=True)

    def _role_changed(self) -> None:
        role = next((r for r in self.ctx.users.list_roles() if r.id == self.role.currentData()), None)
        role_perms = set(role.permissions) if role else set()
        for key, box in self.extra_boxes.items():
            included = key in role_perms
            box.setEnabled(not included)
            if included:
                box.setChecked(True)
            box.setToolTip(("Included in the role. " if included else "") + box.toolTip().replace(
                "Included in the role. ", ""))

    def _generate(self) -> None:
        value = _temp_password()
        self.password.setText(value)
        self.confirm_pw.setText(value)
        self.generated.setText(f"Temporary password: {value}")
        QGuiApplication.clipboard().setText(value)
        self.must_change.setChecked(True)

    def _save(self) -> None:
        role = next((r for r in self.ctx.users.list_roles() if r.id == self.role.currentData()), None)
        role_perms = set(role.permissions) if role else set()
        extras = [k for k, b in self.extra_boxes.items() if b.isChecked() and k not in role_perms]
        try:
            if self.user:
                self.saved = self.ctx.users.update_user(
                    self.user.id, full_name=self.full_name.text(), username=self.username.text(),
                    email=self.email.text(), role_id=self.role.currentData(), extra_permissions=extras,
                    is_active=self.active.isChecked())
            else:
                if self.password.text() != self.confirm_pw.text():
                    self.show_error("The passwords do not match.")
                    return
                self.saved = self.ctx.users.create_user(
                    full_name=self.full_name.text(), username=self.username.text(), email=self.email.text(),
                    role_id=self.role.currentData(), password=self.password.text(), extra_permissions=extras,
                    is_active=self.active.isChecked(), must_change_password=self.must_change.isChecked())
        except Exception as exc:  # noqa: BLE001
            self.show_error(exc)
            return
        self.accept()


class ResetPasswordDialog(Dialog):
    def __init__(self, page, user):
        super().__init__(page, f"Reset password for {user.full_name}", "The user's lockout is also cleared.", "key",
                         width=480)
        self.ctx = page.ctx
        self.user = user
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_pw = QLineEdit()
        self.confirm_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.body.addWidget(self.field("New password", self.password))
        self.body.addWidget(self.field("Confirm password", self.confirm_pw))
        gen = button("Generate temporary password", "key", "ghost", "sm")
        gen.clicked.connect(self._generate)
        self.generated = label("", "mono")
        self.generated.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(gen, alignment=Qt.AlignmentFlag.AlignLeft)
        self.body.addWidget(self.generated)
        self.must_change = QCheckBox("Require a new password at next sign-in")
        self.must_change.setChecked(True)
        self.body.addWidget(self.must_change)
        self.add_cancel()
        self.add_button("Reset password", "primary", self._save, default=True)

    def _generate(self) -> None:
        value = _temp_password()
        self.password.setText(value)
        self.confirm_pw.setText(value)
        self.generated.setText(f"Temporary password (copied): {value}")
        QGuiApplication.clipboard().setText(value)

    def _save(self) -> None:
        if self.password.text() != self.confirm_pw.text():
            self.show_error("The passwords do not match.")
            return
        try:
            self.ctx.users.reset_password(self.user.id, self.password.text(), self.must_change.isChecked())
        except Exception as exc:  # noqa: BLE001
            self.show_error(exc)
            return
        self.accept()


class RoleCard(Card):
    def __init__(self, page, role):
        super().__init__(padding=18, spacing=10)
        self.page = page
        self.role = role
        editable = role.name != ROLE_ADMIN
        head = SectionHeader(role.name, f"{role.user_count} user(s)", "shield")
        head.add_action(Badge("Full access" if not editable else f"{len(role.permissions)} permissions",
                              ROLE_TONES.get(role.name, "neutral"), small=True))
        self.body.addWidget(head)
        self.description = QLineEdit(role.description)
        self.description.setEnabled(editable)
        self.body.addWidget(self.description)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        self.boxes: dict[str, QCheckBox] = {}
        for i, perm in enumerate(PERMISSIONS):
            box = QCheckBox(perm.label)
            box.setToolTip(f"{perm.group}: {perm.description}")
            box.setChecked(perm.key in role.permissions)
            box.setEnabled(editable)
            self.boxes[perm.key] = box
            grid.addWidget(box, i // 2, i % 2)
        self.body.addLayout(grid)
        if editable:
            save = button("Save role", "save", "subtle", "sm")
            save.clicked.connect(self._save)
            self.body.addWidget(save, alignment=Qt.AlignmentFlag.AlignRight)
        else:
            self.body.addWidget(label("The Administrator role always keeps every permission.", "caption"))

    def _save(self) -> None:
        perms = [k for k, b in self.boxes.items() if b.isChecked()]
        self.page.guarded(self.page.ctx.users.update_role, self.role.id, self.description.text(), perms,
                          success=f"{self.role.name} updated")


class UsersPage(BasePage):
    title = "User Management"
    subtitle = "Accounts, roles, permissions and the audit trail of every action."

    def __init__(self, ctx, window):
        super().__init__(ctx, window)
        self.add_button = self.header.add_action(button("Add user", "user_plus", "primary"))
        self.add_button.clicked.connect(self.add_user)
        self.tabs = QTabWidget()
        self.body.addWidget(self.tabs, 1)

        users_tab = Card(padding=16, spacing=10)
        self.search = SearchBox("Search users...")
        self.search.textChanged.connect(self._filter_users)
        users_tab.body.addWidget(self.search, alignment=Qt.AlignmentFlag.AlignLeft)
        self.table = DataTable([
            Column("User", lambda u: u.full_name, kind="title", width=260, subtitle=lambda u: f"@{u.username}",
                   icon=lambda u: ("user", ROLE_TONES.get(u.role_name, "neutral"))),
            Column("Role", lambda u: u.role_name, kind="badge", width=170,
                   tone=lambda u: ROLE_TONES.get(u.role_name, "neutral")),
            Column("Extra permissions", lambda u: f"+{len(u.extra_permissions)}" if u.extra_permissions else "—",
                   width=140, kind="muted", tooltip=lambda u: ", ".join(u.extra_permissions)),
            Column("Email", lambda u: u.email or "—", stretch=True, kind="muted"),
            Column("Status", lambda u: _user_status(u)[0], kind="badge", width=110, tone=lambda u: _user_status(u)[1]),
            Column("Last sign-in", lambda u: (u.last_login_at or "Never")[:16], width=140, kind="muted"),
            Column("", width=190, kind="actions"),
        ], row_height=56)
        from PySide6.QtWidgets import QHeaderView
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(6, 190)
        self.table.set_actions(self._user_actions)
        self.table.row_activated.connect(self.edit_user)
        self.table.setMinimumHeight(420)
        users_tab.body.addWidget(self.table)
        self.tabs.addTab(users_tab, "Users")

        roles_tab = QWidget()
        roles_tab.setObjectName("Transparent")
        self.roles_layout = QVBoxLayout(roles_tab)
        self.roles_layout.setContentsMargins(0, 10, 0, 0)
        self.roles_layout.setSpacing(14)
        self.tabs.addTab(roles_tab, "Roles && permissions")

        activity_tab = Card(padding=16, spacing=10)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.log_search = SearchBox("Search actions, users, details...")
        self.log_search.textChanged.connect(lambda: self._log_timer.start(250))
        bar.addWidget(self.log_search, 2)
        self.level = QComboBox()
        for text, value in (("All levels", ""), ("Info", "info"), ("Warning", "warning"), ("Critical", "critical")):
            self.level.addItem(text, value)
        self.level.currentIndexChanged.connect(self.refresh_activity)
        bar.addWidget(self.level)
        self.module = QComboBox()
        self.module.currentIndexChanged.connect(self.refresh_activity)
        bar.addWidget(self.module)
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("MMM d, yyyy")
        start = date.today() - timedelta(days=30)
        self.date_from.setDate(QDate(start.year, start.month, start.day))
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("MMM d, yyyy")
        self.date_to.setDate(QDate.currentDate())
        for edit in (self.date_from, self.date_to):
            edit.dateChanged.connect(self.refresh_activity)
        bar.addWidget(self.date_from)
        bar.addWidget(label("to", "muted"))
        bar.addWidget(self.date_to)
        export = button("Export CSV", "download", None, "sm")
        export.clicked.connect(self.export_activity)
        bar.addWidget(export)
        activity_tab.body.addLayout(bar)
        self.log_table = DataTable([
            Column("Time", lambda e: e.created_at, width=150, kind="muted"),
            Column("User", lambda e: e.username, width=120),
            Column("Level", lambda e: e.level.title(), kind="badge", width=100,
                   tone=lambda e: LEVEL_TONES.get(e.level, "neutral")),
            Column("Module", lambda e: e.module, width=150, kind="muted"),
            Column("Action", lambda e: e.action, width=210, kind="bold"),
            Column("Details", lambda e: e.details, stretch=True, tooltip=lambda e: e.details),
        ], row_height=40)
        self.log_table.setMinimumHeight(420)
        activity_tab.body.addWidget(self.log_table)
        self.log_count = label("", "caption")
        activity_tab.body.addWidget(self.log_count)
        self.tabs.addTab(activity_tab, "Activity log")

        self._log_timer = QTimer(self)
        self._log_timer.setSingleShot(True)
        self._log_timer.timeout.connect(self.refresh_activity)
        ctx.bus.data_changed.connect(lambda name: self.refresh() if name in ("users", "roles") and self.isVisible()
                                     else None)

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.users = self.ctx.users.list_users()
        self._filter_users()
        while self.roles_layout.count():
            widget = self.roles_layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.roles_layout.addWidget(InfoBanner("Role permissions apply to every account with that role. Use "
                                               "<b>Additional permissions</b> on a user to grant exceptions.", "info"))
        for role in self.ctx.users.list_roles():
            self.roles_layout.addWidget(RoleCard(self, role))
        self.roles_layout.addStretch(1)
        current = self.module.currentData()
        self.module.blockSignals(True)
        self.module.clear()
        self.module.addItem("All modules", "")
        for name in self.ctx.activity.modules():
            self.module.addItem(name, name)
        self.module.setCurrentIndex(max(0, self.module.findData(current or "")))
        self.module.blockSignals(False)
        self.refresh_activity()
        self.tabs.setTabText(0, f"Users ({len(self.users)})")

    def _filter_users(self) -> None:
        text = self.search.text().strip().lower()
        rows = [u for u in getattr(self, "users", []) if not text or text in u.full_name.lower() or
                text in u.username.lower() or text in u.role_name.lower() or text in u.email.lower()]
        self.table.set_rows(rows)

    def _qdate(self, edit) -> date:
        q = edit.date()
        return date(q.year(), q.month(), q.day())

    def refresh_activity(self) -> None:
        entries = self.ctx.activity.search(text=self.log_search.text().strip(), level=self.level.currentData() or "",
                                           module=self.module.currentData() or "",
                                           date_from=self._qdate(self.date_from), date_to=self._qdate(self.date_to))
        self.log_table.set_rows(entries)
        self.log_count.setText(f"{len(entries)} entr{'ies' if len(entries) != 1 else 'y'} shown (newest first, up to "
                               "2000)")
        self._entries = entries

    def _user_actions(self, user) -> QWidget:
        holder = QWidget()
        holder.setObjectName("Transparent")
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(2)
        me = self.user and self.user.id == user.id
        edit = button("", "edit", "ghost", tooltip="Edit user")
        edit.clicked.connect(lambda: self.edit_user(user))
        reset = button("", "key", "ghost", tooltip="Reset password")
        reset.clicked.connect(lambda: self.reset_password(user))
        toggle = button("", "user_x" if user.is_active else "user_check", "ghost",
                        tooltip="Deactivate account" if user.is_active else "Activate account",
                        icon_color="warning" if user.is_active else "success")
        toggle.clicked.connect(lambda: self.set_active(user, not user.is_active))
        toggle.setEnabled(not me)
        delete = button("", "trash", "ghost", tooltip="Delete user", icon_color="danger")
        delete.clicked.connect(lambda: self.delete_user(user))
        delete.setEnabled(not me)
        for b in (edit, reset, toggle, delete):
            layout.addWidget(b)
        layout.addStretch(1)
        return holder

    # ---------------------------------------------------------------- actions
    def add_user(self) -> None:
        dialog = UserDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.toast("success", "User created", f"{dialog.saved.full_name} ({dialog.saved.role_name})")

    def edit_user(self, user) -> None:
        dialog = UserDialog(self, user)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.toast("success", "User updated", dialog.saved.full_name)

    def reset_password(self, user) -> None:
        if ResetPasswordDialog(self, user).exec() == QDialog.DialogCode.Accepted:
            self.toast("success", "Password reset", user.full_name)

    def set_active(self, user, active: bool) -> None:
        if not active and not confirm(self, "Deactivate account?", f"<b>{user.full_name}</b> will no longer be able to "
                                                                   "sign in. History is kept.", "Deactivate",
                                      danger=True, icon_name="user_x"):
            return
        self.guarded(self.ctx.users.set_active, user.id, active,
                     success="Account activated" if active else "Account deactivated")

    def delete_user(self, user) -> None:
        if confirm(self, "Delete user?", f"<b>{user.full_name}</b> will be permanently deleted. Broadcast history keeps "
                                         "their name. Consider deactivating instead.", "Delete user", danger=True,
                   icon_name="trash"):
            self.guarded(self.ctx.users.delete_user, user.id, success="User deleted")

    def export_activity(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export activity log", f"activity_log_{date.today()}.csv",
                                              "CSV files (*.csv)")
        if path:
            try:
                export_activity_csv(getattr(self, "_entries", []), path)
                self.ctx.activity.log("Exported activity log", "User Management", path)
                self.toast("success", "Activity log exported", path)
            except Exception as exc:  # noqa: BLE001
                self.handle_error(exc)
