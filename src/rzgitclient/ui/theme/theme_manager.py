"""Holds the current AppTheme and applies it to the application (spec §7.3).

ThemeManager is the only code that calls QApplication.setStyle, setPalette or
setStyleSheet.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

from rzgitclient.ui.theme.app_theme import AppTheme


class ThemeManager(QObject):
    theme_changed = Signal(object)  # AppTheme

    def __init__(self, app: QApplication, theme: AppTheme, parent: QObject | None = None):
        super().__init__(parent)
        self._app = app
        # Fusion is a neutral base that honours stylesheets fully; the desktop
        # style (e.g. Breeze) would fight the custom look.
        app.setStyle("Fusion")
        hints = app.styleHints()
        if hasattr(hints, "setColorScheme"):  # Qt >= 6.8
            hints.setColorScheme(Qt.ColorScheme.Light)
        self._current = theme
        self._apply(theme)

    @property
    def current(self) -> AppTheme:
        return self._current

    def set_theme(self, theme: AppTheme) -> None:
        if theme == self._current:
            return
        self._current = theme
        self._apply(theme)
        self.theme_changed.emit(theme)

    def _apply(self, theme: AppTheme) -> None:
        self._app.setPalette(theme.palette())
        font = self._app.font()
        font.setPointSizeF(theme.fonts.ui_point_size)
        self._app.setFont(font)
        self._app.setStyleSheet(theme.stylesheet())
