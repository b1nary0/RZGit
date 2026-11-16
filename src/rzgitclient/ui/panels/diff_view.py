"""Read-only diff text beside a DiffGutter (spec §5.4).

The text goes in with one ``setPlainText``: QPlainTextEdit lays out lazily,
so a long diff costs little until it's scrolled to. Row backgrounds are
painted for the visible rows only, under the text, from each row's kind.
Hunk headers can show a link at the right edge. Whole changed lines are
selected (a LineSelection), not text.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from enum import Enum

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QHelpEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import QFrame, QPlainTextEdit, QToolTip, QWidget

from rzgitclient.ui.panels.diff_gutter import DiffGutter
from rzgitclient.ui.panels.diff_rows import DiffRow, RowKind
from rzgitclient.ui.panels.line_selection import LineSelection
from rzgitclient.ui.theme.app_theme import AppTheme
from rzgitclient.ui.theme.style_roles import Role, set_role

_LINK_PADDING = 8  # px around a hunk header's link
_TAB_WIDTH = 4  # in spaces


class Numbers(Enum):
    """Which line numbers the gutter shows."""

    BOTH = ("old", "new")  # unified
    OLD = ("old",)  # the left side
    NEW = ("new",)  # the right side


class DiffView(QPlainTextEdit):
    hunk_clicked = Signal(int)  # the index of the hunk whose header link was clicked
    lines_clicked = Signal(object)  # frozenset of (hunk, line): a gutter button was clicked

    def __init__(self, numbers: Numbers, parent: QWidget | None = None):
        super().__init__(parent)
        self._link = ""  # the hunk headers' link text; "" for none
        self._hunk_tooltip = ""  # shown on hunk headers instead, saying why there's none
        self._link_color = QColor()
        self._rows: tuple[DiffRow, ...] = ()
        self._backgrounds: dict[RowKind, QColor] = {}
        self._text_colors: dict[RowKind, QColor] = {}
        self._base = QColor()
        self._selected_bg: dict[RowKind, QColor] = {}
        self._outline: dict[RowKind, QColor] = {}
        self.selection = LineSelection(self)
        self.selection.changed.connect(self._selection_changed)
        set_role(self, Role.DIFF_VIEW)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.gutter = DiffGutter(self, numbers.value)
        self.gutter.button_clicked.connect(self._on_button)
        self.updateRequest.connect(self._scroll_gutter)
        self.viewport().setMouseTracking(True)  # for the hand over a link
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    @property
    def rows(self) -> tuple[DiffRow, ...]:
        return self._rows

    def set_rows(self, rows: Sequence[DiffRow], keep_scroll: bool = False) -> None:
        """Show ``rows``; with ``keep_scroll``, stay where the view was scrolled to (and
        keep the selection, if the rows are the same)."""
        v, h = self.verticalScrollBar().value(), self.horizontalScrollBar().value()
        rows = tuple(rows)
        if not (keep_scroll and rows == self._rows):
            self.selection.clear()
        self._rows = rows
        self.setPlainText("\n".join(r.text for r in self._rows))
        numbers = [n for r in self._rows for n in (r.old_no, r.new_no) if n is not None]
        self.gutter.set_digits(len(str(max(numbers, default=1))))
        self._color_text()
        self._update_margins()
        if keep_scroll:
            self.verticalScrollBar().setValue(v)
            self.horizontalScrollBar().setValue(h)

    def set_hunk_link(self, text: str, tooltip: str = "") -> None:
        """Show ``text`` as a link on every hunk header ("" for none), or with
        none, ``tooltip`` on the headers to say why."""
        self._link, self._hunk_tooltip = text, tooltip
        self.viewport().update()

    def set_line_buttons(self, label: str) -> None:
        """A button saying ``label`` in the gutter of the changed line under the
        pointer and of selected lines; "" for none (and no room kept)."""
        if label != self.gutter.label:
            self.gutter.set_buttons(label)
            self._update_margins()

    def row_at(self, pos: QPoint) -> DiffRow | None:
        """The row at ``pos``, in the viewport's coordinates."""
        n = self.cursorForPosition(pos).blockNumber()
        return self._rows[n] if 0 <= n < len(self._rows) else None

    def selected_lines(self) -> frozenset[tuple[int, int]]:
        """``(hunk, line)`` of the selected lines."""
        return self.selection.lines()

    def _on_button(self, n: int) -> None:
        """A selected line's button acts on the whole selection; another's on its line."""
        self.lines_clicked.emit(self.selection.lines() if n in self.selection
                                else frozenset({self.selection.line(n)}))

    def _selection_changed(self) -> None:
        self.viewport().update()
        self.gutter.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.selection.key(event):
            event.accept()
        else:
            super().keyPressEvent(event)

    def row_number_at(self, y: int) -> int | None:
        """The number of the row at height ``y`` (viewport or gutter coordinates)."""
        for n, _row, top, height in self.visible_rows(QRect(0, y, 1, 1)):
            if top <= y < top + height:
                return n
        return None

    def background(self, n: int, row: DiffRow) -> QColor | None:
        """Row ``n``'s background, or None for the view's own."""
        if n in self.selection:
            return self._selected_bg.get(row.kind)
        return self._backgrounds.get(row.kind)

    def visible_rows(self, rect: QRect) -> Iterator[tuple[int, DiffRow, float, float]]:
        """``(number, row, top, height)`` from the first visible row down to
        ``rect``'s bottom, in the viewport's (and the gutter's) coordinates."""
        block = self.firstVisibleBlock()
        offset = self.contentOffset()
        while block.isValid() and block.blockNumber() < len(self._rows):
            geometry = self.blockBoundingGeometry(block).translated(offset)
            if geometry.top() > rect.bottom():
                break
            n = block.blockNumber()
            yield n, self._rows[n], geometry.top(), geometry.height()
            block = block.next()

    # ---- hunk links --------------------------------------------------------

    def link_at(self, pos: QPoint) -> int | None:
        """The hunk whose header link is at ``pos`` (viewport coordinates)."""
        if not self._link:
            return None
        for _n, row, top, height in self.visible_rows(self.viewport().rect()):
            if row.kind is RowKind.HUNK and self._link_rect(top, height).contains(pos):
                return row.hunk
        return None

    def _link_rect(self, top: float, height: float) -> QRect:
        width = self.fontMetrics().horizontalAdvance(self._link) + 2 * _LINK_PADDING
        return QRect(self.viewport().width() - width, int(top), width, int(height))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() is not Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        hunk = self.link_at(event.position().toPoint())
        if hunk is not None:
            self.hunk_clicked.emit(hunk)
        else:
            self.selection.press_at(int(event.position().y()), event.modifiers())
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        y = int(event.position().y())
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.selection.drag_to(y)
        self.gutter.set_hover(self.row_number_at(y))
        over = self.link_at(event.position().toPoint()) is not None
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if over
                                  else Qt.CursorShape.ArrowCursor)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.selection.release()
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.mousePressEvent(event)  # no word selection: the same as a click

    def viewportEvent(self, event: QEvent) -> bool:
        if event.type() is QEvent.Type.Leave:
            self.gutter.set_hover(None)
        if event.type() is QEvent.Type.ToolTip and self._hunk_tooltip and not self._link:
            help_event: QHelpEvent = event  # type: ignore[assignment]
            row = self.row_at(help_event.pos())
            if row is not None and row.kind is RowKind.HUNK:
                QToolTip.showText(help_event.globalPos(), self._hunk_tooltip, self.viewport())
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)

    # ---- theme and painting --------------------------------------------------

    def apply_theme(self, theme: AppTheme) -> None:
        c = theme.colors
        self._link_color = QColor(c.accent)
        self._backgrounds = {
            RowKind.HUNK: QColor(c.diff_hunk_bg),
            RowKind.ADDED: QColor(c.diff_added_bg),
            RowKind.REMOVED: QColor(c.diff_removed_bg),
            RowKind.FILLER: QColor(c.diff_filler_bg),
        }
        self._text_colors = {RowKind.HUNK: QColor(c.diff_hunk_text),
                             RowKind.NOTE: QColor(c.text_muted)}
        self._base = QColor(c.content_bg)
        self._selected_bg = {RowKind.ADDED: QColor(c.diff_added_selected_bg),
                             RowKind.REMOVED: QColor(c.diff_removed_selected_bg)}
        self._outline = {RowKind.ADDED: QColor(theme.status.added),
                         RowKind.REMOVED: QColor(theme.status.deleted)}
        self.setFont(theme.mono_font())
        self.gutter.apply_theme(theme)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * _TAB_WIDTH)
        self._color_text()
        self._update_margins()
        self.viewport().update()

    def paintEvent(self, event: QPaintEvent) -> None:
        # QPlainTextEdit doesn't fill behind its text, so these show through.
        painter = QPainter(self.viewport())
        width = self.viewport().width()
        for n, row, top, height in self.visible_rows(event.rect()):
            color = self.background(n, row)
            if color is not None:
                painter.fillRect(QRectF(0, top, width, height), color)
        painter.end()
        super().paintEvent(event)
        if self.selection:
            painter = QPainter(self.viewport())
            self.selection.paint_outlines(painter, event.rect(), self._outline)
            painter.end()
        if self._link:
            self._paint_links(event)

    def _paint_links(self, event: QPaintEvent) -> None:
        """Each hunk header's link, at the right edge, over the header's background."""
        painter = QPainter(self.viewport())
        painter.setFont(self.font())
        painter.setPen(self._link_color)
        background = self._backgrounds.get(RowKind.HUNK, self._base)
        for _n, row, top, height in self.visible_rows(event.rect()):
            if row.kind is RowKind.HUNK:
                rect = self._link_rect(top, height)
                painter.fillRect(rect, background)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._link)
        painter.end()

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

    # ---- the gutter's place ----------------------------------------------------

    def gutter_width(self) -> int:
        return self.gutter.width_needed()

    def _update_margins(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        self._place_gutter()
        self.gutter.update()

    def _place_gutter(self) -> None:
        r = self.contentsRect()
        self.gutter.setGeometry(QRect(r.left(), r.top(), self.gutter_width(), r.height()))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._place_gutter()

    def _scroll_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self.gutter.scroll(0, dy)
            if self.gutter.hover is not None:  # another row is under the pointer now
                y = self.viewport().mapFromGlobal(QCursor.pos()).y()
                self.gutter.set_hover(self.row_number_at(y))
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
