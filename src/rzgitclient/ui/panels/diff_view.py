"""Read-only diff text with a gutter of line numbers and +/− marks (spec §5.4).

The text goes in with one ``setPlainText``: QPlainTextEdit lays out lazily,
so a long diff costs little until it's scrolled to. Row backgrounds are
painted for the visible rows only, under the text, from each row's kind.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from enum import Enum

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QResizeEvent, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QFrame, QPlainTextEdit, QWidget

from rzgitclient.ui.panels.diff_rows import DiffRow, RowKind
from rzgitclient.ui.theme.app_theme import AppTheme
from rzgitclient.ui.theme.style_roles import Role, set_role

_GUTTER_PADDING = 6  # px either side of a number column
_TAB_WIDTH = 4  # in spaces
# Painted in the gutter, not part of the text, so copied text is code only.
MARKS = {RowKind.ADDED: "+", RowKind.REMOVED: "\u2212"}  # − is as wide as + in most fonts


class Numbers(Enum):
    """Which line numbers the gutter shows."""

    BOTH = ("old", "new")  # unified
    OLD = ("old",)  # the left side
    NEW = ("new",)  # the right side


class DiffView(QPlainTextEdit):
    def __init__(self, numbers: Numbers, parent: QWidget | None = None):
        super().__init__(parent)
        self._numbers = numbers.value
        self._rows: tuple[DiffRow, ...] = ()
        self._backgrounds: dict[RowKind, QColor] = {}
        self._text_colors: dict[RowKind, QColor] = {}
        self._gutter_text = QColor()
        self._mark_colors: dict[RowKind, QColor] = {}
        self._base = QColor()
        self._digits = 1
        set_role(self, Role.DIFF_VIEW)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                     | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self._gutter = _Gutter(self)
        self.updateRequest.connect(self._scroll_gutter)

    @property
    def rows(self) -> tuple[DiffRow, ...]:
        return self._rows

    def set_rows(self, rows: Sequence[DiffRow], keep_scroll: bool = False) -> None:
        """Show ``rows``; with ``keep_scroll``, stay where the view was scrolled to."""
        v, h = self.verticalScrollBar().value(), self.horizontalScrollBar().value()
        self._rows = tuple(rows)
        self.setPlainText("\n".join(r.text for r in self._rows))
        numbers = [n for r in self._rows for n in (r.old_no, r.new_no) if n is not None]
        self._digits = len(str(max(numbers, default=1)))
        self._color_text()
        self._update_margins()
        if keep_scroll:
            self.verticalScrollBar().setValue(v)
            self.horizontalScrollBar().setValue(h)

    def apply_theme(self, theme: AppTheme) -> None:
        c = theme.colors
        self._backgrounds = {
            RowKind.HUNK: QColor(c.diff_hunk_bg),
            RowKind.ADDED: QColor(c.diff_added_bg),
            RowKind.REMOVED: QColor(c.diff_removed_bg),
            RowKind.FILLER: QColor(c.diff_filler_bg),
        }
        self._text_colors = {RowKind.HUNK: QColor(c.diff_hunk_text),
                             RowKind.NOTE: QColor(c.text_muted)}
        self._gutter_text = QColor(c.diff_gutter_text)
        self._mark_colors = {RowKind.ADDED: QColor(theme.status.added),
                             RowKind.REMOVED: QColor(theme.status.deleted)}
        self._base = QColor(c.content_bg)
        font = theme.mono_font()
        self.setFont(font)
        self._gutter.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * _TAB_WIDTH)
        self._color_text()
        self._update_margins()
        self.viewport().update()

    # ---- painting --------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        # QPlainTextEdit doesn't fill behind its text, so these show through.
        painter = QPainter(self.viewport())
        width = self.viewport().width()
        for row, top, height in self._visible_rows(event.rect()):
            color = self._backgrounds.get(row.kind)
            if color is not None:
                painter.fillRect(QRectF(0, top, width, height), color)
        painter.end()
        super().paintEvent(event)

    def _paint_gutter(self, event: QPaintEvent) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), self._base)
        column = self._column_width()
        marks_left = column * len(self._numbers)
        align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        centre = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        for row, top, height in self._visible_rows(event.rect()):
            color = self._backgrounds.get(row.kind)
            if color is not None:
                painter.fillRect(QRectF(0, top, self._gutter.width(), height), color)
            painter.setPen(self._gutter_text)
            for i, side in enumerate(self._numbers):
                number = row.old_no if side == "old" else row.new_no
                if number is not None:
                    rect = QRect(i * column, int(top), column - _GUTTER_PADDING, int(height))
                    painter.drawText(rect, align, str(number))
            mark = MARKS.get(row.kind)
            if mark is not None:
                painter.setPen(self._mark_colors[row.kind])
                rect = QRect(marks_left, int(top), self._mark_width(), int(height))
                painter.drawText(rect, centre, mark)
        painter.end()

    def _visible_rows(self, rect: QRect) -> Iterator[tuple[DiffRow, float, float]]:
        """``(row, top, height)`` from the first visible row down to ``rect``'s bottom."""
        block = self.firstVisibleBlock()
        offset = self.contentOffset()
        while block.isValid() and block.blockNumber() < len(self._rows):
            geometry = self.blockBoundingGeometry(block).translated(offset)
            if geometry.top() > rect.bottom():
                break
            yield self._rows[block.blockNumber()], geometry.top(), geometry.height()
            block = block.next()

    # ---- the gutter ------------------------------------------------------

    def _column_width(self) -> int:
        return self.fontMetrics().horizontalAdvance("9") * self._digits + 2 * _GUTTER_PADDING

    def _mark_width(self) -> int:
        metrics = self.fontMetrics()
        return max(metrics.horizontalAdvance(m) for m in MARKS.values())

    def gutter_width(self) -> int:
        """The number columns, then the mark column and a gap before the text."""
        return (self._column_width() * len(self._numbers) + self._mark_width()
                + _GUTTER_PADDING)

    def _update_margins(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        self._place_gutter()
        self._gutter.update()

    def _place_gutter(self) -> None:
        r = self.contentsRect()
        self._gutter.setGeometry(QRect(r.left(), r.top(), self.gutter_width(), r.height()))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._place_gutter()

    def _scroll_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())

    def _color_text(self) -> None:
        """Colour the text of hunk headers and notes (there are few of them)."""
        if not self._rows:
            return
        doc = self.document()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        for n, row in enumerate(self._rows):
            color = self._text_colors.get(row.kind)
            if color is None:
                continue
            block = doc.findBlockByNumber(n)
            cursor.setPosition(block.position())
            cursor.setPosition(block.position() + block.length() - 1,
                               QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            cursor.mergeCharFormat(fmt)
        cursor.endEditBlock()


class _Gutter(QWidget):
    """The line numbers and marks beside a DiffView (as in Qt's code-editor example)."""

    def __init__(self, view: DiffView):
        super().__init__(view)
        self._view = view

    def sizeHint(self) -> QSize:
        return QSize(self._view.gutter_width(), 0)

    def paintEvent(self, event: QPaintEvent) -> None:
        self._view._paint_gutter(event)
