"""Working tree changes in Unstaged and Staged groups (spec §5.4).

Each row is one ``FileSelection``: a file with both staged and unstaged
changes has a row in each group. The selected rows are ``selection.files``,
the current one ``selection.file``.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.git.git_models import FileStatus
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.app_theme import AppTheme
from rzgitclient.ui.theme.style_roles import Surface

STATUS_LABELS = {
    "M": "Modified",
    "A": "Added",
    "D": "Deleted",
    "R": "Renamed",
    "C": "Copied",
    "U": "Conflict",
    "T": "Type changed",
    "?": "Untracked",
}
GROUP_TITLES = {FileSource.WORKTREE: "Unstaged", FileSource.INDEX: "Staged"}

_GROUPS = (FileSource.WORKTREE, FileSource.INDEX)  # top to bottom
_STATUS_COLUMN = 1
_ROW = Qt.ItemDataRole.UserRole  # (FileSelection, status code) of a file row
_Key = tuple[str, FileSource]  # a row: its path and group


def rows(changes: Iterable[FileStatus]) -> dict[FileSource, list[tuple[FileStatus, str]]]:
    """The rows of each group, as ``(status, code)``; ``code`` picks label and colour."""
    groups: dict[FileSource, list[tuple[FileStatus, str]]] = {g: [] for g in _GROUPS}
    for f in changes:
        if f.is_untracked or f.has_unstaged:
            groups[FileSource.WORKTREE].append((f, "?" if f.is_untracked else f.worktree))
        if f.is_staged:
            groups[FileSource.INDEX].append((f, f.index))
    return groups


class FilesPanel(Panel):
    panel_id = "files"
    title = "Files"
    surface = Surface.SIDEBAR

    _tree: QTreeWidget

    def _setup(self) -> None:
        # After Stage or Unstage: the group the files move to, until a status shows them there.
        self._follow: dict[str, FileSource] = {}
        self._rendering = False
        self._rendered_theme: AppTheme | None = None

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(False)
        self._tree.setItemsExpandable(False)
        self._tree.setIndentation(0)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tree)

        actions = self.ctx.actions
        self._tree.itemSelectionChanged.connect(self._on_user_selection)
        self._tree.currentItemChanged.connect(self._on_user_selection)
        self._tree.itemDoubleClicked.connect(lambda item, _col: self._toggle([item]))
        self._tree.customContextMenuRequested.connect(self._show_menu)
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            shortcut = QShortcut(QKeySequence(key), self._tree)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda: self._toggle(self._tree.selectedItems()))
        # The global actions act on selection.files; the rows then follow them.
        actions.stage.triggered.connect(lambda: self._follow_to(FileSource.INDEX))
        actions.unstage.triggered.connect(lambda: self._follow_to(FileSource.WORKTREE))
        self.ctx.session.status_changed.connect(self._render)
        self._render(self.ctx.session.status)

    # ---- rendering -------------------------------------------------------

    def _render(self, changes: tuple[FileStatus, ...]) -> None:
        selected = [self._key(i) for i in self._tree.selectedItems()]
        current = self._key(self._tree.currentItem())
        theme = self._rendered_theme = self.ctx.theme.current
        items: dict[_Key, QTreeWidgetItem] = {}
        self._rendering = True
        blocked = self._tree.blockSignals(True)
        try:
            self._tree.clear()
            muted = QColor(theme.colors.text_muted)
            for source, group_rows in rows(changes).items():
                if not group_rows:
                    continue
                # Built and styled before it joins the tree: styling an item in
                # the tree updates the view each time (5x slower for 5,000 files).
                group = QTreeWidgetItem([f"{GROUP_TITLES[source]} ({len(group_rows)})"])
                group.setFlags(Qt.ItemFlag.ItemIsEnabled)
                group.setForeground(0, muted)
                font = self._tree.font()
                font.setBold(True)
                group.setFont(0, font)
                for f, code in group_rows:
                    renamed = f.orig_path and source is FileSource.INDEX  # renames are staged
                    path = f"{f.orig_path} → {f.path}" if renamed else f.path
                    item = QTreeWidgetItem([path, STATUS_LABELS.get(code, code)])
                    item.setData(0, _ROW, (FileSelection(f.path, source), code))
                    item.setForeground(_STATUS_COLUMN, QColor(theme.status_color(code)))
                    group.addChild(item)
                    items[(f.path, source)] = item
                self._tree.addTopLevelItem(group)
                group.setFirstColumnSpanned(True)
                group.setExpanded(True)
            self._restore(items, selected, current)
        finally:
            self._tree.blockSignals(blocked)
            self._rendering = False
        self._tree.resizeColumnToContents(_STATUS_COLUMN)
        self.set_header(f"{self.title} ({len(changes)})" if changes else self.title)
        self._sync_selection()

    def _restore(self, items: dict[_Key, QTreeWidgetItem],
                 selected: list[_Key | None], current: _Key | None) -> None:
        """Select the same rows again, or where Stage or Unstage moved them."""
        def place(key: _Key | None) -> QTreeWidgetItem | None:
            if key is None:
                return None
            path, source = key
            if (target := self._follow.get(path)) is not None and (path, target) in items:
                del self._follow[path]  # it has arrived
                return items[(path, target)]
            return items.get(key) or items.get((path, _other(source)))

        current_item = place(current)
        chosen = [i for k in selected if (i := place(k)) is not None]
        if current_item is not None:
            self._tree.setCurrentItem(current_item)  # selects it, too
        selection = self._tree.selectionModel()
        for item in chosen:
            index = self._tree.indexFromItem(item)
            selection.select(index, selection.SelectionFlag.Select | selection.SelectionFlag.Rows)

    def apply_theme(self, theme: AppTheme) -> None:
        # Rebuilding is the fast way to recolour (see _render); the selection is kept.
        if theme != self._rendered_theme:
            self._render(self.ctx.session.status)

    # ---- selection and staging -------------------------------------------

    def _on_user_selection(self, *_args: object) -> None:
        self._follow.clear()  # the user chose: nothing to follow any more
        self._sync_selection()

    def _sync_selection(self) -> None:
        if self._rendering:
            return
        selection = self.ctx.selection
        selected = {id(i) for i in self._tree.selectedItems()}
        # In list order, not the order they were clicked.
        selection.files = tuple(self._file(i) for i in self._file_items() if id(i) in selected)
        current = self._tree.currentItem()
        selection.file = self._file(current) if current is not None and id(current) in selected else None

    def _toggle(self, items: list[QTreeWidgetItem]) -> None:
        """Stage the unstaged rows among ``items`` and unstage the staged ones."""
        files = [f for i in items if (f := self._file(i)) is not None]
        stage = [f.path for f in files if f.source is FileSource.WORKTREE]
        unstage = [f.path for f in files if f.source is FileSource.INDEX]
        session = self.ctx.session
        if stage:
            self._follow.update(dict.fromkeys(stage, FileSource.INDEX))
            session.stage(stage)
        if unstage:
            self._follow.update(dict.fromkeys(unstage, FileSource.WORKTREE))
            session.unstage(unstage)

    def _follow_to(self, source: FileSource) -> None:
        """The selected rows of the other group are moving to ``source``."""
        self._follow.update(dict.fromkeys(self.ctx.actions.selected(_other(source)), source))

    def _show_menu(self, pos: QPoint) -> None:
        menu = self._menu()
        menu.exec(self._tree.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def _menu(self) -> QMenu:
        """The context menu: the staging actions (spec §8)."""
        actions = self.ctx.actions
        menu = QMenu(self)
        menu.addActions([actions.stage, actions.unstage, actions.discard])
        menu.addSeparator()
        menu.addActions([actions.stage_all, actions.unstage_all])
        return menu

    # ---- items -----------------------------------------------------------

    def _file_items(self) -> list[QTreeWidgetItem]:
        return [self._tree.topLevelItem(g).child(i)
                for g in range(self._tree.topLevelItemCount())
                for i in range(self._tree.topLevelItem(g).childCount())]

    @staticmethod
    def _file(item: QTreeWidgetItem | None) -> FileSelection | None:
        data = item.data(0, _ROW) if item is not None else None
        return data[0] if data else None

    def _key(self, item: QTreeWidgetItem | None) -> _Key | None:
        f = self._file(item)
        return (f.path, f.source) if f is not None else None


def _other(source: FileSource) -> FileSource:
    return FileSource.INDEX if source is FileSource.WORKTREE else FileSource.WORKTREE
