"""A panel's own internal sections (spec §5.2, "Segments inside a panel").

A PanelSegment is an optional header strip above some content. Pinned, it is
always its content's height; fill, its content scrolls inside it, and it is
never taller than the content. PanelSegments stacks them, and scrolls as a
whole only when even the pinned segments don't fit.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from rzgitclient.ui.theme.style_roles import Role, set_role

_HEADER_MARGINS = (8, 5, 8, 5)  # px around a header strip's content
_SELECTABLE = (Qt.TextInteractionFlag.TextSelectableByMouse
               | Qt.TextInteractionFlag.TextSelectableByKeyboard)


def _no_wider(widget: QWidget) -> None:
    """Never wider than its segment: long text is wrapped or cut off, never scrolled to."""
    policy = widget.sizePolicy()
    policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
    widget.setSizePolicy(policy)


def _transparent(widget: QWidget) -> None:
    """Let the panel's own surface show through."""
    widget.setAutoFillBackground(False)


class _FitScrollArea(QScrollArea):
    """A frameless scroll area that asks for its content's full height, so it is
    never taller than that; the layout may give it less, down to ``min_lines``."""

    def __init__(self, content: QWidget, min_lines: int):
        super().__init__()
        self._min_lines = min_lines
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _transparent(self)
        _transparent(self.viewport())
        self.setWidget(content)
        _transparent(content)  # setWidget turns filling on: a box in the window colour
        content.installEventFilter(self)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def content_height(self, width: int | None = None) -> int:
        content = self.widget()
        width = self.viewport().width() if width is None else width
        if content.hasHeightForWidth() and width > 0:
            return content.heightForWidth(width)
        return content.sizeHint().height()

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self.content_height(self.width()))

    def minimumSizeHint(self) -> QSize:
        return QSize(0, min(self.content_height(self.width()),
                            self._min_lines * self.fontMetrics().lineSpacing()))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()  # the content's height depends on the width

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.widget() and event.type() in (QEvent.Type.LayoutRequest,
                                                         QEvent.Type.Show, QEvent.Type.Hide):
            self.updateGeometry()
        return False


class PanelSegment(QWidget):
    """An optional header strip (``Role.SEGMENT_HEADER``) above ``content``."""

    def __init__(self, content: QWidget | None = None, header: str | QWidget | None = None,
                 *, fill: bool = False, min_lines: int = 1, parent: QWidget | None = None):
        super().__init__(parent)
        _transparent(self)
        self.fill = fill
        self.content = content
        self.header: QWidget | None = None
        self._header_label: QLabel | None = None
        self._area: _FitScrollArea | None = None
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        if header is not None:
            self.header = self._strip(header)
            column.addWidget(self.header)
        if content is not None:
            _no_wider(content)
            if fill:
                self._area = _FitScrollArea(content, min_lines)
                column.addWidget(self._area)
            else:
                column.addWidget(content)
        # Pinned: never less than its natural height (only fill segments give way).
        vertical = QSizePolicy.Policy.Preferred if fill else QSizePolicy.Policy.Minimum
        self.setSizePolicy(QSizePolicy.Policy.Preferred, vertical)

    def _strip(self, header: str | QWidget) -> QWidget:
        strip = QWidget()
        set_role(strip, Role.SEGMENT_HEADER)
        strip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(strip)
        row.setContentsMargins(*_HEADER_MARGINS)
        if isinstance(header, str):
            label = QLabel(header)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)  # a long summary wraps; it's never cut off
            label.setTextInteractionFlags(_SELECTABLE)
            _no_wider(label)
            self._header_label = label
            row.addWidget(label, 1)
            row.addStretch(0)  # what a label capped by its maximum width leaves over
            return strip
        row.addWidget(header)
        return strip

    @property
    def header_label(self) -> QLabel | None:
        """The label of a text header (None for a widget header, or none)."""
        return self._header_label

    @property
    def scroll_area(self) -> QScrollArea | None:
        """A fill segment's scroll area."""
        return self._area


class PanelSegments(QScrollArea):
    """Segments stacked top to bottom, spare space after the last. Scrolls as a whole
    only when the pinned segments and the fill segments' minimums don't fit."""

    def __init__(self, segments: Sequence[PanelSegment], parent: QWidget | None = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _transparent(self)
        _transparent(self.viewport())
        inner = _Stack(self)
        column = QVBoxLayout(inner)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.segments = tuple(segments)
        for segment in self.segments:
            column.addWidget(segment)  # stretch 0: each keeps its own height
        column.addStretch(1)  # what's left over goes here, not into a gap between segments
        self.setWidget(inner)
        _transparent(inner)  # setWidget turns filling on

    def minimum_height(self, width: int) -> int:
        """The pinned segments' heights plus the fill segments' minimums, at ``width``."""
        total = 0
        for segment in self.segments:
            if segment.isHidden():
                continue
            if segment.fill:
                header = segment.header
                if header is not None:
                    total += (header.heightForWidth(width) if header.hasHeightForWidth()
                              else header.sizeHint().height())
                if segment.scroll_area is not None:
                    total += segment.scroll_area.minimumSizeHint().height()
            elif segment.hasHeightForWidth():
                total += segment.heightForWidth(width)
            else:
                total += segment.sizeHint().height()
        return total


class _Stack(QWidget):
    """The widget PanelSegments scrolls. Its height for a width is its minimum, the room
    the segments can't do without, not their full height, so the scroll area makes it
    the viewport's height when that's enough: a long fill segment scrolls by itself."""

    def __init__(self, owner: PanelSegments):
        super().__init__()
        self._owner = owner

    def hasHeightForWidth(self) -> bool:
        return False

    def heightForWidth(self, width: int) -> int:
        # QScrollArea asks this when the layout has height-for-width (wrapped text).
        return self._owner.minimum_height(width)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self._owner.minimum_height(self._owner.viewport().width()))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()  # wrapped text: the minimum depends on the width
