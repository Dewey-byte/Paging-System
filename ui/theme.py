"""Design tokens, light/dark themes and the application stylesheet."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from core import paths

FONT_FAMILY = "Segoe UI"

LIGHT = {
    "bg": "#F8FAFC", "surface": "#FFFFFF", "surface_alt": "#F1F5F9", "surface_alt2": "#FAFCFE",
    "surface_hover": "#F1F5F9", "surface_pressed": "#E2E8F0", "input_bg": "#FFFFFF",
    "border": "#E2E8F0", "border_soft": "#EEF2F7", "border_strong": "#CBD5E1", "border_hover": "#94A3B8",
    "text": "#0F172A", "text_secondary": "#334155", "text_muted": "#64748B", "text_faint": "#94A3B8",
    "primary": "#172554", "primary_soft": "#1E3A8A",
    "accent": "#2563EB", "accent_hover": "#1D4ED8", "accent_pressed": "#1E40AF", "accent_disabled": "#93B4F5",
    "accent_soft": "#DBEAFE", "accent_soft_text": "#1D4ED8",
    "success": "#16A34A", "success_hover": "#15803D", "success_soft": "#DCFCE7", "success_text": "#15803D",
    "warning": "#D97706", "warning_soft": "#FEF3C7", "warning_text": "#B45309",
    "danger": "#DC2626", "danger_hover": "#B91C1C", "danger_pressed": "#991B1B", "danger_soft": "#FEE2E2",
    "danger_text": "#B91C1C",
    "info": "#0284C7", "info_soft": "#E0F2FE", "info_text": "#0369A1",
    "purple": "#7C3AED", "purple_soft": "#EDE9FE", "purple_text": "#6D28D9",
    "neutral_soft": "#F1F5F9", "neutral_text": "#475569",
    "sidebar": "#172554", "sidebar_2": "#13204A", "sidebar_hover": "#22337A", "sidebar_active": "#2563EB",
    "sidebar_text": "#C7D2FE", "sidebar_muted": "#7D8DC0", "sidebar_border": "#22306A",
    "track": "#E2E8F0", "scroll": "#CBD5E1", "scroll_hover": "#94A3B8",
    "tooltip_bg": "#0F172A", "tooltip_text": "#F8FAFC", "selection": "#DBEAFE",
    "shadow": "#0F172A", "map_bg": "#F1F5F9", "map_grid": "#E2E8F0", "map_path": "#E9EEF5",
}

DARK = {
    "bg": "#0B1220", "surface": "#111A2C", "surface_alt": "#0E1626", "surface_alt2": "#121C30",
    "surface_hover": "#18233A", "surface_pressed": "#1F2C47", "input_bg": "#0D1525",
    "border": "#1E2A41", "border_soft": "#172238", "border_strong": "#2A3854", "border_hover": "#3B4C70",
    "text": "#E5EAF3", "text_secondary": "#CBD5E1", "text_muted": "#93A0B8", "text_faint": "#5E6B85",
    "primary": "#0A1433", "primary_soft": "#1E3A8A",
    "accent": "#3B82F6", "accent_hover": "#2563EB", "accent_pressed": "#1D4ED8", "accent_disabled": "#2B4778",
    "accent_soft": "#16264A", "accent_soft_text": "#93C5FD",
    "success": "#22C55E", "success_hover": "#16A34A", "success_soft": "#0D2E1E", "success_text": "#4ADE80",
    "warning": "#F59E0B", "warning_soft": "#33250A", "warning_text": "#FBBF24",
    "danger": "#EF4444", "danger_hover": "#DC2626", "danger_pressed": "#B91C1C", "danger_soft": "#3A1216",
    "danger_text": "#F87171",
    "info": "#38BDF8", "info_soft": "#0B2738", "info_text": "#7DD3FC",
    "purple": "#A78BFA", "purple_soft": "#251C45", "purple_text": "#C4B5FD",
    "neutral_soft": "#1A2438", "neutral_text": "#A3AEC2",
    "sidebar": "#0A1330", "sidebar_2": "#081028", "sidebar_hover": "#16224A", "sidebar_active": "#2563EB",
    "sidebar_text": "#AEB9DA", "sidebar_muted": "#5F6E98", "sidebar_border": "#16204A",
    "track": "#1E2A41", "scroll": "#2A3854", "scroll_hover": "#3B4C70",
    "tooltip_bg": "#E5EAF3", "tooltip_text": "#0B1220", "selection": "#1A2D57",
    "shadow": "#000000", "map_bg": "#0E1626", "map_grid": "#18233A", "map_path": "#141F33",
}

TONES = {
    # tone: (background token, text token, solid token)
    "neutral": ("neutral_soft", "neutral_text", "text_muted"),
    "accent": ("accent_soft", "accent_soft_text", "accent"),
    "info": ("info_soft", "info_text", "info"),
    "success": ("success_soft", "success_text", "success"),
    "warning": ("warning_soft", "warning_text", "warning"),
    "danger": ("danger_soft", "danger_text", "danger"),
    "purple": ("purple_soft", "purple_text", "purple"),
}

QSS = """
* { outline: none; }
QWidget { color: @text; font-family: "@font"; font-size: 10pt; }
QMainWindow, #AppRoot, #ContentArea, QStackedWidget#Pages { background: @bg; }
QDialog { background: @surface; }
QScrollArea, QScrollArea > QWidget > QWidget#PageBody, QWidget#Transparent { background: transparent; border: none; }
QToolTip { background: @tooltip_bg; color: @tooltip_text; border: none; padding: 6px 10px; border-radius: 6px; }

#Sidebar { background: @sidebar; border-right: 1px solid @sidebar_border; }
#Sidebar QLabel { background: transparent; }
QLabel#BrandName { color: #FFFFFF; font-size: 11.5pt; font-weight: 700; }
QLabel#BrandSub { color: @sidebar_muted; font-size: 8.5pt; }
QLabel#NavSection { color: @sidebar_muted; font-size: 7.5pt; font-weight: 700; padding: 14px 14px 4px 16px; }
QPushButton#NavButton { text-align: left; padding: 9px 14px; border: none; border-radius: 10px;
    color: @sidebar_text; background: transparent; font-weight: 600; font-size: 10pt; }
QPushButton#NavButton:hover { background: @sidebar_hover; color: #FFFFFF; }
QPushButton#NavButton:checked { background: @sidebar_active; color: #FFFFFF; }
QFrame#SidebarUser { background: @sidebar_2; border-radius: 12px; }
QLabel#SidebarUserName { color: #FFFFFF; font-weight: 600; }
QLabel#SidebarUserRole { color: @sidebar_muted; font-size: 8.5pt; }
QPushButton#SidebarIcon { background: transparent; border: none; border-radius: 8px; padding: 6px; }
QPushButton#SidebarIcon:hover { background: @sidebar_hover; }

#TopBar { background: @surface; border-bottom: 1px solid @border; }
QLabel#TopSchool { font-size: 12pt; font-weight: 700; color: @text; }
QLabel#TopClock { font-size: 10.5pt; font-weight: 600; color: @text; }
QLabel#TopDate { font-size: 8.5pt; color: @text_muted; }
QPushButton#ProfileButton { background: transparent; border: 1px solid transparent; border-radius: 10px;
    padding: 4px 8px; text-align: left; }
QPushButton#ProfileButton:hover { background: @surface_hover; border-color: @border; }

QLabel[role="h1"] { font-size: 19pt; font-weight: 700; color: @text; }
QLabel[role="h2"] { font-size: 12.5pt; font-weight: 700; color: @text; }
QLabel[role="h3"] { font-size: 10.5pt; font-weight: 700; color: @text; }
QLabel[role="subtitle"] { color: @text_muted; font-size: 10pt; }
QLabel[role="muted"] { color: @text_muted; }
QLabel[role="caption"] { color: @text_muted; font-size: 8.5pt; }
QLabel[role="overline"] { color: @text_muted; font-size: 7.5pt; font-weight: 700; }
QLabel[role="stat"] { font-size: 23pt; font-weight: 700; color: @text; }
QLabel[role="field"] { color: @text_secondary; font-size: 9pt; font-weight: 600; }
QLabel[role="error"] { color: @danger_text; font-size: 9pt; }
QLabel[role="mono"] { font-family: "Cascadia Mono", "Consolas"; color: @text_secondary; }

QPushButton, QToolButton { background: @surface; color: @text; border: 1px solid @border_strong; border-radius: 9px;
    padding: 7px 15px; font-weight: 600; }
QPushButton:hover, QToolButton:hover { background: @surface_hover; border-color: @border_hover; }
QPushButton:pressed, QToolButton:pressed { background: @surface_pressed; }
QPushButton:disabled { color: @text_faint; background: @surface_alt; border-color: @border; }
QPushButton:focus { border-color: @accent; }
QPushButton[variant="primary"] { background: @accent; color: #FFFFFF; border: 1px solid @accent; }
QPushButton[variant="primary"]:hover { background: @accent_hover; border-color: @accent_hover; }
QPushButton[variant="primary"]:pressed { background: @accent_pressed; }
QPushButton[variant="primary"]:disabled { background: @accent_disabled; border-color: @accent_disabled; color: #EEF2FF; }
QPushButton[variant="danger"] { background: @danger; color: #FFFFFF; border: 1px solid @danger; }
QPushButton[variant="danger"]:hover { background: @danger_hover; border-color: @danger_hover; }
QPushButton[variant="danger"]:pressed { background: @danger_pressed; }
QPushButton[variant="danger"]:disabled { background: @danger_soft; border-color: @danger_soft; color: @danger_text; }
QPushButton[variant="success"] { background: @success; color: #FFFFFF; border: 1px solid @success; }
QPushButton[variant="success"]:hover { background: @success_hover; border-color: @success_hover; }
QPushButton[variant="success"]:disabled { background: @success_soft; border-color: @success_soft; color: @success_text; }
QPushButton[variant="ghost"] { background: transparent; border: 1px solid transparent; color: @text_secondary; }
QPushButton[variant="ghost"]:hover { background: @surface_hover; border-color: @border; }
QPushButton[variant="subtle"] { background: @accent_soft; color: @accent_soft_text; border: 1px solid @accent_soft; }
QPushButton[variant="subtle"]:hover { border-color: @accent; }
QPushButton[variant="danger-subtle"] { background: @danger_soft; color: @danger_text; border: 1px solid @danger_soft; }
QPushButton[variant="danger-subtle"]:hover { border-color: @danger; }
QPushButton[size="sm"] { padding: 4px 10px; font-size: 9pt; border-radius: 7px; }
QPushButton[size="lg"] { padding: 11px 20px; font-size: 10.5pt; border-radius: 11px; }
QPushButton[size="xl"] { padding: 16px 26px; font-size: 13pt; font-weight: 700; border-radius: 14px; }
QPushButton[iconOnly="true"] { padding: 6px; }
QPushButton[chip="true"] { border-radius: 12px; padding: 4px 12px; font-size: 9pt; color: @text_secondary; }
QPushButton[chip="true"]:checked { background: @accent_soft; color: @accent_soft_text; border-color: @accent; }

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QComboBox {
    background: @input_bg; border: 1px solid @border_strong; border-radius: 8px; padding: 6px 10px;
    selection-background-color: @accent; selection-color: #FFFFFF; min-height: 20px; }
QPlainTextEdit, QTextEdit { padding: 8px 10px; }
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QDateEdit:hover, QTimeEdit:hover, QComboBox:hover {
    border-color: @border_hover; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus,
QTimeEdit:focus, QComboBox:focus { border: 1px solid @accent; }
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QComboBox:disabled, QDateEdit:disabled,
QTimeEdit:disabled { background: @surface_alt; color: @text_faint; border-color: @border; }
QLineEdit[invalid="true"] { border-color: @danger; }
QComboBox { padding-right: 28px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 28px; border: none; }
QComboBox::down-arrow { image: url("@chevron_down"); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background: @surface; color: @text; border: 1px solid @border; padding: 4px;
    selection-background-color: @accent_soft; selection-color: @text; outline: none; }
QDateEdit::drop-down, QTimeEdit::drop-down { subcontrol-origin: padding; subcontrol-position: center right; width: 26px;
    border: none; }
QDateEdit::down-arrow { image: url("@calendar_icon"); width: 14px; height: 14px; }
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QTimeEdit::up-button, QTimeEdit::down-button { width: 18px; border: none; background: transparent; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QTimeEdit::up-arrow { image: url("@chevron_up"); width: 9px; height: 9px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QTimeEdit::down-arrow { image: url("@chevron_down"); width: 9px;
    height: 9px; }

QCheckBox, QRadioButton { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 17px; height: 17px; border-radius: 5px; border: 1.5px solid @border_strong;
    background: @input_bg; }
QCheckBox::indicator:hover { border-color: @accent; }
QCheckBox::indicator:checked { background: @accent; border-color: @accent; image: url("@check_white"); }
QCheckBox::indicator:disabled { background: @surface_alt; border-color: @border; }
QCheckBox::indicator:checked:disabled { background: @accent_disabled; border-color: @accent_disabled; }
QRadioButton::indicator { width: 17px; height: 17px; border-radius: 9px; border: 1.5px solid @border_strong;
    background: @input_bg; }
QRadioButton::indicator:checked { border: 5px solid @accent; background: #FFFFFF; }

QSlider { background: transparent; min-height: 22px; }
QSlider::groove:horizontal { height: 6px; background: @track; border-radius: 3px; }
QSlider::sub-page:horizontal { background: @accent; border-radius: 3px; }
QSlider::handle:horizontal { background: #FFFFFF; border: 2px solid @accent; width: 14px; height: 14px;
    margin: -6px 0; border-radius: 9px; }
QSlider::handle:horizontal:hover { background: @accent_soft; }
QSlider::sub-page:horizontal:disabled { background: @border_strong; }
QSlider::handle:horizontal:disabled { border-color: @border_strong; }

QScrollBar:vertical { background: transparent; width: 11px; margin: 2px 2px 2px 0; }
QScrollBar::handle:vertical { background: @scroll; border-radius: 4px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: @scroll_hover; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 0 2px 2px 2px; }
QScrollBar::handle:horizontal { background: @scroll; border-radius: 4px; min-width: 36px; }
QScrollBar::handle:horizontal:hover { background: @scroll_hover; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QTableView, QListView, QTreeView { background: @surface; border: none; gridline-color: transparent;
    selection-background-color: @selection; selection-color: @text; alternate-background-color: @surface_alt2; }
QTableView::item { padding: 4px 8px; border-bottom: 1px solid @border_soft; }
QTableView::item:selected { background: @selection; color: @text; }
QListView::item { padding: 8px; border-radius: 8px; }
QListView::item:selected { background: @selection; color: @text; }
QListView::item:hover { background: @surface_hover; }
QHeaderView { background: @surface; border: none; }
QHeaderView::section { background: @surface; color: @text_muted; border: none; border-bottom: 1px solid @border;
    padding: 9px 8px; font-size: 8.5pt; font-weight: 700; }
QTableCornerButton::section { background: @surface; border: none; }

QTabWidget::pane { border: none; }
QTabBar { qproperty-drawBase: 0; background: transparent; }
QTabBar::tab { background: transparent; color: @text_muted; padding: 9px 4px; margin-right: 22px; border: none;
    border-bottom: 2px solid transparent; font-weight: 600; font-size: 10pt; }
QTabBar::tab:hover { color: @text; }
QTabBar::tab:selected { color: @accent; border-bottom: 2px solid @accent; }

QMenu { background: @surface; border: 1px solid @border; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 8px 26px 8px 12px; border-radius: 6px; color: @text; background: transparent; }
QMenu::item:selected { background: @surface_hover; }
QMenu::item:disabled { color: @text_faint; }
QMenu::separator { height: 1px; background: @border; margin: 5px 6px; }
QMenu::icon { padding-left: 10px; }

QProgressBar { background: @track; border: none; border-radius: 4px; max-height: 8px; min-height: 8px;
    text-align: center; color: transparent; }
QProgressBar::chunk { background: @accent; border-radius: 4px; }

QCalendarWidget QWidget#qt_calendar_navigationbar { background: @surface; }
QCalendarWidget QToolButton { color: @text; background: transparent; border: none; font-weight: 700;
    padding: 6px 10px; border-radius: 7px; font-size: 10.5pt; }
QCalendarWidget QToolButton:hover { background: @surface_hover; }
QCalendarWidget QToolButton::menu-indicator { image: none; }
QCalendarWidget QToolButton#qt_calendar_prevmonth { qproperty-icon: url("@chevron_left"); }
QCalendarWidget QToolButton#qt_calendar_nextmonth { qproperty-icon: url("@chevron_right"); }
QCalendarWidget QSpinBox { min-height: 18px; padding: 2px 6px; }
QCalendarWidget QAbstractItemView { background: @surface; color: @text; selection-background-color: @accent;
    selection-color: #FFFFFF; outline: none; }
QCalendarWidget QAbstractItemView:disabled { color: @text_faint; }

QSplitter::handle { background: transparent; }
QFrame[divider="true"] { background: @border; border: none; max-height: 1px; min-height: 1px; }
QFrame[vdivider="true"] { background: @border; border: none; max-width: 1px; min-width: 1px; }
"""

ICON_FILES = {
    "chevron_down": ("chevron_down", "text_muted"), "chevron_up": ("chevron_up", "text_muted"),
    "chevron_left": ("chevron_left", "text"), "chevron_right": ("chevron_right", "text"),
    "calendar_icon": ("calendar", "text_muted"), "check_white": ("check", None),
}


class ThemeManager(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.name = "light"
        self.tokens = dict(LIGHT)

    @property
    def is_dark(self) -> bool:
        return self.name == "dark"

    def hex(self, key: str) -> str:
        return self.tokens.get(key, key if str(key).startswith("#") else "#FF00FF")

    def c(self, key: str, alpha: int | None = None) -> QColor:
        color = QColor(self.hex(key))
        if alpha is not None:
            color.setAlpha(alpha)
        return color

    def tone(self, tone: str) -> tuple[QColor, QColor, QColor]:
        bg, fg, solid = TONES.get(tone, TONES["neutral"])
        return self.c(bg), self.c(fg), self.c(solid)

    def _icon_files(self) -> dict[str, str]:
        from .icons import svg_markup
        folder = paths.ui_cache_dir()
        result = {}
        for key, (icon_name, token) in ICON_FILES.items():
            color = "#FFFFFF" if token is None else self.hex(token)
            path = folder / f"{key}_{self.name}.svg"
            path.write_text(svg_markup(icon_name, color, stroke=2.4 if key == "check_white" else 2.0),
                            encoding="utf-8")
            result[key] = path.as_posix()
        return result

    def stylesheet(self) -> str:
        replacements = {**self.tokens, **self._icon_files(), "font": FONT_FAMILY}
        css = QSS
        for key in sorted(replacements, key=len, reverse=True):
            css = css.replace(f"@{key}", replacements[key])
        return css

    def apply(self, name: str | None = None) -> None:
        if name:
            self.name = "dark" if name == "dark" else "light"
        self.tokens = dict(DARK if self.name == "dark" else LIGHT)
        app = QApplication.instance()
        if app is not None:
            font = QFont(FONT_FAMILY, 10)
            font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
            app.setFont(font)
            palette = app.palette()
            for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.Base, "input_bg"),
                              (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.WindowText, "text"),
                              (QPalette.ColorRole.Button, "surface"), (QPalette.ColorRole.ButtonText, "text"),
                              (QPalette.ColorRole.Highlight, "accent"), (QPalette.ColorRole.ToolTipBase, "tooltip_bg"),
                              (QPalette.ColorRole.ToolTipText, "tooltip_text"),
                              (QPalette.ColorRole.PlaceholderText, "text_faint"),
                              (QPalette.ColorRole.AlternateBase, "surface_alt2")):
                palette.setColor(role, self.c(key))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
            app.setPalette(palette)
            app.setStyleSheet(self.stylesheet())
        self.changed.emit()


theme = ThemeManager()
