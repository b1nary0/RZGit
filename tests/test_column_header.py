import unittest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QStandardItem, QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTreeView

from rzgit.core.column_prefs import ColumnPrefs
from rzgit.ui.theme.app_theme import DARK_THEME, LIGHT_THEME
from rzgit.ui.widgets.column_header import (PADDING_X, RESET_WIDTHS, ColumnHeader,
                                                   short_label)
from tests.support import click_column_label, drag_column_edge, qt_app

LABELS = ("Message", "Branch", "Status")


class ColumnHeaderTests(unittest.TestCase):
    """The column labels of a list (spec §5.2, RZG-41)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.prefs = ColumnPrefs()
        self.view, self.header = self.make_view()

    def make_view(self, **options) -> tuple[QTreeView, ColumnHeader]:
        view = QTreeView()
        self.addCleanup(view.deleteLater)
        header = ColumnHeader("stash", self.prefs, LIGHT_THEME, **options)
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
        self.assertFalse(self.header.sectionsClickable())  # Qt's own clicks: off
        self.assertFalse(self.header.sectionsMovable())
        clicks = []
        self.header.sort_clicked.connect(clicks.append)
        click_column_label(self.header, 1)
        self.assertEqual(clicks, [])

    # ---- sorting (RZG-43) ----------------------------------------------------

    def test_a_sortable_label_emits_its_text_when_clicked(self):
        clicks = []
        self.header.sort_clicked.connect(clicks.append)
        self.header.set_sortable(["Branch", "Status"])
        click_column_label(self.header, 1)
        click_column_label(self.header, 0)  # Message: not sortable
        click_column_label(self.header, 2)
        self.assertEqual(clicks, ["Branch", "Status"])

    def test_a_press_released_on_another_label_or_a_drag_doesn_t_sort(self):
        clicks = []
        self.header.sort_clicked.connect(clicks.append)
        self.header.set_sortable(LABELS)
        viewport = self.header.viewport()
        y = self.header.height() // 2
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=QPoint(5, y))
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton,
                           pos=QPoint(self.header.sectionViewportPosition(2) + 5, y))
        drag_column_edge(self.header, 1, 10)
        self.assertEqual(clicks, [])

    def test_a_sortable_column_keeps_room_for_its_arrow(self):
        """Sorting never shortens a label: the room is there sorted or not."""
        plain = self.header.sectionSizeHint(2)
        self.header.set_sortable(["Status"])
        self.assertGreater(self.header.sectionSizeHint(2), plain)
        self.header.resizeSection(2, self.header.sectionSizeHint(2))
        self.assertEqual(self.header.shown_label(2), "Status")
        self.header.set_sort_indicator("Status")
        self.assertEqual(self.header.shown_label(2), "Status")

    def test_the_sorted_label_shows_an_arrow_up_or_down(self):
        self.header.set_sortable(["Status"])
        self.header.resizeSection(2, 120)
        self.header.fill()
        end = self.header.sectionViewportPosition(2) + self.header.sectionSize(2)
        area = (end - PADDING_X - 12, end - PADDING_X)
        text = QColor(LIGHT_THEME.colors.column_header_text).name()

        def arrow_rows() -> list[int]:
            image = self.header.grab().toImage()
            return [y for y in range(image.height())
                    if any(QColor(image.pixel(x, y)).name() == text for x in range(*area))]

        self.assertEqual(arrow_rows(), [])
        self.header.set_sort_indicator("Status", descending=False)
        up = arrow_rows()
        self.assertTrue(up)
        self.header.set_sort_indicator("Status", descending=True)
        self.assertTrue(arrow_rows())
        self.header.set_sort_indicator(None)
        self.assertEqual(arrow_rows(), [])
        self.header.set_sort_indicator("Branch")  # not sortable: no arrow anywhere
        self.assertEqual(self.header.sort_indicator, ("Branch", False))

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
        actions = self.header.columns_menu().actions()[:len(LABELS)]
        self.assertEqual([a.text() for a in actions], list(LABELS))
        self.assertTrue(all(a.isCheckable() and a.isChecked() for a in actions))
        self.assertFalse(actions[0].isEnabled())
        self.assertTrue(actions[1].isEnabled())

    def test_another_main_column_is_the_one_always_shown_and_taking_the_rest(self):
        """History's graph comes before its main column, Subject (RZG-58)."""
        _view, header = self.make_view(main_column=1)
        actions = header.columns_menu().actions()[:len(LABELS)]
        self.assertEqual([a.isEnabled() for a in actions], [True, False, True])
        header.set_fitted(0, 50)
        header.set_fitted(2, 60)
        self.assertEqual(header.sectionSize(0), 50)
        self.assertEqual(header.sectionSize(1), header.width() - 110)
        header.set_column_shown("Message", False)
        self.assertTrue(header.isSectionHidden(0))
        header.set_column_shown("Branch", False)  # the main one: never hidden
        self.assertFalse(header.isSectionHidden(1))

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



def double_click(widget, x: int) -> None:
    pos = QPointF(x, widget.height() / 2)
    QApplication.sendEvent(widget, QMouseEvent(
        QEvent.Type.MouseButtonDblClick, pos, widget.mapToGlobal(pos), Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


class ColumnWidthTests(unittest.TestCase):
    """Columns that always fill the width and keep the user's widths (spec §5.2, RZG-40)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.prefs = ColumnPrefs()
        self.view, self.header = self.make_view()
        self.fitted = self.sizes()

    def make_view(self) -> tuple[QTreeView, ColumnHeader]:
        view = QTreeView()
        self.addCleanup(view.deleteLater)
        header = ColumnHeader("stash", self.prefs, LIGHT_THEME)
        view.setHeader(header)
        model = QStandardItemModel(0, len(LABELS), view)
        model.setHorizontalHeaderLabels(LABELS)
        model.appendRow([QStandardItem(text) for text in ("a stash", "main", "M")])
        view.setModel(model)
        view.resize(400, 200)
        view.show()
        QTest.qWaitForWindowExposed(view)
        for column in (1, 2):
            header.fit(column)
        return view, header

    def edge(self, logical: int) -> int:
        return self.header.sectionViewportPosition(logical) + self.header.sectionSize(logical) - 1

    def drag(self, logical: int, by: int) -> None:
        drag_column_edge(self.header, logical, by)

    def others(self) -> int:
        return self.header.sectionSize(1) + self.header.sectionSize(2)

    def sizes(self) -> list[int]:
        return [self.header.sectionSize(c) for c in range(len(LABELS))]

    def assert_full_width(self) -> None:
        self.assertEqual(sum(self.sizes()), self.header.width())

    def resize_view(self, width: int) -> None:
        self.view.resize(width, 200)
        QApplication.processEvents()

    def move_to(self, x: int) -> None:
        viewport = self.header.viewport()
        pos = QPointF(x, viewport.height() / 2)
        QApplication.sendEvent(viewport, QMouseEvent(
            QEvent.Type.MouseMove, pos, viewport.mapToGlobal(pos), Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

    # ---- always the full width ------------------------------------------------

    def test_qt_resizing_is_off_the_header_lays_the_columns_out(self):
        for column in range(len(LABELS)):
            self.assertEqual(self.header.sectionResizeMode(column), self.header.ResizeMode.Fixed)

    def test_the_columns_fill_the_width_exactly(self):
        self.assert_full_width()
        self.resize_view(600)
        self.assert_full_width()
        self.assertEqual(self.sizes()[1:], self.fitted[1:])  # the main column took the rest

    def test_a_narrow_list_shrinks_the_other_columns_for_now(self):
        self.header.set_fitted(1, 150)
        self.header.set_fitted(2, 150)
        self.resize_view(250)
        self.assert_full_width()
        self.assertLess(self.header.sectionSize(1), 150)
        self.assertGreaterEqual(self.header.sectionSize(0), self.header.minimumSectionSize())
        self.resize_view(400)
        self.assertEqual(self.sizes()[1:], [150, 150])
        self.assert_full_width()

    def test_a_fitted_column_keeps_its_whole_label(self):
        self.assertEqual(self.header.shown_label(2), "Status")  # wider than its "M"

    # ---- dragging a separator ---------------------------------------------------

    def test_a_separator_moves_only_its_two_columns(self):
        before = self.sizes()
        self.drag(1, 10)  # the Branch | Status separator
        self.assertEqual(self.sizes(), [before[0], before[1] + 10, before[2] - 10])
        self.assertEqual(self.edge(1), before[0] + before[1] + 10 - 1)  # follows the pointer
        self.assertTrue(self.header.is_user_sized(1) and self.header.is_user_sized(2))
        self.assertEqual(self.prefs.widths("stash"),
                         {"Branch": before[1] + 10, "Status": before[2] - 10})

    def test_the_main_columns_separator_moves_the_main_column_and_its_neighbour(self):
        before = self.sizes()
        self.drag(0, -30)
        self.assertEqual(self.sizes(), [before[0] - 30, before[1] + 30, before[2]])
        self.assertFalse(self.header.is_user_sized(0))  # it takes the rest: nothing saved
        self.assertEqual(self.prefs.widths("stash"), {"Branch": before[1] + 30})

    def test_a_dragged_width_stays_through_fitting(self):
        self.drag(1, 10)
        dragged = self.sizes()
        self.header.fit(1)
        self.header.set_fitted(2, 90)
        self.assertEqual(self.sizes(), dragged)
        self.assert_full_width()

    def test_a_separator_stops_at_either_columns_minimum(self):
        before = self.sizes()
        minimum = self.header.minimumSectionSize()
        widest = max(self.header.fontMetrics().horizontalAdvance(short_label(l)) for l in LABELS)
        self.assertEqual(minimum, widest + 2 * PADDING_X)
        self.drag(1, 500)
        self.assertEqual(self.header.sectionSize(2), minimum)
        self.assertEqual(self.header.sectionSize(1), before[1] + before[2] - minimum)
        self.assertEqual(self.header.shown_label(2), "S..")
        self.drag(0, -500)
        self.assertEqual(self.header.sectionSize(0), minimum)
        self.assert_full_width()

    def test_the_outer_edges_are_pinned(self):
        before = self.sizes()
        self.drag(2, -20)  # the last column's right edge
        self.assertEqual(self.sizes(), before)
        self.assertEqual(self.header.handle_at(self.edge(2) + 1), -1)
        self.assertEqual(self.header.handle_at(0), -1)
        self.assertEqual(self.prefs.widths("stash"), {})

    def test_the_split_cursor_shows_on_separators_only(self):
        self.move_to(self.edge(1) + 1)
        self.assertEqual(self.header.cursor().shape(), Qt.CursorShape.SplitHCursor)
        self.move_to(self.edge(2))
        self.assertNotEqual(self.header.cursor().shape(), Qt.CursorShape.SplitHCursor)
        self.move_to(self.edge(0) - 30)
        self.assertNotEqual(self.header.cursor().shape(), Qt.CursorShape.SplitHCursor)

    # ---- getting back ------------------------------------------------------------

    def test_double_clicking_a_separator_fits_its_column_and_the_neighbour_takes_the_rest(self):
        self.drag(1, 10)
        double_click(self.header.viewport(), self.edge(1))
        self.assertEqual(self.sizes(), self.fitted)
        self.assertFalse(self.header.is_user_sized(1))
        self.assertNotIn("Branch", self.prefs.widths("stash"))

    def test_double_clicking_the_main_columns_separator_fits_the_column_after_it(self):
        self.drag(0, -30)
        double_click(self.header.viewport(), self.edge(0))
        self.assertEqual(self.sizes(), self.fitted)
        self.assertEqual(self.prefs.widths("stash"), {})

    def test_reset_column_widths_fits_every_column(self):
        self.drag(1, 10)
        self.drag(0, -20)
        reset = [a for a in self.header.columns_menu().actions() if a.text() == RESET_WIDTHS]
        self.assertEqual(len(reset), 1)
        reset[0].trigger()
        self.assertEqual(self.sizes(), self.fitted)
        self.assertEqual(self.prefs.widths("stash"), {})

    # ---- between runs ---------------------------------------------------------

    def test_a_new_header_gives_the_saved_widths_back(self):
        self.prefs.set_width("stash", "Branch", 90)
        self.prefs.set_width("stash", "Message", 500)  # the main column takes the rest anyway
        self.view, self.header = self.make_view()
        self.assertEqual(self.header.sectionSize(1), 90)
        self.assertTrue(self.header.is_user_sized(1))
        self.assertFalse(self.header.is_user_sized(0))
        self.assertFalse(self.header.is_user_sized(2))
        self.assert_full_width()

if __name__ == "__main__":
    unittest.main()
