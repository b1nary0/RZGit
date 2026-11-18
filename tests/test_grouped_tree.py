import unittest

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTreeWidgetItem

from rzgit.core.sort_options import Group
from rzgit.ui.theme.app_theme import DARK_THEME, LIGHT_THEME
from rzgit.ui.widgets.grouped_tree import GroupedTree, heading_key
from tests.support import qt_app


class GroupedTreeTests(unittest.TestCase):
    """Group headings in a list (spec §5.1, RZG-53)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    @classmethod
    def tearDownClass(cls):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def make(self, selectable_headings=False, groups=(("A", ["a1", "a2"]), ("B", ["b1"]))):
        tree = GroupedTree(LIGHT_THEME, selectable_headings)
        self.addCleanup(tree.deleteLater)
        tree.setColumnCount(2)
        self.fill(tree, groups)
        tree.resize(300, 300)
        tree.show()
        QTest.qWaitForWindowExposed(tree)
        return tree

    def fill(self, tree, groups):
        items = []
        for name, rows in groups:
            heading = tree.heading(Group("g", name, list(rows)))
            heading.addChildren([QTreeWidgetItem([r, "x"]) for r in rows])
            items.append(heading)
        tree.clear()
        tree.addTopLevelItems(items)
        tree.fold_headings()

    def key(self, tree, key):
        QApplication.sendEvent(tree, QKeyEvent(QEvent.Type.KeyPress, key,
                                               Qt.KeyboardModifier.NoModifier))
        current = tree.currentItem()
        return current.text(0) if current is not None else None

    def test_a_heading_shows_its_name_and_count_spanning_the_columns(self):
        tree = self.make()
        heading = tree.topLevelItem(0)
        self.assertEqual(heading.text(0), "A (2)")
        self.assertEqual(heading_key(heading), ("g", "A"))
        self.assertIsNone(heading_key(heading.child(0)))
        self.assertTrue(heading.isFirstColumnSpanned())
        self.assertTrue(heading.isExpanded())
        self.assertTrue(heading.font(0).bold())
        self.assertEqual(tree.heading(Group("g", "C", [1]), count=1234).text(0), "C (1,234)")
        self.assertEqual([i.text(0) for i in tree.rows()], ["a1", "a2", "b1"])
        self.assertEqual([i.text(0) for i in tree.headings()], ["A (2)", "B (1)"])

    def row_colours(self, tree, item):
        """The colours across ``item``'s row, at its middle, as painted."""
        image = tree.viewport().grab().toImage()
        y = tree.visualItemRect(item).center().y()
        return {image.pixelColor(x, y).name() for x in (1, image.width() // 2, image.width() - 2)}

    def test_the_heading_row_is_painted_from_the_theme_under_the_stylesheet(self):
        """The app's stylesheet ignores an item's background: drawRow paints it,
        under the whole row (its arrow too), and set_theme recolours it."""
        for theme in (LIGHT_THEME, DARK_THEME):
            tree = self.make()
            tree.setStyleSheet(theme.stylesheet())
            tree.set_theme(theme)
            heading = tree.topLevelItem(1)
            self.assertEqual(self.row_colours(tree, heading), {theme.colors.group_header_bg},
                             theme.name)
            self.assertNotIn(theme.colors.group_header_bg,
                             self.row_colours(tree, heading.child(0)), theme.name)
            self.assertEqual(heading.foreground(0).color().name(),
                             theme.colors.group_header_text)

    def test_folded_headings_are_folded_again_until_forgotten(self):
        tree = self.make()
        tree.topLevelItem(1).setExpanded(False)  # as the user would
        self.fill(tree, (("A", ["a1"]), ("B", ["b1", "b2"])))
        self.assertTrue(tree.topLevelItem(0).isExpanded())
        self.assertFalse(tree.topLevelItem(1).isExpanded())
        tree.topLevelItem(1).setExpanded(True)
        tree.topLevelItem(0).setExpanded(False)
        self.fill(tree, (("A", ["a1"]), ("B", ["b1"])))
        self.assertEqual([h.isExpanded() for h in tree.headings()], [False, True])
        tree.forget_folded()
        self.fill(tree, (("A", ["a1"]), ("B", ["b1"])))
        self.assertEqual([h.isExpanded() for h in tree.headings()], [True, True])

    def test_the_arrow_keys_skip_headings_that_cannot_be_selected(self):
        tree = self.make()
        tree.setFocus()
        tree.setCurrentItem(tree.topLevelItem(0).child(1))  # a2
        self.assertEqual(self.key(tree, Qt.Key.Key_Down), "b1")  # past "B"
        self.assertEqual(self.key(tree, Qt.Key.Key_Up), "a2")
        self.assertEqual(self.key(tree, Qt.Key.Key_Home), "a1")  # the first row is "A"
        self.assertEqual(self.key(tree, Qt.Key.Key_Up), "a1")  # nothing above but "A"
        tree.topLevelItem(1).setExpanded(False)
        self.assertEqual(self.key(tree, Qt.Key.Key_End), "a2")  # the last row is "B", folded

    def test_a_click_on_a_heading_that_cannot_be_selected_keeps_the_selection(self):
        tree = self.make()
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        b1 = tree.topLevelItem(1).child(0)
        tree.setCurrentItem(b1)
        self.assertEqual(tree.selectedItems(), [b1])
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=tree.visualItemRect(tree.topLevelItem(0)).center())
        self.assertEqual(tree.selectedItems(), [b1])
        self.assertFalse(tree.topLevelItem(0).flags() & Qt.ItemFlag.ItemIsSelectable)

    def test_selectable_headings_are_selected_and_not_skipped(self):
        tree = self.make(selectable_headings=True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        heading = tree.topLevelItem(0)
        self.assertTrue(heading.flags() & Qt.ItemFlag.ItemIsSelectable)
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=tree.visualItemRect(heading).center())
        self.assertEqual(tree.selectedItems(), [heading])
        tree.setFocus()
        tree.setCurrentItem(heading.child(1))
        self.assertEqual(self.key(tree, Qt.Key.Key_Down), "B (1)")


if __name__ == "__main__":
    unittest.main()
