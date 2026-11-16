import dataclasses
import unittest
from unittest import mock

import shiboken6
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import (QHeaderView, QLabel, QLineEdit, QListWidget, QMenu, QMenuBar,
                               QPlainTextEdit, QPushButton, QStatusBar, QTabBar, QTreeView)

from rzgitclient.ui.theme.app_theme import (DARK_CB_THEME, DARK_THEME, LIGHT_CB_THEME,
                                            LIGHT_THEME, FontKind)
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
        self.assertEqual(self.app.font().pointSizeF(), LIGHT_THEME.fonts.point_size(FontKind.UI))
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

    def test_colour_blind_switches_live_in_every_mode(self):
        self.assertFalse(self.manager.colour_blind)
        self.manager.set_colour_blind(True)
        self.assertTrue(self.manager.colour_blind)
        self.assertEqual(self.emitted, [LIGHT_CB_THEME])
        self.assertEqual(self.app.styleSheet(), LIGHT_CB_THEME.stylesheet())
        self.manager.set_colour_blind(True)
        self.assertEqual(len(self.emitted), 1)  # already on
        self.manager.set_mode(ThemeMode.DARK)
        self.assertIs(self.manager.current, DARK_CB_THEME)  # the flag stays with the mode
        self.manager.set_colour_blind(False)
        self.assertIs(self.manager.current, DARK_THEME)
        self.assertEqual(self.emitted, [LIGHT_CB_THEME, DARK_CB_THEME, DARK_THEME])

    def test_colour_blind_follows_the_desktop_in_system(self):
        self.manager.set_mode(ThemeMode.SYSTEM)
        self.manager.set_colour_blind(True)
        self.hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark)
        self.assertIs(self.manager.current, DARK_CB_THEME)
        self.hints.colorSchemeChanged.emit(Qt.ColorScheme.Light)
        self.assertIs(self.manager.current, LIGHT_CB_THEME)
        self.assertEqual(self.schemes[-1], None)  # still following the desktop

    def test_starting_colour_blind_applies_the_variant_first(self):
        manager = ThemeManager(self.app, mode=ThemeMode.DARK, colour_blind=True)
        self.assertTrue(manager.colour_blind)
        self.assertIs(manager.current, DARK_CB_THEME)
        self.assertEqual(self.app.styleSheet(), DARK_CB_THEME.stylesheet())
        self.assertIs(ThemeManager(self.app, LIGHT_THEME, colour_blind=True).current,
                      LIGHT_CB_THEME)

    def test_the_dark_palette_has_dark_bevels(self):
        palette = DARK_THEME.palette()
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base,
                     QPalette.ColorRole.Button, QPalette.ColorRole.Light,
                     QPalette.ColorRole.Mid, QPalette.ColorRole.Dark):
            self.assertLess(palette.color(role).lightness(), 110, role)
        self.assertGreater(palette.color(QPalette.ColorRole.Text).lightness(), 200)


if __name__ == "__main__":
    unittest.main()


class ThemeFontTests(unittest.TestCase):
    """Every kind of text at its theme size, whatever the desktop says (RZG-39, spec §7.3)."""

    DESKTOP = 14  # a size no theme uses

    def setUp(self):
        self.app = qt_app()
        self.addCleanup(self.restore)
        self.desktop_fonts()
        self.manager = ThemeManager(self.app, LIGHT_THEME, mode=ThemeMode.LIGHT)
        self.widgets = self.make_widgets()

    @staticmethod
    def make_widgets():
        return {
            FontKind.UI: (QLabel("x"), QPushButton("x"), QLineEdit(), QPlainTextEdit()),
            FontKind.MENU: (QMenuBar(), QMenu()),
            FontKind.STATUS_BAR: (QStatusBar(),),
            FontKind.LIST: (QListWidget(), QTreeView()),
            FontKind.COLUMN_HEADER: (QHeaderView(Qt.Orientation.Horizontal),),
            FontKind.TAB: (QTabBar(),),
        }

    def fresh_widgets(self):
        """New widgets of each type. Without a running event loop (app.exec), Qt doesn't
        tell existing widgets about a per-class font change; the running app does."""
        for widgets in self.widgets.values():
            for w in widgets:
                shiboken6.delete(w)
        self.widgets = self.make_widgets()

    def restore(self):
        for widgets in getattr(self, "widgets", {}).values():
            for w in widgets:
                shiboken6.delete(w)
        if hasattr(self, "manager"):
            shiboken6.delete(self.manager)  # the app has one; an old one would answer too
        shiboken6.delete(ThemeManager(self.app, LIGHT_THEME))
        QCoreApplication.processEvents()

    def desktop_fonts(self):
        """As a desktop does: its own size for some widget types."""
        font = QFont(self.app.font())
        font.setPointSizeF(self.DESKTOP)
        for name in ("QAbstractItemView", "QMenuBar", "QStatusBar", "QLabel", "QPushButton",
                     "QHeaderView", "QTabBar", "QTipLabel"):
            self.app.setFont(font, name)

    def settle(self):
        for _ in range(3):
            QCoreApplication.processEvents()

    def assert_sizes(self, theme):
        sizes = {kind: theme.fonts.point_size(kind) for kind in FontKind}
        self.assertTrue(self.manager.fonts_match())
        self.assertEqual(self.app.font().pointSizeF(), sizes[FontKind.UI])
        self.assertEqual(self.app.font("QTipLabel").pointSizeF(), sizes[FontKind.TOOLTIP])
        for kind, widgets in self.widgets.items():
            for w in widgets:
                self.assertEqual(w.font().pointSizeF(), sizes[kind], (kind, type(w).__name__))

    def test_the_desktops_sizes_are_replaced_at_start(self):
        self.assert_sizes(LIGHT_THEME)
        self.assertEqual(LIGHT_THEME.fonts.point_size(FontKind.LIST), 10)

    def test_they_stay_after_any_number_of_switches(self):
        for step in (lambda: self.manager.set_mode(ThemeMode.DARK),
                     lambda: self.manager.set_colour_blind(True),
                     lambda: self.manager.set_mode(ThemeMode.LIGHT),
                     lambda: self.manager.set_colour_blind(False)):
            step()
            self.settle()
            self.assert_sizes(self.manager.current)

    def test_a_desktop_font_change_is_undone(self):
        font = QFont(self.app.font())
        font.setPointSizeF(self.DESKTOP)
        self.app.setFont(font)  # the desktop's font changed while running
        self.settle()
        self.fresh_widgets()
        self.assert_sizes(LIGHT_THEME)

    def test_per_class_fonts_after_a_scheme_change_are_undone(self):
        self.desktop_fonts()  # the desktop filled them in again
        self.app.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Light)
        self.settle()
        self.fresh_widgets()
        self.assert_sizes(LIGHT_THEME)

    def test_fonts_go_after_the_stylesheet(self):
        """A new stylesheet makes Qt fill its per-class fonts in from the desktop again."""
        real = self.app.setStyleSheet

        def refill(sheet):
            real(sheet)
            self.desktop_fonts()

        with mock.patch.object(self.app, "setStyleSheet", side_effect=refill):
            self.manager.set_mode(ThemeMode.DARK)
        self.assertTrue(self.manager.fonts_match())  # at once, not only after a check
        self.fresh_widgets()
        self.assert_sizes(DARK_THEME)

    def test_a_theme_can_override_one_kind(self):
        fonts = dataclasses.replace(LIGHT_THEME.fonts, list_point_size=13, mono_point_size=8,
                                    section_header_point_size=9)
        theme = dataclasses.replace(LIGHT_THEME, name="Big lists", fonts=fonts)
        self.manager.set_theme(theme)
        self.settle()
        self.assert_sizes(theme)
        self.assertEqual(self.widgets[FontKind.LIST][0].font().pointSizeF(), 13)
        self.assertEqual(self.widgets[FontKind.UI][0].font().pointSizeF(), 10)
        self.assertEqual(theme.mono_font().pointSizeF(), 8)
        self.assertIn("font-size: 9pt", self.app.styleSheet())
