"""Holds the current AppTheme and applies it to the application (spec §7.3).

ThemeManager is the only code that calls QApplication.setStyle, setPalette,
setStyleSheet or QStyleHints.setColorScheme.
"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

from rzgitclient.ui.theme.app_theme import DARK_THEME, LIGHT_THEME, AppTheme


class ThemeMode(StrEnum):
    """Which theme the user chose; the value is what settings store."""

    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"  # follows the desktop's colour scheme


class ThemeManager(QObject):
    theme_changed = Signal(object)  # AppTheme

    def __init__(self, app: QApplication, theme: AppTheme = LIGHT_THEME,
                 parent: QObject | None = None, *, mode: ThemeMode | None = None):
        """Apply ``theme``, or the theme for ``mode`` when one is given."""
        super().__init__(parent)
        self._app = app
        self._hints = app.styleHints()
        # Fusion is a neutral base that honours stylesheets fully; the desktop
        # style (e.g. Breeze) would fight the custom look.
        app.setStyle("Fusion")
        self._hints.colorSchemeChanged.connect(self._on_color_scheme_changed)
        self._mode = ThemeMode.DARK if theme.dark else ThemeMode.LIGHT
        self._current = theme
        if mode is not None:
            self._mode = mode
            self._current = self._theme_for_mode()
        self._apply(self._current)

    @property
    def current(self) -> AppTheme:
        return self._current

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    def set_mode(self, mode: ThemeMode) -> None:
        """Switch to the theme for ``mode`` now; SYSTEM keeps following the desktop."""
        self._mode = mode
        self.set_theme(self._theme_for_mode())
        self._set_color_scheme(self._current)  # also when the theme didn't change

    def set_theme(self, theme: AppTheme) -> None:
        if theme == self._current:
            return
        self._current = theme
        self._apply(theme)
        self.theme_changed.emit(theme)

    def _theme_for_mode(self) -> AppTheme:
        if self._mode is ThemeMode.SYSTEM:
            if hasattr(self._hints, "unsetColorScheme"):  # Qt >= 6.8
                self._hints.unsetColorScheme()  # so colorScheme() is the desktop's
            return self._theme_for_scheme(self._hints.colorScheme())
        return DARK_THEME if self._mode is ThemeMode.DARK else LIGHT_THEME

    @staticmethod
    def _theme_for_scheme(scheme: Qt.ColorScheme) -> AppTheme:
        return DARK_THEME if scheme == Qt.ColorScheme.Dark else LIGHT_THEME

    def _on_color_scheme_changed(self, scheme: Qt.ColorScheme) -> None:
        if self._mode is ThemeMode.SYSTEM:
            self.set_theme(self._theme_for_scheme(scheme))

    def _set_color_scheme(self, theme: AppTheme) -> None:
        # In SYSTEM mode the scheme stays unset, or the desktop's changes
        # would no longer be reported.
        if self._mode is not ThemeMode.SYSTEM and hasattr(self._hints, "setColorScheme"):
            self._hints.setColorScheme(Qt.ColorScheme.Dark if theme.dark
                                       else Qt.ColorScheme.Light)

    def _apply(self, theme: AppTheme) -> None:
        self._set_color_scheme(theme)
        self._app.setPalette(theme.palette())
        font = self._app.font()
        font.setPointSizeF(theme.fonts.ui_point_size)
        self._app.setFont(font)
        self._app.setStyleSheet(theme.stylesheet())
