"""Modal dialogs: base form dialog, confirmations, errors and text input."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from core.errors import AppError

from ..theme import theme
from .base import IconBubble, InfoBanner, button, divider, label


def form_field(caption: str, widget: QWidget, hint: str = "") -> QWidget:
    """A captioned form field (caption above the widget, optional hint below)."""
    box = QWidget()
    box.setObjectName("Transparent")
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    layout.addWidget(label(caption, "field"))
    layout.addWidget(widget)
    if hint:
        layout.addWidget(label(hint, "caption", wrap=True))
    return box


def form_row(*widgets: QWidget, stretch: list[int] | None = None) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(14)
    for i, widget in enumerate(widgets):
        layout.addWidget(widget, (stretch or [1] * len(widgets))[i])
    return layout


class Dialog(QDialog):
    """Consistent dialog chrome: header with icon/title, scrollable body, footer actions."""

    def __init__(self, parent: QWidget | None, title: str, subtitle: str = "", icon_name: str | None = None,
                 tone: str = "accent", width: int = 540, scroll: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        head = QHBoxLayout(header)
        head.setContentsMargins(26, 22, 26, 16)
        head.setSpacing(14)
        if icon_name:
            head.addWidget(IconBubble(icon_name, tone, 44, 22), alignment=Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        self.title_label = label(title, "h2")
        titles.addWidget(self.title_label)
        if subtitle:
            sub = label(subtitle, "muted", wrap=True)
            titles.addWidget(sub)
        head.addLayout(titles, 1)
        outer.addWidget(header)

        self.error_banner = InfoBanner("", "danger")
        self.error_banner.hide()
        banner_wrap = QVBoxLayout()
        banner_wrap.setContentsMargins(26, 0, 26, 8)
        banner_wrap.addWidget(self.error_banner)
        outer.addLayout(banner_wrap)

        content = QWidget()
        content.setObjectName("Transparent")
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(26, 4, 26, 20)
        self.body.setSpacing(14)
        if scroll:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setWidget(content)
            area.setFrameShape(QScrollArea.Shape.NoFrame)
            outer.addWidget(area, 1)
        else:
            outer.addWidget(content, 1)

        outer.addWidget(divider())
        footer_widget = QWidget()
        footer_widget.setObjectName("DialogFooter")
        footer_widget.setStyleSheet(f"QWidget#DialogFooter {{ background: {theme.hex('surface_alt2')}; }}")
        self.footer = QHBoxLayout(footer_widget)
        self.footer.setContentsMargins(26, 14, 26, 14)
        self.footer.setSpacing(10)
        self.footer_left = QHBoxLayout()
        self.footer.addLayout(self.footer_left)
        self.footer.addStretch(1)
        outer.addWidget(footer_widget)

    # helpers ---------------------------------------------------------------
    def add_button(self, text: str, variant: str | None = None, slot=None, icon_name: str | None = None,
                   default: bool = False) -> QPushButton:
        btn = button(text, icon_name, variant)
        if slot is not None:
            btn.clicked.connect(slot)
        if default:
            btn.setDefault(True)
        self.footer.addWidget(btn)
        return btn

    def add_cancel(self, text: str = "Cancel") -> QPushButton:
        return self.add_button(text, None, self.reject)

    def field(self, caption: str, widget: QWidget, hint: str = "") -> QWidget:
        return form_field(caption, widget, hint)

    def row(self, *widgets: QWidget, stretch: list[int] | None = None) -> QHBoxLayout:
        return form_row(*widgets, stretch=stretch)

    def show_error(self, error) -> None:
        message = error.message if isinstance(error, AppError) else str(error)
        self.error_banner.set_text(message, "danger")
        self.error_banner.show()

    def clear_error(self) -> None:
        self.error_banner.hide()


class ConfirmDialog(Dialog):
    def __init__(self, parent, title: str, message: str, confirm_text: str = "Confirm", danger: bool = False,
                 icon_name: str | None = None, detail: str = ""):
        tone = "danger" if danger else "accent"
        super().__init__(parent, title, "", icon_name or ("alert" if danger else "info"), tone, width=460)
        text = label(message, wrap=True)
        text.setTextFormat(Qt.TextFormat.RichText)
        self.body.addWidget(text)
        if detail:
            self.body.addWidget(InfoBanner(detail, "warning" if danger else "info"))
        self.add_cancel()
        self.confirm_button = self.add_button(confirm_text, "danger" if danger else "primary", self.accept,
                                              default=True)


def confirm(parent, title: str, message: str, confirm_text: str = "Confirm", danger: bool = False,
            icon_name: str | None = None, detail: str = "") -> bool:
    return ConfirmDialog(parent, title, message, confirm_text, danger, icon_name, detail).exec() == QDialog.DialogCode.Accepted


def alert(parent, title: str, message: str, tone: str = "info", icon_name: str | None = None) -> None:
    dialog = Dialog(parent, title, "", icon_name or {"danger": "x_circle", "warning": "alert",
                                                      "success": "check_circle"}.get(tone, "info"), tone, width=440)
    text = label(message, wrap=True)
    text.setTextFormat(Qt.TextFormat.RichText)
    dialog.body.addWidget(text)
    dialog.add_button("OK", "primary", dialog.accept, default=True)
    dialog.exec()


def show_error(parent, error, title: str | None = None) -> None:
    if isinstance(error, AppError):
        alert(parent, title or error.title, error.message, "danger")
    else:
        alert(parent, title or "Something went wrong", str(error), "danger")


def ask_text(parent, title: str, caption: str, value: str = "", confirm_text: str = "Save",
             password: bool = False) -> str | None:
    dialog = Dialog(parent, title, "", "edit", width=440)
    edit = QLineEdit(value)
    if password:
        edit.setEchoMode(QLineEdit.EchoMode.Password)
    edit.selectAll()
    dialog.body.addWidget(dialog.field(caption, edit))
    dialog.add_cancel()
    dialog.add_button(confirm_text, "primary", dialog.accept, default=True)
    edit.setFocus()
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return edit.text()
    return None
