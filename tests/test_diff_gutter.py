import unittest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from rzgitclient.ui.panels.diff_rows import DiffRow, RowKind
from rzgitclient.ui.panels.diff_view import DiffView, Numbers
from rzgitclient.ui.theme.app_theme import DARK_THEME, LIGHT_THEME
from tests.support import qt_app
from tests.test_diff_view import LINKED, near

GREEN = LIGHT_THEME.colors.diff_line_button
DARKER = LIGHT_THEME.colors.diff_line_button_hover


class DiffGutterButtonTests(unittest.TestCase):
    """A button on the changed line under the pointer (RZG-23 decision 7, spec §5.4)."""

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
        self.gutter = self.view.gutter

    def rows(self):
        return list(self.view.visible_rows(self.gutter.rect()))

    def centre(self, index):
        _n, _row, top, height = self.rows()[index]
        return self.gutter.button_rect(top, height).center()

    def text_point(self, index):
        """A point in the text of row ``index`` (viewport coordinates)."""
        _n, _row, top, height = self.rows()[index]
        return QPoint(40, int(top + height / 2))

    def move(self, widget, pos):
        """The pointer moves over ``widget`` (QTest.mouseMove sends nothing offscreen)."""
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(pos), QPointF(widget.mapToGlobal(pos)),
                            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(widget, event)
        QApplication.processEvents()

    def painted(self, colour):
        """Per row: whether its button's square holds ``colour``."""
        image = self.gutter.grab().toImage()
        found = []
        for _n, _row, top, height in self.rows():
            rect = self.gutter.button_rect(top, height)
            found.append(any(near(image.pixelColor(x, y), QColor(colour), 6)
                             for x in range(rect.left(), rect.right() + 1)
                             for y in range(rect.top(), rect.bottom() + 1)))
        return found

    def shown(self, colour=GREEN):
        return [n for n, on in enumerate(self.painted(colour)) if on]

    def test_off_by_default_and_no_room_kept(self):
        self.assertFalse(self.gutter.buttons)
        self.assertEqual(self.gutter.button_width(), 0)
        self.assertIsNone(self.gutter.button_at(self.centre(2)))

    def test_turning_them_on_makes_room_but_shows_none_yet(self):
        narrow = self.view.gutter_width()
        self.view.set_line_buttons("Stage")
        QApplication.processEvents()
        self.assertEqual(self.view.gutter_width(), narrow + self.gutter.button_width())
        self.assertEqual(self.view.viewportMargins().left(), self.view.gutter_width())
        self.assertEqual(self.shown(), [])  # nothing under the pointer

    def test_hovering_a_changed_line_in_the_text_shows_its_button(self):
        self.view.set_line_buttons("Stage")
        self.move(self.view.viewport(), self.text_point(2))
        self.assertEqual(self.shown(), [2])
        self.move(self.view.viewport(), self.text_point(1))  # a context line: no button
        self.assertEqual(self.shown(), [])
        self.move(self.view.viewport(), self.text_point(5))
        self.assertEqual(self.shown(), [5])
        QApplication.sendEvent(self.view.viewport(), QEvent(QEvent.Type.Leave))
        QApplication.processEvents()
        self.assertEqual(self.shown(), [])

    def test_the_button_under_the_pointer_lights_up(self):
        self.view.set_line_buttons("Stage")
        self.move(self.gutter, self.centre(3))
        self.assertEqual(self.gutter.cursor().shape(), Qt.CursorShape.PointingHandCursor)
        self.assertEqual(self.shown(DARKER), [3])
        self.assertEqual(self.shown(), [])  # the hover green, not the plain one
        _n, _row, top, height = self.rows()[3]
        self.move(self.gutter, QPoint(self.gutter.width() - 2, int(top + height / 2)))
        self.assertEqual(self.shown(DARKER), [])  # same row, off the button: plain green
        self.assertEqual(self.shown(), [3])
        self.assertEqual(self.gutter.cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_a_line_of_only_whitespace_has_one_too(self):
        rows = (DiffRow(RowKind.HUNK, "@@ -1,0 +1,2 @@", hunk=0),
                DiffRow(RowKind.ADDED, "    ", None, 1, hunk=0, line=0),
                DiffRow(RowKind.ADDED, "", None, 2, hunk=0, line=1))
        self.view.set_rows(rows)
        self.view.set_line_buttons("Stage")
        for index in (1, 2):
            self.move(self.view.viewport(), self.text_point(index))
            self.assertEqual(self.shown(), [index])

    def test_a_click_sends_the_line(self):
        self.view.set_line_buttons("Stage")
        clicked = []
        self.view.lines_clicked.connect(clicked.append)
        for index in (2, 1, 5, 0):  # removed, context, added (hunk 1), hunk header
            self.move(self.gutter, self.centre(index))
            QTest.mouseClick(self.gutter, Qt.MouseButton.LeftButton, pos=self.centre(index))
        self.assertEqual(clicked, [{(0, 1)}, {(1, 0)}])

    def test_the_button_says_its_label_and_fits_it(self):
        self.view.set_line_buttons("Unstage")
        self.assertEqual(self.gutter.label, "Unstage")
        _n, _row, top, height = self.rows()[2]
        rect = self.gutter.button_rect(top, height)
        self.assertGreater(rect.width(), self.gutter.fontMetrics().horizontalAdvance("Unstage"))
        self.assertLessEqual(rect.height(), int(height))
        self.view.set_line_buttons("")
        self.assertFalse(self.gutter.buttons)
        self.assertEqual(self.gutter.button_width(), 0)

    def test_square_green_and_outlined_in_both_themes(self):
        for theme in (LIGHT_THEME, DARK_THEME):
            c = theme.colors
            self.view.apply_theme(theme)
            self.view.set_line_buttons("Stage")
            self.move(self.view.viewport(), self.text_point(2))
            _n, _row, top, height = self.rows()[2]
            rect = self.gutter.button_rect(top, height)
            image = self.gutter.grab().toImage()
            for x, y in ((rect.left(), rect.top()), (rect.right(), rect.bottom())):
                self.assertTrue(near(image.pixelColor(x, y), QColor(c.diff_line_button_border), 10),
                                (theme.name, x, y))  # the corners too: square
            self.assertTrue(near(image.pixelColor(rect.left() + 2, rect.top() + 2),
                                 QColor(c.diff_line_button), 10), theme.name)

    def test_numbers_move_right_to_make_room(self):
        self.view.set_line_buttons("Stage")
        self.assertEqual(self.gutter.marks_left(),
                         self.gutter.button_width() + 2 * self.gutter.column_width())


if __name__ == "__main__":
    unittest.main()
