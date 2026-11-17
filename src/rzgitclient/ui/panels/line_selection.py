"""Which changed lines of a DiffView are selected (spec §5.4, "Line selection").

The gutter selects whole changed lines, for staging (the text has a text
selection of its own, RZG-73). The gutter passes clicks and drags here by row;
``changed`` says when to repaint. Only changed lines with a place in the diff
are ever selected.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter

from rzgitclient.ui.panels.diff_rows import RowKind

if TYPE_CHECKING:
    from rzgitclient.ui.panels.diff_view import DiffView

_CHANGED = (RowKind.ADDED, RowKind.REMOVED)


class LineSelection(QObject):
    changed = Signal()

    def __init__(self, view: DiffView):
        super().__init__(view)
        self._view = view
        self._rows: frozenset[int] = frozenset()
        self._anchor: int | None = None  # the row of the last plain or Ctrl click
        self._drag_y: int | None = None  # the pointer while a click drags a selection
        self._autoscroll = QTimer(self, interval=50)  # dragging past the top or bottom
        self._autoscroll.timeout.connect(lambda: self.drag_to(self._drag_y))

    # ---- what is selected --------------------------------------------------------

    @property
    def rows(self) -> frozenset[int]:
        return self._rows

    def __contains__(self, row: object) -> bool:
        return row in self._rows

    def __bool__(self) -> bool:
        return bool(self._rows)

    def selectable(self, n: int) -> bool:
        rows = self._view.rows
        if not 0 <= n < len(rows):
            return False
        row = rows[n]
        return row.kind in _CHANGED and row.hunk is not None and row.line is not None

    def line(self, n: int) -> tuple[int, int]:
        """``(hunk, line)`` of row ``n``."""
        row = self._view.rows[n]
        return row.hunk, row.line  # type: ignore[return-value]

    def lines(self) -> frozenset[tuple[int, int]]:
        return frozenset(self.line(n) for n in self._rows)

    def text(self) -> str:
        rows = self._view.rows
        return "\n".join(rows[n].text for n in sorted(self._rows))

    def copy(self) -> None:
        if self._rows:
            QGuiApplication.clipboard().setText(self.text())

    # ---- changing it, by row -----------------------------------------------------

    def press(self, row: int | None, *, shift: bool = False, ctrl: bool = False) -> None:
        """A click on ``row`` (None: below the last row)."""
        if row is None:
            self.clear()
        elif shift and self._anchor is not None:
            self.set_rows(self._range(self._anchor, row))
        else:
            self._anchor = row
            if not ctrl:
                self.set_rows((row,))
            elif self.selectable(row):
                self.set_rows(self._rows ^ {row})

    def drag(self, row: int) -> None:
        """The pointer, still pressed, is over ``row``: the lines from the click to it."""
        if self._anchor is not None:
            self.set_rows(self._range(self._anchor, row))

    def clear(self) -> None:
        self._anchor = None
        self.set_rows(())

    def set_rows(self, rows: Iterable[int]) -> None:
        new = frozenset(r for r in rows if self.selectable(r))
        if new != self._rows:
            self._rows = new
            self.changed.emit()

    @staticmethod
    def _range(a: int, b: int) -> range:
        return range(min(a, b), max(a, b) + 1)

    # ---- from the pointer (gutter coordinates, which are the viewport's) -----------

    def press_at(self, y: int, modifiers: Qt.KeyboardModifier) -> None:
        view = self._view
        view.setFocus(Qt.FocusReason.MouseFocusReason)
        view.clear_text_selection()  # one selection at a time
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        self.press(view.row_number_at(y), shift=shift, ctrl=ctrl)
        self._drag_y = None if shift or ctrl else y

    def drag_to(self, y: int | None) -> None:
        """The pointer, still pressed, is at height ``y``; past an edge, scroll."""
        if y is None or self._drag_y is None:
            return
        self._drag_y = y
        view = self._view
        if y < 0 or y >= view.viewport().height():
            bar = view.verticalScrollBar()
            bar.setValue(bar.value() + (-1 if y < 0 else 1))
            self._autoscroll.start()
        else:
            self._autoscroll.stop()
        self.drag(view.cursorForPosition(QPoint(0, y)).blockNumber())  # the nearest row

    def release(self) -> None:
        self._drag_y = None
        self._autoscroll.stop()

    # ---- painting ----------------------------------------------------------------

    def paint_outlines(self, painter: QPainter, rect: QRect,
                       colors: dict[RowKind, QColor]) -> None:
        """A 1 px line around each run of selected lines of one kind, across the viewport."""
        rows, selected = self._view.rows, self._rows
        right = self._view.viewport().width() - 1
        for n, row, top, height in self._view.visible_rows(rect):
            if n not in selected:
                continue
            painter.setPen(colors[row.kind])
            y0, y1 = int(top), int(top + height) - 1
            painter.drawLine(0, y0, 0, y1)
            painter.drawLine(right, y0, right, y1)
            if n - 1 not in selected or rows[n - 1].kind is not row.kind:
                painter.drawLine(0, y0, right, y0)
            if n + 1 not in selected or rows[n + 1].kind is not row.kind:
                painter.drawLine(0, y1, right, y1)
