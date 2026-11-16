import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from rzgitclient.ui.panels.diff_rows import NO_NEWLINE, DiffRow, RowKind
from rzgitclient.ui.panels.diff_gutter import MARKS
from rzgitclient.ui.panels.diff_view import DiffView, Numbers
from rzgitclient.ui.theme.app_theme import DARK_THEME, LIGHT_THEME
from tests.support import qt_app

UNIFIED = (
    DiffRow(RowKind.HUNK, "@@ -1,3 +1,3 @@"),
    DiffRow(RowKind.CONTEXT, "same", 1, 1),
    DiffRow(RowKind.REMOVED, "old", 2, None),
    DiffRow(RowKind.ADDED, "new", None, 2),
    DiffRow(RowKind.FILLER),
    DiffRow(RowKind.NOTE, NO_NEWLINE),
)


def near(a: QColor, b: QColor, tolerance: int = 40) -> bool:
    return all(abs(x - y) <= tolerance for x, y in
               ((a.red(), b.red()), (a.green(), b.green()), (a.blue(), b.blue())))


class DiffViewMarkTests(unittest.TestCase):
    """+ and − in the gutter (RZG-34, spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def make_view(self, numbers=Numbers.BOTH, rows=UNIFIED, theme=LIGHT_THEME):
        view = DiffView(numbers)
        self.addCleanup(view.deleteLater)
        view.apply_theme(theme)
        view.resize(400, 200)
        view.set_rows(rows)
        view.show()
        QApplication.processEvents()
        return view

    def painted_marks(self, view, theme=LIGHT_THEME):
        """Per row: "+" or "−" if the mark column holds that mark's colour, else ""."""
        gutter = view.gutter.grab().toImage()
        left = view.gutter.marks_left()
        width = view.gutter.mark_width()
        colours = {MARKS[RowKind.ADDED]: QColor(theme.status.added),
                   MARKS[RowKind.REMOVED]: QColor(theme.status.deleted)}
        found = []
        for _n, row, top, height in view.visible_rows(view.viewport().rect()):
            seen = ""
            for mark, colour in colours.items():
                if any(near(gutter.pixelColor(x, y), colour)
                       for x in range(left, left + width)
                       for y in range(int(top), int(top + height))):
                    seen += mark
            found.append(seen)
        return found

    def test_added_and_removed_rows_are_marked(self):
        view = self.make_view()
        self.assertEqual(self.painted_marks(view), ["", "", "−", "+", "", ""])

    def test_side_by_side_each_side_has_its_own_mark(self):
        left = self.make_view(Numbers.OLD, (UNIFIED[0], UNIFIED[2], DiffRow(RowKind.FILLER)))
        right = self.make_view(Numbers.NEW, (UNIFIED[0], DiffRow(RowKind.FILLER), UNIFIED[3]))
        self.assertEqual(self.painted_marks(left), ["", "−", ""])
        self.assertEqual(self.painted_marks(right), ["", "", "+"])

    def test_the_marks_follow_the_theme(self):
        view = self.make_view()
        view.apply_theme(DARK_THEME)
        QApplication.processEvents()
        self.assertEqual(self.painted_marks(view, DARK_THEME), ["", "", "−", "+", "", ""])

    def test_copied_lines_have_no_marks(self):
        view = self.make_view(rows=LINKED)
        QTest.keyClick(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(QApplication.clipboard().text(), "old\nnew\nmore")

    def test_the_gutter_makes_room_for_the_marks(self):
        view = self.make_view()
        numbers = view.gutter.column_width() * 2
        self.assertGreaterEqual(view.gutter_width(), numbers + view.gutter.mark_width())
        self.assertEqual(view.viewportMargins().left(), view.gutter_width())



LINKED = (
    DiffRow(RowKind.HUNK, "@@ -1,2 +1,2 @@", hunk=0),
    DiffRow(RowKind.CONTEXT, "same", 1, 1, hunk=0, line=0),
    DiffRow(RowKind.REMOVED, "old", 2, None, hunk=0, line=1),
    DiffRow(RowKind.ADDED, "new", None, 2, hunk=0, line=2),
    DiffRow(RowKind.HUNK, "@@ -9,1 +9,1 @@", hunk=1),
    DiffRow(RowKind.ADDED, "more", None, 9, hunk=1, line=0),
)


class DiffViewLinkTests(unittest.TestCase):
    """Hunk links and the lines a selection covers (RZG-23, spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.view = DiffView(Numbers.BOTH)
        self.addCleanup(self.view.deleteLater)
        self.view.apply_theme(LIGHT_THEME)
        self.view.resize(500, 200)
        self.view.set_rows(LINKED)
        self.view.show()
        QApplication.processEvents()

    def rows(self):
        return [(row, top, height)
                for _n, row, top, height in self.view.visible_rows(self.view.viewport().rect())]

    def link_centre(self, index):
        _row, top, height = self.rows()[index]
        return self.view._link_rect(top, height).center()

    def test_the_link_is_painted_on_hunk_headers_only(self):
        self.view.set_hunk_link("Stage Hunk")
        QApplication.processEvents()
        image = self.view.viewport().grab().toImage()
        accent = QColor(LIGHT_THEME.colors.accent)
        for index, (row, top, height) in enumerate(self.rows()):
            rect = self.view._link_rect(top, height)
            painted = any(near(image.pixelColor(x, y), accent, 30)
                          for x in range(rect.left(), rect.right())
                          for y in range(rect.top(), rect.bottom()))
            self.assertEqual(painted, row.kind is RowKind.HUNK, index)

    def test_clicking_a_link_sends_its_hunk(self):
        self.view.set_hunk_link("Stage Hunk")
        clicked = []
        self.view.hunk_clicked.connect(clicked.append)
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, pos=self.link_centre(4))
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, pos=self.link_centre(0))
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, pos=self.link_centre(2))
        self.assertEqual(clicked, [1, 0])  # a line's row has no link

    def test_no_link_text_no_link(self):
        self.view.set_hunk_link("", "Renamed")
        self.assertIsNone(self.view.link_at(self.link_centre(0)))

    def test_the_selection_gives_its_changed_lines(self):
        self.view.selection.set_rows(range(1, 6))  # the context line and header aren't taken
        self.assertEqual(self.view.selected_lines(), {(0, 1), (0, 2), (1, 0)})
        self.view.selection.clear()
        self.assertEqual(self.view.selected_lines(), frozenset())

    def test_row_at(self):
        _row, top, height = self.rows()[3]
        self.assertIs(self.view.row_at(QPoint(20, int(top + height / 2))), LINKED[3])


if __name__ == "__main__":
    unittest.main()
