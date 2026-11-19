import sys
import unittest
from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QRectF

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docs" / "screenshots"))
from outline_shape import outline_path, overlap_groups  # noqa: E402


class OverlapGroupsTests(unittest.TestCase):
    """RZG-95: only subjects that overlap share an outline."""

    def test_overlapping_rects_group_and_others_stay_apart(self):
        panel, menu = QRect(0, 0, 100, 50), QRect(60, 48, 80, 40)  # 2 px over the panel
        below = QRect(0, 51, 50, 50)  # 1 px below the panel, clear of the menu
        self.assertEqual(overlap_groups([panel, menu, below]), [[panel, menu], [below]])

    def test_touching_isnt_overlapping(self):
        a, b = QRect(0, 0, 10, 10), QRect(10, 0, 10, 10)  # side by side, no shared pixel
        self.assertEqual(overlap_groups([a, b]), [[a], [b]])

    def test_a_chain_is_one_group(self):
        a, b, c = QRect(0, 0, 10, 10), QRect(8, 0, 10, 10), QRect(16, 0, 10, 10)
        d = QRect(100, 0, 5, 5)
        self.assertEqual(overlap_groups([a, c, d, b]), [[a, c, b], [d]])


class OutlinePathTests(unittest.TestCase):
    """RZG-95: one outline around the union, every corner rounded."""

    # An L: a panel, and a menu hanging off its lower right, overlapping it.
    PANEL, MENU = QRect(0, 0, 100, 50), QRect(60, 40, 80, 60)

    def setUp(self):
        self.path = outline_path([self.PANEL, self.MENU], 6)

    def test_one_shape_covering_both(self):
        self.assertEqual(len(self.path.toSubpathPolygons()), 1)
        for x, y in ((50, 25), (100, 70), (80, 45), (135, 95), (3, 3)):  # in either, or both
            self.assertTrue(self.path.contains(QPointF(x, y)), (x, y))
        self.assertFalse(self.path.contains(QPointF(30, 80)))  # below the panel, left of the menu
        self.assertEqual(self.path.boundingRect().toRect(), QRect(0, 0, 140, 100))

    def test_outside_corners_are_rounded_off(self):
        for x, y in ((0.5, 0.5), (139.5, 99.5), (60.5, 99.5), (99.5, 0.5)):
            self.assertFalse(self.path.contains(QPointF(x, y)), (x, y))

    def test_inside_corners_are_rounded_in(self):
        # Where the panel's bottom meets the menu's left side: the rounding fills it.
        self.assertTrue(self.path.contains(QPointF(59, 51)))
        # The other inside corner, where the panel's right side meets the menu's top.
        self.assertTrue(self.path.contains(QPointF(101, 39)))
        # Far from both, the outside stays outside.
        self.assertFalse(self.path.contains(QPointF(50, 60)))

    def test_a_2_px_overlap_is_one_clean_shape(self):
        # The menu 2 px over the panel (as in the Files screenshot): one shape, no seam.
        path = outline_path([QRect(0, 0, 100, 50), QRect(60, 48, 80, 40)], 6)
        self.assertEqual(len(path.toSubpathPolygons()), 1)
        self.assertEqual(path.boundingRect().toRect(), QRect(0, 0, 140, 88))
        self.assertTrue(path.contains(QPointF(99, 49)))  # where they overlap

    def test_a_2_px_step_rounds_by_1_px_without_overshooting(self):
        # The menu's left side 2 px right of the panel's: a 2 px edge in the outline.
        path = outline_path([QRect(0, 0, 100, 50), QRect(2, 40, 80, 40)], 6)
        self.assertEqual(len(path.toSubpathPolygons()), 1)
        self.assertEqual(path.boundingRect(), QRectF(0, 0, 100, 80))  # nothing sticks out
        self.assertTrue(path.contains(QPointF(1, 49)))  # beside the step, inside the panel

    def test_a_single_rect_is_a_rounded_rect(self):
        path = outline_path([QRect(0, 0, 40, 20)], 6)
        self.assertTrue(path.contains(QPointF(20, 10)))
        self.assertFalse(path.contains(QPointF(0.5, 0.5)))
        self.assertTrue(path.contains(QPointF(6, 0.5)))  # the straight edge past the corner


if __name__ == "__main__":
    unittest.main()
