import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTreeView

from rzgitclient.core.column_prefs import ColumnPrefs
from rzgitclient.ui.theme.app_theme import DARK_THEME, LIGHT_THEME
from rzgitclient.ui.widgets.column_header import (PADDING_X, ColumnHeader, short_label)
from tests.support import qt_app

LABELS = ("Message", "Branch", "Status")


class ColumnHeaderTests(unittest.TestCase):
    """The column labels of a list (spec §5.2, RZG-41)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.prefs = ColumnPrefs()
        self.view, self.header = self.make_view()

    def make_view(self) -> tuple[QTreeView, ColumnHeader]:
        view = QTreeView()
        self.addCleanup(view.deleteLater)
        header = ColumnHeader("stash", self.prefs, LIGHT_THEME)
        view.setHeader(header)
        model = QStandardItemModel(0, len(LABELS), view)
        model.setHorizontalHeaderLabels(LABELS)
        view.setModel(model)
        header.setStretchLastSection(False)
        view.resize(400, 200)
        view.show()
        QTest.qWaitForWindowExposed(view)
        return view, header

    def pixel(self, x: int, y: int) -> str:
        return QColor(self.header.grab().toImage().pixel(x, y)).name()

    # ---- labels ----------------------------------------------------------

    def test_short_label_is_the_first_letter_and_two_dots(self):
        self.assertEqual(short_label("Status"), "S..")
        self.assertEqual(short_label(""), "")

    def test_a_label_too_wide_for_its_column_is_shortened(self):
        self.header.resizeSection(2, 200)
        self.assertEqual(self.header.shown_label(2), "Status")
        self.header.resizeSection(2, 2 * PADDING_X + 4)
        self.assertEqual(self.header.shown_label(2), "S..")

    def test_fitting_a_column_keeps_its_whole_label(self):
        """The size hint is the whole label's, so fitting to contents never shortens it."""
        self.view.resizeColumnToContents(2)  # no rows: the label decides
        self.assertEqual(self.header.shown_label(2), "Status")

    def test_labels_are_not_clickable_or_movable(self):
        self.assertFalse(self.header.sectionsClickable())
        self.assertFalse(self.header.sectionsMovable())

    # ---- look --------------------------------------------------------------

    def test_painted_from_the_theme_tokens(self):
        c = LIGHT_THEME.colors
        self.header.resizeSection(0, 100)
        self.header.resizeSection(1, 100)
        self.header.resizeSection(2, 100)
        middle = self.header.height() // 2
        self.assertEqual(self.pixel(50, 2), c.column_header_bg)  # above the text
        self.assertEqual(self.pixel(50, self.header.height() - 1), c.column_header_border)
        self.assertEqual(self.pixel(99, middle), c.column_header_separator)
        self.assertEqual(self.pixel(350, 2), c.column_header_bg)  # past the last column
        self.assertEqual(self.pixel(350, self.header.height() - 1), c.column_header_border)

    def test_separators_are_inset_and_none_after_the_last_column(self):
        c = LIGHT_THEME.colors
        for column in range(3):
            self.header.resizeSection(column, 100)
        self.assertEqual(self.pixel(99, 1), c.column_header_bg)  # inset: not at the top
        self.assertEqual(self.pixel(199, self.header.height() // 2), c.column_header_separator)
        self.assertEqual(self.pixel(299, self.header.height() // 2), c.column_header_bg)

    def test_a_new_theme_repaints_it(self):
        self.header.set_theme(DARK_THEME)
        self.assertEqual(self.pixel(self.header.width() - 2, 2), DARK_THEME.colors.column_header_bg)

    def test_the_hovered_label_is_tracked(self):
        self.header.resizeSection(0, 100)
        QTest.mouseMove(self.header.viewport(), QPoint(150, 5))
        self.assertEqual(self.header.hover_section, 1)

    # ---- showing and hiding columns --------------------------------------------

    def test_the_menu_lists_every_column_and_the_main_one_cant_be_hidden(self):
        actions = self.header.columns_menu().actions()
        self.assertEqual([a.text() for a in actions], list(LABELS))
        self.assertTrue(all(a.isCheckable() and a.isChecked() for a in actions))
        self.assertFalse(actions[0].isEnabled())
        self.assertTrue(actions[1].isEnabled())

    def test_unchecking_a_column_hides_it_and_saves_the_choice(self):
        self.header.columns_menu().actions()[1].setChecked(False)
        self.assertTrue(self.header.isSectionHidden(1))
        self.assertEqual(self.prefs.hidden("stash"), {"Branch"})
        self.assertFalse(self.header.columns_menu().actions()[1].isChecked())
        self.header.columns_menu().actions()[1].setChecked(True)
        self.assertFalse(self.header.isSectionHidden(1))
        self.assertEqual(self.prefs.hidden("stash"), set())

    def test_a_new_header_hides_what_the_panel_hid(self):
        self.prefs.set_hidden("stash", "Status", True)
        _view, header = self.make_view()
        self.assertTrue(header.isSectionHidden(2))
        self.assertFalse(header.isSectionHidden(1))

    def test_the_main_column_is_never_hidden(self):
        self.prefs.set_hidden("stash", "Message", True)
        _view, header = self.make_view()
        self.assertFalse(header.isSectionHidden(0))

    def test_hiding_the_last_column_moves_the_end_of_the_separators(self):
        c = LIGHT_THEME.colors
        for column in range(3):
            self.header.resizeSection(column, 100)
        self.header.set_column_shown("Status", False)
        self.assertEqual(self.pixel(199, self.header.height() // 2), c.column_header_bg)

    def test_the_labels_have_a_menu(self):
        self.assertEqual(self.header.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)


if __name__ == "__main__":
    unittest.main()
