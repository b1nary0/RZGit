"""The stashes, and the place to apply, pop and drop them (spec §5.4).

Selecting a stash sets ``selection.commit`` to it, so Commit Details, Commit
Files and Diff show it as they show a commit. The panel never runs git or shows
a dialog: it asks ``actions.request_stash``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from rzgitclient.git.git_models import Commit, Stash
from rzgitclient.ui.app_actions import StashOp
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.app_theme import AppTheme
from rzgitclient.ui.theme.style_roles import Role, Surface, set_role

NO_STASHES = "No stashes"
NOT_CLEAN = " (commit or stash your changes first)"
COLUMNS = ("Message", "Branch", "Age")
_MUTED = (1, 2)  # branch and age: muted, as History's author, date and hash
_AGE_COLUMN = 2
_SHA = Qt.ItemDataRole.UserRole
_MINUTE_MS = 60_000


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
    surface = Surface.SIDEBAR

    _tree: QTreeWidget
    _placeholder: QLabel
    _stash_button: QToolButton
    _ages: QTimer

    def _setup(self) -> None:
        self._stash_button = QToolButton()
        self._stash_button.setDefaultAction(self.ctx.actions.stash_changes)
        self._stash_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        bar.addWidget(self._stash_button)
        bar.addStretch(1)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(COLUMNS))
        self._tree.setHeaderLabels(COLUMNS)
        self._tree.setHeaderHidden(True)  # like the other sidebar lists; muted columns explain themselves
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
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
        self.ctx.selection.commit_changed.connect(self._follow_selection)
        self._render()

    # ---- rendering --------------------------------------------------------

    def _render(self, _value: object = None) -> None:
        stashes = self.ctx.session.stashes
        blocked = self._tree.blockSignals(True)
        try:
            self._tree.clear()
            for stash in stashes:
                item = QTreeWidgetItem([stash.message, stash.branch, age(stash.date)])
                item.setData(0, _SHA, stash.sha)
                made = stash.date.astimezone().strftime("%Y-%m-%d %H:%M")
                tip = f"{stash.message}\n\n{stash.ref} · {made}"
                for column in range(len(COLUMNS)):
                    item.setToolTip(column, tip)
                for column in _MUTED:
                    item.setForeground(column, self._muted)
                self._tree.addTopLevelItem(item)
        finally:
            self._tree.blockSignals(blocked)
        self._follow_selection(self.ctx.selection.commit)
        self._tree.setVisible(bool(stashes))
        self._placeholder.setVisible(not stashes)
        self.set_header(f"{self.title} ({len(stashes):,})" if stashes else self.title)

    def apply_theme(self, theme: AppTheme) -> None:
        self._muted = QColor(theme.colors.text_muted)
        for row in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(row)
            for column in _MUTED:
                item.setForeground(column, self._muted)

    def _update_ages(self) -> None:
        for row, stash in enumerate(self.ctx.session.stashes):
            item = self._tree.topLevelItem(row)
            if item is not None:
                item.setText(_AGE_COLUMN, age(stash.date))

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
            for row in range(self._tree.topLevelItemCount()):
                item = self._tree.topLevelItem(row)
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
