"""Application icon and school logo rendering."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap

from .icons import pixmap


def app_pixmap(size: int = 256) -> QPixmap:
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0, QColor("#2563EB"))
    gradient.setColorAt(1, QColor("#172554"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.23, size * 0.23)
    glyph = int(size * 0.58)
    painter.drawPixmap(QPointF((size - glyph) / 2, (size - glyph) / 2), pixmap("megaphone", "#FFFFFF", glyph, 2.0, 1.0))
    painter.end()
    return px


def app_icon() -> QIcon:
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        result.addPixmap(app_pixmap(size))
    return result


def school_logo(path: str, size: int = 40) -> QPixmap:
    """User-provided logo, or a default emblem."""
    if path and Path(path).exists():
        source = QPixmap(path)
        if not source.isNull():
            scaled = source.scaled(size * 2, size * 2, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(2.0)
            return scaled
    px = QPixmap(size * 2, size * 2)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#DBEAFE"))
    painter.drawEllipse(QRectF(0, 0, size * 2, size * 2))
    glyph = int(size * 1.1)
    painter.drawPixmap(QPointF(size - glyph / 2, size - glyph / 2), pixmap("school", "#1D4ED8", glyph, 2.0, 1.0))
    painter.end()
    px.setDevicePixelRatio(2.0)
    return px


def save_icon_files(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    app_pixmap(256).save(str(folder / "app.png"))
    app_pixmap(256).save(str(folder / "app.ico"))
