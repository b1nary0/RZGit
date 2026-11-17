"""Draws a row of History's ref labels (spec §5.4, RZG-77) from its ``RefLabel``s.

Pills left to right, vertically centred, over the cell's stripe, hover and
selection: a local branch (and a detached HEAD) as a tint of its commit's lane
colour with a border in it, a remote-only branch as the border alone, a tag and a
stash in their neutral tokens (a stash's border dashed). The current branch is
bold. A pill is at most ``ref_label_max_width``, its name shortened in the middle;
pills that don't fit the cell leave a ``+N`` pill.
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

    def natural_width(self, labels: Sequence[RefLabel]) -> int:
        """The cell width that shows every label of a row (0 for none)."""
        if not labels:
            return 0
        return self._gap + sum(self.pill_width(label) + self._gap for label in labels)

    def layout(self, labels: Sequence[RefLabel], width: int) -> list[Pill]:
        """The pills of ``labels`` in a cell ``width`` wide."""
        pills: list[Pill] = []
        end = width - self._gap  # the last pill's right edge, at most
        x = self._gap
        for i, label in enumerate(labels):
            rest = len(labels) - i - 1
            w = self.pill_width(label)
            after = self._gap + self._more_width(rest) if rest else 0
            if x + w + after <= end:
                pills.append(self._pill(x, w, label))
                x += w + self._gap
                continue
            hidden = len(labels) - i
            if i == 0:  # not even the first fits: shorten it beside "+N"
                pills.append(self._pill(x, max(end - x - after, 2 * self._pad), label))
                x += pills[-1].width + self._gap
                hidden = rest
            if hidden:
                pills.append(Pill(x, self._more_width(hidden), f"+{hidden}", None))
            break
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
              lane_color: int) -> None:
        """Draw ``labels`` in ``rect``; branches in lane colour ``lane_color`` (an
        index into the theme's lanes)."""
        if not labels:
            return
        metrics = QFontMetrics(self._font)
        height = min(rect.height() - 2, metrics.height() + 2)
        top = rect.top() + (rect.height() - height) // 2
        lane = lane_color % len(self._lane_pens)
        painter.save()
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for pill in self.layout(labels, rect.width()):
            brush, pen, colour = self._style(pill.label, lane)
            box = QRectF(rect.left() + pill.x + 0.5, top + 0.5, pill.width - 1, height - 1)
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
