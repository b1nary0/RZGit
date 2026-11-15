"""The commit table; the selected row is ``selection.commit`` (spec §5.4)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from rzgitclient.git.git_models import Commit
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.app_theme import AppTheme

_MUTED_COLUMNS = (1, 2, 3)  # author, date, hash
_HASH_COLUMN = 3
_COMMIT_ROLE = Qt.ItemDataRole.UserRole


class HistoryPanel(Panel):
    panel_id = "history"
    title = "History"

    _tree: QTreeWidget

    def _setup(self) -> None:
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Subject", "Author", "Date", "Commit"])
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tree)

        self._tree.currentItemChanged.connect(self._sync_selection)
        self.ctx.session.history_changed.connect(self._render)
        self._render(self.ctx.session.history)

    def _render(self, commits: tuple[Commit, ...]) -> None:
        keep = self._commit(self._tree.currentItem())
        theme = self.ctx.theme.current
        blocked = self._tree.blockSignals(True)
        try:
            self._tree.clear()
            for c in commits:
                item = QTreeWidgetItem(
                    [c.subject, c.author, c.date.strftime("%Y-%m-%d %H:%M"), c.short_sha]
                )
                item.setData(0, _COMMIT_ROLE, c)
                item.setToolTip(_HASH_COLUMN, c.sha)
                _style(item, theme)
                self._tree.addTopLevelItem(item)
                if keep is not None and c.sha == keep.sha:
                    self._tree.setCurrentItem(item)
        finally:
            self._tree.blockSignals(blocked)
        for col in _MUTED_COLUMNS:
            self._tree.resizeColumnToContents(col)
        self._sync_selection()

    def apply_theme(self, theme: AppTheme) -> None:
        for i in range(self._tree.topLevelItemCount()):
            _style(self._tree.topLevelItem(i), theme)

    def _sync_selection(self, *_args: object) -> None:
        # Selection emits only on change, so a rebuild that keeps the same
        # commit selected is silent.
        self.ctx.selection.commit = self._commit(self._tree.currentItem())

    @staticmethod
    def _commit(item: QTreeWidgetItem | None) -> Commit | None:
        return item.data(0, _COMMIT_ROLE) if item is not None else None


def _style(item: QTreeWidgetItem, theme: AppTheme) -> None:
    muted = QColor(theme.colors.text_muted)
    for col in _MUTED_COLUMNS:
        item.setForeground(col, muted)
    item.setFont(_HASH_COLUMN, theme.mono_font())
