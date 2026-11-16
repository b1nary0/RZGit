import unittest

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from rzgitclient.ui.panels.diff_rows import NO_NEWLINE, DiffRow, RowKind
from rzgitclient.ui.panels.diff_view import MARKS, DiffView, Numbers
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
        gutter = view._gutter.grab().toImage()
        left = view._column_width() * len(view._numbers)
        width = view._mark_width()
        colours = {MARKS[RowKind.ADDED]: QColor(theme.status.added),
                   MARKS[RowKind.REMOVED]: QColor(theme.status.deleted)}
        found = []
        for row, top, height in view._visible_rows(view.viewport().rect()):
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

    def test_copied_text_has_no_marks(self):
        view = self.make_view()
        view.selectAll()
        view.copy()
        self.assertEqual(QApplication.clipboard().text(),
                         "\n".join(row.text for row in UNIFIED))

    def test_the_gutter_makes_room_for_the_marks(self):
        view = self.make_view()
        numbers = view._column_width() * 2
        self.assertGreaterEqual(view.gutter_width(), numbers + view._mark_width())
        self.assertEqual(view.viewportMargins().left(), view.gutter_width())


if __name__ == "__main__":
    unittest.main()
