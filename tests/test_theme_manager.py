import dataclasses
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette

from rzgitclient.ui.theme.app_theme import DARK_THEME, LIGHT_THEME
from rzgitclient.ui.theme.theme_manager import ThemeManager, ThemeMode
from tests.support import qt_app


def _variant():
    colors = dataclasses.replace(LIGHT_THEME.colors, sidebar_bg="#123456")
    return dataclasses.replace(LIGHT_THEME, name="Variant", colors=colors)


class ThemeManagerTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.manager = ThemeManager(self.app, LIGHT_THEME)
        self.emitted = []
        self.manager.theme_changed.connect(self.emitted.append)

    def tearDown(self):
        ThemeManager(self.app, LIGHT_THEME)  # leave the shared app as found

    def test_applies_on_construction(self):
        self.assertIs(self.manager.current, LIGHT_THEME)
        self.assertEqual(self.app.styleSheet(), LIGHT_THEME.stylesheet())
        self.assertEqual(self.app.font().pointSizeF(), LIGHT_THEME.fonts.ui_point_size)
        # With a stylesheet set, app.style() is Qt's stylesheet wrapper; the base
        # style underneath only shows once the sheet is cleared.
        self.app.setStyleSheet("")
        self.assertEqual(self.app.style().name().lower(), "fusion")

    def test_set_equal_theme_is_noop(self):
        self.manager.set_theme(dataclasses.replace(LIGHT_THEME))
        self.assertEqual(self.emitted, [])
        self.assertIs(self.manager.current, LIGHT_THEME)

    def test_set_theme_applies_and_emits_once(self):
        variant = _variant()
        self.manager.set_theme(variant)
        self.assertEqual(self.emitted, [variant])
        self.assertIs(self.manager.current, variant)
        self.assertIn("#123456", self.app.styleSheet())
        self.manager.set_theme(variant)
        self.assertEqual(len(self.emitted), 1)


class ThemeModeTests(unittest.TestCase):
    """Light, Dark and System (spec §7.3)."""

    def setUp(self):
        self.app = qt_app()
        self.hints = self.app.styleHints()
        # The offscreen platform ignores the scheme it's told: record the calls.
        self.schemes = []
        for name, record in (("setColorScheme", self.schemes.append),
                             ("unsetColorScheme", lambda: self.schemes.append(None))):
            patcher = mock.patch.object(self.hints, name, side_effect=record)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manager = ThemeManager(self.app, LIGHT_THEME)
        self.emitted = []
        self.manager.theme_changed.connect(self.emitted.append)

    def tearDown(self):
        ThemeManager(self.app, LIGHT_THEME)  # leave the shared app as found

    def test_the_mode_follows_the_first_theme(self):
        self.assertIs(self.manager.mode, ThemeMode.LIGHT)
        self.assertEqual(self.schemes, [Qt.ColorScheme.Light])
        dark = ThemeManager(self.app, DARK_THEME)
        self.assertIs(dark.mode, ThemeMode.DARK)
        self.assertEqual(self.schemes[-1], Qt.ColorScheme.Dark)

    def test_starting_in_a_mode_applies_its_theme(self):
        manager = ThemeManager(self.app, mode=ThemeMode.DARK)
        self.assertIs(manager.current, DARK_THEME)
        self.assertEqual(self.app.styleSheet(), DARK_THEME.stylesheet())

    def test_set_mode_switches_live(self):
        self.manager.set_mode(ThemeMode.DARK)
        self.assertEqual(self.emitted, [DARK_THEME])
        self.assertIs(self.manager.mode, ThemeMode.DARK)
        self.assertEqual(self.app.styleSheet(), DARK_THEME.stylesheet())
        self.assertEqual(self.schemes[-1], Qt.ColorScheme.Dark)
        self.assertEqual(self.app.palette().color(QPalette.ColorRole.Base).name(),
                         DARK_THEME.colors.content_bg)
        self.manager.set_mode(ThemeMode.LIGHT)
        self.assertEqual(self.emitted, [DARK_THEME, LIGHT_THEME])
        self.assertEqual(self.schemes[-1], Qt.ColorScheme.Light)

    def test_system_takes_the_desktops_scheme(self):
        self.manager.set_mode(ThemeMode.SYSTEM)
        self.assertEqual(self.schemes[-1], None)  # unset, and left so: it follows the desktop
        expected = DARK_THEME if self.hints.colorScheme() == Qt.ColorScheme.Dark else LIGHT_THEME
        self.assertIs(self.manager.current, expected)

    def test_system_follows_the_desktop_while_it_is_the_mode(self):
        self.manager.set_mode(ThemeMode.SYSTEM)
        self.hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark)
        self.assertIs(self.manager.current, DARK_THEME)
        self.hints.colorSchemeChanged.emit(Qt.ColorScheme.Light)
        self.assertIs(self.manager.current, LIGHT_THEME)
        self.manager.set_mode(ThemeMode.LIGHT)
        self.hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark)
        self.assertIs(self.manager.current, LIGHT_THEME)  # no longer following

    def test_the_dark_palette_has_dark_bevels(self):
        palette = DARK_THEME.palette()
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base,
                     QPalette.ColorRole.Button, QPalette.ColorRole.Light,
                     QPalette.ColorRole.Mid, QPalette.ColorRole.Dark):
            self.assertLess(palette.color(role).lightness(), 110, role)
        self.assertGreater(palette.color(QPalette.ColorRole.Text).lightness(), 200)


if __name__ == "__main__":
    unittest.main()
