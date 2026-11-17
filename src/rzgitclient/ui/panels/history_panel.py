"""Uncommitted changes and the commit table; the selected row is
``selection.commit``, None for Uncommitted changes (spec §5.4). No row is
selected while a stash is (the Stash panel's). Commits are striped, and
Uncommitted changes stays pinned at the top while the table scrolls, and well
down, a "Back to top" control floats below it (RZG-64). The first column is the
branch graph (RZG-58), with each commit's ref label over its dot (RZG-77, RZG-86),
as wide as the widest row in view."""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, QRect, Qt
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from rzgitclient.core.ref_labels import labels_by_commit
from rzgitclient.core.repo_session import HISTORY_PAGE
from rzgitclient.git.git_models import Commit, Stash
from rzgitclient.ui.panels.back_to_top_button import BackToTopButton
from rzgitclient.ui.panels.graph_painter import GraphPainter
from rzgitclient.ui.panels.history_model import (DATE_FORMAT, GRAPH_COLUMN, MUTED_COLUMNS,
                                                 SUBJECT_COLUMN, HistoryModel)
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.panels.ref_label_painter import RefLabelPainter
from rzgitclient.ui.theme.app_theme import AppTheme
from rzgitclient.ui.widgets.column_header import ColumnHeader


# Column widths fit the first page of rows, as they did before paging.
_MEASURED_ROWS = HISTORY_PAGE


class HistoryPanel(Panel):
    panel_id = "history"
    title = "History"

    _view: _HistoryView
    _labels: ColumnHeader
    _model: HistoryModel

    def _setup(self) -> None:
        self._quiet = False  # True while a reset re-selects; selection isn't written
        self._new_commits = 0  # arrived above the view while scrolled down (Back to top)
        self._model = HistoryModel(self.ctx.theme.current)
        self._model.set_date_format(self.ctx.prefs.value(DATE_FORMAT))
        self.ctx.prefs.changed.connect(self._on_pref_changed)
        self._graph = GraphPainter(self.ctx.theme.current)
        self._refs = RefLabelPainter(self.ctx.theme.current)
        self._graph_width = -1  # the width the graph column was last fitted to
        self._view = _HistoryView(self._graph, self._refs)
        self._labels = ColumnHeader(self.panel_id, self.ctx.columns, self.ctx.theme.current,
                                    main_column=SUBJECT_COLUMN)
        self._view.setHorizontalHeader(self._labels)
        self._view.setModel(self._model)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        self._view.selectionModel().currentChanged.connect(self._sync_selection)
        bar = self._view.verticalScrollBar()
        bar.valueChanged.connect(self._load_more_near_the_end)
        bar.rangeChanged.connect(self._load_more_near_the_end)
        bar.valueChanged.connect(self._fit_graph)
        bar.rangeChanged.connect(self._fit_graph)  # also when the view's height changes
        bar.valueChanged.connect(self._forget_new_commits_at_the_top)
        self._model.rowsInserted.connect(self._fit_graph)
        self.set_header_actions([self.ctx.actions.topo_order])
        self.ctx.selection.commit_changed.connect(self._follow_selection)
        session = self.ctx.session
        session.history_changed.connect(self._render)
        session.status_changed.connect(self._show_uncommitted)
        session.repo_changed.connect(self._show_uncommitted)
        for signal in (session.refs_changed, session.stashes_changed, session.head_changed,
                       session.head_sha_changed):
            signal.connect(self._update_labels)
        self._show_uncommitted()
        self._update_labels()
        self._render(session.history)

    def _show_uncommitted(self, *_args: object) -> None:
        """The Uncommitted changes row while a repo is open, with its file count."""
        session = self.ctx.session
        self._quiet = True
        try:
            self._model.set_uncommitted(len(session.status) if session.repo else None)
            self._select_uncommitted_if_none()
        finally:
            self._quiet = False
        self._sync_selection()

    def _select_uncommitted_if_none(self) -> None:
        if isinstance(self.ctx.selection.commit, Stash):
            return  # none, on purpose
        if not self._view.currentIndex().isValid() and self._model.has_uncommitted:
            self._select_row(0)

    def _select_row(self, row: int) -> None:
        """Select ``row`` without scrolling to it: the commit at the top decides the scroll."""
        auto = self._view.hasAutoScroll()
        self._view.setAutoScroll(False)
        self._view.setCurrentIndex(self._model.index(row, SUBJECT_COLUMN))
        self._view.setAutoScroll(auto)

    def _render(self, commits: tuple[Commit, ...]) -> None:
        keep, top = self._current_commit(), self._top_commit()
        newest = self._model.commits[0] if self._model.commits else None
        was_empty = not self._model.commits  # Uncommitted changes may be shown already
        self._quiet = True
        try:
            appended = self._model.set_commits(commits)
            if not appended:  # reset: same selection, same commit at the top
                self._restore(keep, top)
                if newest is not None:  # at the top, the reset below forgets them
                    self._count_new_commits(newest)
            self._select_uncommitted_if_none()
        finally:
            self._quiet = False
        self._forget_new_commits_at_the_top()
        if was_empty or not appended:
            self._view.fit_row_height()
            self._fit_columns()
        self._fit_graph()
        self._sync_selection()

    def _count_new_commits(self, newest: Commit) -> None:
        """After a reset while scrolled down: add the commits above the one that was
        newest. If it's gone (a checkout), they can't be told apart: add nothing."""
        row = self._model.row_of(newest.sha)
        if row >= 0:
            self._new_commits += row - self._model.row_of(self._model.commits[0].sha)
            self._view.back_to_top.set_new_commits(self._new_commits)

    def _forget_new_commits_at_the_top(self, *_args: object) -> None:
        if not self._quiet and self._view.verticalScrollBar().value() == 0:
            self._new_commits = 0
            self._view.back_to_top.set_new_commits(0)
        self._view.place_back_to_top()

    def _load_more_near_the_end(self, *_args: object) -> None:
        """Ask for the next page when the view is within a screen of the end.

        A page (500 rows) is far taller than the view, so the rows always
        overflow it; maximum() is 0 only before the view has laid them out.
        """
        bar = self._view.verticalScrollBar()
        if bar.maximum() > 0 and bar.value() >= bar.maximum() - bar.pageStep():
            self.ctx.session.load_more_history()

    def _fit_columns(self) -> None:
        """Size the muted columns to their widest text, like resizeColumnToContents.

        Qt's version asks the model for every cell of 1000 rows (45 ms for
        500 commits); the widest text is found here with font metrics, and
        only that row is measured by the view.
        """
        rows = range(min(self._model.rowCount(), _MEASURED_ROWS))
        for col in MUTED_COLUMNS:
            width = 0  # no rows: the header's text decides
            if rows:
                metrics = QFontMetrics(self._model.font(col) or self._view.font())
                widest = max(rows, key=lambda r: metrics.horizontalAdvance(self._model.text(r, col)))
                width = self._view.width_hint(self._model.index(widest, col))
            # Unless the user sized it (spec §5.2); the subject fills the rest.
            self._labels.set_fitted(col, max(width, self._labels.sectionSizeHint(col)))

    def _update_labels(self, *_args: object) -> None:
        """Each commit's ref labels, from the session's refs, HEAD and stashes (RZG-77)."""
        session = self.ctx.session
        self._model.set_labels(labels_by_commit(session.refs, session.head, session.head_sha,
                                                session.stashes))
        self._fit_graph()

    def _fit_graph(self, *_args: object) -> None:
        """Size the graph column to the widest row in view (``set_fitted``: unless
        the user sized it): its lanes, up to the cap, or its ref label where that
        reaches further (RZG-86). A label is centred on its dot: where its left half
        wouldn't fit, the lanes move right (the graph's inset). Only rows in view are
        worked out."""
        view, model, graph, refs = self._view, self._model, self._graph, self._refs
        rows = model.rowCount()
        lanes, inset, shown = 1, 0.0, []
        if rows:
            view.ensurePolished()  # the stylesheet's list font
            refs.set_font(view.font())
            first = max(view.rowAt(0), 0)
            last = view.rowAt(view.viewport().height() - 1)
            for row in range(first, (last if last >= 0 else rows - 1) + 1):
                graph_row = model.graph_row(row)
                if graph_row is None:
                    continue
                lanes = max(lanes, graph_row.width)
                labels = model.ref_labels(row)
                if labels:
                    x = graph.lane_x(graph_row.lane)
                    inset = max(inset, refs.left_reach(labels) - x)
                    shown.append((labels, x))
        inset = math.ceil(inset)
        width = max([inset + graph.width(lanes)] +
                    [refs.needed_width(labels, inset + x) for labels, x in shown])
        if inset != graph.inset:
            graph.inset = inset
            view.viewport().update()  # every row's lanes move
        if width != self._graph_width:
            self._graph_width = width
            self._labels.set_fitted(GRAPH_COLUMN, width)

    def _restore(self, keep: Commit | None, top: Commit | None) -> None:
        row = self._model.row_of(keep.sha) if keep is not None else -1
        if row >= 0:
            self._select_row(row)
        row = self._model.row_of(top.sha) if top is not None else -1
        if row >= 0:
            self._view.scrollTo(
                self._model.index(row, SUBJECT_COLUMN),
                QAbstractItemView.ScrollHint.PositionAtTop
            )
        else:  # was at the top: stay there, so new commits show
            self._view.scrollToTop()

    def _on_pref_changed(self, key: str, value: object) -> None:
        if key == DATE_FORMAT.key:
            self._model.set_date_format(value)

    def apply_theme(self, theme: AppTheme) -> None:
        self._labels.set_theme(theme)
        self._model.set_theme(theme)
        self._graph.set_theme(theme)
        self._refs.set_theme(theme)
        self._graph_width = -1  # the lane and label widths may have changed: fit again
        self._fit_graph()
        self._view.set_pinned_line(QColor(theme.colors.border_light))
        self._view.back_to_top.set_colors(theme.colors)
        self._view.fit_row_height()

    def _sync_selection(self, *_args: object) -> None:
        # Selection emits only on change, so a reset that keeps the same
        # commit selected is silent. With no row (a stash is selected) History says nothing.
        if not self._quiet and self._view.currentIndex().isValid():
            self.ctx.selection.commit = self._current_commit()

    def _follow_selection(self, commit: Commit | None) -> None:
        """``selection.commit`` changed elsewhere (a parent link in Commit Details):
        select its row, and scroll to it if it's off screen. A commit that isn't
        loaded leaves the view as it is."""
        if isinstance(commit, Stash):
            self._quiet = True
            try:
                self._view.setCurrentIndex(QModelIndex())
                self._view.clearSelection()
            finally:
                self._quiet = False
            return
        if commit == self._current_commit() and self._view.currentIndex().isValid():
            return
        if commit is None:
            row = 0 if self._model.has_uncommitted else -1
        else:
            row = self._model.row_of(commit.sha)
        if row < 0:
            return
        index = self._model.index(row, SUBJECT_COLUMN)
        self._quiet = True
        try:
            self._view.setCurrentIndex(index)
        finally:
            self._quiet = False
        self._view.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)

    def _current_commit(self) -> Commit | None:
        return self._model.commit_at(self._view.currentIndex().row())

    def _top_commit(self) -> Commit | None:
        """The commit at the top of the view (below the pinned row), or None when
        scrolled to the top."""
        if self._view.verticalScrollBar().value() == 0:
            return None
        return self._model.commit_at(self._view.top_row())


class _HistoryView(QTableView):
    """A table that looks like the app's tree views (spec §5.4).

    A QTreeView keeps a layout entry per row and redoes all of them on every
    insert or reset (230 ms with 100k rows); a table with fixed row heights
    doesn't. These settings give it the tree's look: no grid or row numbers,
    whole-row selection and hover. Its column labels are the panel's ColumnHeader.

    While it's scrolled, row 0 (Uncommitted changes) is pinned: ``_PinnedRow``
    draws it over the top of the rows, which pass under it (spec §5.4). Scrolled
    well down, ``back_to_top`` floats below it; its owner tells it the new commits.
    """

    def __init__(self, graph: GraphPainter, refs: RefLabelPainter) -> None:
        super().__init__()
        self.hover_row = -1
        self.graph = graph  # draws the graph column; the panel keeps it themed
        self.refs = refs  # draws the ref labels over the graph (RZG-86); themed by the panel too
        self.pinned = _PinnedRow(self)
        self.back_to_top = BackToTopButton(self.viewport())
        self.back_to_top.clicked.connect(self.scrollToTop)  # a jump; the selection stays
        self.back_to_top.installEventFilter(self)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setTabKeyNavigation(False)
        self.setCornerButtonEnabled(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        rows = self.verticalHeader()
        rows.hide()
        rows.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        rows.setMinimumSectionSize(1)
        self.setItemDelegate(_RowHoverDelegate(self))
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(self._place_pinned)
        bar.rangeChanged.connect(self._place_pinned)

    def setModel(self, model) -> None:  # type: ignore[override]
        super().setModel(model)
        if model is None:
            return
        self.selectionModel().selectionChanged.connect(self.pinned.update)
        for signal in (model.modelReset, model.rowsInserted, model.rowsRemoved, model.dataChanged):
            signal.connect(self._place_pinned)
        header = self.horizontalHeader()
        header.sectionResized.connect(self.pinned.update)
        header.sectionMoved.connect(self.pinned.update)

    # ---- the pinned row --------------------------------------------------

    def pinned_active(self) -> bool:
        """Uncommitted changes is drawn pinned: it exists, and the table is scrolled."""
        model = self.model()
        return (model is not None and getattr(model, "has_uncommitted", False)
                and self.verticalScrollBar().value() > 0)

    def top_row(self) -> int:
        """The first row in view, below the pinned row while it shows."""
        return self.rowAt(self.pinned.height() if self.pinned_active() else 0)

    def set_pinned_line(self, color: QColor) -> None:
        self.pinned.line = color
        self.pinned.update()

    def _place_pinned(self, *_args: object) -> None:
        active = self.pinned_active()
        if active:
            self.pinned.setGeometry(0, 0, self.viewport().width(), self.rowHeight(0))
            self.pinned.raise_()
            self.pinned.update()
        self.pinned.setVisible(active)
        self.place_back_to_top()

    # ---- back to top -----------------------------------------------------

    BACK_TO_TOP_GAP = 6  # px between the pinned row and the button

    def back_to_top_wanted(self) -> bool:
        """The first commit is more than a screen above the view, or new commits
        arrived above it; never at the top."""
        bar = self.verticalScrollBar()
        return bar.value() > bar.pageStep() or (bar.value() > 0 and self.back_to_top.new_commits > 0)

    def place_back_to_top(self) -> None:
        button = self.back_to_top
        wanted = self.back_to_top_wanted()
        if wanted:
            size = button.sizeHint()
            top = (self.pinned.height() if self.pinned_active() else 0) + self.BACK_TO_TOP_GAP
            button.setGeometry((self.viewport().width() - size.width()) // 2, top,
                               size.width(), size.height())
            button.raise_()
        button.set_wanted(wanted)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.back_to_top and event.type() == QEvent.Type.Enter:
            self._set_hover_row(-1)  # over the button, no row is hovered
        return super().eventFilter(watched, event)

    def updateGeometries(self) -> None:
        super().updateGeometries()
        self._place_pinned()

    def scrollTo(self, index: QModelIndex | QPersistentModelIndex,
                 hint: QAbstractItemView.ScrollHint = QAbstractItemView.ScrollHint.EnsureVisible,
                 ) -> None:
        model = self.model()
        pinned = model is not None and getattr(model, "has_uncommitted", False)
        if pinned and index.isValid() and index.row() == 0:
            return  # always in view: at the top, or pinned there
        super().scrollTo(index, hint)
        bar = self.verticalScrollBar()
        if pinned and index.isValid() and 0 < bar.value() >= index.row():
            bar.setValue(index.row() - 1)  # it would be under the pinned row

    def keyPressEvent(self, event: QKeyEvent) -> None:
        super().keyPressEvent(event)
        if event.key() == Qt.Key.Key_Home and self.currentIndex().row() == 0:
            self.scrollToTop()  # Ctrl+Home goes to the top, not only to the pinned row

    def fit_row_height(self) -> None:
        """Every row is as tall as the first wants (a tree's uniformRowHeights)."""
        model = self.model()
        if model is not None and model.rowCount():
            hint = self._hint(model.index(0, SUBJECT_COLUMN))  # text: the graph has none
            self.verticalHeader().setDefaultSectionSize(hint.height())
        self._place_pinned()

    def width_hint(self, index: QModelIndex) -> int:
        """The width the view wants for one cell (as sizeHintForColumn, for one row)."""
        return self._hint(index).width()

    def _hint(self, index: QModelIndex):
        self.ensurePolished()
        option = QStyleOptionViewItem()
        self.initViewItemOption(option)
        return self.itemDelegateForIndex(index).sizeHint(option, index)

    def viewportEvent(self, event: QEvent) -> bool:
        kind = event.type()
        if kind in (QEvent.Type.HoverEnter, QEvent.Type.HoverMove):
            y = int(event.position().y())
            under_pin = self.pinned.isVisible() and y < self.pinned.height()
            button = self.back_to_top
            if button.isVisible() and button.geometry().contains(int(event.position().x()), y):
                self._set_hover_row(-1)
            else:
                self._set_hover_row(0 if under_pin else self.rowAt(y))
        elif kind == QEvent.Type.HoverLeave:
            self._set_hover_row(-1)
        return super().viewportEvent(event)

    def _set_hover_row(self, row: int) -> None:
        if row == self.hover_row:
            return
        old, self.hover_row = self.hover_row, row
        width = self.viewport().width()
        for r in (old, row):
            if r >= 0:
                self.viewport().update(QRect(0, self.rowViewportPosition(r), width, self.rowHeight(r)))
        if 0 in (old, row):
            self.pinned.update()


class _PinnedRow(QWidget):
    """Row 0, Uncommitted changes, drawn at the top of a scrolled ``_HistoryView``
    with the view's own delegate, selection and hover, so it looks just like the
    row; a line under it shows the commits passing beneath. Clicking it selects it."""

    def __init__(self, view: _HistoryView):
        super().__init__(view.viewport())
        self._view = view
        self.line = QColor()  # the theme's border_light, set by the panel
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        self.hide()

    def paintEvent(self, _event: QPaintEvent) -> None:
        view, model = self._view, self._view.model()
        if model is None or not model.rowCount():
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), view.palette().color(QPalette.ColorRole.Base))
        header = view.horizontalHeader()
        selection = view.selectionModel()
        for column in range(model.columnCount()):
            if header.isSectionHidden(column):
                continue
            index = model.index(0, column)
            option = QStyleOptionViewItem()
            view.initViewItemOption(option)
            option.rect = QRect(view.columnViewportPosition(column), 0,
                                view.columnWidth(column), self.height())
            if selection is not None and selection.isSelected(index):
                option.state |= QStyle.StateFlag.State_Selected
            view.itemDelegateForIndex(index).paint(painter, option, index)
        if self.line.isValid():
            painter.setPen(self.line)
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        view, model = self._view, self._view.model()
        if model is not None and event.button() == Qt.MouseButton.LeftButton:
            column = max(view.columnAt(int(event.position().x())), 0)
            view.setFocus(Qt.FocusReason.MouseFocusReason)
            view.setCurrentIndex(model.index(0, column))
        event.accept()

    def enterEvent(self, event: QEvent) -> None:
        self._view._set_hover_row(0)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        if self._view.hover_row == 0:
            self._view._set_hover_row(-1)
        super().leaveEvent(event)


_CELL_STATES = QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus


class _RowHoverDelegate(QStyledItemDelegate):
    """Marks every cell of the hovered row as hovered, as a tree view does, and
    paints a row's stripe (its BackgroundRole) under it: the stylesheet's item
    rule keeps Qt from painting that brush itself. Draws the graph column over the
    cell (``GraphPainter``), and a commit's ref label over its dot (``RefLabelPainter``)."""

    def __init__(self, view: _HistoryView):
        super().__init__(view)
        self._view = view

    def initStyleOption(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        super().initStyleOption(option, index)
        if index.row() == self._view.hover_row:
            option.state |= QStyle.StateFlag.State_MouseOver
        else:
            option.state &= ~QStyle.StateFlag.State_MouseOver

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex | QPersistentModelIndex) -> None:
        stripe = index.data(Qt.ItemDataRole.BackgroundRole)
        if isinstance(stripe, QBrush):
            painter.fillRect(option.rect, stripe)  # hover and selection are drawn over it
        model = index.model()
        column = index.column()
        if column != GRAPH_COLUMN or not isinstance(model, HistoryModel):
            super().paint(painter, option, index)
            return
        # A graph cell has no text: the style draws only its hover, selection
        # or focus. Asking it for a plain cell costs more than the graph.
        if (option.state & _CELL_STATES) or index.row() == self._view.hover_row:
            super().paint(painter, option, index)
        row = index.row()
        graph = self._view.graph
        if model.has_uncommitted and row == 0:
            graph.paint_uncommitted(painter, option.rect, model.graph_row(1))
        elif (commit := model.graph_row(row)) is not None:
            graph.paint(painter, option.rect, commit, joined_above=model.has_uncommitted and row == 1)
            labels = model.ref_labels(row)
            if labels:  # over the dot (RZG-86)
                refs = self._view.refs
                refs.set_font(option.font)
                refs.paint(painter, option.rect, labels, commit.color,
                           graph.node_x(commit.lane), model.striped(row))
