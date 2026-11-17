"""The branch list, and the place to work with branches (spec §5.4).

Sort By (RZG-43), its header menu: by name (git's order) or by last commit.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QMenu, QVBoxLayout

from rzgitclient.core.sort_options import SortOption, SortOptions
from rzgitclient.git.git_models import Branch
from rzgitclient.ui.app_actions import BranchOp, WorktreeOp
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.widgets.sort_menu import SortMenu

_NAME = Qt.ItemDataRole.UserRole  # a branch row's name; None for "(no commits)"
_CURRENT = " (current branch)"


def _last_commit(branch: Branch) -> float:
    """Its tip's date as a number; one with none sorts as the oldest."""
    return branch.date.timestamp() if branch.date is not None else float("-inf")


# Sort By (spec §5.4): git's order is by name.
SORTS: SortOptions[Branch] = SortOptions((
    SortOption("name", "Name"),
    SortOption("last_commit", "Last Commit", _last_commit, descending=True),
))


class BranchesPanel(Panel):
    panel_id = "branches"
    title = "Branches"

    _list: QListWidget
    sort: SortMenu

    def _setup(self) -> None:
        self.sort = SortMenu(self.panel_id, SORTS, self.ctx.sorts, self)
        self.sort.order_changed.connect(self._on_sorted)
        self.set_header_actions([self.sort.menuAction()])
        self._list = QListWidget()
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
        self._render()

    def _render(self, _value: object = None) -> None:
        # Depends on both the branches and HEAD, so it reads the session.
        branches, head = self.ctx.session.branches, self.ctx.session.head
        current = self._list.currentItem()
        keep = current.data(_NAME) if current else None

        blocked = self._list.blockSignals(True)
        try:
            self._list.clear()
            for b in self.sort.apply(branches):
                item = QListWidgetItem(b.name)
                item.setData(_NAME, b.name)
                if b.is_current:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self._list.addItem(item)
                if b.name == keep:
                    self._list.setCurrentItem(item)
            if not branches and head:
                # Fresh repo: the branch exists in HEAD but has no ref yet.
                self._list.addItem(QListWidgetItem(f"{head} (no commits)"))
        finally:
            self._list.blockSignals(blocked)

    def _on_sorted(self) -> None:
        self._render()
        if (current := self._list.currentItem()) is not None:
            self._list.scrollToItem(current)

    # ---- operations (spec §5.4): asked of AppActions, never run here ------

    def _is_current(self, name: str) -> bool:
        return any(b.name == name and b.is_current for b in self.ctx.session.branches)

    def _on_double_click(self, item: QListWidgetItem) -> None:
        name = item.data(_NAME)
        if name is not None and not self._is_current(name):
            self.ctx.actions.request_branch(BranchOp.SWITCH, name)

    def _show_menu(self, pos: QPoint) -> None:
        item = self._list.itemAt(pos)
        name = item.data(_NAME) if item is not None else None
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
