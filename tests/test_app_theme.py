import dataclasses
import re
import unittest

from PySide6.QtGui import QColor, QFont, QPalette

from rzgitclient.ui.theme.app_theme import (DARK_CB_THEME, DARK_THEME, LIGHT_CB_THEME,
                                            LIGHT_THEME, FontKind, colour_blind_theme)
from rzgitclient.ui.theme.style_roles import Role, Surface
from rzgitclient.ui.theme.theme_stylesheet import template_values
from tests.colour_vision import VISIONS, contrast, delta_e, min_delta_e
from tests.support import qt_app


THEMES = (LIGHT_THEME, DARK_THEME)
VARIANTS = ((LIGHT_THEME, LIGHT_CB_THEME), (DARK_THEME, DARK_CB_THEME))
ALL_THEMES = (*THEMES, LIGHT_CB_THEME, DARK_CB_THEME)




class AppThemeTests(unittest.TestCase):
    def test_stylesheet_has_no_unreplaced_placeholders(self):
        for theme in ALL_THEMES:
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
        for theme in ALL_THEMES:
            rule = re.search(r'QLabel\[role="section-header"\] \{(.*?)\}',
                             theme.stylesheet(), re.S)
            self.assertIsNotNone(rule)
            self.assertIn(f"background: {theme.colors.header_bg};", rule.group(1))

    def test_colours_are_valid_hex(self):
        for theme in ALL_THEMES:
            for group in (theme.colors, theme.status):
                for name, value in dataclasses.asdict(group).items():
                    self.assertRegex(value, r"^#[0-9a-f]{6}$", f"{theme.name}.{name}")

    def test_line_buttons_are_green_readable_and_outlined(self):
        for theme in THEMES:
            c = theme.colors
            for fill in (c.diff_line_button, c.diff_line_button_hover):
                colour = QColor(fill)
                self.assertTrue(colour.green() > colour.red() and colour.green() > colour.blue(),
                                (theme.name, fill))
            self.assertGreaterEqual(contrast(c.diff_line_button_text, c.diff_line_button), 4.5,
                                    theme.name)
            for background in (c.content_bg, c.diff_added_bg, c.diff_removed_bg):
                self.assertGreaterEqual(contrast(c.diff_line_button_border, background), 3,
                                        (theme.name, background))
        self.assertEqual(DARK_THEME.colors.diff_line_button_border, "#ffffff")

    def test_selected_lines_are_a_stronger_shade_and_readable(self):
        for theme in THEMES:
            c = theme.colors
            for plain, selected in ((c.diff_added_bg, c.diff_added_selected_bg),
                                    (c.diff_removed_bg, c.diff_removed_selected_bg)):
                self.assertGreaterEqual(contrast(plain, selected), 1.1, (theme.name, selected))
                self.assertLess(contrast(plain, selected), 1.6, (theme.name, selected))
                self.assertGreaterEqual(contrast(c.text, selected), 4.5, (theme.name, selected))

    def test_every_kind_of_text_is_the_default_size_unless_overridden(self):
        for theme in THEMES + (LIGHT_CB_THEME, DARK_CB_THEME):
            self.assertEqual({theme.fonts.point_size(k) for k in FontKind}, {10}, theme.name)
        fonts = dataclasses.replace(LIGHT_THEME.fonts, default_point_size=11, tab_point_size=7)
        self.assertEqual(fonts.point_size(FontKind.TAB), 7)
        self.assertEqual({fonts.point_size(k) for k in FontKind if k is not FontKind.TAB}, {11})
        big = dataclasses.replace(LIGHT_THEME, fonts=fonts)
        self.assertIn("font-size: 11pt;", big.stylesheet())  # the section header follows
        self.assertEqual(big.mono_font().pointSizeF(), 11)

    def test_float_values_render_cleanly(self):
        # 8.0 must come out as "8pt", the same as the original hand-written QSS.
        self.assertIn("font-size: 10pt;", LIGHT_THEME.stylesheet())  # not "10.0pt"
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

    def test_column_labels_are_their_own_row_of_chrome(self):
        """RZG-41: a background a step from both lists' and the section header's,
        text readable on it, a visible line under the row."""
        for theme in ALL_THEMES:
            c = theme.colors
            for other in (c.sidebar_bg, c.content_bg, c.header_bg):
                self.assertGreaterEqual(contrast(c.column_header_bg, other), 1.05, (theme.name, other))
            self.assertGreaterEqual(contrast(c.column_header_text, c.column_header_bg), 7, theme.name)
            self.assertGreater(contrast(c.column_header_text_hover, c.column_header_bg),
                               contrast(c.column_header_text, c.column_header_bg), theme.name)
            self.assertGreaterEqual(contrast(c.column_header_border, c.column_header_bg), 1.25,
                                    theme.name)
            self.assertGreaterEqual(contrast(c.column_header_separator, c.column_header_bg), 1.5,
                                    theme.name)
            self.assertNotIn("QHeaderView", theme.stylesheet())

    def test_group_headings_are_a_row_of_their_own(self):
        """RZG-53: ΔE 3 from both list backgrounds; text at least 7:1 on it."""
        for theme in ALL_THEMES:
            c = theme.colors
            for other in (c.sidebar_bg, c.content_bg):
                self.assertGreaterEqual(delta_e(c.group_header_bg, other), 3, (theme.name, other))
            self.assertGreaterEqual(contrast(c.group_header_text, c.group_header_bg), 7, theme.name)

    def test_palette_and_mono_font(self):
        qt_app()
        palette = LIGHT_THEME.palette()
        self.assertEqual(
            palette.color(QPalette.ColorRole.Base), QColor(LIGHT_THEME.colors.content_bg)
        )
        font = LIGHT_THEME.mono_font()
        self.assertEqual(font.styleHint(), QFont.StyleHint.Monospace)
        self.assertEqual(font.pointSizeF(), LIGHT_THEME.fonts.point_size(FontKind.MONO))


# The colour tokens a colour-blind variant changes (spec §7.1); every other is its base's.
CB_COLOURS = {"warning", "diff_added_bg", "diff_added_word", "diff_added_selected_bg",
              "diff_removed_bg", "diff_removed_word", "diff_removed_selected_bg",
              "diff_line_button", "diff_line_button_hover", "diff_line_button_border",
              "diff_line_button_text"}
# Pairs that must stay apart however the colours are seen, and by how much (CIEDE2000).
STATUS_PAIRS = (("added", "deleted", 30), ("modified", "renamed", 12),
                ("modified", "conflict", 12), ("renamed", "conflict", 12))
DIFF_PAIRS = (("diff_added_bg", "diff_removed_bg"), ("diff_added_word", "diff_removed_word"),
              ("diff_added_selected_bg", "diff_removed_selected_bg"))


class ColourBlindThemeTests(unittest.TestCase):
    """The colour-blind friendly variants (RZG-33), under simulated colour blindness."""

    def test_a_variant_is_its_base_but_for_the_colours_that_tell_kinds_apart(self):
        for base, variant in VARIANTS:
            self.assertEqual(variant.name, f"{base.name}, colour-blind")
            self.assertEqual((variant.dark, variant.fonts, variant.metrics),
                             (base.dark, base.fonts, base.metrics))
            changed = {name for name, value in dataclasses.asdict(variant.colors).items()
                       if value != getattr(base.colors, name)}
            self.assertLessEqual(changed, CB_COLOURS, variant.name)
            for name in ("added", "deleted", "modified", "conflict"):
                self.assertNotEqual(getattr(variant.status, name), getattr(base.status, name))

    def test_colour_blind_theme_picks_the_variant(self):
        self.assertIs(colour_blind_theme(LIGHT_THEME), LIGHT_CB_THEME)
        self.assertIs(colour_blind_theme(DARK_THEME), DARK_CB_THEME)
        self.assertIs(colour_blind_theme(DARK_CB_THEME), DARK_CB_THEME)

    def test_the_simulation_sees_the_problem_in_the_base_themes(self):
        # Under deuteranopia the base themes' added and deleted are hard to tell apart.
        for base in THEMES:
            self.assertLess(delta_e(base.status.added, base.status.deleted, "deuteranopia"), 12)
            self.assertLess(min_delta_e(base.colors.diff_added_bg, base.colors.diff_removed_bg),
                            8)

    def test_kinds_stay_apart_under_every_kind_of_colour_vision(self):
        for _base, variant in VARIANTS:
            s, c = variant.status, variant.colors
            for a, b, minimum in STATUS_PAIRS:
                for vision in VISIONS:
                    self.assertGreaterEqual(delta_e(getattr(s, a), getattr(s, b), vision),
                                            minimum, (variant.name, a, b, vision))
            for a, b in DIFF_PAIRS:
                self.assertGreaterEqual(min_delta_e(getattr(c, a), getattr(c, b)), 20,
                                        (variant.name, a, b))
            for name in ("diff_added_bg", "diff_removed_bg"):  # a changed line vs context
                self.assertGreaterEqual(min_delta_e(getattr(c, name), c.content_bg), 8,
                                        (variant.name, name))
            self.assertGreaterEqual(min_delta_e(c.warning, c.text_muted), 20, variant.name)
            for name, colour in dataclasses.asdict(s).items():  # not mistaken for plain text
                self.assertGreaterEqual(min_delta_e(colour, c.text), 12, (variant.name, name))

    def test_contrast_is_at_least_the_base_themes(self):
        for base, variant in VARIANTS:
            s, c, bc = variant.status, variant.colors, base.colors
            for background in ("sidebar_bg", "content_bg", "selection"):
                bg = getattr(c, background)
                floor = max(3, min(contrast(v, bg) for v in dataclasses.asdict(base.status).values()))
                for name, colour in dataclasses.asdict(s).items():
                    self.assertGreaterEqual(contrast(colour, bg), floor,
                                            (variant.name, name, background))
            for kind in ("bg", "word", "selected_bg"):
                added, removed = f"diff_added_{kind}", f"diff_removed_{kind}"
                floor = min(contrast(bc.text, getattr(bc, added)),
                            contrast(bc.text, getattr(bc, removed)))
                for name in (added, removed):
                    self.assertGreaterEqual(contrast(c.text, getattr(c, name)), floor,
                                            (variant.name, name))
            self.assertGreaterEqual(contrast(c.warning, c.content_bg),
                                    contrast(bc.warning, bc.content_bg), variant.name)

    def test_line_buttons_are_readable_and_outlined(self):
        for _base, variant in VARIANTS:
            c = variant.colors
            for fill in (c.diff_line_button, c.diff_line_button_hover):
                self.assertGreaterEqual(contrast(c.diff_line_button_text, fill), 4,
                                        (variant.name, fill))
            for background in (c.content_bg, c.diff_added_bg, c.diff_removed_bg):
                self.assertGreaterEqual(contrast(c.diff_line_button_border, background), 3,
                                        (variant.name, background))

    def test_selected_lines_are_a_stronger_shade_and_readable(self):
        for _base, variant in VARIANTS:
            c = variant.colors
            for plain, selected in ((c.diff_added_bg, c.diff_added_selected_bg),
                                    (c.diff_removed_bg, c.diff_removed_selected_bg)):
                self.assertGreaterEqual(contrast(plain, selected), 1.1, (variant.name, selected))
                self.assertLess(contrast(plain, selected), 1.6, (variant.name, selected))
                self.assertGreaterEqual(contrast(c.text, selected), 4.5, (variant.name, selected))



class RowStripeTests(unittest.TestCase):
    """History's stripes are easy to see, and clear of hover and selection (RZG-63, RZG-66)."""

    def test_the_four_row_backgrounds_stay_apart(self):
        for theme in ALL_THEMES:
            c = theme.colors
            with self.subTest(theme.name):
                self.assertGreaterEqual(delta_e(c.row_alt_bg, c.content_bg), 3.5)
                self.assertGreaterEqual(delta_e(c.row_alt_bg, c.content_selection), 5)
                for row in (c.content_bg, c.row_alt_bg, c.content_selection):
                    self.assertGreaterEqual(delta_e(c.content_hover, row), 3, row)

    def test_text_on_a_stripe_keeps_its_contrast(self):
        for theme in ALL_THEMES:
            c = theme.colors
            with self.subTest(theme.name):
                self.assertGreaterEqual(contrast(c.text, c.row_alt_bg), 7)
                self.assertGreaterEqual(contrast(c.text_muted, c.row_alt_bg), 2.9)


if __name__ == "__main__":
    unittest.main()


class GraphLaneTests(unittest.TestCase):
    """History's branch graph lanes (RZG-58): lines that show, neighbours apart."""

    def neighbours(self, lanes):
        return [(lanes[i], lanes[(i + 1) % len(lanes)]) for i in range(len(lanes))]

    def test_eight_valid_colours_in_every_theme(self):
        for theme in ALL_THEMES:
            self.assertEqual(len(theme.graph.lanes), 8, theme.name)
            for colour in theme.graph.lanes:
                self.assertRegex(colour, r"^#[0-9a-f]{6}$", theme.name)

    def test_a_line_shows_on_every_row_background(self):
        for theme in ALL_THEMES:
            c = theme.colors
            for colour in theme.graph.lanes:
                with self.subTest(theme.name, colour=colour):
                    self.assertGreaterEqual(contrast(colour, c.content_bg), 3)
                    self.assertGreaterEqual(contrast(colour, c.row_alt_bg), 3)
                    self.assertGreaterEqual(contrast(colour, c.content_selection), 2.5)

    def test_neighbouring_colours_stay_apart(self):
        for theme in THEMES:
            for a, b in self.neighbours(theme.graph.lanes):
                self.assertGreaterEqual(delta_e(a, b), 20, (theme.name, a, b))
            self.assertEqual(len(set(theme.graph.lanes)), 8, theme.name)

    def test_neighbouring_colours_stay_apart_under_every_kind_of_colour_vision(self):
        for base, variant in VARIANTS:
            self.assertNotEqual(variant.graph, base.graph)
            for a, b in self.neighbours(variant.graph.lanes):
                self.assertGreaterEqual(min_delta_e(a, b), 12, (variant.name, a, b))
                self.assertGreaterEqual(delta_e(a, b), 20, (variant.name, a, b))


def _blend(colour: str, under: str, alpha: int) -> str:
    """``colour`` at ``alpha`` (0-255) over ``under``."""
    a, b = QColor(colour), QColor(under)
    t = alpha / 255
    mix = [round(x * t + y * (1 - t)) for x, y in
           ((a.red(), b.red()), (a.green(), b.green()), (a.blue(), b.blue()))]
    return "#{:02x}{:02x}{:02x}".format(*mix)


class RefLabelTokenTests(unittest.TestCase):
    """History's ref labels (RZG-77, spec §7.1): readable text, borders that show."""

    def fills(self, theme, under):
        g = theme.graph
        return [*(_blend(lane, under, g.label_fill_alpha) for lane in g.lanes),
                g.tag_fill, g.stash_fill]

    def test_valid_colours(self):
        for theme in ALL_THEMES:
            g = theme.graph
            for colour in (g.tag_fill, g.tag_border, g.stash_fill, g.stash_border):
                self.assertRegex(colour, r"^#[0-9a-f]{6}$", theme.name)
            self.assertTrue(0 < g.label_fill_alpha < 255, theme.name)

    def test_text_is_readable_on_every_label(self):
        for theme in ALL_THEMES:
            c = theme.colors
            for under in (c.content_bg, c.row_alt_bg):
                for fill in self.fills(theme, under):
                    with self.subTest(theme.name, fill=fill):
                        self.assertGreaterEqual(contrast(c.text, fill), 4.5)

    def test_borders_show(self):
        for theme in ALL_THEMES:
            g, c = theme.graph, theme.colors
            for border in (g.tag_border, g.stash_border, c.text_muted):  # text_muted: "+N"
                for under in (c.content_bg, c.row_alt_bg):
                    with self.subTest(theme.name, border=border, under=under):
                        self.assertGreaterEqual(contrast(border, under), 1.5)

    def test_colour_blind_variants_share_them(self):
        for base, variant in VARIANTS:
            self.assertEqual(dataclasses.replace(variant.graph, lanes=base.graph.lanes), base.graph)


class DarkThemeTests(unittest.TestCase):
    """The dark theme (RZG-32): its own colours, readable on its backgrounds."""

    def test_is_dark_and_the_light_theme_is_not(self):
        self.assertTrue(DARK_THEME.dark)
        self.assertFalse(LIGHT_THEME.dark)

    def test_backgrounds_are_dark(self):
        c = DARK_THEME.colors
        for name in ("menubar_bg", "toolbar_bg", "tab_bg", "header_bg", "sidebar_bg",
                     "content_bg", "button_bg", "tooltip_bg", "diff_added_bg",
                     "diff_removed_bg", "diff_hunk_bg", "diff_filler_bg",
                     "diff_added_selected_bg", "diff_removed_selected_bg"):
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
                           c.diff_removed_word, c.diff_added_selected_bg,
                           c.diff_removed_selected_bg):
            self.assertGreaterEqual(contrast(c.text, background), 4.5, background)

    def test_status_colours_are_readable_and_distinct(self):
        status, c = DARK_THEME.status, DARK_THEME.colors
        for name, colour in dataclasses.asdict(status).items():
            for background in (c.sidebar_bg, c.content_bg, c.selection):
                self.assertGreaterEqual(contrast(colour, background), 3, (name, background))
        main = (status.modified, status.added, status.deleted, status.renamed,
                status.conflict, status.untracked)
        self.assertEqual(len(set(main)), len(main))



class FloatingButtonTests(unittest.TestCase):
    """History's Back to top reads as a control over the rows, not as a row (RZG-64)."""

    def test_it_stands_apart_from_the_rows_and_its_text_reads(self):
        for theme in ALL_THEMES:
            c = theme.colors
            with self.subTest(theme.name):
                for row in (c.content_bg, c.row_alt_bg, c.content_hover):
                    self.assertGreaterEqual(delta_e(c.floating_button_bg, row), 3)
                self.assertGreaterEqual(delta_e(c.floating_button_hover, c.floating_button_bg), 3)
                for fill in (c.floating_button_bg, c.floating_button_hover):
                    self.assertGreaterEqual(contrast(c.floating_button_text, fill), 4.5)
                self.assertGreaterEqual(contrast(c.floating_button_border, c.floating_button_bg), 1.5)


class FindMatchColourTests(unittest.TestCase):
    """Find's matches stand out from every diff background and read (RZG-74)."""

    BACKGROUNDS = ("content_bg", "text_selection", "diff_added_bg", "diff_added_word",
                   "diff_removed_bg", "diff_removed_word", "diff_added_selected_bg",
                   "diff_removed_selected_bg", "diff_hunk_bg", "diff_filler_bg")

    def test_text_reads_on_it_and_it_stands_apart_under_every_vision(self):
        for theme in ALL_THEMES:
            c = theme.colors
            with self.subTest(theme.name):
                self.assertGreaterEqual(contrast(c.text, c.diff_find_match), 4.5)
                for name in self.BACKGROUNDS:
                    self.assertGreaterEqual(min_delta_e(c.diff_find_match, getattr(c, name)), 6,
                                            name)

    def test_the_current_matchs_outline_shows_on_everything_under_it(self):
        for theme in ALL_THEMES:
            c = theme.colors
            with self.subTest(theme.name):
                for name in ("diff_find_match", *self.BACKGROUNDS):
                    self.assertGreaterEqual(contrast(c.diff_find_current, getattr(c, name)), 3,
                                            name)
