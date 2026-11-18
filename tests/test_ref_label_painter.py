import unittest

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from rzgit.core.ref_labels import RefKind, RefLabel
from rzgit.ui.panels.ref_label_painter import RefLabelPainter
from rzgit.ui.theme.app_theme import DARK_THEME, LIGHT_THEME
from tests.support import qt_app

HEIGHT = 22
M = LIGHT_THEME.metrics


def branch(text, current=False):
    return RefLabel(RefKind.BRANCH, text, (text,), current)


def remote(text):
    return RefLabel(RefKind.REMOTE, text, (text,))


def tag(text):
    return RefLabel(RefKind.TAG, text, (text,))


def stash(index):
    return RefLabel(RefKind.STASH, f"stash@{{{index}}}", (f"stash@{{{index}}}",), message="m")


class RefLabelPainterTests(unittest.TestCase):
    """History's ref label over the graph, drawn (RZG-77, RZG-86, spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.refs = RefLabelPainter(LIGHT_THEME, QFont())

    def draw(self, labels, width, lane=0, theme=LIGHT_THEME, centre=None, striped=False,
             under=None):
        """``labels`` drawn over a ``content_bg`` cell (``under``: a colour filled
        there first, as a graph would be), centred on ``centre`` (its middle)."""
        self.refs.set_theme(theme)
        image = QImage(width, HEIGHT, QImage.Format.Format_ARGB32)
        image.fill(QColor(under or theme.colors.content_bg))
        painter = QPainter(image)
        self.refs.paint(painter, QRect(0, 0, width, HEIGHT), labels, lane,
                        width / 2 if centre is None else centre, striped)
        painter.end()
        return image

    def test_one_label_centred_on_the_dot_and_a_count_of_the_rest(self):
        labels = [branch("main", True), tag("v1.0"), stash(0)]
        pills = self.refs.layout(labels, 400, 200)
        self.assertEqual([p.text for p in pills], ["main", "+2"])
        self.assertEqual(pills[0].label, labels[0])
        self.assertIsNone(pills[1].label)
        self.assertLessEqual(abs(pills[0].x + pills[0].width / 2 - 200), 1)
        self.assertEqual(pills[1].x, pills[0].x + pills[0].width + M.ref_label_gap)
        self.assertEqual([p.text for p in self.refs.layout(labels[:1], 400, 200)], ["main"])
        self.assertEqual(self.refs.layout([], 400, 200), [])

    def test_left_reach_is_the_room_a_centred_label_needs(self):
        label = branch("a-branch")
        reach = self.refs.left_reach([label])
        self.assertEqual(reach, self.refs.pill_width(label) / 2 + M.ref_label_gap)
        pill = self.refs.layout([label], 400, reach)[0]
        self.assertEqual(pill.x, M.ref_label_gap)  # just fits, centred
        self.assertLessEqual(abs(pill.x + pill.width / 2 - reach), 1)
        self.assertEqual(self.refs.left_reach([]), 0)

    def test_with_no_room_on_the_left_it_stays_inside(self):
        pill = self.refs.layout([branch("a-branch")], 400, 4)[0]
        self.assertEqual(pill.x, M.ref_label_gap)
        self.assertEqual(pill.width, self.refs.pill_width(branch("a-branch")))  # whole

    def test_the_needed_width_shows_it_all(self):
        labels = [branch("main"), tag("v1.0")]
        for centre in (4, 60):
            with self.subTest(centre=centre):
                width = self.refs.needed_width(labels, centre)
                pills = self.refs.layout(labels, width, centre)
                self.assertEqual(pills[0].text, "main")
                self.assertEqual(pills[-1].x + pills[-1].width, width - M.ref_label_gap)
        self.assertEqual(self.refs.needed_width([], 10), 0)

    def test_the_current_branch_is_bold_and_wider(self):
        bold, plain = self.refs.pill_width(branch("main", True)), self.refs.pill_width(branch("main"))
        self.assertGreater(bold, plain)
        self.assertTrue(self.refs.layout([branch("main", True)], 200, 100)[0].bold)

    def test_a_long_name_is_shortened_in_the_middle_to_the_maximum(self):
        long = branch("feature/" + "x" * 60 + "-the-end")
        self.assertEqual(self.refs.pill_width(long), M.ref_label_max_width)
        pill = self.refs.layout([long], 400, 200)[0]
        self.assertEqual(pill.width, M.ref_label_max_width)
        self.assertTrue(pill.text.startswith("feature/"), pill.text)
        self.assertTrue(pill.text.endswith("the-end"), pill.text)
        self.assertIn("…", pill.text)

    def test_in_a_narrow_cell_it_moves_left_then_is_shortened(self):
        labels = [branch("main"), tag("v1.0")]
        whole = self.refs.needed_width(labels, 10)
        pills = self.refs.layout(labels, whole, 30)  # centred on 30 it would not fit
        self.assertEqual(pills[0].text, "main")
        self.assertEqual(pills[-1].x + pills[-1].width, whole - M.ref_label_gap)
        labels = [branch("a-rather-long-branch-name"), tag("v1.0")]
        pills = self.refs.layout(labels, 90, 10)
        self.assertEqual(pills[0].x, M.ref_label_gap)
        self.assertIn("…", pills[0].text)
        self.assertEqual(pills[1].text, "+1")
        self.assertLessEqual(pills[1].x + pills[1].width, 90 - M.ref_label_gap)

    def test_a_pill_is_opaque_over_the_graph(self):
        under = "#ff00ff"  # a graph line, say
        for label in (branch("main"), remote("origin/x"), tag("v1.0")):
            with self.subTest(label.kind):
                image = self.draw([label], 120, under=under)
                inside = image.pixelColor(60 - self.refs.pill_width(label) // 2 + 2, HEIGHT // 2)
                self.assertGreater(self.distance(inside, QColor(under)), 150, inside.name())
        striped = self.draw([remote("origin/x")], 120, under=under, striped=True)
        inside = striped.pixelColor(60 - self.refs.pill_width(remote("origin/x")) // 2 + 2,
                                    HEIGHT // 2)
        self.assertEqual(inside.name(), LIGHT_THEME.colors.row_alt_bg)  # the row's own stripe

    def test_colours_by_kind(self):
        lanes = LIGHT_THEME.graph.lanes
        for label, border in ((branch("main"), lanes[2]), (remote("origin/x"), lanes[2]),
                              (tag("v1.0"), LIGHT_THEME.graph.tag_border)):
            with self.subTest(label.kind):
                image = self.draw([label], 120, lane=2, centre=0)
                x = M.ref_label_gap + 4  # inside the pill, left of its text
                edge = image.pixelColor(x, (HEIGHT - self.pill_height()) // 2)
                self.assertLess(self.distance(edge, QColor(border)), 60, edge.name())
        bg = QColor(LIGHT_THEME.colors.content_bg)
        inside = lambda img: img.pixelColor(M.ref_label_gap + 2, HEIGHT // 2)  # noqa: E731
        self.assertNotEqual(inside(self.draw([branch("main")], 120, lane=2, centre=0)), bg)  # tint
        self.assertEqual(inside(self.draw([remote("origin/x")], 120, lane=2, centre=0)), bg)  # none
        self.assertEqual(inside(self.draw([tag("v1.0")], 120, centre=0)).name(),
                         LIGHT_THEME.graph.tag_fill)
        self.assertEqual(inside(self.draw([stash(0)], 120, centre=0)).name(),
                         LIGHT_THEME.graph.stash_fill)

    def test_a_theme_change_recolours(self):
        dark = self.draw([tag("v1.0")], 120, theme=DARK_THEME, centre=0)
        self.assertEqual(dark.pixelColor(M.ref_label_gap + 2, HEIGHT // 2).name(),
                         DARK_THEME.graph.tag_fill)

    def test_nothing_is_drawn_outside_the_cell(self):
        image = QImage(300, HEIGHT, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        self.refs.paint(painter, QRect(0, 0, 50, HEIGHT), [branch("x" * 40), tag("v")], 0, 25)
        painter.end()
        self.assertTrue(all(image.pixelColor(x, y).alpha() == 0
                            for x in range(50, 300, 5) for y in range(HEIGHT)))

    def pill_height(self):
        from PySide6.QtGui import QFontMetrics
        return min(HEIGHT - 2, QFontMetrics(QFont()).height() + 2)

    @staticmethod
    def distance(a, b):
        return abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())


if __name__ == "__main__":
    unittest.main()
