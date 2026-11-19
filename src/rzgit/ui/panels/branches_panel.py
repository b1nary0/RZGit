"""The branch list, and the place to work with branches (spec §5.4).

Sort By (RZG-43), its header menu: by name (git's order) or by last commit.
Group By (RZG-53): the name's prefix ("feature/") or merged, under headings
that can't be selected (``GroupedTree``). The current branch, or a detached HEAD,
is the first row in every order, above the headings (RZG-99).
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMenu, QTreeWidgetItem, QVBoxLayout

from rzgit.core.sort_options import GroupOption, SortOption, SortOptions
from rzgit.git.git_models import Branch
from rzgit.ui.app_actions import BranchOp, WorktreeOp
from rzgit.ui.panels.panel import Panel
from rzgit.ui.theme.app_theme import AppTheme
from rzgit.ui.widgets.grouped_tree import GroupedTree
from rzgit.ui.widgets.sort_menu import SortMenu

_NAME = Qt.ItemDataRole.UserRole  # a branch row's name; None for "(no commits)" and HEAD
_CURRENT = " (current branch)"
OTHER = "Other"  # Group By Prefix: the branches without one
NOT_MERGED, MERGED = "Not merged", "Merged"


def _last_commit(branch: Branch) -> float:
    """Its tip's date as a number; one with none sorts as the oldest."""
    return branch.date.timestamp() if branch.date is not None else float("-inf")


def prefix(branch: Branch) -> str:
    """Its name up to the first "/", with it ("feature/"); "Other" without one."""
    head, slash, _rest = branch.name.partition("/")
    return f"{head}/" if slash and head else OTHER


def detached_text(sha: str) -> str:
    return f"HEAD (detached at {sha[:8]})"


# Sort By (spec §5.4): git's order is by name. Group By: prefix or merged. The
# current branch is pinned first, above the groups (RZG-99).
SORTS: SortOptions[Branch] = SortOptions((
    SortOption("name", "Name"),
    SortOption("last_commit", "Last Commit", _last_commit, descending=True),
), first=(lambda b: b.is_current,), groups=(
    GroupOption("prefix", "Prefix", prefix, last=(OTHER,)),
    GroupOption("merged", "Merged", lambda b: MERGED if b.merged else NOT_MERGED,
                order=(NOT_MERGED, MERGED)),
))


class BranchesPanel(Panel):
    panel_id = "branches"
    title = "Branches"

    _list: GroupedTree
    sort: SortMenu

    def _setup(self) -> None:
        self.sort = SortMenu(self.panel_id, SORTS, self.ctx.sorts, self)
        self.sort.order_changed.connect(self._on_sorted)
        self.set_header_actions(self.sort.header_actions())
        self._list = GroupedTree(self.ctx.theme.current)
        self._list.setColumnCount(1)
        self._list.setHeaderHidden(True)
        self._list.setRootIsDecorated(False)
        self._list.setUniformRowHeights(True)
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_menu)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._list)

        session = self.ctx.session
        session.branches_changed.connect(self._render)
        session.head_changed.connect(self._render)
        session.head_sha_changed.connect(self._on_head_sha)
        session.repo_changed.connect(lambda _repo: self._list.forget_folded())
        self._render()

    def _render(self, _value: object = None) -> None:
        # Depends on both the branches and HEAD, so it reads the session.
        branches, head = self.ctx.session.branches, self.ctx.session.head
        current = self._list.currentItem()
        keep = current.data(0, _NAME) if current else None

        def bold(item: QTreeWidgetItem) -> QTreeWidgetItem:
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            return item

        def item_of(b: Branch) -> QTreeWidgetItem:
            item = QTreeWidgetItem([b.name])
            item.setData(0, _NAME, b.name)
            return bold(item) if b.is_current else item

        # Pinned first (RZG-99): a detached HEAD, else the current branch (in `top`).
        sha = self.ctx.session.head_sha
        items = [bold(QTreeWidgetItem([detached_text(sha)]))] if head is None and sha else []
        groups = self.sort.grouped(branches)
        if groups is None:
            items += [item_of(b) for b in self.sort.apply(branches)]
        else:
            items += [item_of(b) for b in self.sort.top(branches)]
            for group in groups:
                heading = self._list.heading(group)
                heading.addChildren([item_of(b) for b in group.items])
                items.append(heading)
        blocked = self._list.blockSignals(True)
        try:
            self._list.clear()
            self._list.setRootIsDecorated(groups is not None)
            self._list.addTopLevelItems(items)
            if not branches and head:
                # Fresh repo: the branch exists in HEAD but has no ref yet.
                self._list.addTopLevelItem(QTreeWidgetItem([f"{head} (no commits)"]))
            self._list.fold_headings()
            for item in self._list.rows():
                if keep is not None and item.data(0, _NAME) == keep:
                    self._list.setCurrentItem(item)
        finally:
            self._list.blockSignals(blocked)

    def _on_head_sha(self, _sha: object) -> None:
        if self.ctx.session.head is None:  # the detached row shows the sha
            self._render()

    def _on_sorted(self) -> None:
        self._render()
        if (current := self._list.currentItem()) is not None:
            self._list.scrollToItem(current)

    # ---- operations (spec §5.4): asked of AppActions, never run here ------

    def _is_current(self, name: str) -> bool:
        return any(b.name == name and b.is_current for b in self.ctx.session.branches)

    def apply_theme(self, theme: AppTheme) -> None:
        self._list.set_theme(theme)

    def _on_double_click(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        name = item.data(0, _NAME)
        if name is not None and not self._is_current(name):
            self.ctx.actions.request_branch(BranchOp.SWITCH, name)

    def _show_menu(self, pos: QPoint) -> None:
        item = self._list.itemAt(pos)
        name = item.data(0, _NAME) if item is not None else None
        if name is not None:
            self.menu_for(name).exec(self._list.viewport().mapToGlobal(pos))

    def menu_for(self, name: str) -> QMenu:
        """The right-click menu of branch ``name``."""
        menu = QMenu(self)
        is_current = self._is_current(name)
        current = _menu_text(self.ctx.session.head or "HEAD")
        label = _menu_text(name)
        request = self.ctx.actions.request_branch

        def add(target: QMenu, text: str, op: BranchOp, not_current: bool = False) -> None:
            blocked = not_current and is_current
            action = target.addAction(text + (_CURRENT if blocked else ""))
            action.setEnabled(not blocked)
            action.triggered.connect(lambda _=False: request(op, name))

        add(menu, f"Switch to {label}", BranchOp.SWITCH, not_current=True)
        add(menu, f"New Branch from {label}…", BranchOp.NEW)
        worktree = menu.addAction(f"New Worktree from {label}…")
        worktree.triggered.connect(
            lambda _=False: self.ctx.actions.request_worktree(WorktreeOp.NEW, name))
        add(menu, f"Rename {label}…", BranchOp.RENAME)
        add(menu, f"Delete {label}…", BranchOp.DELETE, not_current=True)
        integrate = menu.addMenu("Merge / Rebase")
        add(integrate, f"Merge {label} into {current}…", BranchOp.MERGE, not_current=True)
        add(integrate, f"Rebase {current} onto {label}…", BranchOp.REBASE, not_current=True)
        menu.addSeparator()
        copy = menu.addAction("Copy Name")
        copy.triggered.connect(lambda _=False: QGuiApplication.clipboard().setText(name))
        return menu


def _menu_text(name: str) -> str:
    """``name`` as menu text: a branch may hold "&", which Qt reads as a shortcut mark."""
    return name.replace("&", "&&")
