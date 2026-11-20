"""One commit or stash in a window of its own (spec §8.1, RZG-84).

Opened from History or Stashes by a double-click, Enter or Open in New Window,
and owned by the main window it came from (``MainWindow.open_commit_window``):
it closes when that window closes or changes repository. It has its own
RepoSession, which reads only what its panels ask for, its own Selection, set to
the commit once, and its own AppActions, so choosing a file here never reaches
the main window. Commit Files above Commit Details, and Diff, in a fixed layout.
"""

from __future__ import annotations

import dataclasses

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox, QVBoxLayout, QWidget

from rzgit.core.app_settings import AppSettings
from rzgit.core.repo_session import Refresh, RepoSession
from rzgit.core.selection import Selection
from rzgit.git.git_models import Commit, Stash
from rzgit.ui.app_actions import AppActions
from rzgit.ui.layout.layout_host import LayoutHost
from rzgit.ui.layout.layout_model import Area, Arrangement, LayoutState, Orientation, PanelNode, SplitNode
from rzgit.ui.panels.panel import PanelContext

GONE = " (no longer in history)"
DEFAULT_SIZE = (900, 600)

# Commit Files above Commit Details on the left, Diff on the right (spec §8.1).
COMMIT_WINDOW_LAYOUT = LayoutState(
    {Area.SIDEBAR: SplitNode(Orientation.VERTICAL,
                             (PanelNode("commit_files"), PanelNode("commit_details")),
                             sizes=(300, 300)),
     Area.MAIN: PanelNode("diff")},
    Arrangement.SIDEBAR_LEFT, (320, 580))


class CommitWindow(QWidget):
    closed = Signal()  # after it saved, on a close that was accepted

    def __init__(self, commit: Commit, main: PanelContext, settings: AppSettings):
        """``commit`` (or a ``Stash``) of the repository open in ``main``, the main
        window's context: its theme, preferences and view toggles are the start."""
        super().__init__(None)  # top-level, not kept on top of the main window
        repo = main.session.repo
        if repo is None:
            raise ValueError("a commit window needs an open repository")
        self.commit = commit
        self.settings = settings
        self._main = main
        self._repo_name = repo.worktree_name
        self._gone = False
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self.session = session = RepoSession()
        self.selection = selection = Selection(session)
        self.actions = actions = AppActions(session, selection)
        # The view toggles start from the main window's, then stay this window's.
        for name in ("side_by_side_diff", "whole_file_diff", "file_tree"):
            getattr(actions, name).setChecked(getattr(main.actions, name).isChecked())
        actions.hunk_headers[main.actions.hunk_headers_mode()].setChecked(True)
        # VS Code opens through the main window's launcher (spec §8, RZG-96).
        actions.open_in_vscode_requested.connect(main.actions.open_in_vscode_requested)
        session.error_occurred.connect(self._show_error)
        self.ctx = dataclasses.replace(main, session=session, selection=selection,
                                       actions=actions, worktrees=None)
        session.open(repo, load=Refresh(0))  # nothing but what the panels ask for
        selection.commit = commit

        self.layout_host = LayoutHost(self.ctx, fixed=True)
        self.layout_host.apply(COMMIT_WINDOW_LAYOUT)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.layout_host)
        # Deleted with it, after the panels connected to them; the session last.
        for owned in (actions, selection, session):
            owned.setParent(self)

        if (geometry := settings.commit_window_geometry) is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(*DEFAULT_SIZE)
        main.session.history_changed.connect(self._check_still_there)
        main.session.stashes_changed.connect(self._check_still_there)
        self._update_title()

    # ---- behaviour ------------------------------------------------------------

    @property
    def gone(self) -> bool:
        """The main window's history (or stashes) no longer has the commit."""
        return self._gone

    def _check_still_there(self, _value: object = None) -> None:
        sha, main = self.commit.sha, self._main.session
        if isinstance(self.commit, Stash):
            same = next((s for s in main.stashes if s.sha == sha), None)
            gone = same is None
            if same is not None and same != self.commit:  # its stash@{n} moved
                self.commit = same
                self._update_title()
        else:
            gone = not any(c.sha == sha for c in main.history)
        if gone != self._gone:
            self._gone = gone
            self._update_title()

    def _update_title(self) -> None:
        self.setWindowTitle(commit_window_title(self.commit, self._repo_name, self._gone))

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Git error", message)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.settings.commit_window_geometry = self.saveGeometry()
        super().closeEvent(event)
        if event.isAccepted():
            self._disconnect_main()
            self.session.shutdown()  # waits for a running git command
            self.closed.emit()

    def _disconnect_main(self) -> None:
        main = self._main.session
        main.history_changed.disconnect(self._check_still_there)
        main.stashes_changed.disconnect(self._check_still_there)


def commit_window_title(commit: Commit, repo_name: str, gone: bool = False) -> str:
    """``<short sha> <subject> — <worktree>``; a stash ``stash@{n} <message> — …``."""
    if isinstance(commit, Stash):
        text = f"{commit.ref} {commit.message}"
    else:
        text = f"{commit.short_sha} {commit.subject}"
    return f"{text} — {repo_name}" + (GONE if gone else "")
