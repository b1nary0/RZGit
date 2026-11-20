"""Uncommitted changes and the commit table; the selected row is
``selection.commit``, None for Uncommitted changes (spec §5.4). No row is
selected while a stash is (the Stash panel's). Commits are striped, and
Uncommitted changes stays pinned at the top while the table scrolls, and well
down, a "Back to top" control floats below it (RZG-64). The first column is the
branch graph (RZG-58), with each commit's ref label over its dot (RZG-77, RZG-86),
as wide as the widest row in view. Double-click, Enter or the right-click menu
opens a commit in a window of its own, through ``actions`` (RZG-84). Ctrl+F filters
it down to the commits that match a query, searched in every commit by the session
(RZG-88)."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, QPoint, QRect, Qt
from PySide6.QtGui import (QBrush, QColor, QFontMetrics, QKeyEvent, QKeySequence, QMouseEvent,
                           QPainter, QPaintEvent, QPalette, QShortcut)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from rzgit.core.history_search import HistoryMatches
from rzgit.core.ref_labels import labels_by_commit
from rzgit.core.repo_session import HISTORY_PAGE
from rzgit.git.git_models import Commit, Stash
from rzgit.ui.app_actions import OPEN_COMMIT_TEXT
from rzgit.ui.panels.back_to_top_button import BackToTopButton
from rzgit.ui.panels.find_bar import INVALID, NO_RESULTS, FindBar
from rzgit.ui.panels.graph_painter import GRAPH_MAX_LANES, GraphPainter
from rzgit.ui.panels.find_query import FindQuery
from rzgit.ui.panels.history_model import (AUTHOR_COLUMN, DATE_FORMAT, GRAPH_COLUMN,
                                           MUTED_COLUMNS, SUBJECT_COLUMN, HistoryModel)
from rzgit.ui.panels.panel import Panel
from rzgit.ui.panels.ref_label_painter import RefLabelPainter
from rzgit.ui.theme.app_theme import AppTheme
from rzgit.ui.widgets.column_header import ColumnHeader


# Column widths fit the first page of rows, as they did before paging.
_MEASURED_ROWS = HISTORY_PAGE
SEARCHING = "Searching…"
_SHA_PREFIX = re.compile(r"[0-9a-fA-F]{4,40}")


def sha_prefix(text: str) -> str | None:
    """``text`` as a SHA prefix (4 to 40 hex digits, lowercased), or None."""
    return text.lower() if _SHA_PREFIX.fullmatch(text) else None


def filter_terms(query: FindQuery) -> tuple[re.Pattern[str], ...]:
    """The filter's query as the patterns a commit must all match (spec §5.4): each
    word on its own, in any order, or with .* the whole text as one. Raises
    ``ValueError`` for a regex that doesn't compile."""
    if query.regex:
        pattern = query.pattern()
        return () if pattern is None else (pattern,)
    words = dict.fromkeys(query.text.split())  # each once, in order
    return tuple(FindQuery(word, query.case, query.word).pattern() for word in words)


def highlight_pattern(terms: Sequence[re.Pattern[str]]) -> re.Pattern[str] | None:
    """Any of ``terms``, for the highlight: the longest first, so a word that holds
    another is filled whole."""
    if len(terms) < 2:
        return terms[0] if terms else None
    ordered = sorted(terms, key=lambda p: len(p.pattern), reverse=True)
    return re.compile("|".join(f"(?:{p.pattern})" for p in ordered), terms[0].flags)


def matches_message(matches: HistoryMatches) -> str:
    """The find bar's count for a finished search."""
    n = len(matches.commits)
    if not n:
        return NO_RESULTS
    if matches.capped:
        return f"{n:,}+ commits"
    return f"{n:,} commit{'' if n == 1 else 's'}"


class HistoryPanel(Panel):
    panel_id = "history"
    title = "History"

    _view: _HistoryView
    _labels: ColumnHeader
    _model: HistoryModel
    find_bar: FindBar

    def _setup(self) -> None:
        self._quiet = False  # True while a reset re-selects; selection isn't written
        self._new_commits = 0  # arrived above the view while scrolled down (Back to top)
        # The filter (RZG-88): the running search's id, the commit to keep selected if it
        # matches, whether the panel still picks the selection, and the commit to select
        # once it's loaded, after the filter closed.
        self._search_id: int | None = None
        self._kept: Commit | None = None
        self._auto_select = False
        self._restore_sha: str | None = None
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
        layout.setSpacing(0)
        layout.addWidget(self._view)
        self.find_bar = FindBar(keep=False)
        self.find_bar.hide()
        self.find_bar.changed.connect(self._search)
        self.find_bar.next_requested.connect(lambda: self.find_step(1))
        self.find_bar.previous_requested.connect(lambda: self.find_step(-1))
        self.find_bar.closed.connect(self._on_find_closed)
        layout.addWidget(self.find_bar)
        context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for keys, slot in ((QKeySequence.StandardKey.Find, self.open_find),
                           (QKeySequence("F3"), lambda: self.find_step(1)),
                           (QKeySequence("Shift+F3"), lambda: self.find_step(-1))):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(context)
            shortcut.activated.connect(slot)

        self._view.selectionModel().currentChanged.connect(self._sync_selection)
        # Open in New Window (RZG-84): double-click, Enter, or the right-click menu.
        self._view.doubleClicked.connect(lambda index: self._open(self._model.commit_at(index.row())))
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            shortcut = QShortcut(QKeySequence(key), self._view)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda: self._open(self._current_commit()))
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._show_menu)
        bar = self._view.verticalScrollBar()
        bar.valueChanged.connect(self._load_more_near_the_end)
        bar.rangeChanged.connect(self._load_more_near_the_end)
        bar.valueChanged.connect(self._forget_new_commits_at_the_top)
        self._model.rowsInserted.connect(self._fit_graph)
        self.set_header_actions([self.ctx.actions.topo_order])
        self.ctx.selection.commit_changed.connect(self._follow_selection)
        session = self.ctx.session
        session.history_changed.connect(self._render)
        session.status_changed.connect(self._show_uncommitted)
        session.repo_changed.connect(self._show_uncommitted)
        session.repo_changed.connect(lambda _repo: self.find_bar.close_bar())
        session.history_matches.connect(self._on_matches)
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
        if isinstance(self.ctx.selection.commit, Stash) or self._restore_sha is not None:
            return  # none, on purpose; or the filter's commit is on its way
        if not self._view.currentIndex().isValid() and self._model.has_uncommitted:
            self._select_row(0)

    def _select_row(self, row: int) -> None:
        """Select ``row`` without scrolling to it: the commit at the top decides the scroll."""
        auto = self._view.hasAutoScroll()
        self._view.setAutoScroll(False)
        self._view.setCurrentIndex(self._model.index(row, SUBJECT_COLUMN))
        self._view.setAutoScroll(auto)

    def _render(self, commits: tuple[Commit, ...]) -> None:
        if self._model.filtered:  # kept for when the filter goes; a reset searches again
            if not self._model.set_commits(commits):
                self._search()
            return
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
        if self._restore_sha is not None:
            self._select_restored()

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
        if self._model.filtered:
            return  # the matches are all there
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
        """Size the graph column to every loaded commit (RZG-92; ``set_fitted``:
        unless the user sized it): the most lanes, up to the cap, or the widest ref
        label and its ``+N`` where that reaches further (RZG-86). A label is centred
        on its dot: where its left half wouldn't fit, the lanes move right (the
        graph's inset). Not the rows in view, so scrolling never changes it."""
        view, model, graph, refs = self._view, self._model, self._graph, self._refs
        if model.filtered:
            return  # the column keeps the history's width while it's empty
        lanes, inset, shown = 1, 0.0, []
        if model.rowCount():
            view.ensurePolished()  # the stylesheet's list font
            refs.set_font(view.font())
            by_sha, commits = model.labels, model.commits
            first = model.rowCount() - len(commits)  # after Uncommitted changes
            for row, commit in enumerate(commits, first):
                labels = by_sha.get(commit.sha)
                if lanes >= GRAPH_MAX_LANES and not labels:
                    continue  # the lanes are at the cap: only labels can widen it
                graph_row = model.graph_row(row)
                if graph_row is None:
                    continue
                lanes = max(lanes, graph_row.width)
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
        self._view.find_match = QColor(theme.colors.diff_find_match)
        self._view.find_text = QColor(theme.colors.text)
        self._view.fit_row_height()

    def _sync_selection(self, *_args: object) -> None:
        # Selection emits only on change, so a reset that keeps the same
        # commit selected is silent. With no row (a stash is selected) History says nothing.
        if not self._quiet and self._view.currentIndex().isValid():
            self._auto_select = False  # chosen: the filter leaves the selection be
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

    # ---- Open in New Window (spec §5.4, RZG-84) -------------------------------

    def _open(self, commit: Commit | None) -> None:
        """Ask for ``commit`` in a window of its own; Uncommitted changes opens nothing."""
        if commit is not None:
            self.ctx.actions.request_open_commit(commit)

    def _show_menu(self, pos: QPoint) -> None:
        if self._view.pinned_active() and pos.y() < self._view.pinned.height():
            return  # Uncommitted changes, drawn over the row below it
        commit = self._model.commit_at(self._view.indexAt(pos).row())
        if commit is not None:
            self.menu_for(commit).exec(self._view.viewport().mapToGlobal(pos))

    def menu_for(self, commit: Commit) -> QMenu:
        """The right-click menu of ``commit``'s row."""
        menu = QMenu(self)
        menu.addAction(OPEN_COMMIT_TEXT).triggered.connect(lambda _=False: self._open(commit))
        return menu

    # ---- the filter (spec §5.4, RZG-88) ----------------------------------

    def open_find(self) -> None:
        """Ctrl+F: open the find bar and focus its field; the query it still holds
        filters again."""
        was_open = self.find_bar.isVisible()
        self.find_bar.open()
        if not was_open:
            self._search()

    def _search(self) -> None:
        """Filter by the bar's query: search every commit, or with an empty field
        show the whole History again."""
        bar = self.find_bar
        if not bar.isVisible():
            return
        query = bar.query()
        try:
            terms = filter_terms(query)
        except ValueError:
            self._search_id = self.ctx.session.search_history(None)
            self._enter_filter(None)
            self._set_filter(())
            bar.set_message(INVALID, warning=True, steps=False)
            return
        prefix = sha_prefix(query.text.strip())
        if not terms and prefix is None:
            self._search_id = self.ctx.session.search_history(None)
            self._exit_filter()
            bar.set_message("", steps=False)
            return
        self._enter_filter(highlight_pattern(terms))
        bar.set_message(SEARCHING, steps=bool(self._model.rowCount()))
        self._search_id = self.ctx.session.search_history(terms, prefix)

    def _enter_filter(self, pattern: re.Pattern[str] | None) -> None:
        """A new query: the commit selected now stays selected if it matches."""
        selected = self.ctx.selection.commit
        self._kept = None if isinstance(selected, Stash) else selected
        self._auto_select = True
        self._restore_sha = None
        self._model.set_highlight(pattern)
        self._view.back_to_top.set_new_commits(0)

    def _on_matches(self, matches: HistoryMatches) -> None:
        if matches.search_id != self._search_id or not self.find_bar.isVisible():
            return  # an older search's
        self._set_filter(matches.commits)
        if self._auto_select:
            row = self._model.row_of(self._kept.sha) if self._kept is not None else -1
            if row < 0 and matches.done and matches.commits:
                row = 0
            if row >= 0:
                self._auto_select = False
                self._go_to(row)
        if matches.done:
            self._auto_select = False
            self.find_bar.set_message(matches_message(matches), steps=bool(matches.commits))
        else:
            self.find_bar.set_message(SEARCHING, steps=bool(matches.commits))

    def _set_filter(self, commits: tuple[Commit, ...]) -> None:
        current = self._current_commit() if self._model.filtered else None
        self._quiet = True
        try:
            self._model.set_filter(commits)
            if current is not None and not self._view.currentIndex().isValid():
                row = self._model.row_of(current.sha)  # a reset: the same commit, if still there
                if row >= 0:
                    self._select_row(row)
        finally:
            self._quiet = False
        self._view.place_back_to_top()

    def find_step(self, delta: int) -> None:
        """Enter, F3 or ↓ (``delta`` 1), Shift+Enter, Shift+F3 or ↑ (-1): the next or
        previous commit in the filtered list, wrapping."""
        rows = self._model.rowCount()
        if not self.find_bar.isVisible() or not self._model.filtered or not rows:
            return
        if self.find_bar.flush():
            return  # a waiting keystroke: it searches first
        current = self._view.currentIndex()
        if current.isValid():
            row = (current.row() + delta) % rows
        else:
            row = 0 if delta > 0 else rows - 1
        self._go_to(row)

    def _go_to(self, row: int) -> None:
        """Select ``row`` (written to the selection) and scroll it into view."""
        self._auto_select = False
        index = self._model.index(row, SUBJECT_COLUMN)
        self._view.setCurrentIndex(index)
        self._view.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)

    def _on_find_closed(self) -> None:
        self._search_id = self.ctx.session.search_history(None)
        # Focus first: a view focused with no current row makes its first row current,
        # which after the reset would be Uncommitted changes.
        self._quiet = True
        try:
            self._view.setFocus(Qt.FocusReason.OtherFocusReason)
        finally:
            self._quiet = False
        self._exit_filter()

    def _exit_filter(self) -> None:
        """The whole History again, with ``selection.commit`` selected and in view."""
        self._auto_select = False
        self._model.set_highlight(None)
        if not self._model.filtered:
            return
        self._quiet = True
        try:
            self._model.set_filter(None)
        finally:
            self._quiet = False
        self._view.fit_row_height()
        self._fit_graph()
        commit = self.ctx.selection.commit
        if isinstance(commit, Stash):
            return  # no row, on purpose
        if commit is None:
            self._quiet = True
            try:
                self._select_uncommitted_if_none()
            finally:
                self._quiet = False
            self._view.scrollToTop()
            return
        self._restore_sha = commit.sha
        self._select_restored()

    def _select_restored(self) -> None:
        """Select the commit the filter had selected, once it's loaded: ask for
        pages until it is."""
        sha = self._restore_sha
        row = self._model.row_of(sha) if sha is not None else -1
        if row >= 0:
            self._restore_sha = None
            index = self._model.index(row, SUBJECT_COLUMN)
            self._quiet = True
            try:
                self._view.setCurrentIndex(index)
            finally:
                self._quiet = False
            self._view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        elif self.ctx.session.history_complete:
            self._restore_sha = None  # it's gone
        else:
            self.ctx.session.load_more_history()

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
        self.find_match = QColor()  # the filter's highlight fill and its text (RZG-88)
        self.find_text = QColor()
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
        if getattr(self.model(), "filtered", False):
            return False  # off while History is filtered (RZG-88)
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
            pattern = model.highlight if isinstance(model, HistoryModel) else None
            if pattern is not None and column in (SUBJECT_COLUMN, AUTHOR_COLUMN):
                self._paint_matches(painter, option, index, pattern)
            return
        # A graph cell has no text: the style draws only its hover, selection
        # or focus. Asking it for a plain cell costs more than the graph.
        if (option.state & _CELL_STATES) or index.row() == self._view.hover_row:
            super().paint(painter, option, index)
        row = index.row()
        graph = self._view.graph
        if model.has_uncommitted and row == 0:
            graph.paint_uncommitted(painter, option.rect, model.graph_row(1))
        elif model.filtered:  # no graph (RZG-88): the label at the first lane
            labels = model.ref_labels(row)
            if labels:
                refs = self._view.refs
                refs.set_font(option.font)
                refs.paint(painter, option.rect, labels, 0, graph.node_x(0), model.striped(row))
        elif (commit := model.graph_row(row)) is not None:
            graph.paint(painter, option.rect, commit, joined_above=model.has_uncommitted and row == 1)
            labels = model.ref_labels(row)
            if labels:  # over the dot (RZG-86)
                refs = self._view.refs
                refs.set_font(option.font)
                refs.paint(painter, option.rect, labels, commit.color,
                           graph.node_x(commit.lane), model.striped(row))

    def _paint_matches(self, painter: QPainter, option: QStyleOptionViewItem,
                       index: QModelIndex | QPersistentModelIndex,
                       pattern: re.Pattern[str]) -> None:
        """Fill the filter's matches in the cell's text (RZG-88): over what the style
        drew, then the text again over the fill, clipped to it."""
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return
        spans = [(m.start(), m.end()) for m in pattern.finditer(text) if m.end() > m.start()]
        if not spans:
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        view = self._view
        style = view.style()
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, view)
        margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, None, view) + 1
        rect = rect.adjusted(margin, 0, -margin, 0)
        metrics = QFontMetrics(opt.font)
        shown = metrics.elidedText(text, opt.textElideMode, rect.width())
        visible = len(text) if shown == text else max(len(shown) - 1, 0)  # before the "…"
        top = rect.top() + (rect.height() - metrics.height()) // 2
        painter.save()
        painter.setFont(opt.font)
        painter.setPen(view.find_text)
        for start, end in spans:
            end = min(end, visible)
            if start >= end:
                continue
            x0 = rect.left() + metrics.horizontalAdvance(text[:start])
            x1 = rect.left() + metrics.horizontalAdvance(text[:end])
            fill = QRect(x0, top, x1 - x0, metrics.height()).intersected(rect)
            painter.setClipRect(fill)
            painter.fillRect(fill, view.find_match)
            painter.drawText(rect, int(opt.displayAlignment), shown)
        painter.restore()
