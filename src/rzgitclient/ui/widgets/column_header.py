"""The column labels of a list (spec §5.2, RZG-41).

A ``QHeaderView``, so the columns stay aligned with the list and keep Qt's
drag-resizing, that paints its own sections from the theme's
``column_header_*`` tokens: a row of chrome with an inset separator between
labels. A label too wide for its column shows its first letter and "..".
Right-clicking the labels shows or hides columns (the first is always shown);
the choice is kept per panel in ``ColumnPrefs``.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractItemModel, QEvent, QRect, QSize, Qt
from PySide6.QtGui import QColor, QHelpEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QHeaderView, QMenu, QToolTip, QWidget

from rzgitclient.core.column_prefs import ColumnPrefs
from rzgitclient.ui.theme.app_theme import AppTheme

SHORT_SUFFIX = ".."  # "Status" → "S.."
PADDING_X, PADDING_Y = 6, 4  # around a label's text
SEPARATOR_INSET = 0.2  # of the row's height, above and below a separator (60% drawn)
MAIN_COLUMN = 0  # Subject, Message, Name: always shown


def short_label(label: str) -> str:
    """``label`` as its first letter and "..", for a column too narrow for it."""
    return f"{label[:1]}{SHORT_SUFFIX}" if label else ""


class ColumnHeader(QHeaderView):
    """The labels of a list's columns, painted from ``theme``. ``panel_id`` keys
    the columns it hides in ``prefs``."""

    def __init__(self, panel_id: str, prefs: ColumnPrefs, theme: AppTheme,
                 parent: QWidget | None = None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.panel_id, self._prefs = panel_id, prefs
        self._theme = theme
        self.hover_section = -1  # the label under the pointer
        self.setSectionsClickable(False)
        self.setSectionsMovable(False)
        self.setHighlightSections(False)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.columns_menu().popup(self.mapToGlobal(pos)))
        self.sectionCountChanged.connect(lambda *_: self.apply_hidden())

    # ---- labels ----------------------------------------------------------

    def label(self, logical: int) -> str:
        model = self.model()
        if model is None:
            return ""
        value = model.headerData(logical, Qt.Orientation.Horizontal,
                                 Qt.ItemDataRole.DisplayRole)
        return "" if value is None else str(value)

    def shown_label(self, logical: int) -> str:
        """The label as drawn at the column's width: whole, or shortened."""
        label = self.label(logical)
        room = self.sectionSize(logical) - 2 * PADDING_X
        return label if self.fontMetrics().horizontalAdvance(label) <= room else short_label(label)

    def sectionSizeFromContents(self, logical: int) -> QSize:
        """The whole label's size: fitting a column never shortens its label."""
        metrics = self.fontMetrics()
        width = metrics.horizontalAdvance(self.label(logical)) + 2 * PADDING_X
        return QSize(width, metrics.height() + 2 * PADDING_Y + 1)  # + the line under the row

    # ---- theme and painting ----------------------------------------------

    def set_theme(self, theme: AppTheme) -> None:
        self._theme = theme
        self.viewport().update()

    def paintSection(self, painter: QPainter, rect: QRect, logical: int) -> None:
        c = self._theme.colors
        painter.save()
        painter.fillRect(rect, QColor(c.column_header_bg))
        hover = logical == self.hover_section
        painter.setPen(QColor(c.column_header_text_hover if hover else c.column_header_text))
        painter.setFont(self.font())
        text_rect = rect.adjusted(PADDING_X, 0, -PADDING_X, -1)
        painter.setClipRect(text_rect)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self.shown_label(logical))
        painter.setClipping(False)
        if self.visualIndex(logical) < self._last_visual():
            inset = round(rect.height() * SEPARATOR_INSET)
            painter.setPen(QColor(c.column_header_separator))
            painter.drawLine(rect.right(), rect.top() + inset, rect.right(), rect.bottom() - inset)
        painter.setPen(QColor(c.column_header_border))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        painter.restore()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)  # the sections, then the style's empty area: painted over
        end = self.length() - self.offset()
        viewport = self.viewport().rect()
        if end > viewport.right():
            return
        c = self._theme.colors
        painter = QPainter(self.viewport())
        empty = QRect(max(end, 0), 0, viewport.width() - max(end, 0), viewport.height())
        painter.fillRect(empty, QColor(c.column_header_bg))
        painter.setPen(QColor(c.column_header_border))
        painter.drawLine(empty.left(), empty.bottom(), empty.right(), empty.bottom())
        painter.end()

    def _last_visual(self) -> int:
        for visual in range(self.count() - 1, -1, -1):
            if not self.isSectionHidden(self.logicalIndex(visual)):
                return visual
        return -1

    # ---- pointer -----------------------------------------------------------

    def viewportEvent(self, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.Type.ToolTip:
            self._show_tooltip(event)  # type: ignore[arg-type]
            return True
        if kind in (QEvent.Type.MouseMove, QEvent.Type.HoverMove, QEvent.Type.HoverEnter):
            self._set_hover(self.logicalIndexAt(int(event.position().x())))  # type: ignore[attr-defined]
        elif kind in (QEvent.Type.Leave, QEvent.Type.HoverLeave):
            self._set_hover(-1)
        return super().viewportEvent(event)

    def _set_hover(self, logical: int) -> None:
        if logical != self.hover_section:
            self.hover_section = logical
            self.viewport().update()

    def _show_tooltip(self, event: QHelpEvent) -> None:
        """The whole label, when the column shows it shortened."""
        logical = self.logicalIndexAt(event.pos().x())
        if logical >= 0 and self.shown_label(logical) != self.label(logical):
            QToolTip.showText(event.globalPos(), self.label(logical), self)
        else:
            QToolTip.hideText()

    # ---- showing and hiding columns ----------------------------------------

    def setModel(self, model: QAbstractItemModel | None) -> None:
        super().setModel(model)
        if model is not None:  # labels set after the model: apply the choices again
            # A bound method, not a lambda: disconnected when the header is destroyed.
            model.headerDataChanged.connect(self._on_labels_changed)
        self.apply_hidden()

    def _on_labels_changed(self, *_args: object) -> None:
        self.apply_hidden()

    def apply_hidden(self) -> None:
        """Hide the columns ``prefs`` says this panel hides; the main one never."""
        hidden = self._prefs.hidden(self.panel_id)
        for logical in range(self.count()):
            self.setSectionHidden(logical, logical != MAIN_COLUMN and self.label(logical) in hidden)

    def columns_menu(self) -> QMenu:
        """A checkable entry per column; unchecking one hides it (saved)."""
        menu = QMenu(self)
        for logical in range(self.count()):
            label = self.label(logical)
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not self.isSectionHidden(logical))
            if logical == MAIN_COLUMN:
                action.setEnabled(False)
            else:
                action.toggled.connect(
                    lambda on, column=label: self.set_column_shown(column, on))
        return menu

    def set_column_shown(self, label: str, shown: bool) -> None:
        self._prefs.set_hidden(self.panel_id, label, not shown)
        self.apply_hidden()
