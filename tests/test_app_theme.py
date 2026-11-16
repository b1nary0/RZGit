import dataclasses
import re
import unittest

from PySide6.QtGui import QColor, QFont, QPalette

from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from rzgitclient.ui.theme.style_roles import Role, Surface
from rzgitclient.ui.theme.theme_stylesheet import template_values
from tests.support import qt_app


class AppThemeTests(unittest.TestCase):
    def test_stylesheet_has_no_unreplaced_placeholders(self):
        self.assertNotIn("$", LIGHT_THEME.stylesheet())

    def test_field_names_unique_across_groups(self):
        groups = (LIGHT_THEME.colors, LIGHT_THEME.fonts, LIGHT_THEME.metrics)
        names = [f.name for g in groups for f in dataclasses.fields(g)]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(template_values(LIGHT_THEME)), len(names))

    def test_every_surface_and_role_is_styled(self):
        qss = LIGHT_THEME.stylesheet()
        for surface in Surface:
            self.assertIn(f'[surface="{surface}"]', qss)
        for role in Role:
            self.assertIn(f'[role="{role}"]', qss)

    def test_section_header_has_its_own_background(self):
        rule = re.search(r'QLabel\[role="section-header"\] \{(.*?)\}',
                         LIGHT_THEME.stylesheet(), re.S)
        self.assertIsNotNone(rule)
        self.assertIn(f"background: {LIGHT_THEME.colors.header_bg};", rule.group(1))

    def test_colours_are_valid_hex(self):
        for group in (LIGHT_THEME.colors, LIGHT_THEME.status):
            for name, value in dataclasses.asdict(group).items():
                self.assertRegex(value, r"^#[0-9a-f]{6}$", name)

    def test_float_values_render_cleanly(self):
        # 8.0 must come out as "8pt", the same as the original hand-written QSS.
        self.assertIn("font-size: 8pt;", LIGHT_THEME.stylesheet())
        self.assertIsNone(re.search(r"\d\.0(pt|px)", LIGHT_THEME.stylesheet()))

    def test_status_for_code(self):
        status = LIGHT_THEME.status
        expected = {
            "M": status.modified,
            "A": status.added,
            "D": status.deleted,
            "R": status.renamed,
            "C": status.copied,
            "T": status.type_changed,
            "U": status.conflict,
            "?": status.untracked,
        }
        for code, colour in expected.items():
            self.assertEqual(status.for_code(code), colour, code)
        self.assertIsNone(status.for_code("X"))
        self.assertIsNone(status.for_code(" "))

    def test_status_color_falls_back_to_text(self):
        self.assertEqual(LIGHT_THEME.status_color("M"), LIGHT_THEME.status.modified)
        self.assertEqual(LIGHT_THEME.status_color("X"), LIGHT_THEME.colors.text)

    def test_theme_is_immutable(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            LIGHT_THEME.colors.text = "#000000"  # type: ignore[misc]

    def test_palette_and_mono_font(self):
        qt_app()
        palette = LIGHT_THEME.palette()
        self.assertEqual(
            palette.color(QPalette.ColorRole.Base), QColor(LIGHT_THEME.colors.content_bg)
        )
        font = LIGHT_THEME.mono_font()
        self.assertEqual(font.styleHint(), QFont.StyleHint.Monospace)
        self.assertEqual(font.pointSizeF(), LIGHT_THEME.fonts.ui_point_size)


if __name__ == "__main__":
    unittest.main()
