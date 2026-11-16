import dataclasses
import re
import unittest

from PySide6.QtGui import QColor, QFont, QPalette

from rzgitclient.ui.theme.app_theme import DARK_THEME, LIGHT_THEME
from rzgitclient.ui.theme.style_roles import Role, Surface
from rzgitclient.ui.theme.theme_stylesheet import template_values
from tests.support import qt_app


THEMES = (LIGHT_THEME, DARK_THEME)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two #rrggbb colours."""
    def luminance(hex_colour: str) -> float:
        channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                   for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class AppThemeTests(unittest.TestCase):
    def test_stylesheet_has_no_unreplaced_placeholders(self):
        for theme in THEMES:
            self.assertNotIn("$", theme.stylesheet(), theme.name)

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
        for theme in THEMES:
            rule = re.search(r'QLabel\[role="section-header"\] \{(.*?)\}',
                             theme.stylesheet(), re.S)
            self.assertIsNotNone(rule)
            self.assertIn(f"background: {theme.colors.header_bg};", rule.group(1))

    def test_colours_are_valid_hex(self):
        for theme in THEMES:
            for group in (theme.colors, theme.status):
                for name, value in dataclasses.asdict(group).items():
                    self.assertRegex(value, r"^#[0-9a-f]{6}$", f"{theme.name}.{name}")

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


class DarkThemeTests(unittest.TestCase):
    """The dark theme (RZG-32): its own colours, readable on its backgrounds."""

    def test_is_dark_and_the_light_theme_is_not(self):
        self.assertTrue(DARK_THEME.dark)
        self.assertFalse(LIGHT_THEME.dark)

    def test_backgrounds_are_dark(self):
        c = DARK_THEME.colors
        for name in ("menubar_bg", "toolbar_bg", "tab_bg", "header_bg", "sidebar_bg",
                     "content_bg", "button_bg", "tooltip_bg", "diff_added_bg",
                     "diff_removed_bg", "diff_hunk_bg", "diff_filler_bg"):
            self.assertLess(QColor(getattr(c, name)).lightness(), 80, name)

    def test_text_is_readable(self):
        c = DARK_THEME.colors
        for background in (c.sidebar_bg, c.content_bg, c.header_bg, c.selection):
            self.assertGreaterEqual(contrast(c.text, background), 7, background)
        for background in (c.sidebar_bg, c.content_bg, c.header_bg):
            self.assertGreaterEqual(contrast(c.text_muted, background), 4.5, background)
        # The image's muted grey on a selected row: a little under 4.5, kept.
        self.assertGreaterEqual(contrast(c.text_muted, c.selection), 4)
        self.assertGreaterEqual(contrast(c.accent_text, c.accent), 4.5)
        self.assertGreaterEqual(contrast(c.diff_hunk_text, c.diff_hunk_bg), 4.5)
        for background in (c.diff_added_bg, c.diff_removed_bg, c.diff_added_word,
                           c.diff_removed_word):
            self.assertGreaterEqual(contrast(c.text, background), 4.5, background)

    def test_status_colours_are_readable_and_distinct(self):
        status, c = DARK_THEME.status, DARK_THEME.colors
        for name, colour in dataclasses.asdict(status).items():
            for background in (c.sidebar_bg, c.content_bg, c.selection):
                self.assertGreaterEqual(contrast(colour, background), 3, (name, background))
        main = (status.modified, status.added, status.deleted, status.renamed,
                status.conflict, status.untracked)
        self.assertEqual(len(set(main)), len(main))

