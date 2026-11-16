"""The commit table's Qt model (spec §5.4).

Nothing is created per row: ``data()`` reads the commit and the current theme
when the view asks, so 100k commits cost no more to show than 500. Pages are
asked for by the panel, not through Qt's fetchMore, which Qt also calls while
laying out rows, so it would load pages nobody scrolled to.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QColor, QFont

from rzgitclient.git.git_models import Commit
from rzgitclient.ui.theme.app_theme import AppTheme

COLUMNS = ("Subject", "Author", "Date", "Commit")
MUTED_COLUMNS = (1, 2, 3)  # author, date, hash
HASH_COLUMN = 3

_Index = QModelIndex | QPersistentModelIndex
_STYLE_ROLES = [Qt.ItemDataRole.ForegroundRole, Qt.ItemDataRole.FontRole]


class HistoryModel(QAbstractTableModel):
    def __init__(self, theme: AppTheme):
        super().__init__()
        self._commits: tuple[Commit, ...] = ()
        self._muted = QColor()
        self._mono = QFont()
        self.set_theme(theme)

    # ---- contents -------------------------------------------------------

    @property
    def commits(self) -> tuple[Commit, ...]:
        return self._commits

    def set_commits(self, commits: tuple[Commit, ...]) -> bool:
        """Show ``commits``. Returns False if the model was reset.

        When ``commits`` only adds to the end of what's shown (a page, whose
        first part is the same objects), the new rows are inserted instead.
        """
        old, n = self._commits, len(self._commits)
        if len(commits) >= n and all(a is b for a, b in zip(old, commits)):
            if len(commits) > n:
                self.beginInsertRows(QModelIndex(), n, len(commits) - 1)
                self._commits = commits
                self.endInsertRows()
            return True
        self.beginResetModel()
        self._commits = commits
        self.endResetModel()
        return False

    def commit_at(self, row: int) -> Commit | None:
        return self._commits[row] if 0 <= row < len(self._commits) else None

    def text(self, row: int, col: int) -> str:
        commit = self._commits[row]
        if col == 0:
            return commit.subject
        if col == 1:
            return commit.author
        if col == 2:
            return commit.date.strftime("%Y-%m-%d %H:%M")
        return commit.short_sha

    def font(self, col: int) -> QFont | None:
        """The column's font, or None for the view's."""
        return self._mono if col == HASH_COLUMN else None

    def row_of(self, sha: str) -> int:
        """The row of the commit ``sha``, or -1."""
        for row, commit in enumerate(self._commits):
            if commit.sha == sha:
                return row
        return -1

    def set_theme(self, theme: AppTheme) -> None:
        self._muted = QColor(theme.colors.text_muted)
        self._mono = theme.mono_font()
        if self._commits:
            last = self.index(len(self._commits) - 1, len(COLUMNS) - 1)
            self.dataChanged.emit(self.index(0, 0), last, _STYLE_ROLES)

    # ---- QAbstractTableModel --------------------------------------------

    def hasChildren(self, parent: _Index = QModelIndex()) -> bool:
        # A flat table. The tree view asks this for every row it lays out;
        # answering here saves a rowCount and a columnCount call per row.
        return not parent.isValid() and bool(self._commits)

    def rowCount(self, parent: _Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._commits)

    def columnCount(self, parent: _Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        row, col = index.row(), index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self.text(row, col)
        if role == Qt.ItemDataRole.ForegroundRole and col in MUTED_COLUMNS:
            return self._muted
        if role == Qt.ItemDataRole.FontRole and col == HASH_COLUMN:
            return self._mono
        if role == Qt.ItemDataRole.ToolTipRole and col == HASH_COLUMN:
            return self._commits[row].sha
        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None
