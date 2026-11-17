"""The open repository and its latest data (spec §4.1).

RepoSession is the single owner of "the open repository". It pushes each kind
of data to whoever listens through its own signal, emitted only when the value
changes. Git runs on one worker thread, one job at a time; signals are emitted
on the main thread.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Flag, auto
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal

from rzgitclient.core.diff_patch import Line
from rzgitclient.core.diff_request import DiffRequest, load_diff
from rzgitclient.core.git_commands import CommandKind, GitCommand, run_command
from rzgitclient.core.selection import FileSelection
from rzgitclient.git.git_models import (
    Branch,
    Commit,
    CommitDetails,
    FileDiff,
    FileStatus,
    HistoryOrder,
    LastCommit,
    Stash,
)
from rzgitclient.git.repo import GitError, Repo

HISTORY_PAGE = 500  # commits per page of history (spec §4.1)

log = logging.getLogger(__name__)


class Refresh(Flag):
    """Which parts of the repository's data to reload."""

    HEAD = auto()
    BRANCHES = auto()
    HISTORY = auto()
    STATUS = auto()
    COUNT = auto()  # commits reachable from HEAD; never in the same job as HISTORY
    STASHES = auto()
    ALL = HEAD | BRANCHES | HISTORY | STATUS | STASHES | COUNT
    LAST_COMMIT = auto()  # HEAD's message, for amending: only when asked for, not in ALL


# Reset by open() and close(): emitted for the new repo even if equal.
_RESET = Refresh.ALL | Refresh.LAST_COMMIT


# The order parts load and emit in (spec §4.1), and their value with no repo.
_PARTS = (Refresh.HEAD, Refresh.BRANCHES, Refresh.HISTORY, Refresh.STATUS, Refresh.STASHES,
          Refresh.COUNT, Refresh.LAST_COMMIT)
_EMPTY: dict[Refresh, object] = {
    Refresh.HEAD: (None, None),  # (branch, sha): two values, two signals (spec §4.1)
    Refresh.BRANCHES: (),
    Refresh.HISTORY: (),
    Refresh.STATUS: (),
    Refresh.STASHES: (),
    Refresh.COUNT: None,
    Refresh.LAST_COMMIT: None,
}


class RepoSession(QObject):
    repo_changed = Signal(object)  # Repo | None
    head_changed = Signal(object)  # str | None  (None = detached, or no repo)
    head_sha_changed = Signal(object)  # str | None  (None before the first commit, or no repo)
    branches_changed = Signal(object)  # tuple[Branch, ...]
    history_changed = Signal(object)  # tuple[Commit, ...]
    status_changed = Signal(object)  # tuple[FileStatus, ...]
    stashes_changed = Signal(object)  # tuple[Stash, ...], newest first
    commit_count_changed = Signal(object)  # int | None  (None: not counted yet)
    diff_changed = Signal(object, object)  # FileSelection | None, FileDiff | None
    commit_files_changed = Signal(object, object)  # sha | None, tuple[FileStatus, ...] | None
    last_commit_changed = Signal(object)  # LastCommit | None  (asked for: Refresh.LAST_COMMIT)
    commit_details_changed = Signal(object, object)  # sha | None, CommitDetails | None
    command_finished = Signal(object, bool)  # GitCommand, whether it ran without error
    error_occurred = Signal(str)
    notice_occurred = Signal(str)  # a command worked, but not quite as asked
    busy_changed = Signal(bool)  # True while git work is queued or running

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._repo: Repo | None = None
        # Bumped by open() and close(); results of older jobs are dropped.
        self._generation = 0
        self._pending = Refresh(0)  # parts waiting for the next job
        self._running = False
        self._busy = False
        self._history_complete = True  # nothing to page in until a load says otherwise
        self._more_pending = False  # a history page waits for the next job
        self._loading_more = False  # the running job loads a history page
        self._history_order = HistoryOrder.DATE  # how history is listed (spec §4.1)
        self._loaded_order: HistoryOrder | None = None  # the order ``history`` was read in
        # The diff target (spec §4.1). Each request bumps the counter, so the
        # results of older requests are dropped.
        self._diff_request: DiffRequest | None = None
        self._diff_generation = 0
        self._diff_pending = False  # the target waits for the next job
        # The latest result: its file, its diff, and whether it was read whole.
        self._diff: tuple[FileSelection | None, FileDiff | None, bool] = (None, None, False)
        # The commit files target, the same way (spec §4.1).
        self._files_sha: str | None = None
        self._files_generation = 0
        self._files_pending = False
        self._commit_files: tuple[str | None, tuple[FileStatus, ...] | None] = (None, None)
        # The commit details target, the same way; read again for its refs (spec §4.1).
        self._details_sha: str | None = None
        self._details_generation = 0
        self._details_pending = False
        self._commit_details: tuple[str | None, CommitDetails | None] = (None, None)
        self._commands: list[GitCommand] = []  # waiting for the next job, in order
        self._job_commands: tuple[GitCommand, ...] = ()  # the running job's
        # One thread: git commands on a repo can clash on index.lock, and
        # in-order jobs can't deliver results out of order.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        # No parent: a job still running when the session is deleted can
        # emit on it safely.
        self._relay = _Relay()
        self._relay.done.connect(self._on_done)
        self._values: dict[Refresh, object] = dict(_EMPTY)
        # Parts that must emit on their next store even if the value is equal:
        # after switching repos, equal data still belongs to a different repo.
        self._forced = Refresh(0)
        self._signals = {
            Refresh.HEAD: self.head_changed,
            Refresh.BRANCHES: self.branches_changed,
            Refresh.HISTORY: self.history_changed,
            Refresh.STATUS: self.status_changed,
            Refresh.STASHES: self.stashes_changed,
            Refresh.COUNT: self.commit_count_changed,
            Refresh.LAST_COMMIT: self.last_commit_changed,
        }

    # ---- latest values --------------------------------------------------

    @property
    def repo(self) -> Repo | None:
        return self._repo

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def head(self) -> str | None:
        return self._values[Refresh.HEAD][0]  # type: ignore[index]

    @property
    def head_sha(self) -> str | None:
        """The commit HEAD points at; None before the first commit, or with no repo."""
        return self._values[Refresh.HEAD][1]  # type: ignore[index]

    @property
    def branches(self) -> tuple[Branch, ...]:
        return self._values[Refresh.BRANCHES]  # type: ignore[return-value]

    @property
    def history(self) -> tuple[Commit, ...]:
        return self._values[Refresh.HISTORY]  # type: ignore[return-value]

    @property
    def history_order(self) -> HistoryOrder:
        return self._history_order

    @property
    def status(self) -> tuple[FileStatus, ...]:
        return self._values[Refresh.STATUS]  # type: ignore[return-value]

    @property
    def stashes(self) -> tuple[Stash, ...]:
        """The stashes, newest first."""
        return self._values[Refresh.STASHES]  # type: ignore[return-value]

    @property
    def commit_count(self) -> int | None:
        """Commits reachable from HEAD, however much history is loaded; None until counted."""
        return self._values[Refresh.COUNT]  # type: ignore[return-value]

    @property
    def last_commit(self) -> LastCommit | None:
        """HEAD's commit as last read by ``refresh(Refresh.LAST_COMMIT)``."""
        return self._values[Refresh.LAST_COMMIT]  # type: ignore[return-value]

    @property
    def history_complete(self) -> bool:
        """True once ``history`` reaches the first commit (and with no repo)."""
        return self._history_complete

    @property
    def diff_file(self) -> FileSelection | None:
        """The file the latest ``diff_changed`` was for."""
        return self._diff[0]

    @property
    def diff(self) -> FileDiff | None:
        """The latest diff, of ``diff_file``; None if it couldn't be read."""
        return self._diff[1]

    @property
    def diff_whole(self) -> bool:
        """Whether the latest diff was read as the whole file (spec §4.1)."""
        return self._diff[2]

    @property
    def commit_files_sha(self) -> str | None:
        """The commit the latest ``commit_files_changed`` was for."""
        return self._commit_files[0]

    @property
    def commit_files(self) -> tuple[FileStatus, ...] | None:
        """The files ``commit_files_sha`` changed; None if they couldn't be read."""
        return self._commit_files[1]

    @property
    def commit_details_sha(self) -> str | None:
        """The commit the latest ``commit_details_changed`` was for."""
        return self._commit_details[0]

    @property
    def commit_details(self) -> CommitDetails | None:
        """``commit_details_sha`` in full; None if it couldn't be read."""
        return self._commit_details[1]

    @property
    def commands_in_flight(self) -> tuple[GitCommand, ...]:
        """Commands queued or running, a job's until its ``command_finished``
        signals have gone out (the watcher tells the app's own changes by it)."""
        return self._job_commands + tuple(self._commands)

    # ---- opening and closing ------------------------------------------------

    def open(self, path: str | Path | Repo, *, report: bool = True) -> bool:
        """Open the repository containing ``path`` (or ``path`` itself, a ``Repo``
        already read: the window manager reads it first to find its window, §8.1).

        Checks the folder now; the data loads in the background. On failure,
        reports through ``error_occurred`` (unless not ``report``), keeps the
        current repository open and returns False.
        """
        try:
            repo = path if isinstance(path, Repo) else Repo(path)
        except GitError as exc:
            if report:
                self.error_occurred.emit(str(exc))
            return False
        self._repo = repo
        self._generation += 1
        self._pending = Refresh(0)
        self._more_pending = False
        self._history_complete = True
        self._forced = _RESET
        self._commands = []
        self._drop_diff()
        self._drop_commit_files()
        self._drop_commit_details()
        self.repo_changed.emit(repo)
        self.refresh()
        return True

    def close(self) -> None:
        if self._repo is None:
            return
        self._repo = None
        self._generation += 1
        self._pending = Refresh(0)
        self._more_pending = False
        self._forced = _RESET
        self._commands = []
        self._drop_diff()
        self._drop_commit_files()
        self._drop_commit_details()
        self.repo_changed.emit(None)
        self._store_forced_as_empty()

    def shutdown(self) -> None:
        """Drop queued work and wait for the running git command (on quit)."""
        self._generation += 1
        self._pending = Refresh(0)
        self._more_pending = False
        self._diff_pending = False
        self._files_pending = False
        self._details_pending = False
        self._commands = []
        self._pool.waitForDone()

    # ---- loading ------------------------------------------------------------

    def refresh(self, parts: Refresh = Refresh.ALL) -> None:
        """Reload ``parts`` from git in the background; changed ones are emitted.

        A request made while another job is waiting merges into it. A job
        stops at the first part that fails and sends one ``error_occurred``.
        Parts already loaded keep their new values; the rest keep the old.
        History reloads as many commits as are loaded, in whole pages.
        """
        if self._repo is None:
            return
        self._pending |= parts
        if not self._running:
            self._start_next()

    def load_more_history(self) -> None:
        """Load the next page of history in the background.

        Does nothing when history is complete or a page is already on its way.
        """
        if (self._repo is None or self._history_complete
                or self._more_pending or self._loading_more):
            return
        self._more_pending = True
        if not self._running:
            self._start_next()

    def set_history_order(self, order: HistoryOrder) -> None:
        """List history in ``order`` from now on, reloading it from the top."""
        if order is self._history_order:
            return
        self._history_order = order
        self.refresh(Refresh.HISTORY)

    def request_diff(self, file: FileSelection | None, full: bool = False,
                     whole: bool = False) -> None:
        """Make ``file`` the diff target and read its diff in the background.

        The result comes as ``diff_changed``; results of older requests are
        dropped. ``None`` clears the target. ``full`` reads a diff over the
        size limit anyway; ``whole`` reads the whole file, not only the changes.
        Jobs that load the status read the target again.
        """
        self._diff_generation += 1
        self._diff_request = DiffRequest(file, full, whole) if file is not None else None
        self._diff_pending = file is not None and self._repo is not None
        if file is None:
            self._store_diff(None, None)
        elif self._diff_pending and not self._running:
            self._start_next()

    def request_commit_files(self, sha: str | None) -> None:
        """Make ``sha`` the commit files target and read the files it changed in
        the background. The result comes as ``commit_files_changed``; results of
        older requests are dropped. ``None`` clears the target."""
        if sha == self._files_sha:
            return
        self._files_generation += 1
        self._files_sha = sha
        self._files_pending = sha is not None and self._repo is not None
        if sha is None:
            self._store_commit_files(None, None)
        elif self._files_pending and not self._running:
            self._start_next()

    def request_commit_details(self, sha: str | None) -> None:
        """Make ``sha`` the commit details target and read it in full in the
        background. The result comes as ``commit_details_changed``; results of
        older requests are dropped. ``None`` clears the target. Jobs that load
        the branches or history read it again, for its refs."""
        if sha == self._details_sha:
            return
        self._details_generation += 1
        self._details_sha = sha
        self._details_pending = sha is not None and self._repo is not None
        if sha is None:
            self._store_commit_details(None, None)
        elif self._details_pending and not self._running:
            self._start_next()

    # ---- commands (spec §4.1) ------------------------------------------------
    # Each returns the queued command, which ``command_finished`` reports, or
    # None with no repo.

    def stage(self, paths: Iterable[str]) -> GitCommand | None:
        """Stage ``paths`` (changes, new files, deletions) in the background."""
        return self._command(GitCommand(CommandKind.STAGE, tuple(paths)))

    def unstage(self, paths: Iterable[str]) -> GitCommand | None:
        """Unstage ``paths``; the working tree keeps its changes."""
        return self._command(GitCommand(CommandKind.UNSTAGE, tuple(paths)))

    def stage_all(self) -> GitCommand | None:
        return self._command(GitCommand(CommandKind.STAGE_ALL))

    def unstage_all(self) -> GitCommand | None:
        return self._command(GitCommand(CommandKind.UNSTAGE_ALL))

    def discard(self, paths: Iterable[str]) -> GitCommand | None:
        """Throw away the unstaged changes to ``paths``; staged ones are kept.
        Untracked files are moved to the trash. Callers confirm first."""
        return self._command(GitCommand(CommandKind.DISCARD, tuple(paths)))

    def stage_lines(self, diff: FileDiff, lines: Iterable[Line]) -> GitCommand | None:
        """Stage the chosen ``(hunk, line)`` lines of ``diff``, the shown worktree diff."""
        return self._lines(CommandKind.STAGE_LINES, diff, lines)

    def unstage_lines(self, diff: FileDiff, lines: Iterable[Line]) -> GitCommand | None:
        """Unstage the chosen lines of ``diff``, the shown index diff."""
        return self._lines(CommandKind.UNSTAGE_LINES, diff, lines)

    def discard_lines(self, diff: FileDiff, lines: Iterable[Line]) -> GitCommand | None:
        """Throw away the chosen lines of ``diff`` from the working tree. No
        undo: callers confirm first."""
        return self._lines(CommandKind.DISCARD_LINES, diff, lines)

    def _lines(self, kind: CommandKind, diff: FileDiff,
               lines: Iterable[Line]) -> GitCommand | None:
        return self._command(GitCommand(kind, (diff.path,), diff=diff, lines=frozenset(lines)))

    def commit(self, message: str) -> GitCommand | None:
        """Commit the staged changes with ``message``."""
        return self._command(GitCommand(CommandKind.COMMIT, message=message),
                             Refresh.HEAD | Refresh.BRANCHES | Refresh.HISTORY
                             | Refresh.STATUS | Refresh.COUNT)

    # Branches (spec §4.1, "Branch commands").

    def switch_branch(self, name: str, stash: bool = False) -> GitCommand | None:
        """Switch to ``name``; with ``stash``, stash every change first."""
        return self._command(GitCommand(CommandKind.SWITCH, branch=name, stash=stash),
                             Refresh.ALL)

    def create_branch(self, name: str, start: str, switch: bool = False) -> GitCommand | None:
        """A new branch ``name`` at ``start``; ``switch`` to it too."""
        return self._command(
            GitCommand(CommandKind.CREATE_BRANCH, branch=start, new_name=name, switch=switch),
            Refresh.ALL if switch else Refresh.BRANCHES)

    def rename_branch(self, old: str, new: str) -> GitCommand | None:
        return self._command(GitCommand(CommandKind.RENAME_BRANCH, branch=old, new_name=new),
                             Refresh.HEAD | Refresh.BRANCHES)

    def delete_branch(self, name: str, force: bool = False) -> GitCommand | None:
        """Delete ``name``; git refuses an unmerged one unless ``force``.
        Callers confirm first."""
        return self._command(GitCommand(CommandKind.DELETE_BRANCH, branch=name, force=force),
                             Refresh.BRANCHES)

    def merge(self, name: str) -> GitCommand | None:
        """Merge ``name`` into the current branch; a conflict aborts it."""
        return self._command(GitCommand(CommandKind.MERGE, branch=name), Refresh.ALL)

    def rebase(self, onto: str) -> GitCommand | None:
        """Rebase the current branch onto ``onto``; a conflict aborts it.
        Callers confirm first: it rewrites commits."""
        return self._command(GitCommand(CommandKind.REBASE, branch=onto), Refresh.ALL)

    # Stashes (spec §4.1, "Stash commands"): by sha, which doesn't move.

    def stash_changes(self, message: str, untracked: bool = True) -> GitCommand | None:
        """Stash the changes, with ``untracked`` files too; "" for git's own message."""
        return self._command(GitCommand(CommandKind.STASH_PUSH, message=message,
                                        untracked=untracked),
                             Refresh.STATUS | Refresh.STASHES)

    def apply_stash(self, sha: str, pop: bool = False) -> GitCommand | None:
        """Apply stash ``sha`` to the clean working tree, rolled back if it fails;
        with ``pop``, drop it once applied."""
        return self._command(GitCommand(CommandKind.STASH_APPLY, sha=sha, pop=pop),
                             Refresh.STATUS | Refresh.STASHES)

    def drop_stash(self, sha: str) -> GitCommand | None:
        """Drop stash ``sha``. No undo: callers confirm first."""
        return self._command(GitCommand(CommandKind.STASH_DROP, sha=sha), Refresh.STASHES)

    # Worktrees (spec §4.1): the list is the WorktreeReader's (§4.4), so they reload
    # only the branches.
    def add_worktree(self, path: str, branch: str,
                     start: str | None = None) -> GitCommand | None:
        """A worktree at ``path`` on a new branch ``branch`` from ``start``, or
        on the existing ``branch`` without ``start``."""
        return self._command(GitCommand(CommandKind.WORKTREE_ADD, path=str(path),
                                        branch=branch, start=start), Refresh.BRANCHES)

    def remove_worktree(self, path: str, force: bool = False) -> GitCommand | None:
        """Delete the worktree at ``path``; git refuses one with changes unless
        ``force``. Callers confirm first."""
        return self._command(GitCommand(CommandKind.WORKTREE_REMOVE, path=str(path),
                                        force=force), Refresh.BRANCHES)

    def prune_worktrees(self) -> GitCommand | None:
        """Drop the records of missing worktrees. Callers confirm first."""
        return self._command(GitCommand(CommandKind.WORKTREE_PRUNE), Refresh.BRANCHES)

    def amend(self, message: str) -> GitCommand | None:
        """Replace HEAD's commit with the staged changes, if any, and ``message``."""
        return self._command(GitCommand(CommandKind.AMEND, message=message), Refresh.ALL)

    def _command(self, command: GitCommand, parts: Refresh = Refresh.STATUS) -> GitCommand | None:
        """Queue ``command``; its job then loads ``parts``, what it changes."""
        if self._repo is None:
            return None
        self._commands.append(command)
        self.refresh(parts)
        return command

    def _drop_diff(self) -> None:
        """Forget the target and its diff (the repo changes)."""
        self._diff_generation += 1
        self._diff_request = None
        self._diff_pending = False
        self._store_diff(None, None)

    def _drop_commit_files(self) -> None:
        self._files_generation += 1
        self._files_sha = None
        self._files_pending = False
        self._store_commit_files(None, None)

    def _drop_commit_details(self) -> None:
        self._details_generation += 1
        self._details_sha = None
        self._details_pending = False
        self._store_commit_details(None, None)

    def _start_next(self) -> None:
        parts, self._pending = self._pending, Refresh(0)
        more, self._more_pending = self._more_pending, False
        diff = self._diff_request
        if not (self._diff_pending or (Refresh.STATUS in parts and diff and diff.follows_status)):
            diff = None
        self._diff_pending = False
        files_sha = self._files_sha if self._files_pending else None
        files_stash = next((s for s in self.stashes if s.sha == files_sha), None)
        self._files_pending = False
        refs_may_change = bool(parts & (Refresh.BRANCHES | Refresh.HISTORY))
        details_sha = self._details_sha if self._details_pending or refs_may_change else None
        self._details_pending = False
        commands, self._commands = tuple(self._commands), []
        self._job_commands = commands
        if Refresh.COUNT in parts and (Refresh.HISTORY in parts or more):
            # Count in the next job, so it doesn't delay the history.
            parts &= ~Refresh.COUNT
            self._pending |= Refresh.COUNT
        # The first load of a repo starts from nothing, not the old repo's history;
        # so does the first in another order.
        fresh = Refresh.HISTORY in self._forced or self._loaded_order is not self._history_order
        loaded = () if fresh else self.history
        known = self.commit_files or ()
        job = _Job(self._repo, self._generation, parts, loaded, more,  # type: ignore[arg-type]
                   diff, self._diff_generation, self.status, commands,
                   files_sha, self._files_generation, known,
                   details_sha, self._details_generation, files_stash, self._history_order)
        relay = self._relay
        self._running = True
        self._loading_more = more
        self._set_busy(True)
        self._pool.start(lambda: relay.done.emit(
            dataclasses.replace(_run_job(job), history_order=job.history_order)))

    def _on_done(self, result: _JobResult) -> None:
        # _running stays True while the results are emitted: a listener that
        # asks for more (a refresh, the next page) only queues it, and it
        # starts below. Otherwise it would start mid-way and busy would lie.
        self._loading_more = False  # this page has arrived; the next may be asked for
        if result.generation == self._generation:
            if result.history_complete is not None:  # before history_changed fires
                self._history_complete = result.history_complete
                self._loaded_order = result.history_order
            for part, value in result.values:
                self._store(part, value)
            if result.files_generation == self._files_generation and self._files_sha:
                if result.files_read:
                    self._store_commit_files(self._files_sha, result.files)
                elif result.files_wanted:  # an earlier part failed: read them by themselves
                    self._files_pending = True
            if result.details_generation == self._details_generation and self._details_sha:
                if result.details_read:
                    self._store_commit_details(self._details_sha, result.details)
                elif result.details_wanted:  # an earlier part failed: read it by itself
                    self._details_pending = True
            # After the parts: a listener of status_changed may have changed the target.
            if result.diff_generation == self._diff_generation and self._diff_request:
                if result.diff_read:
                    self._store_diff(self._diff_request.file, result.diff,
                                     self._diff_request.whole)
                elif result.diff_wanted:  # an earlier part failed: read it by itself
                    self._diff_pending = True
            if result.error is not None:
                self.error_occurred.emit(result.error)
                if not result.command_failed:
                    # A count queued behind a failed load would fail too: one error is enough.
                    self._pending &= ~Refresh.COUNT
            self._store_forced_as_empty()  # parts a failed first load left behind
            for notice in result.notices:
                self.notice_occurred.emit(notice)
            for i, command in enumerate(result.commands):
                self.command_finished.emit(command, i < result.commands_ok)
        self._job_commands = ()
        self._running = False
        if (self._pending or self._more_pending or self._diff_pending
                or self._files_pending or self._details_pending) and self._repo is not None:
            self._start_next()
        else:
            self._pending = Refresh(0)
            self._more_pending = False
            self._diff_pending = False
            self._files_pending = False
            self._details_pending = False
            self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        if busy != self._busy:
            self._busy = busy
            self.busy_changed.emit(busy)

    def _store(self, part: Refresh, value: object) -> None:
        forced = part in self._forced
        self._forced &= ~part
        old = self._values[part]
        if not forced and old == value:
            return
        self._values[part] = value
        if part is Refresh.HEAD:
            # (branch, sha): each has its own signal, emitted only for its own change.
            for i, signal in enumerate((self.head_changed, self.head_sha_changed)):
                if forced or old[i] != value[i]:  # type: ignore[index]
                    signal.emit(value[i])  # type: ignore[index]
        else:
            self._signals[part].emit(value)

    def _store_diff(self, file: FileSelection | None, diff: FileDiff | None,
                    whole: bool = False) -> None:
        if (file, diff, whole) == self._diff:
            return
        self._diff = (file, diff, whole)
        self.diff_changed.emit(file, diff)

    def _store_commit_files(self, sha: str | None,
                            files: tuple[FileStatus, ...] | None) -> None:
        if (sha, files) == self._commit_files:
            return
        self._commit_files = (sha, files)
        self.commit_files_changed.emit(sha, files)

    def _store_commit_details(self, sha: str | None, details: CommitDetails | None) -> None:
        if (sha, details) == self._commit_details:
            return
        self._commit_details = (sha, details)
        self.commit_details_changed.emit(sha, details)

    def _store_forced_as_empty(self) -> None:
        for part in _PARTS:
            if part in self._forced and part not in self._pending:  # queued: still coming
                if part is Refresh.HISTORY:
                    self._history_complete = True
                self._store(part, _EMPTY[part])


# ---- the worker side (runs on the session's pool thread; no Qt objects) -----


@dataclass(frozen=True)
class _Job:
    repo: Repo
    generation: int
    parts: Refresh
    loaded: tuple[Commit, ...]  # history so far; shared safely, it's immutable
    more: bool  # add a page of history
    diff: DiffRequest | None  # read after the parts
    diff_generation: int
    status: tuple[FileStatus, ...]  # the latest status, for the diff, unless STATUS loads
    commands: tuple[GitCommand, ...] = ()  # run first, in order
    files_sha: str | None = None  # the commit whose files to read, after the parts
    files_generation: int = 0
    commit_files: tuple[FileStatus, ...] = ()  # the latest, for a commit file's diff
    details_sha: str | None = None  # the commit to read in full, after its files
    details_generation: int = 0
    files_stash: Stash | None = None  # files_sha's stash, if it is one
    history_order: HistoryOrder = HistoryOrder.DATE


@dataclass(frozen=True)
class _JobResult:
    generation: int
    values: tuple[tuple[Refresh, object], ...]  # in _PARTS order
    history_complete: bool | None = None  # None: history wasn't loaded
    error: str | None = None
    diff_generation: int = 0
    diff_wanted: bool = False  # the job was to read the diff
    diff_read: bool = False  # it tried; ``diff`` is None if that failed
    diff: FileDiff | None = None
    command_failed: bool = False  # ``error`` is a command's; the parts still loaded
    commands: tuple[GitCommand, ...] = ()  # the job's
    commands_ok: int = 0  # how many of them ran without error, from the first
    files_generation: int = 0
    files_wanted: bool = False  # the job was to read a commit's files
    files_read: bool = False  # it tried; ``files`` is None if that failed
    files: tuple[FileStatus, ...] | None = None
    details_generation: int = 0
    details_wanted: bool = False  # the job was to read a commit's details
    details_read: bool = False  # it tried; ``details`` is None if that failed
    details: CommitDetails | None = None
    notices: tuple[str, ...] = ()  # the commands'
    history_order: HistoryOrder = HistoryOrder.DATE  # the job's (history_complete says if it loaded)


class _Relay(QObject):
    """Carries a job's result back to the main thread (a queued connection)."""

    done = Signal(object)  # _JobResult


def _run_job(job: _Job) -> _JobResult:
    values: list[tuple[Refresh, object]] = []
    complete: bool | None = None
    diff: dict[str, object] = {"diff_generation": job.diff_generation,
                               "diff_wanted": job.diff is not None,
                               "files_generation": job.files_generation,
                               "files_wanted": job.files_sha is not None,
                               "details_generation": job.details_generation,
                               "details_wanted": job.details_sha is not None}
    notices: list[str] = []
    for ran, command in enumerate(job.commands):
        try:
            notice = run_command(job.repo, command)
        except Exception as exc:
            # Skip the commands after it, but load the parts: show what really happened.
            loaded = _run_job(dataclasses.replace(job, commands=()))
            # The command's error comes first: it happened first.
            return dataclasses.replace(loaded, error=_error(exc, command.kind),
                                       command_failed=True, commands=job.commands,
                                       commands_ok=ran, notices=tuple(notices))
        if notice:
            notices.append(notice)
    if job.commands:
        loaded = _run_job(dataclasses.replace(job, commands=()))
        return dataclasses.replace(loaded, commands=job.commands, commands_ok=len(job.commands),
                                   notices=tuple(notices))
    for part in _PARTS:
        wanted = part in job.parts or (part is Refresh.HISTORY and job.more)
        if not wanted:
            continue
        try:
            if part is Refresh.HISTORY:
                value, complete = _load_history(job)
            else:
                value = _load(job.repo, part)
        except Exception as exc:
            return _JobResult(job.generation, tuple(values), complete, _error(exc, part), **diff)
        values.append((part, value))
    commit_files = job.commit_files
    if job.files_sha is not None:
        try:
            if job.files_stash is not None:
                commit_files = tuple(job.repo.stash_files(job.files_stash))
            else:
                commit_files = tuple(job.repo.commit_files(job.files_sha))
        except Exception as exc:
            return _JobResult(job.generation, tuple(values), complete,
                              _error(exc, "the commit's files"), **diff, files_read=True)
        diff.update(files_read=True, files=commit_files)
    if job.details_sha is not None:
        try:
            details = job.repo.commit_details(job.details_sha)
        except Exception as exc:
            return _JobResult(job.generation, tuple(values), complete,
                              _error(exc, "the commit's details"), **diff, details_read=True)
        diff.update(details_read=True, details=details)
    if job.diff is None:
        return _JobResult(job.generation, tuple(values), complete, **diff)
    status = dict(values).get(Refresh.STATUS, job.status)
    try:
        value = load_diff(job.repo, job.diff, status, commit_files)  # type: ignore[arg-type]
    except Exception as exc:
        return _JobResult(job.generation, tuple(values), complete, _error(exc, "the diff"),
                          **diff, diff_read=True)
    return _JobResult(job.generation, tuple(values), complete, **diff, diff_read=True, diff=value)


def _error(exc: Exception, what: object) -> str:
    """The message for a failed load. A bug must not leave the session busy forever."""
    if isinstance(exc, GitError):
        return str(exc)
    log.exception("Loading %s failed", what, exc_info=exc)
    return f"Unexpected error: {exc}"


def _load(repo: Repo, part: Refresh) -> object:
    if part is Refresh.HEAD:
        return repo.current_branch(), repo.head_sha()
    if part is Refresh.BRANCHES:
        return tuple(repo.branches())
    if part is Refresh.STATUS:
        return tuple(repo.status())
    if part is Refresh.STASHES:
        return tuple(repo.stashes())
    if part is Refresh.COUNT:
        return repo.commit_count()
    if part is Refresh.LAST_COMMIT:
        return repo.last_commit()
    raise ValueError(f"not a single part: {part!r}")


def _load_history(job: _Job) -> tuple[tuple[Commit, ...], bool | None]:
    """The commits to show, and whether they reach the first commit (None: unknown).

    Reads ask git for one commit more than they keep, to know whether there
    are more.
    """
    loaded, page = job.loaded, HISTORY_PAGE
    if loaded and job.repo.head_sha() == loaded[0].sha:
        # HEAD hasn't moved, so the loaded commits are still exactly right.
        if not job.more:
            return loaded, None
        new = job.repo.log(page + 1, skip=len(loaded), order=job.history_order)
        return loaded + tuple(new[:page]), len(new) <= page
    # Reload. Read a page more than was loaded, for commits that are new on
    # top, then keep everything down to the oldest commit loaded before, so
    # what the user scrolled to and selected is still there.
    pages = max(1, -(-len(loaded) // page)) * page  # loaded, in whole pages
    extra = page if job.more else 0
    want = pages + (page if loaded else 0) + extra
    commits = job.repo.log(want + 1, order=job.history_order)
    keep = pages + extra
    if loaded:
        oldest = loaded[-1].sha
        found = next((i for i, c in enumerate(commits) if c.sha == oldest), -1)
        if found >= 0:
            keep = min(want, found + 1 + extra)
    return tuple(commits[:keep]), len(commits) <= keep
