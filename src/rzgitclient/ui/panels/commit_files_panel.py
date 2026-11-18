"""Commit Files: the files the commit selected in History changed, or the
stash selected in the Stash panel holds (spec §5.4).

The list is a ``FileList`` in commit mode. The panel asks the session for the
commit's files when ``selection.commit`` changes, and collapses while it is
None (Uncommitted changes). It takes the place of Unstaged Files, Staged Files
and Commit, which collapse while a commit is selected.
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout

from rzgitclient.core.selection import FileSource
from rzgitclient.git.git_models import Commit, Stash
from rzgitclient.ui.panels.file_list import SORTS, FileList
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.app_theme import AppTheme
from rzgitclient.ui.widgets.sort_menu import SortMenu


class CommitFilesPanel(Panel):
    panel_id = "commit_files"
    title = "Commit Files"

    # Built when a commit is first selected: the panel starts collapsed.
    _list: FileList | None
    sort: SortMenu  # made now: the header menu has it while the list isn't built

    def _setup(self) -> None:
        self._list = None
        self.sort = SortMenu(self.panel_id, SORTS, self.ctx.sorts, self)
        self.set_header_actions(self.sort.header_actions())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.set_collapsed(True)
        self.ctx.selection.commit_changed.connect(self._on_commit)
        self.ctx.session.commit_files_changed.connect(self._update_header)
        self._on_commit(self.ctx.selection.commit)

    def _on_commit(self, commit: Commit | None) -> None:
        self.ctx.session.request_commit_files(commit.sha if commit is not None else None)
        if commit is not None and self._list is None:
            self._list = FileList(self.ctx, FileSource.COMMIT, ([], []), self.panel_id,
                                  self.sort)
            self.layout().addWidget(self._list)
            self._list.ensurePolished()  # styled before its first rows (see StagedPanel)
            self._list.render()
        self._update_header()
        self.set_collapsed(commit is None)

    def _update_header(self, *_args: object) -> None:
        commit = self.ctx.selection.commit
        if commit is None:
            self.set_header(self.title)
            return
        files = self.ctx.session.commit_files
        loaded = self.ctx.session.commit_files_sha == commit.sha and files is not None
        count = f" ({len(files):,})" if loaded else ""
        name = commit.ref if isinstance(commit, Stash) else commit.short_sha
        self.set_header(f"Files in {name}{count}")

    def apply_theme(self, theme: AppTheme) -> None:
        if self._list is not None:
            self._list.apply_theme(theme)
