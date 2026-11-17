"""Draws a row of History's branch graph (spec §5.4, RZG-58) from a ``GraphRow``.

Over the cell's stripe, hover and selection, antialiased: a straight line down
each lane, a short curve where a line changes lane (in a row's bottom half), and
a dot at the commit. Uncommitted changes has a hollow dot, joined to HEAD's
below it. At most ``GRAPH_MAX_LANES`` lanes are drawn; a wider row is cut off
there and its last lane fades out. ``inset`` moves the lanes right, so a ref
label centred on a dot near the left edge isn't cut off (RZG-86).
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen

from rzgitclient.core.commit_graph import GraphRow
from rzgitclient.ui.theme.app_theme import AppTheme

GRAPH_MAX_LANES = 8
PADDING_X = 4  # left and right of the lanes


class GraphPainter:
    def __init__(self, theme: AppTheme):
        self._colors: list[QColor] = []
        self._brushes: list[QBrush] = []  # one per colour, made once: rows are painted often
        self._pens: list[QPen] = []
        self._lane = self._line = self._dot = 0.0
        self.inset = 0  # px left of the lanes, for the ref labels (set by the panel)
        self.set_theme(theme)

    def set_theme(self, theme: AppTheme) -> None:
        self._colors = [QColor(c) for c in theme.graph.lanes]
        m = theme.metrics
        self._lane, self._line, self._dot = m.graph_lane_width, m.graph_line_width, m.graph_dot_radius
        self._brushes = [QBrush(c) for c in self._colors]
        self._pens = [self._make_pen(b) for b in self._brushes]

    def width(self, lanes: int) -> int:
        """The column width that shows ``lanes`` lanes (no more than the cap), without
        the inset."""
        return int(min(max(lanes, 1), GRAPH_MAX_LANES) * self._lane) + 2 * PADDING_X

    def lane_x(self, lane: int) -> float:
        """The middle of a commit's dot in ``lane``, from the cell's left edge, without
        the inset; past the cap, the last lane drawn."""
        return PADDING_X + (min(lane, GRAPH_MAX_LANES - 1) + 0.5) * self._lane

    def node_x(self, lane: int) -> float:
        """As ``lane_x``, with the inset: where a ref label is centred (RZG-86)."""
        return self.inset + self.lane_x(lane)

    def _x(self, rect: QRect, lane: int) -> float:
        """The middle of ``lane``, on a pixel's centre so a straight line is crisp."""
        return math.floor(rect.left() + self.inset + PADDING_X + (lane + 0.5) * self._lane) + 0.5

    def _brush(self, color: int, rect: QRect, fade: bool) -> QBrush:
        color %= len(self._colors)
        if not fade:
            return self._brushes[color]
        colour = self._colors[color]
        # The last lane drawn fades out: there are more to its right.
        end = rect.left() + self.inset + PADDING_X + GRAPH_MAX_LANES * self._lane
        gradient = QLinearGradient(end - self._lane, 0, end, 0)
        clear = QColor(colour)
        clear.setAlpha(0)
        gradient.setColorAt(0, colour)
        gradient.setColorAt(1, clear)
        return QBrush(gradient)

    def paint(self, painter: QPainter, rect: QRect, row: GraphRow,
              joined_above: bool = False) -> None:
        """Draw ``row`` in ``rect``. ``joined_above``: a line up to the Uncommitted
        changes dot (HEAD's row, under it)."""
        fade = row.width > GRAPH_MAX_LANES
        top, bottom = float(rect.top()), float(rect.top() + rect.height())
        mid = (top + bottom) / 2
        painter.save()
        self._clip(painter, rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        upper = list(row.upper)
        if joined_above:
            upper.append((row.lane, row.lane, row.color))
        for a, _b, color in upper:  # the top half is straight down
            if a < GRAPH_MAX_LANES:
                painter.setPen(self._pen(color, rect, fade))
                x = self._x(rect, a)
                painter.drawLine(QPointF(x, top), QPointF(x, mid))
        for a, b, color in row.lower:
            if min(a, b) >= GRAPH_MAX_LANES:
                continue
            painter.setPen(self._pen(color, rect, fade))
            xa, xb = self._x(rect, a), self._x(rect, b)
            if a == b:
                painter.drawLine(QPointF(xa, mid), QPointF(xa, bottom))
            else:
                bend = (mid + bottom) / 2
                path = QPainterPath(QPointF(xa, mid))
                path.cubicTo(QPointF(xa, bend), QPointF(xb, bend), QPointF(xb, bottom))
                painter.drawPath(path)
        if row.lane < GRAPH_MAX_LANES:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._brush(row.color, rect, fade))
            painter.drawEllipse(QPointF(self._x(rect, row.lane), mid), self._dot, self._dot)
        painter.restore()

    def paint_uncommitted(self, painter: QPainter, rect: QRect, head: GraphRow | None) -> None:
        """The Uncommitted changes dot: hollow, in HEAD's lane and colour, with a
        line down to HEAD's dot (``head``, None before the first commit)."""
        lane, color = (head.lane, head.color) if head is not None else (0, 0)
        if lane >= GRAPH_MAX_LANES:
            return
        top, bottom = float(rect.top()), float(rect.top() + rect.height())
        mid = (top + bottom) / 2
        x = self._x(rect, lane)
        painter.save()
        self._clip(painter, rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = self._pen(color, rect, False)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if head is not None:
            painter.drawLine(QPointF(x, mid + self._dot), QPointF(x, bottom))
        ring = self._dot - self._line / 2  # the same outer size as a commit's dot
        painter.drawEllipse(QPointF(x, mid), ring, ring)
        painter.restore()

    def _pen(self, color: int, rect: QRect, fade: bool) -> QPen:
        if not fade:
            return self._pens[color % len(self._pens)]
        return self._make_pen(self._brush(color, rect, fade))

    def _make_pen(self, brush: QBrush) -> QPen:
        pen = QPen(brush, self._line)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)  # halves of a line meet without overlap
        return pen

    def _clip(self, painter: QPainter, rect: QRect) -> None:
        """Nothing outside the cell (a column made narrower than its graph). Past the
        cap needs no clip: a row that reaches it fades to nothing there."""
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
