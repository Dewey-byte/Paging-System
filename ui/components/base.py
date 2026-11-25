"""Basic building blocks: cards, labels, buttons, badges, avatars, empty states."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from ..icons import icon, pixmap
from ..theme import theme


# ------------------------------------------------------------------ helpers
def label(text: str = "", role: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    if selectable:
        widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def button(text: str = "", icon_name: str | None = None, variant: str | None = None, size: str | None = None,
           tooltip: str = "", icon_color: str | None = None, icon_size: int = 16) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if variant:
        btn.setProperty("variant", variant)
    if size:
        btn.setProperty("size", size)
    if not text:
        btn.setProperty("iconOnly", True)
    if tooltip:
        btn.setToolTip(tooltip)
    if icon_name:
        _apply_button_icon(btn, icon_name, variant, icon_color, icon_size)
        theme.changed.connect(lambda b=btn: _apply_button_icon(b, icon_name, variant, icon_color, icon_size))
    return btn


def _apply_button_icon(btn: QPushButton, name: str, variant: str | None, color: str | None, size: int) -> None:
    try:
        if color:
            resolved = theme.hex(color)
        elif variant in ("primary", "danger", "success"):
            resolved = "#FFFFFF"
        elif variant == "subtle":
            resolved = theme.hex("accent_soft_text")
        elif variant == "danger-subtle":
            resolved = theme.hex("danger_text")
        else:
            resolved = theme.hex("text_secondary")
        btn.setIcon(icon(name, resolved, size))
        btn.setIconSize(QSize(size, size))
    except RuntimeError:
        pass  # widget already deleted


def set_variant(btn: QPushButton, variant: str | None) -> None:
    btn.setProperty("variant", variant)
    btn.style().unpolish(btn)
    btn.style().polish(btn)


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def hbox(*items, spacing: int = 8, margins=(0, 0, 0, 0), align=None) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    _fill(layout, items)
    if align is not None:
        layout.setAlignment(align)
    return layout


def vbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    _fill(layout, items)
    return layout


def _fill(layout, items) -> None:
    for item in items:
        if item is None:
            continue
        if item == "stretch":
            layout.addStretch(1)
        elif isinstance(item, int):
            layout.addSpacing(item)
        elif isinstance(item, QLayout):
            layout.addLayout(item)
        else:
            layout.addWidget(item)


class ElidedLabel(QLabel):
    """Single-line label that shows an ellipsis instead of clipping long text."""

    def __init__(self, text: str = "", role: str | None = None, parent=None):
        super().__init__(parent)
        self._full = text
        if role:
            self.setProperty("role", role)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.setToolTip(text)
        super().setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self.setToolTip(text)
        self._elide()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        metrics = QFontMetrics(self.font())
        super().setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, max(10, self.width())))


class ClampLabel(QLabel):
    """Word-wrapped label limited to ``lines`` lines, ending with an ellipsis when truncated."""

    def __init__(self, text: str = "", role: str | None = None, lines: int = 2, parent=None):
        super().__init__(parent)
        self._full = text
        self._lines = lines
        if role:
            self.setProperty("role", role)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setToolTip(text if len(text) > 60 else "")
        self._apply()

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self._apply()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def _apply(self) -> None:
        metrics = QFontMetrics(self.font())
        self.setFixedHeight(metrics.lineSpacing() * self._lines + 2)
        width = max(60, self.width())
        limit = metrics.lineSpacing() * self._lines + 1

        def fits(candidate: str) -> bool:
            rect = metrics.boundingRect(0, 0, width, 10_000, int(Qt.TextFlag.TextWordWrap), candidate)
            return rect.height() <= limit

        if fits(self._full):
            super().setText(self._full)
            return
        lo, hi = 0, len(self._full)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if fits(self._full[:mid].rstrip() + "…"):
                lo = mid
            else:
                hi = mid - 1
        super().setText(self._full[:lo].rstrip() + "…")


def divider(vertical: bool = False) -> QFrame:
    line = QFrame()
    line.setProperty("vdivider" if vertical else "divider", True)
    return line


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


# -------------------------------------------------------------------- card
class Card(QFrame):
    """Rounded surface with a soft painted shadow (no QGraphicsEffect, so it nests safely)."""

    SHADOW = (5, 3, 5, 8)  # left, top, right, bottom room for the shadow

    def __init__(self, parent: QWidget | None = None, padding: int = 20, radius: int = 14, shadow: bool = True,
                 hoverable: bool = False, spacing: int = 12):
        super().__init__(parent)
        self._radius = radius
        self._shadow = shadow
        self._hoverable = hoverable
        self._hover = False
        self._accent: str | None = None
        self._fill_token = "surface"
        self._border_token = "border"
        l, t, r, b = self.SHADOW if shadow else (0, 0, 0, 0)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(l + padding, t + padding, r + padding, b + padding)
        self.body.setSpacing(spacing)
        if hoverable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        theme.changed.connect(self.update)

    def set_accent(self, color: str | None) -> None:
        self._accent = color
        self.update()

    def set_fill(self, fill_token: str, border_token: str | None = None) -> None:
        self._fill_token = fill_token
        if border_token:
            self._border_token = border_token
        self.update()

    def enterEvent(self, event):
        if self._hoverable:
            self._hover = True
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._hoverable:
            self._hover = False
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        l, t, r, b = self.SHADOW if self._shadow else (0, 0, 0, 0)
        rect = QRectF(self.rect()).adjusted(l, t, -r, -b)
        if self._shadow:
            base = theme.c("shadow")
            strength = 2.2 if theme.is_dark else 1.0
            layers = 6 if not self._hover else 8
            for i in range(layers, 0, -1):
                color = QColor(base)
                color.setAlphaF(min(1.0, (0.010 + 0.004 * (layers - i)) * strength * (1.6 if self._hover else 1)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                spread = i * 0.9
                painter.drawRoundedRect(rect.adjusted(-spread, -spread + 1.5, spread, spread + 2.5),
                                        self._radius + spread, self._radius + spread)
        painter.setBrush(theme.c(self._fill_token))
        border = theme.c("accent") if self._hover else theme.c(self._border_token)
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), self._radius, self._radius)
        if self._accent:
            path = QPainterPath()
            path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), self._radius, self._radius)
            painter.setClipPath(path)
            painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 4), QColor(self._accent))


class IconBubble(QWidget):
    """Icon inside a soft tinted circle / rounded square."""

    def __init__(self, icon_name: str, tone: str = "accent", size: int = 42, icon_size: int = 20,
                 color: str | None = None, rounded: bool = True, parent=None):
        super().__init__(parent)
        self._icon = icon_name
        self._tone = tone
        self._color = color
        self._icon_size = icon_size
        self._rounded = rounded
        self.setFixedSize(size, size)
        theme.changed.connect(self.update)

    def set_icon(self, name: str, tone: str | None = None, color: str | None = None) -> None:
        self._icon = name
        if tone:
            self._tone = tone
        self._color = color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg, fg, solid = theme.tone(self._tone)
        if self._color:
            fg = QColor(self._color)
            bg = QColor(self._color)
            bg.setAlpha(40 if theme.is_dark else 28)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        r = self.rect()
        if self._rounded:
            painter.drawRoundedRect(r, r.width() * 0.28, r.width() * 0.28)
        else:
            painter.drawEllipse(r)
        px = pixmap(self._icon, fg.name(), self._icon_size)
        x = (r.width() - self._icon_size) / 2
        painter.drawPixmap(QPointF(x, x), px)


class Badge(QWidget):
    """Pill-shaped status label painted with theme tones."""

    def __init__(self, text: str = "", tone: str = "neutral", dot: bool = False, parent=None, small: bool = False):
        super().__init__(parent)
        self._text = text
        self._tone = tone
        self._dot = dot
        self._small = small
        font = QFont(self.font())
        font.setPointSizeF(8.0 if small else 8.5)
        font.setBold(True)
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        theme.changed.connect(self.update)

    def set(self, text: str, tone: str | None = None) -> None:
        self._text = text
        if tone:
            self._tone = tone
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self.font())
        extra = 14 if self._dot else 0
        return QSize(fm.horizontalAdvance(self._text) + 18 + extra, 20 if self._small else 22)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg, fg, solid = theme.tone(self._tone)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        x = 9
        if self._dot:
            painter.setBrush(solid)
            painter.drawEllipse(QPointF(x + 3, r.center().y()), 3.2, 3.2)
            x += 13
        painter.setPen(fg)
        painter.drawText(QRectF(x, 0, r.width() - x, r.height()), Qt.AlignmentFlag.AlignVCenter, self._text)


class StatusDot(QWidget):
    """Small colored dot; ``pulse=True`` animates a halo (used for on-air states)."""

    def __init__(self, tone: str = "success", size: int = 10, pulse: bool = False, parent=None):
        super().__init__(parent)
        self._tone = tone
        self._size = size
        self._phase = 0.0
        self.setFixedSize(size * 2, size * 2)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.set_pulse(pulse)
        theme.changed.connect(self.update)

    def set_tone(self, tone: str, pulse: bool | None = None) -> None:
        self._tone = tone
        if pulse is not None:
            self.set_pulse(pulse)
        self.update()

    def set_pulse(self, pulse: bool) -> None:
        if pulse and not self._timer.isActive():
            self._timer.start(40)
        elif not pulse:
            self._timer.stop()
            self._phase = 0.0
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.04) % 1.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _, _, solid = theme.tone(self._tone)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self._timer.isActive():
            halo = QColor(solid)
            halo.setAlphaF(0.45 * (1 - self._phase))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            radius = self._size / 2 + self._phase * self._size / 2
            painter.drawEllipse(c, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(solid)
        painter.drawEllipse(c, self._size / 2 - 1, self._size / 2 - 1)


class Avatar(QWidget):
    def __init__(self, initials: str = "", size: int = 34, color: str = "#2563EB", parent=None):
        super().__init__(parent)
        self._initials = initials
        self._color = color
        self.setFixedSize(size, size)

    def set_initials(self, initials: str) -> None:
        self._initials = initials
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._color))
        painter.drawEllipse(self.rect().adjusted(0, 0, -1, -1))
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(max(7.5, self.width() / 3.6))
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._initials)


class EmptyState(QWidget):
    action_clicked = Signal()

    def __init__(self, icon_name: str, title: str, message: str = "", action_text: str = "",
                 action_icon: str = "plus", tone: str = "accent", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 36, 24, 36)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bubble = IconBubble(icon_name, tone, 56, 26, rounded=False)
        layout.addWidget(bubble, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(6)
        self.title = label(title, "h3")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        self.message = label(message, "muted", wrap=True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setMaximumWidth(420)
        layout.addWidget(self.message, alignment=Qt.AlignmentFlag.AlignHCenter)
        if action_text:
            layout.addSpacing(8)
            self.button = button(action_text, action_icon, "primary")
            self.button.clicked.connect(self.action_clicked)
            layout.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignHCenter)


class StatCard(Card):
    def __init__(self, title: str, icon_name: str, tone: str = "accent", parent=None):
        super().__init__(parent, padding=18, spacing=6)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.title = label(title, "muted")
        top.addWidget(self.title)
        top.addStretch(1)
        self.bubble = IconBubble(icon_name, tone, 38, 19)
        top.addWidget(self.bubble)
        self.body.addLayout(top)
        self.value = label("0", "stat")
        self.body.addWidget(self.value)
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self.badge = Badge("", tone, small=True)
        self.badge.hide()
        bottom.addWidget(self.badge)
        self.caption = label("", "caption", wrap=True)
        bottom.addWidget(self.caption, 1)
        self.body.addLayout(bottom)
        self.extra: QWidget | None = None

    def set_value(self, value, caption: str = "", badge: str = "", badge_tone: str | None = None) -> None:
        self.value.setText(str(value))
        self.caption.setText(caption)
        self.badge.setVisible(bool(badge))
        if badge:
            self.badge.set(badge, badge_tone)


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = label(title, "h1")
        self.subtitle = label(subtitle, "subtitle", wrap=True)
        text.addWidget(self.title)
        if subtitle:
            text.addWidget(self.subtitle)
        layout.addLayout(text, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget


class SectionHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", icon_name: str | None = None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        if icon_name:
            layout.addWidget(IconBubble(icon_name, "accent", 32, 16))
        text = QVBoxLayout()
        text.setSpacing(0)
        self.title = label(title, "h2")
        text.addWidget(self.title)
        self.subtitle = label(subtitle, "caption", wrap=True)
        if subtitle:
            text.addWidget(self.subtitle)
        layout.addLayout(text, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(6)
        layout.addLayout(self.actions)

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget


class InfoBanner(QFrame):
    """Inline message box (info / warning / danger / success)."""

    def __init__(self, text: str = "", tone: str = "info", icon_name: str | None = None, parent=None):
        super().__init__(parent)
        self._tone = tone
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        self._icon_name = icon_name or {"info": "info", "warning": "alert", "danger": "alert_octagon",
                                        "success": "check_circle", "purple": "layers"}.get(tone, "info")
        self.icon = QLabel()
        self.icon.setFixedSize(18, 18)
        layout.addWidget(self.icon, alignment=Qt.AlignmentFlag.AlignTop)
        self.text = label(text, wrap=True)
        self.text.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.text, 1)
        theme.changed.connect(self._restyle)
        self._restyle()

    def set_text(self, text: str, tone: str | None = None) -> None:
        if tone and tone != self._tone:
            self._tone = tone
            self._icon_name = {"info": "info", "warning": "alert", "danger": "alert_octagon",
                               "success": "check_circle"}.get(tone, "info")
            self._restyle()
        self.text.setText(text)

    def _restyle(self) -> None:
        bg, fg, solid = theme.tone(self._tone)
        self.setStyleSheet(f"InfoBanner {{ background: {bg.name()}; border-radius: 10px; }}"
                           f"QLabel {{ color: {fg.name()}; background: transparent; }}")
        self.icon.setPixmap(pixmap(self._icon_name, solid.name(), 18))
