"""Staged Files: the index's changes, between Unstaged Files and Commit (spec §5.4).

The list is a ``FileList``, as in Unstaged Files. The panel collapses while
nothing is staged, and while a commit is selected (Commit Files takes its place).
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout

from rzgitclient.core.selection import FileSource
from rzgitclient.git.git_models import FileStatus
from rzgitclient.ui.panels.file_list import FileList
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.app_theme import AppTheme


class StagedPanel(Panel):
    panel_id = "staged"
    title = "Staged Files"

    # Built when something is first staged: the panel starts collapsed (no
    # repo yet), and the list would only cost startup time (6 ms).
    _list: FileList | None

    def _setup(self) -> None:
        self._list = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.set_collapsed(True)
        self.ctx.session.status_changed.connect(self._on_status)
        self.ctx.selection.commit_changed.connect(self._update_collapsed)
        self._on_status(self.ctx.session.status)

    def _on_status(self, status: tuple[FileStatus, ...]) -> None:
        if self._list is None and any(f.is_staged for f in status):
            actions = self.ctx.actions
            self._list = FileList(self.ctx, FileSource.INDEX,
                                  ([actions.unstage], [actions.unstage_all]), self.panel_id)
            self.layout().addWidget(self._list)
            # Styled before its first rows: a tree made after the window was
            # shown would size them before the stylesheet's row padding applies.
            self._list.ensurePolished()
            self._list.count_changed.connect(self._on_count)
            self._list.render()

    def _on_count(self, count: int) -> None:
        self.set_header(f"{self.title} ({count})" if count else self.title)
        self._update_collapsed()

    def _update_collapsed(self, *_args: object) -> None:
        count = self._list.count if self._list is not None else 0
        self.set_collapsed(not count or self.ctx.selection.commit is not None)

    def apply_theme(self, theme: AppTheme) -> None:
        if self._list is not None:
            self._list.apply_theme(theme)
