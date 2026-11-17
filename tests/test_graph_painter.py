import unittest

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter

from rzgitclient.core.commit_graph import GraphRow
from rzgitclient.ui.panels.graph_painter import GRAPH_MAX_LANES, PADDING_X, GraphPainter
from rzgitclient.ui.theme.app_theme import DARK_THEME, LIGHT_THEME
from tests.support import qt_app

HEIGHT = 24
LANE = LIGHT_THEME.metrics.graph_lane_width
LANES = LIGHT_THEME.graph.lanes


def x_of(lane: int) -> int:
    return int(PADDING_X + (lane + 0.5) * LANE)


class GraphPainterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.graph = GraphPainter(LIGHT_THEME)

    def draw(self, row=None, *, uncommitted_head=None, joined_above=False, lanes=12):
        image = QImage(PADDING_X * 2 + lanes * LANE, HEIGHT, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        rect = QRect(0, 0, image.width(), HEIGHT)
        if row is not None:
            self.graph.paint(painter, rect, row, joined_above=joined_above)
        else:
            self.graph.paint_uncommitted(painter, rect, uncommitted_head)
        painter.end()
        return image

    def colour(self, image, x, y):
        return image.pixelColor(x, y)

    def test_width_fits_the_lanes_up_to_the_cap(self):
        self.assertEqual(self.graph.width(1), LANE + 2 * PADDING_X)
        self.assertEqual(self.graph.width(3), 3 * LANE + 2 * PADDING_X)
        self.assertEqual(self.graph.width(30), self.graph.width(GRAPH_MAX_LANES))

    def test_a_dot_in_its_lane_and_colour_with_lines_above_and_below(self):
        row = GraphRow(1, 2, ((0, 0, 0), (1, 1, 2)), ((0, 0, 0), (1, 1, 2)))
        image = self.draw(row)
        self.assertEqual(self.colour(image, x_of(1), HEIGHT // 2).name(), LANES[2])  # the dot
        self.assertEqual(self.colour(image, x_of(0), 1).name(), LANES[0])  # lane 0 passes by
        self.assertEqual(self.colour(image, x_of(0), HEIGHT - 1).name(), LANES[0])
        self.assertEqual(self.colour(image, x_of(2), HEIGHT // 2).alpha(), 0)  # nothing there

    def test_a_line_changing_lane_curves_into_the_other_lane_at_the_bottom(self):
        row = GraphRow(0, 0, (), ((0, 0, 0), (0, 2, 1)))
        image = self.draw(row)
        self.assertEqual(self.colour(image, x_of(2), HEIGHT - 1).name(), LANES[1])
        self.assertEqual(self.colour(image, x_of(2), 1).alpha(), 0)  # nothing in the top half

    def test_a_colour_index_wraps_round_the_theme_colours(self):
        row = GraphRow(0, len(LANES) + 3, (), ())
        self.assertEqual(self.colour(self.draw(row), x_of(0), HEIGHT // 2).name(), LANES[3])

    def test_a_row_wider_than_the_cap_is_cut_off_and_its_last_lane_fades(self):
        last = GRAPH_MAX_LANES - 1
        lines = tuple((i, i, i) for i in range(GRAPH_MAX_LANES + 2))
        image = self.draw(GraphRow(0, 0, lines, lines))
        first = self.colour(image, x_of(0), 2).alpha()
        faded = self.colour(image, x_of(last), 2).alpha()
        self.assertEqual(first, 255)
        self.assertLess(faded, 200)
        self.assertGreater(faded, 0)
        self.assertEqual(self.colour(image, x_of(GRAPH_MAX_LANES), 2).alpha(), 0)  # cut off
        curve = self.draw(GraphRow(0, 0, (), ((0, 0, 0), (3, 11, 1))))  # into a lane past it
        self.assertEqual(self.colour(curve, x_of(11), HEIGHT - 1).alpha(), 0)

    def test_a_cell_narrower_than_its_graph_clips_it(self):
        lines = tuple((i, i, 0) for i in range(4))
        image = QImage(PADDING_X * 2 + 6 * LANE, HEIGHT, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        self.graph.paint(painter, QRect(0, 0, PADDING_X + 2 * LANE, HEIGHT), GraphRow(0, 0, lines, lines))
        painter.end()
        self.assertEqual(self.colour(image, x_of(1), 2).alpha(), 255)
        self.assertEqual(self.colour(image, x_of(3), 2).alpha(), 0)

    def test_a_row_within_the_cap_doesnt_fade(self):
        last = GRAPH_MAX_LANES - 1
        lines = tuple((i, i, 0) for i in range(GRAPH_MAX_LANES))
        image = self.draw(GraphRow(0, 0, lines, lines))
        self.assertEqual(self.colour(image, x_of(last), 2).alpha(), 255)

    def test_uncommitted_changes_is_a_hollow_dot_joined_to_heads(self):
        head = GraphRow(1, 4, (), ())
        image = self.draw(uncommitted_head=head)
        self.assertEqual(self.colour(image, x_of(1), HEIGHT // 2).alpha(), 0)  # hollow
        self.assertEqual(self.colour(image, x_of(1), HEIGHT - 1).name(), LANES[4])  # to HEAD
        self.assertEqual(self.colour(image, x_of(1), 1).alpha(), 0)  # nothing above it
        ring = int(x_of(1) + LIGHT_THEME.metrics.graph_dot_radius - 1)
        self.assertGreater(self.colour(image, ring, HEIGHT // 2).alpha(), 100)

    def test_before_the_first_commit_the_hollow_dot_has_no_line(self):
        image = self.draw(uncommitted_head=None)
        self.assertEqual(self.colour(image, x_of(0), HEIGHT - 1).alpha(), 0)

    def test_heads_row_is_joined_up_to_uncommitted_changes(self):
        head = GraphRow(0, 0, (), ((0, 0, 0),))
        self.assertEqual(self.colour(self.draw(head), x_of(0), 1).alpha(), 0)
        image = self.draw(head, joined_above=True)
        self.assertEqual(self.colour(image, x_of(0), 1).name(), LANES[0])

    def test_set_theme_recolours(self):
        self.graph.set_theme(DARK_THEME)
        image = self.draw(GraphRow(0, 1, (), ()))
        self.assertEqual(self.colour(image, x_of(0), HEIGHT // 2).name(), DARK_THEME.graph.lanes[1])


if __name__ == "__main__":
    unittest.main()
