"""One source's changed files, as a folder tree or a flat list (spec §5.4).

The body of the Unstaged Files (``FilesPanel``), Staged Files
(``StagedPanel``) and Commit Files (``CommitFilesPanel``) panels: a panel
never imports another, so they share this widget. With ``FileSource.COMMIT``
it lists ``session.commit_files``, and has no staging menu or double-click. Each row is one ``FileSelection``. A list shows only the rows of
``selection.files`` that are its own, so one of the two holds the selection
at a time, and Selection decides what stays selected after a refresh (§4.2).
With Show Files as Tree on, files sit under folder rows (``file_tree.py``);
selecting a folder selects every file under it.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from rzgitclient.core.selection import FileSelection, FileSource, other_source
from rzgitclient.git.git_models import FileStatus
from rzgitclient.ui.panels.file_tree import FolderNode, build_tree, file_label
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
# The status column's letter; the label is the tooltip. Untracked is U, as in
# VS Code, so a conflict is "!".
STATUS_LETTERS = {"M": "M", "A": "A", "D": "D", "R": "R", "C": "C", "U": "!", "T": "T", "?": "U"}

_STATUS_COLUMN = 1
_ROW = Qt.ItemDataRole.UserRole  # (FileSelection, status code) of a file row
_FOLDER = Qt.ItemDataRole.UserRole + 1  # the folder path of a folder row
_INDENT = 10  # px per tree level


def rows(changes: Iterable[FileStatus], source: FileSource) -> list[tuple[FileStatus, str]]:
    """The rows for ``source``, as ``(status, code)``; ``code`` picks label and colour."""
    if source is FileSource.COMMIT:  # a commit's changes, coded like staged ones
        return [(f, f.index) for f in changes]
    if source is FileSource.INDEX:
        return [(f, f.index) for f in changes if f.is_staged]
    return [(f, "?" if f.is_untracked else f.worktree)
            for f in changes if f.is_untracked or f.has_unstaged]


class _GuidedTree(QTreeWidget):
    """A tree that draws a faint vertical guide for each folder level, as in
    VS Code, so a file's folder is easy to follow."""

    guide_color = QColor()

    def drawBranches(self, painter: QPainter, rect: QRect, index: QModelIndex) -> None:
        super().drawBranches(painter, rect, index)
        depth, parent = 0, index.parent()
        while parent.isValid():
            depth, parent = depth + 1, parent.parent()
        if not depth or not self.guide_color.isValid():
            return
        indent = self.indentation()
        painter.save()
        painter.setPen(QPen(self.guide_color, 1))
        for level in range(depth):  # under each ancestor folder's arrow
            x = rect.left() + level * indent + indent // 2
            painter.drawLine(x, rect.top(), x, rect.bottom())
        painter.restore()


class FileList(QWidget):
    """The changed files of one ``source``, as a folder tree or a flat list;
    the body of the Unstaged Files, Staged Files and Commit Files panels."""

    count_changed = Signal(int)  # files after each render: for the header
    count = 0  # files after the last render

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
        self._collapsed: set[str] = set()  # folder paths the user collapsed

        self._tree = _GuidedTree()
        self._tree.setColumnCount(2)
        self._tree.setHeaderHidden(True)
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
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.itemCollapsed.connect(lambda item: self._set_collapsed(item, True))
        self._tree.itemExpanded.connect(lambda item: self._set_collapsed(item, False))
        self.ctx.actions.file_tree.toggled.connect(self.render)
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
        session.repo_changed.connect(lambda _repo: self._collapsed.clear())
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
        tree = self.ctx.actions.file_tree.isChecked()

        def file_item(f: FileStatus, code: str) -> QTreeWidgetItem:
            # Renames are staged, or committed.
            orig = f.orig_path if self.source is not FileSource.WORKTREE else None
            if tree:
                text = file_label(f.path, orig)
            else:
                text = f"{orig} → {f.path}" if orig else f.path
            item = QTreeWidgetItem([text, STATUS_LETTERS.get(code, code)])
            item.setData(0, _ROW, (FileSelection(f.path, self.source, commit), code))
            # Styled before it joins the tree: styling an item in the tree
            # updates the view each time (5x slower for 5,000 files).
            item.setForeground(_STATUS_COLUMN, QColor(theme.status_color(code)))
            tip = f"{f.path} · {STATUS_LABELS.get(code, code)}"
            for column in (0, _STATUS_COLUMN):
                item.setToolTip(column, tip)
            item.setTextAlignment(_STATUS_COLUMN, Qt.AlignmentFlag.AlignCenter)
            return item

        changes = rows(self._changes(), self.source)
        if tree:
            items = self._tree_items(build_tree((f.path, (f, code)) for f, code in changes),
                                     file_item, QColor(theme.colors.text_muted))
        else:
            items = [file_item(f, code) for f, code in changes]
        blocked = self._tree.blockSignals(True)
        try:
            self._tree.clear()
            self._tree.guide_color = QColor(theme.colors.tree_guide)
            self._tree.setRootIsDecorated(tree)
            self._tree.setIndentation(_INDENT if tree else 0)
            self._tree.addTopLevelItems(items)
            if tree:
                self._tree.expandAll()
                for item in self._folder_items():
                    if item.data(0, _FOLDER) in self._collapsed:
                        item.setExpanded(False)
        finally:
            self._tree.blockSignals(blocked)
        self._tree.resizeColumnToContents(_STATUS_COLUMN)
        self._show_selection()
        self.count = len(changes)
        self.count_changed.emit(len(changes))

    @staticmethod
    def _tree_items(folder: FolderNode, file_item, muted: QColor) -> list[QTreeWidgetItem]:
        """The rows of ``folder``'s contents: its folders' rows (muted, so the
        files stand out), then its files'."""
        items = []
        for sub in folder.folders:
            item = QTreeWidgetItem([sub.name, ""])
            item.setData(0, _FOLDER, sub.path)
            item.setForeground(0, muted)
            item.setToolTip(0, sub.path)
            item.addChildren(FileList._tree_items(sub, file_item, muted))
            items.append(item)
        items.extend(file_item(*node.data) for node in folder.files)
        return items

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
        selected = self._tree.selectedItems()
        chosen = {id(i) for i in self._with_files_under(selected)}
        files = [f for i in self._items() if id(i) in chosen and (f := self._file(i))]
        current = self._tree.currentItem()
        is_selected = current is not None and id(current) in {id(i) for i in selected}
        mine = self._file(current) if is_selected else None  # None for a folder
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
        paths = tuple(f.path for i in self._with_files_under(items)
                      if (f := self._file(i)) is not None)
        self.ctx.actions.move(paths, other_source(self.source))

    def _on_double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._file(item) is not None:  # a folder folds, as usual
            self._move([item])

    def _set_collapsed(self, item: QTreeWidgetItem, collapsed: bool) -> None:
        """The user folded or unfolded a folder: remembered by its path."""
        path = item.data(0, _FOLDER)
        if path is not None:
            (self._collapsed.add if collapsed else self._collapsed.discard)(path)

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

    def _all_items(self) -> list[QTreeWidgetItem]:
        """Every row, folders and files, in tree order."""
        found, it = [], QTreeWidgetItemIterator(self._tree)
        while (item := it.value()) is not None:
            found.append(item)
            it += 1
        return found

    def _items(self) -> list[QTreeWidgetItem]:
        """The file rows, in tree order."""
        return [i for i in self._all_items() if i.data(0, _ROW)]

    def _folder_items(self) -> list[QTreeWidgetItem]:
        return [i for i in self._all_items() if i.data(0, _FOLDER) is not None]

    @staticmethod
    def _with_files_under(items: Iterable[QTreeWidgetItem]) -> list[QTreeWidgetItem]:
        """``items``, and every row under the folders among them."""
        found, stack = [], list(items)
        while stack:
            item = stack.pop()
            found.append(item)
            stack.extend(item.child(i) for i in range(item.childCount()))
        return found

    @staticmethod
    def _file(item: QTreeWidgetItem | None) -> FileSelection | None:
        data = item.data(0, _ROW) if item is not None else None
        return data[0] if data else None
