"""The stashes, and the place to apply, pop and drop them (spec §5.4).

Selecting a stash sets ``selection.commit`` to it, so Commit Details, Commit
Files and Diff show it as they show a commit. The panel never runs git or shows
a dialog: it asks ``actions.request_stash``. Sort By (RZG-43): age (git's
order), message or branch. Group By (RZG-53): branch, under headings that
can't be selected (``GroupedTree``).
"""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QTreeWidgetItem,
    QVBoxLayout,
)

from rzgit.core.sort_options import GroupOption, SortOption, SortOptions
from rzgit.git.git_models import Commit, Stash
from rzgit.ui.app_actions import StashOp
from rzgit.ui.panels.panel import Panel
from rzgit.ui.theme.app_theme import AppTheme
from rzgit.ui.theme.style_roles import Role, set_role
from rzgit.ui.widgets.column_header import ColumnHeader
from rzgit.ui.widgets.grouped_tree import GroupedTree
from rzgit.ui.widgets.sort_menu import SortMenu

NO_STASHES = "No stashes"
NOT_CLEAN = " (commit or stash your changes first)"
COLUMNS = ("Message", "Branch", "Age")
_MUTED = (1, 2)  # branch and age: muted, as History's author, date and hash
_AGE_COLUMN = 2
_SHA = Qt.ItemDataRole.UserRole
_MINUTE_MS = 60_000

# Sort By (spec §5.4): git's order is newest first; the others then by age.
# Group By: the branch, groups by name.
SORTS: SortOptions[Stash] = SortOptions((
    SortOption("age", "Age", column="Age"),
    SortOption("message", "Message", lambda s: s.message.casefold(), column="Message"),
    SortOption("branch", "Branch", lambda s: s.branch.casefold(), column="Branch"),
), groups=(
    GroupOption("branch", "Branch", lambda s: s.branch),
))


def age(date: datetime, now: datetime | None = None) -> str:
    """How long ago ``date`` was, roughly: "now", "5 min", "3 h", "2 days", "3 weeks",
    "4 months", "2 years"."""
    now = now or datetime.now(timezone.utc)
    seconds = max(0, int((now - date).total_seconds()))
    minutes, hours, days = seconds // 60, seconds // 3600, seconds // 86400
    if minutes < 1:
        return "now"
    if hours < 1:
        return f"{minutes} min"
    if days < 1:
        return f"{hours} h"
    for size, unit in ((365, "year"), (30, "month"), (7, "week"), (1, "day")):
        if days >= size:
            count = days // size
            return f"{count} {unit}" + ("" if count == 1 else "s")
    raise AssertionError("unreachable")


class StashPanel(Panel):
    panel_id = "stash"
    title = "Stashes"

    _tree: GroupedTree
    _labels: ColumnHeader
    _placeholder: QLabel
    _stash_button: QToolButton
    _ages: QTimer
    sort: SortMenu

    def _setup(self) -> None:
        self.sort = SortMenu(self.panel_id, SORTS, self.ctx.sorts, self)
        self.sort.order_changed.connect(self._on_sorted)
        self.set_header_actions(self.sort.header_actions())
        self._stash_button = QToolButton()
        self._stash_button.setDefaultAction(self.ctx.actions.stash_changes)
        self._stash_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        bar.addWidget(self._stash_button)
        bar.addStretch(1)

        self._tree = GroupedTree(self.ctx.theme.current)
        self._labels = ColumnHeader(self.panel_id, self.ctx.columns, self.ctx.theme.current)
        self._tree.setHeader(self._labels)
        self._tree.setColumnCount(len(COLUMNS))
        self._tree.setHeaderLabels(COLUMNS)
        self.sort.attach(self._labels)
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_menu)
        self._tree.itemSelectionChanged.connect(self._on_selected)

        self._placeholder = QLabel(NO_STASHES)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_role(self._placeholder, Role.PLACEHOLDER)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(self._tree, 1)
        layout.addWidget(self._placeholder, 1)

        self._muted = QColor(self.ctx.theme.current.colors.text_muted)
        self._ages = QTimer(self)
        self._ages.setInterval(_MINUTE_MS)
        self._ages.timeout.connect(self._update_ages)
        self._ages.start()

        self.ctx.session.stashes_changed.connect(self._render)
        self.ctx.session.repo_changed.connect(lambda _repo: self._tree.forget_folded())
        self.ctx.selection.commit_changed.connect(self._follow_selection)
        self._render()

    # ---- rendering --------------------------------------------------------

    def _render(self, _value: object = None) -> None:
        stashes = self.ctx.session.stashes
        groups = self.sort.grouped(stashes)
        if groups is None:
            items = [self._item(stash) for stash in self.sort.apply(stashes)]
        else:
            items = []
            for group in groups:
                heading = self._tree.heading(group)
                heading.addChildren([self._item(stash) for stash in group.items])
                items.append(heading)
        blocked = self._tree.blockSignals(True)
        try:
            self._tree.clear()
            self._tree.setRootIsDecorated(groups is not None)
            self._tree.addTopLevelItems(items)
            self._tree.fold_headings()
        finally:
            self._tree.blockSignals(blocked)
        self._follow_selection(self.ctx.selection.commit)
        self._fit_columns()
        self._tree.setVisible(bool(stashes))
        self._placeholder.setVisible(not stashes)
        self.set_header(f"{self.title} ({len(stashes):,})" if stashes else self.title)

    def _item(self, stash: Stash) -> QTreeWidgetItem:
        item = QTreeWidgetItem([stash.message, stash.branch, age(stash.date)])
        item.setData(0, _SHA, stash.sha)
        made = stash.date.astimezone().strftime("%Y-%m-%d %H:%M")
        tip = f"{stash.message}\n\n{stash.ref} · {made}"
        for column in range(len(COLUMNS)):
            item.setToolTip(column, tip)
        for column in _MUTED:
            item.setForeground(column, self._muted)
        return item

    def apply_theme(self, theme: AppTheme) -> None:
        self._labels.set_theme(theme)
        self._tree.set_theme(theme)
        self._muted = QColor(theme.colors.text_muted)
        for item in self._tree.rows():
            for column in _MUTED:
                item.setForeground(column, self._muted)

    def _on_sorted(self) -> None:
        self._render()
        if (current := self._tree.currentItem()) is not None and current.isSelected():
            self._tree.scrollToItem(current)

    def _update_ages(self) -> None:
        for item in self._tree.rows():
            if (stash := self.stash_at(item)) is not None:
                item.setText(_AGE_COLUMN, age(stash.date))
        self._labels.fit(_AGE_COLUMN)

    def _fit_columns(self) -> None:
        """Branch and age fit their text; the message fills the rest (spec §5.2)."""
        for column in _MUTED:
            self._labels.fit(column)

    # ---- selection (spec §4.2) -----------------------------------------------

    def stash_at(self, item: QTreeWidgetItem | None) -> Stash | None:
        if item is None:
            return None
        sha = item.data(0, _SHA)
        return next((s for s in self.ctx.session.stashes if s.sha == sha), None)

    def _on_selected(self) -> None:
        selected = self._tree.selectedItems()
        stash = self.stash_at(selected[0]) if selected else None
        if stash is not None:
            self.ctx.selection.commit = stash
        elif isinstance(self.ctx.selection.commit, Stash):  # unselected (Ctrl+click)
            self.ctx.selection.commit = None

    def _follow_selection(self, commit: Commit | None) -> None:
        """Show the selected stash selected; nothing when something else is."""
        sha = commit.sha if isinstance(commit, Stash) else None
        blocked = self._tree.blockSignals(True)
        try:
            for item in self._tree.rows():
                selected = item.data(0, _SHA) == sha
                item.setSelected(selected)
                if selected:
                    self._tree.setCurrentItem(item)
        finally:
            self._tree.blockSignals(blocked)

    # ---- operations (spec §5.4): asked of AppActions, never run here --------

    def _show_menu(self, pos: QPoint) -> None:
        stash = self.stash_at(self._tree.itemAt(pos))
        if stash is not None:
            self.menu_for(stash).exec(self._tree.viewport().mapToGlobal(pos))

    def menu_for(self, stash: Stash) -> QMenu:
        """The right-click menu of ``stash``."""
        menu = QMenu(self)
        clean = not self.ctx.session.status
        request = self.ctx.actions.request_stash

        def add(text: str, op: StashOp, needs_clean: bool) -> None:
            blocked = needs_clean and not clean
            action = menu.addAction(text + (NOT_CLEAN if blocked else ""))
            action.setEnabled(not blocked)
            action.triggered.connect(lambda _=False: request(op, stash))

        add("Apply", StashOp.APPLY, needs_clean=True)
        add("Pop", StashOp.POP, needs_clean=True)
        menu.addSeparator()
        add("Drop…", StashOp.DROP, needs_clean=False)
        return menu
