"""Working tree changes; the selected row is ``selection.file`` (spec §5.4)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem, QVBoxLayout

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

_STAGED_COLUMN, _UNSTAGED_COLUMN = 1, 2
_FILE_ROLE = Qt.ItemDataRole.UserRole


def file_selection(f: FileStatus) -> FileSelection:
    """Unstaged or untracked changes select the worktree; staged-only the index."""
    source = FileSource.WORKTREE if f.has_unstaged or f.is_untracked else FileSource.INDEX
    return FileSelection(f.path, source)


class FilesPanel(Panel):
    panel_id = "files"
    title = "Files"
    surface = Surface.SIDEBAR

    _tree: QTreeWidget

    def _setup(self) -> None:
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Path", "Staged", "Unstaged"])
        self._tree.setRootIsDecorated(False)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tree)

        self._tree.currentItemChanged.connect(self._sync_selection)
        self.ctx.session.status_changed.connect(self._render)
        self._render(self.ctx.session.status)

    def _render(self, changes: tuple[FileStatus, ...]) -> None:
        current = self._status(self._tree.currentItem())
        keep = current.path if current else None
        theme = self.ctx.theme.current
        blocked = self._tree.blockSignals(True)
        try:
            self._tree.clear()
            for f in changes:
                path = f"{f.orig_path} → {f.path}" if f.orig_path else f.path
                staged = STATUS_LABELS.get(f.index, "") if f.is_staged else ""
                unstaged = (
                    "Untracked" if f.is_untracked
                    else STATUS_LABELS.get(f.worktree, "") if f.has_unstaged
                    else ""
                )
                item = QTreeWidgetItem([path, staged, unstaged])
                item.setData(0, _FILE_ROLE, f)
                _style(item, theme)
                self._tree.addTopLevelItem(item)
                if f.path == keep:
                    self._tree.setCurrentItem(item)
        finally:
            self._tree.blockSignals(blocked)
        for col in (_STAGED_COLUMN, _UNSTAGED_COLUMN):
            self._tree.resizeColumnToContents(col)
        self.set_header(f"{self.title} ({len(changes)})" if changes else self.title)
        # The same path may now have another source (e.g. it was staged).
        self._sync_selection()

    def apply_theme(self, theme: AppTheme) -> None:
        for i in range(self._tree.topLevelItemCount()):
            _style(self._tree.topLevelItem(i), theme)

    def _sync_selection(self, *_args: object) -> None:
        f = self._status(self._tree.currentItem())
        self.ctx.selection.file = file_selection(f) if f else None

    @staticmethod
    def _status(item: QTreeWidgetItem | None) -> FileStatus | None:
        return item.data(0, _FILE_ROLE) if item is not None else None


def _style(item: QTreeWidgetItem, theme: AppTheme) -> None:
    f: FileStatus = item.data(0, _FILE_ROLE)
    if f.is_staged:
        item.setForeground(_STAGED_COLUMN, QColor(theme.status_color(f.index)))
    if f.is_untracked or f.has_unstaged:
        code = "?" if f.is_untracked else f.worktree
        item.setForeground(_UNSTAGED_COLUMN, QColor(theme.status_color(code)))
