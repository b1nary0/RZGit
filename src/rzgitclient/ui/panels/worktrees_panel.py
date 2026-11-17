"""The repository's worktrees and their state; open one (spec §5.4).

Below Branches, from the window's ``ctx.worktrees`` reader, which it attaches
to while it exists. This window's worktree is in bold. Collapsed while there
is only one worktree. The panel never runs git or opens a window: it asks
``actions.request_open_worktree`` (or ``request_open_worktree_here``).
"""

from __future__ import annotations

import os

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from rzgitclient.git.git_models import Worktree, WorktreeState
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.app_theme import AppTheme
from rzgitclient.ui.theme.style_roles import Role, Surface, set_role
from rzgitclient.ui.widgets.column_header import ColumnHeader

NO_WORKTREES = "No worktrees"
CANT_LIST = "Can't list worktrees: {error}"
COLUMNS = ("Name", "Branch", "State", "↑↓", "Path")
NAME, BRANCH, STATE, SYNC, PATH = range(len(COLUMNS))
_MUTED = (BRANCH, SYNC, PATH)
HIDDEN_AT_FIRST = ("Branch", "Path")  # the sidebar is narrow; both are in the tooltip
OPEN_NEW = "Open in New Window"
OPEN_HERE = "Open in This Window"
_PATH_ROLE = Qt.ItemDataRole.UserRole
THIS_WINDOW = " (this window)"
PATH_MAX_CHARS = 48  # the path column fits its text up to about this; it elides in the middle


def short_path(path: str, home: str | None = None) -> str:
    """``path`` with the home folder as ``~``."""
    home = (home if home is not None else os.path.expanduser("~")).rstrip("/")
    if home and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home):]
    return path


def branch_text(worktree: Worktree) -> str:
    if worktree.branch is not None:
        return worktree.branch
    if worktree.head is None:
        return "(no commits)"
    return f"detached at {worktree.head[:8]}"


def state_text(worktree: Worktree, state: WorktreeState | None) -> str:
    """"clean", "3 changed files", "missing folder", "…" until read; " · locked"."""
    if worktree.missing:
        text = "missing folder"
    elif state is None:
        text = "…"
    elif state.changed == 0:
        text = "clean"
    else:
        text = f"{state.changed:,} changed file" + ("" if state.changed == 1 else "s")
    return text + (" · locked" if worktree.locked else "")


def sync_text(state: WorktreeState | None) -> str:
    """``↑2 ↓1``, leaving out a count of 0: nothing when even or without an upstream."""
    if state is None:
        return ""
    parts = []
    if state.ahead:
        parts.append(f"↑{state.ahead}")
    if state.behind:
        parts.append(f"↓{state.behind}")
    return " ".join(parts)


def sync_tooltip(state: WorktreeState | None) -> str:
    if state is None:
        return ""
    if state.upstream is None:
        return "no upstream"
    if state.ahead is None:
        return f"{state.upstream} is gone"
    if not state.ahead and not state.behind:
        return f"up to date with {state.upstream}"
    parts = []
    if state.ahead:
        parts.append(f"{state.ahead} ahead of")
    if state.behind:
        parts.append(f"{state.behind} behind")
    return f"{', '.join(parts)} {state.upstream}"


class WorktreesPanel(Panel):
    panel_id = "worktrees"
    title = "Worktrees"
    surface = Surface.SIDEBAR

    # The list is built the first time there is something to choose from (or an
    # error to show): most repositories have one worktree, and it costs startup time.
    _tree: QTreeWidget | None
    _labels: ColumnHeader | None
    _placeholder: QLabel
    _layout: QVBoxLayout
    open_action: QAction
    refresh_action: QAction

    def _setup(self) -> None:
        self._tree = self._labels = None
        self._placeholder = QLabel(NO_WORKTREES)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        set_role(self._placeholder, Role.PLACEHOLDER)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._layout.addWidget(self._placeholder, 1)

        # The header menu (spec §5.1). Enter in the list opens too.
        self.open_action = QAction("&Open", self)
        self.open_action.setShortcuts([QKeySequence(Qt.Key.Key_Return),
                                       QKeySequence(Qt.Key.Key_Enter)])
        self.open_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.open_action.triggered.connect(lambda: self._open(self._selected_item()))
        self.refresh_action = QAction("&Refresh", self)
        self.refresh_action.triggered.connect(self._refresh)
        self.set_header_actions((self.open_action, self.refresh_action))

        self._muted = QColor(self.ctx.theme.current.colors.text_muted)
        if (reader := self.ctx.worktrees) is not None:
            reader.worktrees_changed.connect(self._render)
            reader.state_changed.connect(self._render_state)
            reader.error_occurred.connect(self._render)
            # Read only while this panel exists (spec §4.4).
            self.destroyed.connect(lambda _obj=None: reader.detach())  # not self: it's gone
            reader.attach()
        self.ctx.session.repo_changed.connect(self._render)
        self._render()

    # ---- rendering --------------------------------------------------------

    @property
    def list_built(self) -> bool:
        return self._tree is not None

    def _build_list(self) -> QTreeWidget:
        tree = self._tree = QTreeWidget()
        labels = self._labels = ColumnHeader(self.panel_id, self.ctx.columns,
                                             self.ctx.theme.current,
                                             hidden_by_default=HIDDEN_AT_FIRST)
        tree.setHeader(labels)
        tree.setColumnCount(len(COLUMNS))
        tree.setHeaderLabels(COLUMNS)
        tree.setRootIsDecorated(False)
        tree.setUniformRowHeights(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        tree.itemDoubleClicked.connect(lambda item, _column: self._open(item))
        tree.itemSelectionChanged.connect(self._update_actions)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._show_menu)
        tree.addAction(self.open_action)
        self._layout.insertWidget(0, tree, 1)
        return tree

    @property
    def worktrees(self) -> tuple[Worktree, ...]:
        reader = self.ctx.worktrees
        return reader.worktrees if reader is not None else ()

    def _owner_path(self) -> str | None:
        repo = self.ctx.session.repo
        return str(repo.root) if repo is not None else None

    def _render(self, _value: object = None) -> None:
        worktrees = self.worktrees
        reader = self.ctx.worktrees
        error = reader.error if reader is not None else None
        self._placeholder.setText(CANT_LIST.format(error=error) if error else NO_WORKTREES)
        self.set_header(f"{self.title} ({len(worktrees):,})" if worktrees else self.title)
        self.set_collapsed(len(worktrees) <= 1 and not error)  # nothing to choose from
        tree = self._tree
        if tree is None:
            if self.collapsed:
                self._update_actions()
                return
            tree = self._build_list()
        selected = self.selected_path()
        owner = self._owner_path()
        blocked = tree.blockSignals(True)
        try:
            tree.clear()
            for worktree in worktrees:
                item = QTreeWidgetItem(
                    [worktree.name, branch_text(worktree), "", "", short_path(worktree.path)])
                item.setData(NAME, _PATH_ROLE, worktree.path)
                if worktree.path == owner:
                    font = item.font(NAME)
                    font.setBold(True)
                    item.setFont(NAME, font)
                for column in _MUTED:
                    item.setForeground(column, self._muted)
                tree.addTopLevelItem(item)
                self._fill_state(item, worktree)
                if worktree.path == selected:
                    item.setSelected(True)
                    tree.setCurrentItem(item)
        finally:
            tree.blockSignals(blocked)
        self._fit_columns()
        tree.setVisible(bool(worktrees))
        self._placeholder.setVisible(not worktrees)
        self._update_actions()

    def _fill_state(self, item: QTreeWidgetItem, worktree: Worktree) -> None:
        reader = self.ctx.worktrees
        state = reader.state(worktree.path) if reader is not None else None
        item.setText(STATE, state_text(worktree, state))
        item.setText(SYNC, sync_text(state))
        tip = f"{worktree.path}{THIS_WINDOW if worktree.path == self._owner_path() else ''}"
        tip += f"\n{branch_text(worktree)}"
        if worktree.locked:
            tip += "\nlocked" + (f": {worktree.lock_reason}" if worktree.lock_reason else "")
        for column in (NAME, BRANCH, STATE, PATH):
            item.setToolTip(column, tip)
        item.setToolTip(SYNC, sync_tooltip(state) or tip)

    def _render_state(self, path: str, _state: object) -> None:
        if self._tree is None or self._labels is None:
            return
        for row, worktree in enumerate(self.worktrees):
            if worktree.path == path and (item := self._tree.topLevelItem(row)) is not None:
                self._fill_state(item, worktree)
                self._labels.fit(STATE)
                self._labels.fit(SYNC)
                return

    def _fit_columns(self) -> None:
        """The others fit their text; the name takes the rest (spec §5.2). A long
        path is capped, so the name keeps room."""
        assert self._tree is not None and self._labels is not None
        for column in (BRANCH, STATE, SYNC):
            self._labels.fit(column)
        cap = self._tree.fontMetrics().averageCharWidth() * PATH_MAX_CHARS
        natural = max(self._tree.sizeHintForColumn(PATH), self._labels.sectionSizeHint(PATH))
        self._labels.set_fitted(PATH, min(natural, cap))

    def apply_theme(self, theme: AppTheme) -> None:
        self._muted = QColor(theme.colors.text_muted)
        if self._tree is None or self._labels is None:
            return  # the list takes the theme when it's built
        self._labels.set_theme(theme)
        for row in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(row)
            for column in _MUTED:
                item.setForeground(column, self._muted)

    # ---- opening ------------------------------------------------------------

    def _selected_item(self) -> QTreeWidgetItem | None:
        items = self._tree.selectedItems() if self._tree is not None else []
        return items[0] if items else None

    def selected_path(self) -> str | None:
        item = self._selected_item()
        return item.data(NAME, _PATH_ROLE) if item is not None else None

    def worktree_at(self, item: QTreeWidgetItem | None) -> Worktree | None:
        if item is None:
            return None
        path = item.data(NAME, _PATH_ROLE)
        return next((w for w in self.worktrees if w.path == path), None)

    def _update_actions(self) -> None:
        worktree = self.worktree_at(self._selected_item())
        self.open_action.setEnabled(worktree is not None and not worktree.missing)
        self.refresh_action.setEnabled(self.ctx.worktrees is not None)

    def _open(self, item: QTreeWidgetItem | None) -> None:
        worktree = self.worktree_at(item)
        if worktree is not None and not worktree.missing:
            self.ctx.actions.request_open_worktree(worktree.path)

    def _show_menu(self, pos: QPoint) -> None:
        assert self._tree is not None  # it sent the request
        worktree = self.worktree_at(self._tree.itemAt(pos))
        if worktree is not None:
            self.menu_for(worktree).exec(self._tree.viewport().mapToGlobal(pos))

    def menu_for(self, worktree: Worktree) -> QMenu:
        """The right-click menu of ``worktree``."""
        menu = QMenu(self)
        actions = self.ctx.actions
        new = menu.addAction(OPEN_NEW)
        new.setEnabled(not worktree.missing)
        new.triggered.connect(lambda _=False: actions.request_open_worktree(worktree.path))
        here = menu.addAction(OPEN_HERE)
        here.setEnabled(not worktree.missing and worktree.path != self._owner_path())
        here.triggered.connect(lambda _=False: actions.request_open_worktree_here(worktree.path))
        return menu

    def _refresh(self) -> None:
        if self.ctx.worktrees is not None:
            self.ctx.worktrees.refresh()
