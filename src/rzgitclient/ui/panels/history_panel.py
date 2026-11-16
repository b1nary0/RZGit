"""The commit table; the selected row is ``selection.commit`` (spec §5.4)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, QPoint, QRect, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
)

from rzgitclient.core.repo_session import HISTORY_PAGE
from rzgitclient.git.git_models import Commit
from rzgitclient.ui.panels.history_model import MUTED_COLUMNS, HistoryModel
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.app_theme import AppTheme


# Column widths fit the first page of rows, as they did before paging.
_MEASURED_ROWS = HISTORY_PAGE


class HistoryPanel(Panel):
    panel_id = "history"
    title = "History"

    _view: _HistoryView
    _model: HistoryModel

    def _setup(self) -> None:
        self._quiet = False  # True while a reset re-selects; selection isn't written
        self._model = HistoryModel(self.ctx.theme.current)
        self._view = _HistoryView()
        self._view.setModel(self._model)
        header = self._view.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        self._view.selectionModel().currentChanged.connect(self._sync_selection)
        bar = self._view.verticalScrollBar()
        bar.valueChanged.connect(self._load_more_near_the_end)
        bar.rangeChanged.connect(self._load_more_near_the_end)
        self.ctx.session.history_changed.connect(self._render)
        self._render(self.ctx.session.history)

    def _render(self, commits: tuple[Commit, ...]) -> None:
        keep, top = self._current_commit(), self._top_commit()
        was_empty = self._model.rowCount() == 0
        self._quiet = True
        try:
            appended = self._model.set_commits(commits)
            if not appended:  # reset: same selection, same commit at the top
                self._restore(keep, top)
        finally:
            self._quiet = False
        if was_empty or not appended:
            self._view.fit_row_height()
            self._fit_columns()
        self._sync_selection()

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
        rows = range(min(len(self._model.commits), _MEASURED_ROWS))
        header = self._view.horizontalHeader()
        for col in MUTED_COLUMNS:
            width = 0  # no rows: the header's text decides
            if rows:
                metrics = QFontMetrics(self._model.font(col) or self._view.font())
                widest = max(rows, key=lambda r: metrics.horizontalAdvance(self._model.text(r, col)))
                width = self._view.width_hint(self._model.index(widest, col))
            header.resizeSection(col, max(width, header.sectionSizeHint(col)))

    def _restore(self, keep: Commit | None, top: Commit | None) -> None:
        row = self._model.row_of(keep.sha) if keep is not None else -1
        if row >= 0:
            # Without scrolling to it: the commit at the top decides the scroll.
            auto = self._view.hasAutoScroll()
            self._view.setAutoScroll(False)
            self._view.setCurrentIndex(self._model.index(row, 0))
            self._view.setAutoScroll(auto)
        row = self._model.row_of(top.sha) if top is not None else -1
        if row >= 0:
            self._view.scrollTo(
                self._model.index(row, 0), QAbstractItemView.ScrollHint.PositionAtTop
            )
        else:  # was at the top: stay there, so new commits show
            self._view.scrollToTop()

    def apply_theme(self, theme: AppTheme) -> None:
        self._model.set_theme(theme)
        self._view.fit_row_height()

    def _sync_selection(self, *_args: object) -> None:
        # Selection emits only on change, so a reset that keeps the same
        # commit selected is silent.
        if not self._quiet:
            self.ctx.selection.commit = self._current_commit()

    def _current_commit(self) -> Commit | None:
        return self._model.commit_at(self._view.currentIndex().row())

    def _top_commit(self) -> Commit | None:
        """The commit at the top of the view, or None when scrolled to the top."""
        if self._view.verticalScrollBar().value() == 0:
            return None
        return self._model.commit_at(self._view.indexAt(QPoint(0, 0)).row())


class _HistoryView(QTableView):
    """A table that looks like the app's tree views (spec §5.4).

    A QTreeView keeps a layout entry per row and redoes all of them on every
    insert or reset (230 ms with 100k rows); a table with fixed row heights
    doesn't. These settings give it the tree's look: no grid or row numbers,
    whole-row selection and hover, a left-aligned header that isn't clickable.
    """

    def __init__(self) -> None:
        super().__init__()
        self.hover_row = -1
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
        columns = self.horizontalHeader()
        columns.setHighlightSections(False)
        columns.setSectionsClickable(False)
        columns.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setItemDelegate(_RowHoverDelegate(self))

    def fit_row_height(self) -> None:
        """Every row is as tall as the first wants (a tree's uniformRowHeights)."""
        model = self.model()
        if model is not None and model.rowCount():
            self.verticalHeader().setDefaultSectionSize(self._hint(model.index(0, 0)).height())

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
            self._set_hover_row(self.rowAt(int(event.position().y())))
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


class _RowHoverDelegate(QStyledItemDelegate):
    """Marks every cell of the hovered row as hovered, as a tree view does."""

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
