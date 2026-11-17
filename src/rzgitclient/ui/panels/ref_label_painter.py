"""Draws a commit's ref label over History's branch graph (spec §5.4, RZG-77,
RZG-86) from its ``RefLabel``s.

One pill, the commit's first label, centred on its dot (``centre``), and a
``+N`` pill after it for the others. Each pill is filled with the row's
background first, so it hides the graph under it. A local branch (and a
detached HEAD) is a tint of its commit's lane colour with a border in it, a
remote-only branch the border alone, a tag and a stash their neutral tokens (a
stash's border dashed). The current branch is bold. A pill is at most
``ref_label_max_width``, its name shortened in the middle. The panel moves the
graph's lanes right so a label's left half fits (``left_reach``); in a cell made
too narrow, the label moves left and is shortened.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen

from rzgitclient.core.ref_labels import RefKind, RefLabel
from rzgitclient.ui.theme.app_theme import AppTheme


@dataclass(frozen=True, slots=True)
class Pill:
    """One pill of a row, laid out: ``x`` from the cell's left edge."""

    x: int
    width: int
    text: str  # as drawn: shortened, or "+N"
    label: RefLabel | None  # None: the "+N" pill
    bold: bool = False


class RefLabelPainter:
    def __init__(self, theme: AppTheme, font: QFont | None = None):
        self._font = QFont()
        self._bold = QFont()
        self._bold.setBold(True)
        self._widths: dict[tuple[str, bool], int] = {}  # a text's width, per font
        self.set_theme(theme)
        if font is not None:
            self.set_font(font)

    def set_theme(self, theme: AppTheme) -> None:
        g, c, m = theme.graph, theme.colors, theme.metrics
        self._max, self._pad, self._gap = m.ref_label_max_width, m.ref_label_padding, m.ref_label_gap
        self._radius = m.ref_label_radius
        self._text = QColor(c.text)
        self._row_bg = (QBrush(QColor(c.content_bg)), QBrush(QColor(c.row_alt_bg)))
        self._muted = QColor(c.text_muted)
        # Made once per theme: rows are painted often.
        self._lane_pens = [QPen(QColor(lane), 1) for lane in g.lanes]
        tints = []
        for lane in g.lanes:
            tint = QColor(lane)
            tint.setAlpha(g.label_fill_alpha)
            tints.append(QBrush(tint))
        self._lane_tints = tints
        self._tag = (QBrush(QColor(g.tag_fill)), QPen(QColor(g.tag_border), 1))
        stash_pen = QPen(QColor(g.stash_border), 1)
        stash_pen.setStyle(Qt.PenStyle.DashLine)
        self._stash = (QBrush(QColor(g.stash_fill)), stash_pen)
        self._more_pen = QPen(self._muted, 1)
        self._widths.clear()

    def set_font(self, font: QFont) -> None:
        """The list's font; the current branch is drawn in its bold."""
        if font == self._font:
            return
        self._font = QFont(font)
        self._bold = QFont(font)
        self._bold.setBold(True)
        self._widths.clear()

    # ---- layout ------------------------------------------------------------

    def _text_width(self, text: str, bold: bool) -> int:
        key = (text, bold)
        width = self._widths.get(key)
        if width is None:
            width = QFontMetrics(self._bold if bold else self._font).horizontalAdvance(text)
            self._widths[key] = width
        return width

    def pill_width(self, label: RefLabel) -> int:
        """Its whole text and padding, at most ``ref_label_max_width``."""
        return min(self._text_width(label.text, label.current) + 2 * self._pad, self._max)

    def _more_width(self, hidden: int) -> int:
        return self._text_width(f"+{hidden}", False) + 2 * self._pad

    def _first(self, labels: Sequence[RefLabel]) -> tuple[int, int, int]:
        """The shown label's whole width, the hidden count and the room the ``+N``
        pill takes after it (0 for none)."""
        hidden = len(labels) - 1
        after = self._gap + self._more_width(hidden) if hidden else 0
        return self.pill_width(labels[0]), hidden, after

    def left_reach(self, labels: Sequence[RefLabel]) -> float:
        """How far left of its centre a row's label reaches, with its gap (0 for none)."""
        return self.pill_width(labels[0]) / 2 + self._gap if labels else 0

    def needed_width(self, labels: Sequence[RefLabel], centre: float) -> int:
        """The cell width that shows a row's label, centred on ``centre`` (from the
        cell's left edge, at least ``left_reach``), and its ``+N`` whole (0 for none)."""
        if not labels:
            return 0
        width, _hidden, after = self._first(labels)
        return max(self._gap, int(centre - width / 2)) + width + after + self._gap

    def layout(self, labels: Sequence[RefLabel], width: int, centre: float) -> list[Pill]:
        """The pills of ``labels`` in a cell ``width`` wide: the first label centred
        on ``centre``, then ``+N``; both kept ``ref_label_gap`` inside the cell."""
        if not labels:
            return []
        w, hidden, after = self._first(labels)
        end = width - self._gap  # the last pill's right edge, at most
        x = max(self._gap, int(centre - w / 2))
        if x + w + after > end:  # a narrowed cell: move left, then shorten
            x = max(self._gap, end - w - after)
            w = max(min(w, end - x - after), 2 * self._pad)
        pills = [self._pill(x, w, labels[0])]
        if hidden:
            pills.append(Pill(x + w + self._gap, self._more_width(hidden), f"+{hidden}", None))
        return pills

    def _pill(self, x: int, width: int, label: RefLabel) -> Pill:
        font = self._bold if label.current else self._font
        room = width - 2 * self._pad
        text = label.text
        if self._text_width(text, label.current) > room:
            text = QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideMiddle, room)
        return Pill(x, width, text, label, label.current)

    # ---- painting ----------------------------------------------------------

    def paint(self, painter: QPainter, rect: QRect, labels: Sequence[RefLabel],
              lane_color: int, centre: float, striped: bool = False) -> None:
        """Draw ``labels`` in ``rect``, centred on ``centre`` (from its left edge);
        branches in lane colour ``lane_color`` (an index into the theme's lanes).
        ``striped``: the row has the stripe, so the pills are filled with it."""
        if not labels:
            return
        metrics = QFontMetrics(self._font)
        height = min(rect.height() - 2, metrics.height() + 2)
        top = rect.top() + (rect.height() - height) // 2
        lane = lane_color % len(self._lane_pens)
        painter.save()
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        row_bg = self._row_bg[1 if striped else 0]
        for pill in self.layout(labels, rect.width(), centre):
            brush, pen, colour = self._style(pill.label, lane)
            box = QRectF(rect.left() + pill.x + 0.5, top + 0.5, pill.width - 1, height - 1)
            painter.setPen(Qt.PenStyle.NoPen)  # opaque: the graph doesn't show through
            painter.setBrush(row_bg)
            painter.drawRoundedRect(box, self._radius, self._radius)
            painter.setPen(pen)
            painter.setBrush(brush if brush is not None else Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, self._radius, self._radius)
            painter.setPen(colour)
            painter.setFont(self._bold if pill.bold else self._font)
            text_box = QRect(rect.left() + pill.x + self._pad, top,
                             pill.width - 2 * self._pad, height)
            painter.drawText(text_box, Qt.AlignmentFlag.AlignCenter, pill.text)
        painter.restore()

    def _style(self, label: RefLabel | None, lane: int) -> tuple[QBrush | None, QPen, QColor]:
        """The pill's fill (None: none), border and text colour."""
        if label is None:
            return None, self._more_pen, self._muted
        if label.kind is RefKind.TAG:
            return *self._tag, self._text
        if label.kind is RefKind.STASH:
            return *self._stash, self._text
        if label.kind is RefKind.REMOTE:
            return None, self._lane_pens[lane], self._text
        return self._lane_tints[lane], self._lane_pens[lane], self._text
