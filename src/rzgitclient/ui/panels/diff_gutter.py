"""The gutter beside a DiffView: line buttons, line numbers and +/− marks (spec §5.4).

Drawn like Qt's code-editor example: a widget in the view's left margin that
paints the visible rows only. From the left: a button on the changed line under
the pointer and on selected lines (when the panel turns them on), the number
columns, then the marks. Clicks elsewhere select lines, through the view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from rzgitclient.ui.panels.diff_rows import DiffRow, RowKind
from rzgitclient.ui.theme.app_theme import AppTheme

if TYPE_CHECKING:
    from rzgitclient.ui.panels.diff_view import DiffView

PADDING = 6  # px either side of a number column
_BUTTON_MARGIN = 4  # px either side of a line button
_BUTTON_PADDING = 5  # px between a line button's edge and its label
# Painted here, not part of the text, so copied text is code only.
MARKS = {RowKind.ADDED: "+", RowKind.REMOVED: "−"}  # − is as wide as + in most fonts


class DiffGutter(QWidget):
    button_clicked = Signal(int)  # the row whose line button was clicked

    def __init__(self, view: DiffView, numbers: tuple[str, ...]):
        super().__init__(view)
        self._view = view
        self._numbers = numbers  # "old", "new" or both
        self._digits = 1
        self._label = ""  # the line buttons' text, "Stage" or "Unstage"; "" for none
        self._hover: int | None = None  # the row under the pointer, in the text or here
        self._on_button = False  # the pointer is on that row's button
        self._text = QColor()
        self._button = QColor()
        self._button_hover = QColor()
        self._button_border = QColor()
        self._button_text = QColor()
        self._base = QColor()
        self._mark_colors: dict[RowKind, QColor] = {}
        self.setMouseTracking(True)

    # ---- set by the view -------------------------------------------------------

    def apply_theme(self, theme: AppTheme) -> None:
        c = theme.colors
        self._text = QColor(c.diff_gutter_text)
        self._button = QColor(c.diff_line_button)
        self._button_hover = QColor(c.diff_line_button_hover)
        self._button_border = QColor(c.diff_line_button_border)
        self._button_text = QColor(c.diff_line_button_text)
        self._base = QColor(c.content_bg)
        self._mark_colors = {RowKind.ADDED: QColor(theme.status.added),
                             RowKind.REMOVED: QColor(theme.status.deleted)}
        self.setFont(theme.mono_font())

    def set_digits(self, digits: int) -> None:
        self._digits = digits

    def set_buttons(self, label: str) -> None:
        """A button saying ``label`` on the changed line under the pointer; "" for none."""
        self._label = label
        self.set_hover(None)

    def set_hover(self, row: int | None, on_button: bool = False) -> None:
        """The row under the pointer (None: none): only its button shows."""
        if (row, on_button) != (self._hover, self._on_button):
            self._hover, self._on_button = row, on_button
            self.setCursor(Qt.CursorShape.PointingHandCursor if on_button
                           else Qt.CursorShape.ArrowCursor)
            self.update()

    @property
    def hover(self) -> int | None:
        return self._hover

    @property
    def buttons(self) -> bool:
        return bool(self._label)

    @property
    def label(self) -> str:
        return self._label

    # ---- geometry ----------------------------------------------------------------

    def button_width(self) -> int:
        """The column the buttons take, kept even when none shows, so nothing moves."""
        return self._button_size().width() + 2 * _BUTTON_MARGIN if self._label else 0

    def _button_size(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self._label) + 2 * _BUTTON_PADDING,
                     metrics.height())

    def column_width(self) -> int:
        return self.fontMetrics().horizontalAdvance("9") * self._digits + 2 * PADDING

    def marks_left(self) -> int:
        return self.button_width() + self.column_width() * len(self._numbers)

    def mark_width(self) -> int:
        return max(self.fontMetrics().horizontalAdvance(m) for m in MARKS.values())

    def width_needed(self) -> int:
        """The buttons, the number columns, then the mark column and a gap before the text."""
        return self.marks_left() + self.mark_width() + PADDING

    def sizeHint(self) -> QSize:
        return QSize(self.width_needed(), 0)

    def button_rect(self, top: float, height: float) -> QRect:
        size = self._button_size()
        h = min(size.height(), int(height))
        return QRect(_BUTTON_MARGIN, int(top + (height - h) / 2), size.width(), h)

    def button_at(self, pos: QPoint) -> tuple[int, DiffRow] | None:
        """``(row number, row)`` of the line button at ``pos``, if there is one (it
        shows once the pointer is there)."""
        if not self._label:
            return None
        for n, row, top, height in self._view.visible_rows(self.rect()):
            if _has_button(row) and self.button_rect(top, height).contains(pos):
                return n, row
        return None

    # ---- painting --------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(event.rect(), self._base)
        column = self.column_width()
        numbers_left = self.button_width()
        marks_left = self.marks_left()
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        centre = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        for n, row, top, height in self._view.visible_rows(event.rect()):
            background = self._view.background(n, row)
            if background is not None:
                painter.fillRect(QRectF(0, top, self.width(), height), background)
            painter.setPen(self._text)
            for i, side in enumerate(self._numbers):
                number = row.old_no if side == "old" else row.new_no
                if number is not None:
                    rect = QRect(numbers_left + i * column, int(top), column - PADDING,
                                 int(height))
                    painter.drawText(rect, right, str(number))
            mark = MARKS.get(row.kind)
            if mark is not None:
                painter.setPen(self._mark_colors[row.kind])
                painter.drawText(QRect(marks_left, int(top), self.mark_width(), int(height)),
                                 centre, mark)
            shown = n == self._hover or n in self._view.selection
            if self._label and shown and _has_button(row):
                self._paint_button(painter, self.button_rect(top, height), self._on_button)
        painter.end()

    def _paint_button(self, painter: QPainter, rect: QRect, hover: bool) -> None:
        """Square, green, with a thin outline; another green when the pointer is on it."""
        painter.save()
        painter.fillRect(rect, self._button_hover if hover else self._button)
        painter.setPen(QPen(self._button_border, 1))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))  # inside the rect
        painter.setPen(self._button_text)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._label)
        painter.restore()

    # ---- the pointer -----------------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._view.selection.drag_to(pos.y())
        self.set_hover(self._view.row_number_at(pos.y()), self.button_at(pos) is not None)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.set_hover(None)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() is not Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        hit = self.button_at(event.position().toPoint())
        if hit is not None:
            self.button_clicked.emit(hit[0])
        else:
            self._view.selection.press_at(int(event.position().y()), event.modifiers())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._view.selection.release()
        super().mouseReleaseEvent(event)


def _has_button(row: DiffRow) -> bool:
    return row.kind in MARKS and row.hunk is not None and row.line is not None
