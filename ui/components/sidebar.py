"""Left navigation sidebar (permission-aware, collapsible)."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core import paths

from ..branding import app_pixmap
from ..icons import icon
from ..theme import theme
from .base import Avatar


class Sidebar(QWidget):
    navigate = Signal(str)
    logout = Signal()

    EXPANDED, COLLAPSED = 248, 76

    def __init__(self, pages: list[tuple[str, str, str, str]], user, parent=None):
        """``pages`` = [(key, label, icon, section)] already filtered by permission."""
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(self.EXPANDED)
        self.collapsed = False
        self.buttons: dict[str, QPushButton] = {}
        self._icons: dict[str, str] = {}
        self._labels: dict[str, str] = {}
        self._section_labels: list[QLabel] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(2)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        self.logo = QLabel()
        logo = app_pixmap(80)
        logo.setDevicePixelRatio(2.0)
        self.logo.setPixmap(logo)
        self.logo.setFixedSize(40, 40)
        brand.addWidget(self.logo)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.brand_name = QLabel("School Paging")
        self.brand_name.setObjectName("BrandName")
        self.brand_sub = QLabel("Communication System")
        self.brand_sub.setObjectName("BrandSub")
        texts.addWidget(self.brand_name)
        texts.addWidget(self.brand_sub)
        brand.addLayout(texts, 1)
        layout.addLayout(brand)
        layout.addSpacing(12)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        current_section = None
        for key, text, icon_name, section in pages:
            if section != current_section:
                current_section = section
                caption = QLabel(section.upper())
                caption.setObjectName("NavSection")
                self._section_labels.append(caption)
                layout.addWidget(caption)
            btn = QPushButton(f"  {text}")
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIconSize(QSize(19, 19))
            btn.setToolTip(text)
            btn.clicked.connect(lambda _=False, k=key: self.navigate.emit(k))
            btn.toggled.connect(lambda _=False: self._refresh_icons())
            self.group.addButton(btn)
            layout.addWidget(btn)
            self.buttons[key] = btn
            self._icons[key] = icon_name
            self._labels[key] = text
        layout.addStretch(1)

        self.user_card = QFrame()
        self.user_card.setObjectName("SidebarUser")
        card = QHBoxLayout(self.user_card)
        card.setContentsMargins(10, 10, 8, 10)
        card.setSpacing(10)
        self.avatar = Avatar(user.initials, 34, "#3B82F6")
        card.addWidget(self.avatar)
        info = QVBoxLayout()
        info.setSpacing(0)
        self.user_name = QLabel(user.display_name)
        self.user_name.setObjectName("SidebarUserName")
        self.user_role = QLabel(user.role_name)
        self.user_role.setObjectName("SidebarUserRole")
        info.addWidget(self.user_name)
        info.addWidget(self.user_role)
        self.user_info = QWidget()
        self.user_info.setLayout(info)
        card.addWidget(self.user_info, 1)
        self.logout_button = QPushButton()
        self.logout_button.setObjectName("SidebarIcon")
        self.logout_button.setIcon(icon("logout", "#AEB9DA", 18))
        self.logout_button.setToolTip("Sign out")
        self.logout_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.logout_button.clicked.connect(self.logout)
        card.addWidget(self.logout_button)
        layout.addWidget(self.user_card)
        self.version = QLabel(f"v{paths.APP_VERSION}")
        self.version.setObjectName("BrandSub")
        self.version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(6)
        layout.addWidget(self.version)
        theme.changed.connect(self._refresh_icons)
        self._refresh_icons()

    def _refresh_icons(self) -> None:
        for key, btn in self.buttons.items():
            color = "#FFFFFF" if btn.isChecked() else theme.hex("sidebar_text")
            btn.setIcon(icon(self._icons[key], color, 19))

    def set_active(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)

    def set_user(self, user) -> None:
        self.user_name.setText(user.display_name)
        self.user_role.setText(user.role_name)
        self.avatar.set_initials(user.initials)

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self.collapsed:
            return
        self.collapsed = collapsed
        self.setFixedWidth(self.COLLAPSED if collapsed else self.EXPANDED)
        for key, btn in self.buttons.items():
            btn.setText("" if collapsed else f"  {self._labels[key]}")
            btn.setStyleSheet("text-align: center; padding: 10px;" if collapsed else "")
        for widget in (self.brand_name, self.brand_sub, self.user_info, self.version):
            widget.setVisible(not collapsed)
        for caption in self._section_labels:
            caption.setText("" if collapsed else caption.text())
            caption.setVisible(not collapsed)
