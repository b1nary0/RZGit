"""Every QAction, defined once (spec §8).

Menus, the Controls panel, context menus and panels all show these same
objects, so text, icon, shortcut and enabled state are set in one place.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QAction, QIcon, QKeySequence

from rzgitclient.core.repo_session import RepoSession
from rzgitclient.core.selection import FileSource, Selection


class AppActions(QObject):
    """Holds the actions and their enabled state. Window-level handlers (the
    Open dialog, closing the window, resetting the layout, confirming a
    discard) are connected by MainWindow; Commit by the Commit panel, which
    holds the message."""

    def __init__(self, session: RepoSession, selection: Selection,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._session, self._selection = session, selection
        self.open_repo = self._action(
            "document-open", "&Open Repository…", QKeySequence.StandardKey.Open
        )
        self.refresh = self._action(
            "view-refresh", "&Refresh", QKeySequence.StandardKey.Refresh
        )
        self.quit = self._action(
            "application-exit", "&Quit", QKeySequence.StandardKey.Quit
        )
        self.reset_layout = self._action("view-restore", "&Reset Layout")
        self.side_by_side_diff = self._action("view-split-left-right", "&Side by Side Diff")
        self.side_by_side_diff.setCheckable(True)
        self.file_tree = self._action("view-list-tree", "Show Files as &Tree")
        self.file_tree.setCheckable(True)
        self.file_tree.setChecked(True)

        # Staging (spec §8): on the selected rows of their kind, or on everything.
        self.stage = self._action("list-add", "&Stage", "Ctrl+S")
        self.unstage = self._action("list-remove", "&Unstage", "Ctrl+U")
        self.discard = self._action("edit-undo", "&Discard Changes…", "Ctrl+Backspace")
        self.stage_all = self._action("list-add", "Stage &All", "Ctrl+Shift+S")
        self.unstage_all = self._action("list-remove", "U&nstage All", "Ctrl+Shift+U")
        self.commit = self._action("vcs-commit", "&Commit", "Ctrl+Return")
        self.commit.setShortcuts([QKeySequence("Ctrl+Return"), QKeySequence("Ctrl+Enter")])
        self._commit_ready = False

        # triggered(bool) would land in refresh's `parts` argument.
        self.refresh.triggered.connect(lambda: session.refresh())
        self.stage.triggered.connect(lambda: self.move(self.selected(FileSource.WORKTREE),
                                                       FileSource.INDEX))
        self.unstage.triggered.connect(lambda: self.move(self.selected(FileSource.INDEX),
                                                         FileSource.WORKTREE))
        self.stage_all.triggered.connect(lambda: session.stage_all())
        self.unstage_all.triggered.connect(lambda: session.unstage_all())
        for signal in (session.repo_changed, session.status_changed, selection.files_changed):
            signal.connect(self._update_enabled)
        self._update_enabled()

    def set_commit_ready(self, ready: bool) -> None:
        """The Commit panel says whether its message can be committed now."""
        self._commit_ready = ready
        self._update_enabled()

    @Slot()
    def clear_commit_ready(self) -> None:
        """Not ready: for the Commit panel's ``destroyed``. A bound slot, so Qt
        drops the connection if these actions go first (at exit)."""
        self.set_commit_ready(False)

    def move(self, paths: tuple[str, ...], to: FileSource) -> None:
        """Stage ``paths`` (``to`` the index) or unstage them, and have their new
        rows selected once they arrive (spec §4.2)."""
        if not paths:
            return
        self._selection.follow(paths, to)
        if to is FileSource.INDEX:
            self._session.stage(paths)
        else:
            self._session.unstage(paths)

    def selected(self, source: FileSource) -> tuple[str, ...]:
        """Paths of the selected rows from ``source`` (Stage, Unstage, Discard)."""
        return tuple(f.path for f in self._selection.files if f.source is source)

    def _action(
        self, icon: str, text: str, shortcut: QKeySequence.StandardKey | str | None = None
    ) -> QAction:
        action = QAction(QIcon.fromTheme(icon), text, self)
        if isinstance(shortcut, str):
            action.setShortcut(QKeySequence(shortcut))
        elif shortcut is not None:
            action.setShortcuts(shortcut)  # every platform binding, e.g. F5 and the Refresh key
        return action

    def _update_enabled(self, *_args: object) -> None:
        session = self._session
        self.refresh.setEnabled(session.repo is not None)
        worktree = bool(self.selected(FileSource.WORKTREE))
        self.stage.setEnabled(worktree)
        self.discard.setEnabled(worktree)
        self.unstage.setEnabled(bool(self.selected(FileSource.INDEX)))
        self.stage_all.setEnabled(any(f.has_unstaged or f.is_untracked for f in session.status))
        staged = any(f.is_staged for f in session.status)
        self.unstage_all.setEnabled(staged)
        self.commit.setEnabled(staged and self._commit_ready and session.repo is not None)
