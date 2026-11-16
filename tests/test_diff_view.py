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


WORDS = (
    DiffRow(RowKind.HUNK, "@@ -1,3 +1,3 @@"),
    DiffRow(RowKind.REMOVED, "count = total + 1", 1, None),
    DiffRow(RowKind.REMOVED, "\tx = old", 2, None),
    DiffRow(RowKind.ADDED, "count = total + 2", None, 1),
    DiffRow(RowKind.ADDED, "\tx = new", None, 2),
    DiffRow(RowKind.CONTEXT, "same", 3, 3),
)
WORD_PARTNERS = {1: WORDS[3].text, 3: WORDS[1].text, 2: WORDS[4].text, 4: WORDS[2].text}


class DiffViewWordTests(unittest.TestCase):
    """Changed words are filled in the word colours (RZG-22, spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def make_view(self, rows=WORDS, partners=WORD_PARTNERS, theme=LIGHT_THEME):
        view = DiffView(Numbers.BOTH)
        self.addCleanup(view.deleteLater)
        view.apply_theme(theme)
        view.resize(500, 200)
        view.set_rows(rows, partners=partners)
        view.show()
        QApplication.processEvents()
        return view

    def row_geometry(self, view, n):
        for m, _row, top, height in view.visible_rows(view.viewport().rect()):
            if m == n:
                return top, height
        raise AssertionError(n)

    def test_the_rects_cover_the_changed_word_only(self):
        view = self.make_view()
        top, height = self.row_geometry(view, 1)
        [rect] = view.word_rects(1, top, height)
        cursor = view.textCursor()
        block = view.document().findBlockByNumber(1)
        cursor.setPosition(block.position() + 16)  # before "1"
        self.assertAlmostEqual(rect.left(), view.cursorRect(cursor).left(), delta=1)
        self.assertAlmostEqual(rect.width(), view.fontMetrics().horizontalAdvance("1"), delta=1)
        self.assertEqual((rect.top(), rect.height()), (top, height))

    def test_a_tab_before_the_word_is_allowed_for(self):
        view = self.make_view()
        top, height = self.row_geometry(view, 2)
        [rect] = view.word_rects(2, top, height)
        cursor = view.textCursor()
        cursor.setPosition(view.document().findBlockByNumber(2).position() + 5)  # before "old"
        self.assertAlmostEqual(rect.left(), view.cursorRect(cursor).left(), delta=1)

    def test_the_word_colours_are_painted(self):
        for theme in (LIGHT_THEME, DARK_THEME):
            view = self.make_view(theme=theme)
            image = view.viewport().grab().toImage()
            for n, token in ((1, theme.colors.diff_removed_word),
                             (3, theme.colors.diff_added_word)):
                top, height = self.row_geometry(view, n)
                [rect] = view.word_rects(n, top, height)
                inside = image.pixelColor(int(rect.left()) + 1, int(top + 1))
                outside = image.pixelColor(int(rect.left()) - 20, int(top + 1))
                self.assertTrue(near(inside, QColor(token), 10), (theme.name, n))
                self.assertFalse(near(outside, QColor(token), 10), (theme.name, n))

    def test_selected_rows_keep_their_word_colour(self):
        view = self.make_view()
        view.selection.set_rows([1])
        QApplication.processEvents()
        image = view.viewport().grab().toImage()
        top, height = self.row_geometry(view, 1)
        [rect] = view.word_rects(1, top, height)
        self.assertTrue(near(image.pixelColor(int(rect.left()) + 1, int(top + 2)),
                             QColor(LIGHT_THEME.colors.diff_removed_word), 10))

    def test_unpaired_and_unlike_rows_have_no_words(self):
        view = self.make_view(partners={1: "nothing at all alike here"})
        self.assertEqual(view.word_spans(1), ())  # too little in common
        self.assertEqual(view.word_spans(3), ())  # no partner
        self.assertEqual(view.word_spans(5), ())  # context

    def test_spans_are_worked_out_once_per_set_rows(self):
        view = self.make_view()
        self.assertEqual(view.word_spans(1), ((16, 17),))
        self.assertIn(1, view._spans)
        view.set_rows(WORDS)
        self.assertEqual(view._spans, {})
        self.assertEqual(view.word_spans(1), ())  # no partners now

    def test_characters_outside_the_bmp_count_twice_for_qt(self):
        rows = (DiffRow(RowKind.REMOVED, "😀 a = 1", 1, None),
                DiffRow(RowKind.ADDED, "😀 a = 2", None, 1))
        view = self.make_view(rows, {0: rows[1].text, 1: rows[0].text})
        top, height = self.row_geometry(view, 0)
        [rect] = view.word_rects(0, top, height)
        cursor = view.textCursor()
        cursor.setPosition(len("😀 a = ".encode("utf-16-le")) // 2)
        self.assertAlmostEqual(rect.left(), view.cursorRect(cursor).left(), delta=1)


if __name__ == "__main__":
    unittest.main()
