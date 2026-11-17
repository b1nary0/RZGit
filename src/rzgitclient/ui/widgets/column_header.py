"""The column labels of a list (spec §5.2, RZG-41 and RZG-40).

A ``QHeaderView``, so the columns stay aligned with the list, that paints its
own sections from the theme's ``column_header_*`` tokens: a row of chrome
with an inset separator between labels. A label too wide for its column shows
its first letter and "..".

Widths (RZG-40): the columns always fill the list's width exactly; its left
and right edges are pinned. The panel fits the other columns to their contents
(``fit``, ``set_fitted``) and the first, main column takes what they leave.
Dragging a separator moves only the two columns beside it; the widths the
user gives the other columns are kept, and saved per panel in
``ColumnPrefs``. Double-clicking a separator fits its column again; **Reset
Column Widths** fits them all. Qt's own resizing is off (``Fixed``): this
class does the dragging, the cursor and the fitting. Right-clicking the
labels also shows or hides columns (the main one is always shown).
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QAbstractItemModel, QEvent, QRect, QSize, Qt
from PySide6.QtGui import (QColor, QHelpEvent, QMouseEvent, QPainter, QPaintEvent,
                           QResizeEvent)
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QStyle, QToolTip, QWidget

from rzgitclient.core.column_prefs import ColumnPrefs
from rzgitclient.ui.theme.app_theme import AppTheme

SHORT_SUFFIX = ".."  # "Status" → "S.."
PADDING_X, PADDING_Y = 6, 4  # around a label's text
SEPARATOR_INSET = 0.2  # of the row's height, above and below a separator (60% drawn)
MAIN_COLUMN = 0  # Subject, Message, Name: always shown; takes what the others leave
RESET_WIDTHS = "Reset Column Widths"


def short_label(label: str) -> str:
    """``label`` as its first letter and "..", for a column too narrow for it."""
    return f"{label[:1]}{SHORT_SUFFIX}" if label else ""


class ColumnHeader(QHeaderView):
    """The labels of a list's columns, painted from ``theme``. ``panel_id`` keys
    the columns' saved widths and the columns it hides in ``prefs``."""

    def __init__(self, panel_id: str, prefs: ColumnPrefs, theme: AppTheme,
                 parent: QWidget | None = None, *, hidden_by_default: Iterable[str] = ()):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.panel_id, self._prefs = panel_id, prefs
        self._hidden_by_default = frozenset(hidden_by_default)  # until the user chooses
        self._theme = theme
        self.hover_section = -1  # the label under the pointer
        self._fitted: dict[int, int] = {}  # the panel's fitted widths, by column
        self._user: dict[int, int] = {}  # the user's widths, by column (never the main one)
        # A separator being dragged: (left, right, their widths, the press's x).
        self._pair: tuple[int, int, int, int, int] | None = None
        self.setSectionsClickable(False)
        self.setSectionsMovable(False)
        self.setHighlightSections(False)
        self.setStretchLastSection(False)
        self.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)  # resized only by fill()
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.columns_menu().popup(self.mapToGlobal(pos)))
        self.sectionCountChanged.connect(lambda *_: self.apply_prefs())

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

    def _update_minimum(self) -> None:
        """No column narrower than its short label."""
        metrics = self.fontMetrics()
        widest = max((metrics.horizontalAdvance(short_label(self.label(c)))
                      for c in range(self.count())), default=0)
        self.setMinimumSectionSize(widest + 2 * PADDING_X)

    # ---- widths (RZG-40) ------------------------------------------------------

    def is_user_sized(self, logical: int) -> bool:
        return logical in self._user

    def set_fitted(self, logical: int, width: int) -> None:
        """The panel's fitted width for ``logical``: used unless the user gave
        the column a width of their own."""
        self._fitted[logical] = width
        self.fill()

    def fit(self, logical: int) -> None:
        """Fit ``logical`` to the list's contents and its label (``set_fitted``)."""
        view = self.parent()
        contents = view.sizeHintForColumn(logical) if isinstance(view, QAbstractItemView) else 0
        self.set_fitted(logical, max(contents, self.sectionSizeHint(logical)))

    def _wanted(self, logical: int) -> int:
        """The user's width, else the fitted one, else the current one."""
        return self._user.get(logical, self._fitted.get(logical, self.sectionSize(logical)))

    def _shown(self) -> list[int]:
        """The shown columns, left to right."""
        return [self.logicalIndex(v) for v in range(self.count())
                if not self.isSectionHidden(self.logicalIndex(v))]

    def fill(self) -> None:
        """Lay the columns out across the whole width: each other column at its
        wanted width, the main column taking the rest. When even the main
        column's minimum doesn't fit, the others shrink for now, in proportion
        (their wanted widths are kept for when there is room again)."""
        if self.count() <= MAIN_COLUMN:
            return
        minimum = self.minimumSectionSize()
        others = {c: max(minimum, self._wanted(c)) for c in self._shown() if c != MAIN_COLUMN}
        room = self.width() - minimum
        total = sum(others.values())
        if total > room > 0:
            others = {c: max(minimum, w * room // total) for c, w in others.items()}
        for logical, width in others.items():
            self.resizeSection(logical, width)
        self.resizeSection(MAIN_COLUMN, max(minimum, self.width() - sum(others.values())))

    def _set_user_width(self, logical: int, width: int) -> None:
        """Keep the user's ``width`` for ``logical`` (the main column has none: it
        takes the rest)."""
        if logical != MAIN_COLUMN:
            self._user[logical] = width

    def fit_column(self, left: int) -> None:
        """Double-click on the separator after ``left``: fit the column beside it
        (``left``, or the one after the main column); its other neighbour gives
        or takes the difference, so no other separator moves."""
        right = self._right_of(left)
        if right < 0:
            return
        target, other = (left, right) if left != MAIN_COLUMN else (right, left)
        if target not in self._fitted:
            self.fit(target)
        fitted = self._fitted[target]
        minimum = self.minimumSectionSize()
        change = min(fitted - self.sectionSize(target), self.sectionSize(other) - minimum)
        self._user.pop(target, None)
        self._prefs.set_width(self.panel_id, self.label(target), None)
        if other != MAIN_COLUMN:
            self._set_user_width(other, self.sectionSize(other) - change)
            self._prefs.set_width(self.panel_id, self.label(other), self._user[other])
        self.fill()

    def reset_widths(self) -> None:
        """Every column back to its fitted width, the main one taking the rest."""
        self._prefs.reset_widths(self.panel_id)
        self._user.clear()
        self.fill()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        x = int(event.position().x())
        left = self.handle_at(x) if event.button() == Qt.MouseButton.LeftButton else -1
        if left >= 0:  # a separator: start dragging it
            right = self._right_of(left)
            self._pair = (left, right, self.sectionSize(left), self.sectionSize(right), x)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x = int(event.position().x())
        if self._pair is None:
            super().mouseMoveEvent(event)
            if self.handle_at(x) >= 0:
                self.setCursor(Qt.CursorShape.SplitHCursor)
            else:
                self.unsetCursor()
            return
        left, right, left_width, right_width, start = self._pair
        minimum = self.minimumSectionSize()
        delta = min(max(x - start, minimum - left_width), right_width - minimum)
        self._set_user_width(left, left_width + delta)  # the total stays: nothing else moves
        self._set_user_width(right, right_width - delta)
        self.fill()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._pair is None:
            super().mouseReleaseEvent(event)
            return
        left, right = self._pair[:2]
        self._pair = None
        for logical in (left, right):
            if logical in self._user:
                self._prefs.set_width(self.panel_id, self.label(logical), self._user[logical])
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """On a separator: fit its column (``fit_column``)."""
        left = self.handle_at(int(event.position().x()))
        if left < 0:
            super().mouseDoubleClickEvent(event)
            return
        self.fit_column(left)
        event.accept()

    def handle_at(self, x: int) -> int:
        """The column whose separator (right edge) is at ``x``, within the grip
        margin, or -1. The last column's right edge is pinned: not a separator."""
        margin = self.style().pixelMetric(QStyle.PixelMetric.PM_HeaderGripMargin, None, self)
        for logical in self._shown()[:-1]:
            edge = self.sectionViewportPosition(logical) + self.sectionSize(logical)
            if abs(x - edge) <= margin:
                return logical
        return -1

    def _right_of(self, logical: int) -> int:
        """The next shown column to the right of ``logical``, or -1."""
        shown = self._shown()
        at = shown.index(logical) if logical in shown else len(shown)
        return shown[at + 1] if at + 1 < len(shown) else -1

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.fill()

    # ---- theme and painting ----------------------------------------------

    def set_theme(self, theme: AppTheme) -> None:
        self._theme = theme
        self.viewport().update()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self._update_minimum()

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

    # ---- saved choices: hidden columns and widths ----------------------------

    def setModel(self, model: QAbstractItemModel | None) -> None:
        super().setModel(model)
        if model is not None:  # labels set after the model: apply the choices again
            # A bound method, not a lambda: disconnected when the header is destroyed.
            model.headerDataChanged.connect(self._on_labels_changed)
        self.apply_prefs()

    def _on_labels_changed(self, *_args: object) -> None:
        self.apply_prefs()

    def apply_prefs(self) -> None:
        """Hide the columns this panel hides (the main one never), and give the
        user's widths back (the main one has none: it takes the rest)."""
        hidden = self._prefs.hidden(self.panel_id, self._hidden_by_default)
        widths = self._prefs.widths(self.panel_id)
        self._user.clear()
        for logical in range(self.count()):
            label = self.label(logical)
            self.setSectionHidden(logical, logical != MAIN_COLUMN and label in hidden)
            if label in widths:
                self._set_user_width(logical, widths[label])
        self._update_minimum()
        self.fill()

    def columns_menu(self) -> QMenu:
        """A checkable entry per column (unchecking one hides it), then Reset
        Column Widths. Choices are saved."""
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
        menu.addSeparator()
        menu.addAction(RESET_WIDTHS, self.reset_widths)
        return menu

    def set_column_shown(self, label: str, shown: bool) -> None:
        self._prefs.set_hidden(self.panel_id, label, not shown, self._hidden_by_default)
        self.apply_prefs()
