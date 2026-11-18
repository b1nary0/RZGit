"""Unstaged Files: the working tree's unstaged and untracked files (spec §5.4).

The list is a ``FileList``; Staged Files (staged_panel.py) is the same for
the index. It collapses while a commit is selected: Commit Files takes its place.
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout

from rzgitclient.core.selection import FileSource
from rzgitclient.ui.panels.file_list import SORTS, FileList
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.app_theme import AppTheme
from rzgitclient.ui.widgets.sort_menu import SortMenu


class FilesPanel(Panel):
    panel_id = "files"  # its id from before Staged Files had a panel: saved layouts keep it
    title = "Unstaged Files"

    _list: FileList
    sort: SortMenu

    def _setup(self) -> None:
        actions = self.ctx.actions
        self.sort = SortMenu(self.panel_id, SORTS, self.ctx.sorts, self)
        self.set_header_actions(self.sort.header_actions())
        self._list = FileList(self.ctx, FileSource.WORKTREE,
                              ([actions.stage, actions.discard], [actions.stage_all]),
                              self.panel_id, self.sort)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._list)
        self._list.count_changed.connect(self._on_count)
        self._list.render()
        self.ctx.selection.commit_changed.connect(self._update_collapsed)
        self._update_collapsed()

    def _update_collapsed(self, *_args: object) -> None:
        self.set_collapsed(self.ctx.selection.commit is not None)

    def _on_count(self, count: int) -> None:
        self.set_header(f"{self.title} ({count})" if count else self.title)

    def apply_theme(self, theme: AppTheme) -> None:
        self._list.apply_theme(theme)
