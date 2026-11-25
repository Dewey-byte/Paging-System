"""Sign-in window, first-run setup and password change dialog."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QRadialGradient
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QStackedWidget, QVBoxLayout,
                               QWidget)

from core import paths
from core.errors import AppError

from .branding import app_icon, app_pixmap
from .components.base import InfoBanner, button, label
from .components.dialogs import Dialog
from .icons import icon, pixmap
from .theme import theme

FEATURES = [
    ("mic", "Live paging to any zone with push-to-talk"),
    ("siren", "Emergency alarms with priority override"),
    ("calendar_clock", "Automatic bells and scheduled announcements"),
    ("history", "Complete broadcast history and reports"),
]


class BrandPanel(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(44, 44, 44, 36)
        layout.setSpacing(0)
        logo = QLabel()
        px = app_pixmap(112)
        px.setDevicePixelRatio(2.0)
        logo.setPixmap(px)
        layout.addWidget(logo)
        layout.addSpacing(26)
        school = QLabel(ctx.settings.school_name if ctx.auth.has_users() else paths.APP_NAME)
        school.setWordWrap(True)
        school.setStyleSheet("color: white; font-size: 21pt; font-weight: 700; background: transparent;")
        layout.addWidget(school)
        layout.addSpacing(6)
        tag = QLabel("School Paging & Announcement Management System")
        tag.setWordWrap(True)
        tag.setStyleSheet("color: #BFDBFE; font-size: 10.5pt; background: transparent;")
        layout.addWidget(tag)
        layout.addSpacing(34)
        for icon_name, text in FEATURES:
            row = QHBoxLayout()
            row.setSpacing(12)
            glyph = QLabel()
            glyph.setPixmap(pixmap(icon_name, "#93C5FD", 18))
            glyph.setFixedSize(34, 34)
            glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            glyph.setStyleSheet("background: rgba(255,255,255,0.08); border-radius: 10px;")
            row.addWidget(glyph)
            caption = QLabel(text)
            caption.setStyleSheet("color: #E0E7FF; font-size: 10pt; background: transparent;")
            row.addWidget(caption, 1)
            layout.addLayout(row)
            layout.addSpacing(12)
        layout.addStretch(1)
        footer = QLabel(f"Version {paths.APP_VERSION}  |  Data stored locally on this computer")
        footer.setStyleSheet("color: #7D8DC0; font-size: 8.5pt; background: transparent;")
        layout.addWidget(footer)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor("#1E3A8A"))
        gradient.setColorAt(0.55, QColor("#172554"))
        gradient.setColorAt(1, QColor("#0B1433"))
        painter.fillRect(self.rect(), gradient)
        glow = QRadialGradient(QPointF(self.width() * 0.95, self.height() * 0.1), self.width() * 0.8)
        glow.setColorAt(0, QColor(59, 130, 246, 90))
        glow.setColorAt(1, QColor(59, 130, 246, 0))
        painter.fillRect(self.rect(), glow)
        painter.setPen(QColor(255, 255, 255, 18))
        for i in range(1, 6):
            r = 90 * i
            painter.drawEllipse(QPointF(self.width() + 30, self.height() + 30), r, r)


class LoginWindow(QWidget):
    logged_in = Signal(object)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(paths.APP_NAME)
        self.setWindowIcon(app_icon())
        self.setFixedSize(1000, 640)
        self.setObjectName("AppRoot")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(BrandPanel(ctx), 9)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 11)
        self.login_form = self._build_login()
        self.stack.addWidget(self.login_form)
        if not ctx.auth.has_users():
            self.setup_form = self._build_setup()
            self.stack.addWidget(self.setup_form)
            self.stack.setCurrentWidget(self.setup_form)
        self._center()

    def _center(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2)

    # --------------------------------------------------------------- login
    def _build_login(self) -> QWidget:
        page = QWidget()
        page.setObjectName("FormPage")
        page.setStyleSheet(f"QWidget#FormPage {{ background: {theme.hex('surface')}; }}")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(70, 60, 70, 40)
        outer.setSpacing(0)
        outer.addStretch(1)
        outer.addWidget(label("Welcome back", "h1"))
        outer.addSpacing(4)
        outer.addWidget(label("Sign in to manage campus announcements and alarms.", "subtitle", wrap=True))
        outer.addSpacing(28)
        self.error = InfoBanner("", "danger")
        self.error.hide()
        outer.addWidget(self.error)
        outer.addSpacing(6)
        outer.addWidget(label("Username", "field"))
        outer.addSpacing(6)
        self.username = QLineEdit()
        self.username.setPlaceholderText("Enter your username")
        self.username.setMinimumHeight(42)
        self.username.addAction(icon("user", theme.hex("text_faint"), 16), QLineEdit.ActionPosition.LeadingPosition)
        outer.addWidget(self.username)
        outer.addSpacing(16)
        outer.addWidget(label("Password", "field"))
        outer.addSpacing(6)
        self.password = QLineEdit()
        self.password.setPlaceholderText("Enter your password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMinimumHeight(42)
        self.password.addAction(icon("lock", theme.hex("text_faint"), 16), QLineEdit.ActionPosition.LeadingPosition)
        self._eye = self.password.addAction(icon("eye", theme.hex("text_faint"), 16),
                                            QLineEdit.ActionPosition.TrailingPosition)
        self._eye.setToolTip("Show / hide password")
        self._eye.triggered.connect(self._toggle_password)
        outer.addWidget(self.password)
        self.caps = label("Caps Lock is on", "caption")
        self.caps.setStyleSheet(f"color: {theme.hex('warning_text')};")
        self.caps.hide()
        outer.addWidget(self.caps)
        outer.addSpacing(24)
        self.sign_in = button("Sign in", "logout", "primary", "lg")
        self.sign_in.setMinimumHeight(46)
        self.sign_in.clicked.connect(self._login)
        outer.addWidget(self.sign_in)
        outer.addSpacing(18)
        outer.addWidget(label("Forgot your password? Ask a system administrator to reset it.", "caption", wrap=True))
        outer.addStretch(2)
        self.username.returnPressed.connect(self.password.setFocus)
        self.password.returnPressed.connect(self._login)
        self.password.textEdited.connect(self._check_caps)
        return page

    def _toggle_password(self) -> None:
        visible = self.password.echoMode() == QLineEdit.EchoMode.Password
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self._eye.setIcon(icon("eye_off" if visible else "eye", theme.hex("text_faint"), 16))

    def _check_caps(self, text: str) -> None:
        if text and text[-1].isalpha():
            self.caps.setVisible(text[-1].isupper() and not QGuiApplication.keyboardModifiers() &
                                 Qt.KeyboardModifier.ShiftModifier)

    def _login(self) -> None:
        self.error.hide()
        try:
            user = self.ctx.auth.login(self.username.text(), self.password.text())
        except AppError as exc:
            self.error.set_text(exc.message, "danger")
            self.error.show()
            self.password.selectAll()
            self.password.setFocus()
            return
        if user.must_change_password:
            dialog = ChangePasswordDialog(self.ctx, self, forced=True, current=self.password.text())
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self.ctx.auth.logout("Password change cancelled")
                return
        self.password.clear()
        self.logged_in.emit(self.ctx.auth.current_user)

    def showEvent(self, event):
        super().showEvent(event)
        (self.username if not self.username.text() else self.password).setFocus()

    # --------------------------------------------------------------- setup
    def _build_setup(self) -> QWidget:
        page = QWidget()
        page.setObjectName("FormPage")
        page.setStyleSheet(f"QWidget#FormPage {{ background: {theme.hex('surface')}; }}")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(64, 40, 64, 30)
        outer.setSpacing(10)
        outer.addWidget(label("Set up your paging system", "h1"))
        outer.addWidget(label("Create the administrator account. You can add more users later.", "subtitle",
                              wrap=True))
        outer.addSpacing(8)
        self.setup_error = InfoBanner("", "danger")
        self.setup_error.hide()
        outer.addWidget(self.setup_error)
        self.s_school = QLineEdit()
        self.s_school.setPlaceholderText("e.g. Riverside National High School")
        self.s_name = QLineEdit()
        self.s_name.setPlaceholderText("Your full name")
        self.s_user = QLineEdit("admin")
        self.s_email = QLineEdit()
        self.s_email.setPlaceholderText("Optional")
        self.s_pass = QLineEdit()
        self.s_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.s_pass.setPlaceholderText("At least 8 characters with a number")
        self.s_confirm = QLineEdit()
        self.s_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        for caption, widget in (("School name", self.s_school), ("Administrator name", self.s_name)):
            outer.addWidget(label(caption, "field"))
            outer.addWidget(widget)
        row = QHBoxLayout()
        row.setSpacing(12)
        for caption, widget in (("Username", self.s_user), ("Email", self.s_email)):
            col = QVBoxLayout()
            col.setSpacing(6)
            col.addWidget(label(caption, "field"))
            col.addWidget(widget)
            row.addLayout(col)
        outer.addLayout(row)
        row2 = QHBoxLayout()
        row2.setSpacing(12)
        for caption, widget in (("Password", self.s_pass), ("Confirm password", self.s_confirm)):
            col = QVBoxLayout()
            col.setSpacing(6)
            col.addWidget(label(caption, "field"))
            col.addWidget(widget)
            row2.addLayout(col)
        outer.addLayout(row2)
        outer.addSpacing(4)
        self.s_demo = QCheckBox("Add sample announcements and the example daily bell schedule")
        self.s_demo.setChecked(True)
        self.s_local = QCheckBox("Play the 'Main Building' zone through this computer's speakers")
        self.s_local.setChecked(True)
        outer.addWidget(self.s_demo)
        outer.addWidget(self.s_local)
        hint = label("Other zones start as clearly labelled simulated devices. Map them to real outputs, "
                     "amplifiers or IP speakers later under Audio Zones.", "caption", wrap=True)
        hint.setContentsMargins(26, 0, 0, 0)
        outer.addWidget(hint)
        outer.addStretch(1)
        create = button("Create administrator && continue", "check", "primary", "lg")
        create.setMinimumHeight(44)
        create.clicked.connect(self._create_admin)
        outer.addWidget(create)
        return page

    def _create_admin(self) -> None:
        self.setup_error.hide()
        if self.s_pass.text() != self.s_confirm.text():
            self.setup_error.set_text("The passwords do not match.", "danger")
            self.setup_error.show()
            return
        try:
            self.ctx.complete_first_run(school_name=self.s_school.text(), full_name=self.s_name.text(),
                                        username=self.s_user.text(), password=self.s_pass.text(),
                                        email=self.s_email.text(), demo_content=self.s_demo.isChecked(),
                                        use_local_output=self.s_local.isChecked())
            user = self.ctx.auth.login(self.s_user.text(), self.s_pass.text())
        except AppError as exc:
            self.setup_error.set_text(exc.message, "danger")
            self.setup_error.show()
            return
        self.logged_in.emit(user)


class ChangePasswordDialog(Dialog):
    def __init__(self, ctx, parent=None, forced: bool = False, current: str = ""):
        subtitle = ("Your administrator requires you to choose a new password before continuing." if forced else
                    "Use at least 8 characters including a letter and a number.")
        super().__init__(parent, "Change password", subtitle, "key", width=460)
        self.ctx = ctx
        self.current = QLineEdit(current)
        self.current.setEchoMode(QLineEdit.EchoMode.Password)
        self.new = QLineEdit()
        self.new.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        if not current:
            self.body.addWidget(self.field("Current password", self.current))
        self.body.addWidget(self.field("New password", self.new))
        self.body.addWidget(self.field("Confirm new password", self.confirm))
        self.add_cancel("Sign out" if forced else "Cancel")
        self.add_button("Update password", "primary", self._save, default=True)
        (self.new if current else self.current).setFocus()

    def _save(self) -> None:
        if self.new.text() != self.confirm.text():
            self.show_error("The new passwords do not match.")
            return
        try:
            self.ctx.auth.change_password(self.current.text(), self.new.text())
        except AppError as exc:
            self.show_error(exc)
            return
        self.accept()
