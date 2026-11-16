"""The flat list of one source's changed files (spec §5.4).

The body of the Unstaged Files (``FilesPanel``), Staged Files
(``StagedPanel``) and Commit Files (``CommitFilesPanel``) panels: a panel
never imports another, so they share this widget. With ``FileSource.COMMIT``
it lists ``session.commit_files``, and has no staging menu or double-click. Each row is one ``FileSelection``. A list shows only the rows of
``selection.files`` that are its own, so one of the two holds the selection
at a time, and Selection decides what stays selected after a refresh (§4.2).
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rzgitclient.core.selection import FileSelection, FileSource, other_source
from rzgitclient.git.git_models import FileStatus
from rzgitclient.ui.panels.panel import PanelContext
from rzgitclient.ui.theme.app_theme import AppTheme

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

_STATUS_COLUMN = 1
_ROW = Qt.ItemDataRole.UserRole  # (FileSelection, status code) of a row


def rows(changes: Iterable[FileStatus], source: FileSource) -> list[tuple[FileStatus, str]]:
    """The rows for ``source``, as ``(status, code)``; ``code`` picks label and colour."""
    if source is FileSource.COMMIT:  # a commit's changes, coded like staged ones
        return [(f, f.index) for f in changes]
    if source is FileSource.INDEX:
        return [(f, f.index) for f in changes if f.is_staged]
    return [(f, "?" if f.is_untracked else f.worktree)
            for f in changes if f.is_untracked or f.has_unstaged]


class FileList(QWidget):
    """The changed files of one ``source``, as a flat list; the body of the
    Unstaged Files, Staged Files and Commit Files panels."""

    count_changed = Signal(int)  # rows after each render: for the header
    count = 0  # rows after the last render

    def __init__(self, ctx: PanelContext, source: FileSource,
                 menu: tuple[list[QAction], list[QAction]], parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx, self.source, self._menu_actions = ctx, source, menu
        self._showing = False  # putting Selection on screen: not the user's choice
        # Passing the user's choice to Selection: the tree already shows it, and
        # mid-click it is half done (current moved, not selected yet), so putting
        # Selection back would undo the click.
        self._choosing = False
        self._rendered_theme: AppTheme | None = None

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(False)
        self._tree.setIndentation(0)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tree)

        self._tree.itemSelectionChanged.connect(self._on_user_selection)
        self._tree.currentItemChanged.connect(self._on_user_selection)
        self._tree.itemDoubleClicked.connect(lambda item, _col: self._move([item]))
        self._tree.customContextMenuRequested.connect(self._show_menu)
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            shortcut = QShortcut(QKeySequence(key), self._tree)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda: self._move(self._tree.selectedItems()))
        session = self.ctx.session
        if source is FileSource.COMMIT:
            session.commit_files_changed.connect(self.render)
        else:
            session.status_changed.connect(self.render)
        self.ctx.selection.files_changed.connect(self._show_selection)
        self.ctx.selection.file_changed.connect(self._show_selection)

    # ---- rendering -------------------------------------------------------

    def _changes(self) -> tuple[FileStatus, ...]:
        session = self.ctx.session
        if self.source is FileSource.COMMIT:
            return session.commit_files or ()
        return session.status

    def render(self, *_args: object) -> None:
        theme = self._rendered_theme = self.ctx.theme.current
        commit = self.ctx.session.commit_files_sha if self.source is FileSource.COMMIT else None
        items = []
        for f, code in rows(self._changes(), self.source):
            # Renames are staged, or committed.
            renamed = f.orig_path and self.source is not FileSource.WORKTREE
            path = f"{f.orig_path} → {f.path}" if renamed else f.path
            item = QTreeWidgetItem([path, STATUS_LABELS.get(code, code)])
            item.setData(0, _ROW, (FileSelection(f.path, self.source, commit), code))
            # Styled before it joins the tree: styling an item in the tree
            # updates the view each time (5x slower for 5,000 files).
            item.setForeground(_STATUS_COLUMN, QColor(theme.status_color(code)))
            items.append(item)
        blocked = self._tree.blockSignals(True)
        try:
            self._tree.clear()
            self._tree.addTopLevelItems(items)
        finally:
            self._tree.blockSignals(blocked)
        self._tree.resizeColumnToContents(_STATUS_COLUMN)
        self._show_selection()
        self.count = len(items)
        self.count_changed.emit(len(items))

    def _show_selection(self, *_args: object) -> None:
        """Select the rows ``selection.files`` has of this panel's (none, if the
        other panel holds the selection)."""
        if self._choosing:
            return
        selection = self.ctx.selection
        chosen = set(selection.files)
        model = self._tree.selectionModel()
        flags = QItemSelectionModel.SelectionFlag
        self._showing = True
        blocked = self._tree.blockSignals(True)
        try:
            model.clearSelection()
            current = None
            for item in self._items():
                f = self._file(item)
                if f in chosen:
                    model.select(self._tree.indexFromItem(item), flags.Select | flags.Rows)
                if f == selection.file:
                    current = item
            index = self._tree.indexFromItem(current) if current is not None else QModelIndex()
            model.setCurrentIndex(index, flags.NoUpdate)
        finally:
            self._tree.blockSignals(blocked)
            self._showing = False

    def apply_theme(self, theme: AppTheme) -> None:
        # Rebuilding is the fast way to recolour (see render); the selection is kept.
        if theme != self._rendered_theme:
            self.render()

    # ---- selection and staging -------------------------------------------

    def _on_user_selection(self, *_args: object) -> None:
        if self._showing:
            return
        selected = {id(i) for i in self._tree.selectedItems()}
        files = [f for i in self._items() if id(i) in selected and (f := self._file(i))]
        current = self._tree.currentItem()
        mine = self._file(current) if current is not None and id(current) in selected else None
        if files or any(f.source is self.source for f in self.ctx.selection.files):
            self._choosing = True
            try:
                self.ctx.selection.select(files, mine)  # in list order, not click order
            finally:
                self._choosing = False

    def _move(self, items: list[QTreeWidgetItem]) -> None:
        """Stage the rows (unstage, in Staged Files): they move to the other panel."""
        if self.source is FileSource.COMMIT:  # a commit's files go nowhere
            return
        paths = tuple(f.path for i in items if (f := self._file(i)) is not None)
        self.ctx.actions.move(paths, other_source(self.source))

    def _show_menu(self, pos: QPoint) -> None:
        if not any(self._menu_actions):
            return
        menu = self._menu()
        menu.exec(self._tree.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def _menu(self) -> QMenu:
        """The context menu: the panel's staging actions (spec §8)."""
        menu = QMenu(self)
        first, rest = self._menu_actions
        menu.addActions(first)
        menu.addSeparator()
        menu.addActions(rest)
        return menu

    # ---- items -----------------------------------------------------------

    def _items(self) -> list[QTreeWidgetItem]:
        return [self._tree.topLevelItem(i) for i in range(self._tree.topLevelItemCount())]

    @staticmethod
    def _file(item: QTreeWidgetItem | None) -> FileSelection | None:
        data = item.data(0, _ROW) if item is not None else None
        return data[0] if data else None
