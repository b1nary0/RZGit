import dataclasses
import unittest

from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from rzgitclient.ui.theme.theme_manager import ThemeManager
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


if __name__ == "__main__":
    unittest.main()
