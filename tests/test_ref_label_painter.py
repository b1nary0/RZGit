import unittest

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from rzgitclient.core.ref_labels import RefKind, RefLabel
from rzgitclient.ui.panels.ref_label_painter import RefLabelPainter
from rzgitclient.ui.theme.app_theme import DARK_THEME, LIGHT_THEME
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
    """History's ref labels, drawn (RZG-77, spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.refs = RefLabelPainter(LIGHT_THEME, QFont())

    def draw(self, labels, width, lane=0, theme=LIGHT_THEME):
        self.refs.set_theme(theme)
        image = QImage(width, HEIGHT, QImage.Format.Format_ARGB32)
        image.fill(QColor(theme.colors.content_bg))
        painter = QPainter(image)
        self.refs.paint(painter, QRect(0, 0, width, HEIGHT), labels, lane)
        painter.end()
        return image

    def test_every_label_fits_its_natural_width(self):
        labels = [branch("main", True), tag("v1.0"), stash(0)]
        width = self.refs.natural_width(labels)
        pills = self.refs.layout(labels, width)
        self.assertEqual([p.text for p in pills], ["main", "v1.0", "stash@{0}"])
        self.assertEqual([p.label for p in pills], labels)
        self.assertEqual(pills[0].x, M.ref_label_gap)
        for a, b in zip(pills, pills[1:]):
            self.assertEqual(b.x, a.x + a.width + M.ref_label_gap)  # no overlap, a gap between
        self.assertLessEqual(pills[-1].x + pills[-1].width, width - M.ref_label_gap)
        self.assertEqual(self.refs.natural_width([]), 0)

    def test_the_current_branch_is_bold_and_wider(self):
        bold, plain = self.refs.pill_width(branch("main", True)), self.refs.pill_width(branch("main"))
        self.assertGreater(bold, plain)
        self.assertTrue(self.refs.layout([branch("main", True)], 200)[0].bold)

    def test_a_long_name_is_shortened_in_the_middle_to_the_maximum(self):
        long = branch("feature/" + "x" * 60 + "-the-end")
        self.assertEqual(self.refs.pill_width(long), M.ref_label_max_width)
        pill = self.refs.layout([long], 400)[0]
        self.assertEqual(pill.width, M.ref_label_max_width)
        self.assertTrue(pill.text.startswith("feature/"), pill.text)
        self.assertTrue(pill.text.endswith("the-end"), pill.text)
        self.assertIn("…", pill.text)

    def test_labels_that_dont_fit_leave_a_count(self):
        labels = [branch("main"), tag("v1.0"), tag("v1.1"), stash(0)]
        first = self.refs.pill_width(labels[0])
        width = first + 3 * M.ref_label_gap + 40  # main, then room for "+3" only
        pills = self.refs.layout(labels, width)
        self.assertEqual([p.text for p in pills], ["main", "+3"])
        self.assertIsNone(pills[1].label)
        self.assertLessEqual(pills[-1].x + pills[-1].width, width - M.ref_label_gap)

    def test_a_first_label_too_wide_is_shortened_beside_the_count(self):
        labels = [branch("a-rather-long-branch-name"), tag("v1.0")]
        pills = self.refs.layout(labels, 90)
        self.assertEqual(len(pills), 2)
        self.assertIn("…", pills[0].text)
        self.assertEqual(pills[1].text, "+1")
        self.assertLessEqual(pills[1].x + pills[1].width, 90 - M.ref_label_gap)
        alone = self.refs.layout([branch("a-rather-long-branch-name")], 60)
        self.assertEqual(len(alone), 1)  # no count: nothing is left out
        self.assertLessEqual(alone[0].x + alone[0].width, 60 - M.ref_label_gap)

    def test_colours_by_kind(self):
        lanes = LIGHT_THEME.graph.lanes
        for label, border in ((branch("main"), lanes[2]), (remote("origin/x"), lanes[2]),
                              (tag("v1.0"), LIGHT_THEME.graph.tag_border)):
            with self.subTest(label.kind):
                image = self.draw([label], 120, lane=2)
                x = M.ref_label_gap + 4  # inside the pill, left of its text
                edge = image.pixelColor(x, (HEIGHT - self.pill_height()) // 2)
                self.assertLess(self.distance(edge, QColor(border)), 60, edge.name())
        bg = QColor(LIGHT_THEME.colors.content_bg)
        inside = lambda img: img.pixelColor(M.ref_label_gap + 2, HEIGHT // 2)  # noqa: E731
        self.assertNotEqual(inside(self.draw([branch("main")], 120, lane=2)), bg)  # a tint
        self.assertEqual(inside(self.draw([remote("origin/x")], 120, lane=2)), bg)  # no fill
        self.assertEqual(inside(self.draw([tag("v1.0")], 120)).name(), LIGHT_THEME.graph.tag_fill)
        self.assertEqual(inside(self.draw([stash(0)], 120)).name(), LIGHT_THEME.graph.stash_fill)

    def test_a_theme_change_recolours(self):
        dark = self.draw([tag("v1.0")], 120, theme=DARK_THEME)
        self.assertEqual(dark.pixelColor(M.ref_label_gap + 2, HEIGHT // 2).name(),
                         DARK_THEME.graph.tag_fill)

    def test_nothing_is_drawn_outside_the_cell(self):
        image = QImage(300, HEIGHT, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        self.refs.paint(painter, QRect(0, 0, 50, HEIGHT), [branch("x" * 40), tag("v")], 0)
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
