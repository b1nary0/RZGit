import unittest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from rzgit.ui.panels.diff_rows import DiffRow, RowKind
from rzgit.ui.panels.diff_view import DiffView, Numbers
from rzgit.ui.theme.app_theme import DARK_THEME, LIGHT_THEME
from tests.support import qt_app
from tests.test_diff_view import LINKED, near

NONE = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton


def long_rows(count=200):
    rows = [DiffRow(RowKind.HUNK, f"@@ -1,{count} +1,{count} @@", hunk=0)]
    rows += [DiffRow(RowKind.ADDED, f"line {i}", None, i + 1, hunk=0, line=i)
             for i in range(count)]
    return tuple(rows)


class LineSelectionTests(unittest.TestCase):
    """Selecting whole changed lines in the gutter (RZG-23 decision 7, RZG-73,
    spec §5.4 "Line selection")."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.view = self.make_view(LINKED)

    def make_view(self, rows, theme=LIGHT_THEME):
        view = DiffView(Numbers.BOTH)
        self.addCleanup(view.deleteLater)
        view.apply_theme(theme)
        view.resize(500, 200)
        view.set_rows(rows)
        view.set_line_buttons("Stage")
        view.show()
        QApplication.processEvents()
        return view

    def y(self, index, view=None):
        view = view or self.view
        _n, _row, top, height = next(r for r in view.visible_rows(view.viewport().rect())
                                     if r[0] == index)
        return int(top + height / 2)

    def send(self, widget, kind, y, modifiers=NONE, x=60):
        pos = QPointF(x, y)
        buttons = Qt.MouseButton.NoButton if kind is QEvent.Type.MouseButtonRelease else LEFT
        event = QMouseEvent(kind, pos, QPointF(widget.mapToGlobal(pos.toPoint())), LEFT,
                            buttons, modifiers)
        QApplication.sendEvent(widget, event)
        QApplication.processEvents()

    def click(self, index, modifiers=NONE, widget=None):
        widget = widget or self.view.gutter
        y = self.y(index)
        x = widget.width() - 4 if widget is self.view.gutter else 60
        self.send(widget, QEvent.Type.MouseButtonPress, y, modifiers, x=x)
        self.send(widget, QEvent.Type.MouseButtonRelease, y, modifiers, x=x)

    def selected(self, view=None):
        return sorted((view or self.view).selection.rows)

    # ---- choosing --------------------------------------------------------------

    def test_a_click_selects_one_changed_line(self):
        self.click(2)
        self.assertEqual(self.selected(), [2])
        self.click(5)
        self.assertEqual(self.selected(), [5])
        self.click(1)  # a context line: clears
        self.assertEqual(self.selected(), [])
        self.click(3)
        self.click(0)  # a hunk header: clears
        self.assertEqual(self.selected(), [])

    def test_no_text_is_selected(self):
        self.click(2)
        self.assertFalse(self.view.textCursor().hasSelection())

    def test_a_click_in_the_text_selects_no_lines(self):
        self.click(2, widget=self.view.viewport())
        self.assertEqual(self.selected(), [])

    def test_dragging_selects_the_changed_lines_between(self):
        viewport = self.view.gutter
        self.send(viewport, QEvent.Type.MouseButtonPress, self.y(1))  # from a context line
        self.send(viewport, QEvent.Type.MouseMove, self.y(3))
        self.assertEqual(self.selected(), [2, 3])
        self.send(viewport, QEvent.Type.MouseMove, self.y(5))
        self.assertEqual(self.selected(), [2, 3, 5])  # not the hunk header
        self.send(viewport, QEvent.Type.MouseMove, self.y(2))  # back up
        self.assertEqual(self.selected(), [2])
        self.send(viewport, QEvent.Type.MouseButtonRelease, self.y(2))
        self.send(viewport, QEvent.Type.MouseMove, self.y(5))  # released: no change
        self.assertEqual(self.selected(), [2])

    def test_shift_and_ctrl(self):
        self.click(2)
        self.click(5, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.selected(), [2, 3, 5])
        self.click(3, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.selected(), [2, 5])
        self.click(3, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.selected(), [2, 3, 5])
        self.click(1, Qt.KeyboardModifier.ControlModifier)  # a context line: nothing
        self.assertEqual(self.selected(), [2, 3, 5])

    def test_dragging_past_the_bottom_scrolls(self):
        view = self.make_view(long_rows())
        bar = view.verticalScrollBar()
        self.send(view.gutter, QEvent.Type.MouseButtonPress, self.y(2, view))
        self.send(view.gutter, QEvent.Type.MouseMove, view.viewport().height() + 20)
        self.assertGreater(bar.value(), 0)
        QTest.qWait(200)  # the pointer stays there: it keeps scrolling
        self.assertGreater(bar.value(), 1)
        self.assertGreater(max(view.selection.rows), 10)
        self.send(view.gutter, QEvent.Type.MouseButtonRelease, 0)
        value = bar.value()
        QTest.qWait(150)
        self.assertEqual(bar.value(), value)

    def test_keys(self):
        self.click(2)
        self.click(5, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyClick(self.view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(QApplication.clipboard().text(), "old\nnew\nmore")
        QTest.keyClick(self.view, Qt.Key.Key_Escape)
        self.assertEqual(self.selected(), [])

    def test_the_same_rows_again_keep_it_and_others_clear_it(self):
        self.view.selection.set_rows({2, 3})
        self.view.set_rows(LINKED, keep_scroll=True)  # a refresh with no change
        self.assertEqual(self.selected(), [2, 3])
        self.view.set_rows(LINKED)  # another file, or the view switched
        self.assertEqual(self.selected(), [])
        self.view.selection.set_rows({2, 3})
        self.view.set_rows(LINKED[:4], keep_scroll=True)  # the diff changed
        self.assertEqual(self.selected(), [])

    # ---- how it looks ------------------------------------------------------------

    def test_selected_lines_are_shaded_and_outlined(self):
        for theme in (LIGHT_THEME, DARK_THEME):
            view = self.make_view(LINKED, theme)
            view.selection.set_rows({2, 3})
            QApplication.processEvents()
            image = view.viewport().grab().toImage()
            gutter = view.gutter.grab().toImage()
            for index, kind, shade, line in (
                    (2, "removed", theme.colors.diff_removed_selected_bg, theme.status.deleted),
                    (3, "added", theme.colors.diff_added_selected_bg, theme.status.added)):
                y = self.y(index, view)
                for x, colour in ((0, line), (image.width() - 1, line),
                                  (image.width() // 2, shade)):
                    self.assertTrue(near(image.pixelColor(x, y), QColor(colour), 8),
                                    (theme.name, kind, x))
                self.assertTrue(near(gutter.pixelColor(gutter.width() - 2, y), QColor(shade), 8),
                                (theme.name, kind, "gutter"))
            y = self.y(5, view)  # not selected: its own background, no outline
            self.assertTrue(near(image.pixelColor(0, y), QColor(theme.colors.diff_added_bg), 8))

    def test_a_run_of_one_kind_shares_one_outline(self):
        view = self.make_view(long_rows(5))
        view.selection.set_rows({1, 2, 3})
        QApplication.processEvents()
        image = view.viewport().grab().toImage()
        outline = QColor(LIGHT_THEME.status.added)
        rows = {n: (int(top), int(top + height) - 1)
                for n, _r, top, height in view.visible_rows(view.viewport().rect())}
        x = image.width() // 2
        self.assertTrue(near(image.pixelColor(x, rows[1][0]), outline, 8))  # the top
        self.assertTrue(near(image.pixelColor(x, rows[3][1]), outline, 8))  # the bottom
        for inside in (rows[1][1], rows[2][0], rows[2][1], rows[3][0]):  # none between
            self.assertFalse(near(image.pixelColor(x, inside), outline, 8), inside)

    # ---- the buttons ---------------------------------------------------------------

    def test_selected_lines_keep_their_buttons(self):
        green = QColor(LIGHT_THEME.colors.diff_line_button)
        self.view.selection.set_rows({2, 5})
        QApplication.processEvents()
        image = self.view.gutter.grab().toImage()
        for n, _row, top, height in self.view.visible_rows(self.view.gutter.rect()):
            rect = self.view.gutter.button_rect(top, height)
            shown = near(image.pixelColor(rect.left() + 2, rect.top() + 2), green, 6)
            self.assertEqual(shown, n in (2, 5), n)

    def test_a_selected_lines_button_acts_on_them_all(self):
        clicked = []
        self.view.lines_clicked.connect(clicked.append)
        self.view.selection.set_rows({2, 5})
        QApplication.processEvents()
        gutter = self.view.gutter
        for index in (5, 3):  # selected; then a hovered line that isn't
            _n, _row, top, height = next(r for r in self.view.visible_rows(gutter.rect())
                                         if r[0] == index)
            centre = gutter.button_rect(top, height).center()
            self.send(gutter, QEvent.Type.MouseMove, centre.y(), x=centre.x())
            QTest.mouseClick(gutter, LEFT, pos=centre)
        self.assertEqual(clicked, [{(0, 1), (1, 0)}, {(0, 2)}])
        self.assertEqual(self.selected(), [2, 5])  # a button click doesn't change it


if __name__ == "__main__":
    unittest.main()
