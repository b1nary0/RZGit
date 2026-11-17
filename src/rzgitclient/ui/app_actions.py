"""Every QAction, defined once (spec §8).

Menus, the Controls panel, context menus and panels all show these same
objects, so text, icon, shortcut and enabled state are set in one place.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import StrEnum

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence

from rzgitclient.core.repo_session import RepoSession
from rzgitclient.core.selection import FileSource, Selection
from rzgitclient.git.git_models import FileDiff, LastCommit, Stash
from rzgitclient.ui.theme.theme_manager import ThemeMode


class BranchOp(StrEnum):
    """A branch operation the Branches panel asks for (spec §8)."""

    SWITCH = "switch"
    NEW = "new"  # a new branch from it
    RENAME = "rename"
    DELETE = "delete"
    MERGE = "merge"  # it into the current branch
    REBASE = "rebase"  # the current branch onto it


class StashOp(StrEnum):
    """A stash operation the Stash panel asks for (spec §8)."""

    APPLY = "apply"
    POP = "pop"
    DROP = "drop"


class WorktreeOp(StrEnum):
    """A worktree operation the Worktrees or Branches panel asks for (spec §8)."""

    NEW = "new"  # target: the branch to start from, or "" for none
    REMOVE = "remove"  # target: the worktree's path, as for the rest
    PRUNE = "prune"  # the missing worktrees' records; no target
    OPEN_FOLDER = "open_folder"
    OPEN_TERMINAL = "open_terminal"


class AppActions(QObject):
    """Holds the actions and their enabled state. Window-level handlers (the
    Open dialog, closing the window, resetting the layout, confirming a
    discard) are connected by MainWindow; Commit by the Commit panel, which
    holds the message."""

    # The Diff panel asks to discard lines; MainWindow confirms, then discards (spec §8).
    discard_lines_requested = Signal(object, object)  # FileDiff, frozenset[(hunk, line)]
    # The Branches panel asks for a branch operation; BranchDialogs asks, then runs it.
    branch_requested = Signal(object, str)  # BranchOp, branch name
    # The Stash panel asks for a stash operation; StashDialogs asks if need be, then runs it.
    stash_requested = Signal(object, object)  # StashOp, Stash
    # The Worktrees panel asks to open a worktree; MainWindow opens it in a window (§8.1),
    # a window of its own or this one.
    open_worktree_requested = Signal(str)  # the worktree's path
    open_worktree_here_requested = Signal(str)
    # A worktree operation; WorktreeDialogs asks if need be, then runs it (spec §8).
    worktree_requested = Signal(object, str)  # WorktreeOp, path or branch ("" for none)

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
        # View → Theme: one of three, checked by MainWindow (spec §8).
        self._theme_group = QActionGroup(self)
        self.theme_modes: dict[ThemeMode, QAction] = {}
        for mode, text in ((ThemeMode.LIGHT, "&Light"), (ThemeMode.DARK, "&Dark"),
                           (ThemeMode.SYSTEM, "&System")):
            action = QAction(text, self._theme_group)
            action.setCheckable(True)
            self.theme_modes[mode] = action
        # ... and below them, colour-blind friendly colours for whichever it is.
        self.colour_blind = QAction("Colour-blind &Friendly Colours", self)
        self.colour_blind.setCheckable(True)

        # Staging (spec §8): on the selected rows of their kind, or on everything.
        self.stage = self._action("list-add", "&Stage", "Ctrl+S")
        self.unstage = self._action("list-remove", "&Unstage", "Ctrl+U")
        self.discard = self._action("edit-undo", "&Discard Changes…", "Ctrl+Backspace")
        self.stage_all = self._action("list-add", "Stage &All", "Ctrl+Shift+S")
        self.unstage_all = self._action("list-remove", "U&nstage All", "Ctrl+Shift+U")
        self.commit = self._action("vcs-commit", "&Commit", "Ctrl+Return")
        self.commit.setShortcuts([QKeySequence("Ctrl+Return"), QKeySequence("Ctrl+Enter")])
        self._commit_ready = False
        # Amend (spec §8): checked on HEAD's commit; unchecked when HEAD moves.
        self.amend = self._action("edit-redo", "&Amend Last Commit")
        self.amend.setCheckable(True)
        self.amend.toggled.connect(self._on_amend_toggled)
        self._amend_sha: str | None = None
        self._confirm_amend: Callable[[LastCommit], bool] = lambda _last: True
        # Stash Changes… (spec §8): connected by StashDialogs, which asks for the message.
        self.stash_changes = self._action("document-save", "Stas&h Changes…")
        # Show Worktrees (spec §8): MainWindow shows the Worktrees panel and raises it.
        self.worktrees = self._action("view-list-details", "Show &Worktrees", "Ctrl+Shift+W")
        # New Worktree… (spec §8): WorktreeDialogs asks for the branch and folder.
        self.new_worktree = self._action("folder-new", "New Wor&ktree…")
        self.new_worktree.triggered.connect(lambda: self.request_worktree(WorktreeOp.NEW))

        # triggered(bool) would land in refresh's `parts` argument.
        self.refresh.triggered.connect(lambda: session.refresh())
        self.stage.triggered.connect(lambda: self.move(self.selected(FileSource.WORKTREE),
                                                       FileSource.INDEX))
        self.unstage.triggered.connect(lambda: self.move(self.selected(FileSource.INDEX),
                                                         FileSource.WORKTREE))
        self.stage_all.triggered.connect(lambda: session.stage_all())
        self.unstage_all.triggered.connect(lambda: session.unstage_all())
        # Another repository: its history isn't loaded yet, so don't wait for it.
        session.repo_changed.connect(lambda _repo: self.amend.setChecked(False))
        for signal in (session.repo_changed, session.status_changed, session.history_changed,
                       selection.files_changed):
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

    def set_amend_confirmer(self, ask: Callable[[LastCommit], bool]) -> None:
        """MainWindow's question before amending ``last`` (spec §8)."""
        self._confirm_amend = ask

    def confirm_amend(self, last: LastCommit) -> bool:
        """Whether to go ahead and amend ``last``: True without a confirmer."""
        return self._confirm_amend(last)

    def _on_amend_toggled(self, checked: bool) -> None:
        history = self._session.history
        self._amend_sha = history[0].sha if checked and history else None
        self._update_enabled()

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

    def request_discard_lines(self, diff: FileDiff, lines: Iterable[tuple[int, int]]) -> None:
        """Ask for the chosen lines of ``diff`` to be discarded, once confirmed."""
        chosen = frozenset(lines)
        if chosen:
            self.discard_lines_requested.emit(diff, chosen)

    def request_branch(self, op: BranchOp, name: str) -> None:
        """Ask for ``op`` on branch ``name``, once confirmed (spec §8)."""
        self.branch_requested.emit(op, name)

    def request_stash(self, op: StashOp, stash: Stash) -> None:
        """Ask for ``op`` on ``stash``, once confirmed where it loses work (spec §8)."""
        self.stash_requested.emit(op, stash)

    def request_open_worktree(self, path: str) -> None:
        """Show the worktree at ``path`` in a main window of its own (spec §8)."""
        self.open_worktree_requested.emit(path)

    def request_open_worktree_here(self, path: str) -> None:
        """Show the worktree at ``path`` in this window (spec §8)."""
        self.open_worktree_here_requested.emit(path)

    def request_worktree(self, op: WorktreeOp, target: str = "") -> None:
        """Ask for ``op`` on the worktree at ``target`` (for NEW, the branch to
        start from), once confirmed where it loses work (spec §8)."""
        self.worktree_requested.emit(op, target)

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
        self.worktrees.setEnabled(session.repo is not None)
        self.new_worktree.setEnabled(session.repo is not None)
        worktree = bool(self.selected(FileSource.WORKTREE))
        self.stage.setEnabled(worktree)
        self.discard.setEnabled(worktree)
        self.unstage.setEnabled(bool(self.selected(FileSource.INDEX)))
        self.stage_all.setEnabled(any(f.has_unstaged or f.is_untracked for f in session.status))
        staged = any(f.is_staged for f in session.status)
        self.unstage_all.setEnabled(staged)
        self.stash_changes.setEnabled(session.repo is not None and bool(session.status))
        history = session.history
        self.amend.setEnabled(session.repo is not None and bool(history))
        if self.amend.isChecked() and (not self.amend.isEnabled()
                                       or history[0].sha != self._amend_sha):
            self.amend.setChecked(False)  # another repo, or HEAD moved: calls back here
            return
        self.commit.setEnabled((staged or self.amend.isChecked()) and self._commit_ready
                               and session.repo is not None)
