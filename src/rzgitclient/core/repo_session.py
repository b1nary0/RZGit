"""The open repository and its latest data (spec §4.1).

RepoSession is the single owner of "the open repository". It pushes each kind
of data to whoever listens through its own signal, emitted only when the value
changes. Git runs on one worker thread, one job at a time; signals are emitted
on the main thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Flag, auto
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal

from rzgitclient.git.git_models import Branch, Commit, FileStatus
from rzgitclient.git.repo import GitError, Repo

HISTORY_PAGE = 500  # commits per page of history (spec §4.1)

log = logging.getLogger(__name__)


class Refresh(Flag):
    """Which parts of the repository's data to reload."""

    HEAD = auto()
    BRANCHES = auto()
    HISTORY = auto()
    STATUS = auto()
    ALL = HEAD | BRANCHES | HISTORY | STATUS


# The order parts load and emit in (spec §4.1), and their value with no repo.
_PARTS = (Refresh.HEAD, Refresh.BRANCHES, Refresh.HISTORY, Refresh.STATUS)
_EMPTY: dict[Refresh, object] = {
    Refresh.HEAD: None,
    Refresh.BRANCHES: (),
    Refresh.HISTORY: (),
    Refresh.STATUS: (),
}


class RepoSession(QObject):
    repo_changed = Signal(object)  # Repo | None
    head_changed = Signal(object)  # str | None  (None = detached, or no repo)
    branches_changed = Signal(object)  # tuple[Branch, ...]
    history_changed = Signal(object)  # tuple[Commit, ...]
    status_changed = Signal(object)  # tuple[FileStatus, ...]
    error_occurred = Signal(str)
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
        return self._values[Refresh.HEAD]  # type: ignore[return-value]

    @property
    def branches(self) -> tuple[Branch, ...]:
        return self._values[Refresh.BRANCHES]  # type: ignore[return-value]

    @property
    def history(self) -> tuple[Commit, ...]:
        return self._values[Refresh.HISTORY]  # type: ignore[return-value]

    @property
    def status(self) -> tuple[FileStatus, ...]:
        return self._values[Refresh.STATUS]  # type: ignore[return-value]

    @property
    def history_complete(self) -> bool:
        """True once ``history`` reaches the first commit (and with no repo)."""
        return self._history_complete

    # ---- opening and closing ------------------------------------------------

    def open(self, path: str | Path) -> bool:
        """Open the repository containing ``path``.

        Checks the folder now; the data loads in the background. On failure,
        reports through ``error_occurred``, keeps the current repository open
        and returns False.
        """
        try:
            repo = Repo(path)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return False
        self._repo = repo
        self._generation += 1
        self._pending = Refresh(0)
        self._more_pending = False
        self._history_complete = True
        self._forced = Refresh.ALL
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
        self._forced = Refresh.ALL
        self.repo_changed.emit(None)
        self._store_forced_as_empty()

    def shutdown(self) -> None:
        """Drop queued work and wait for the running git command (on quit)."""
        self._generation += 1
        self._pending = Refresh(0)
        self._more_pending = False
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

    def _start_next(self) -> None:
        parts, self._pending = self._pending, Refresh(0)
        more, self._more_pending = self._more_pending, False
        # The first load of a repo starts from nothing, not the old repo's history.
        loaded = () if Refresh.HISTORY in self._forced else self.history
        job = _Job(self._repo, self._generation, parts, loaded, more)  # type: ignore[arg-type]
        relay = self._relay
        self._running = True
        self._loading_more = more
        self._set_busy(True)
        self._pool.start(lambda: relay.done.emit(_run_job(job)))

    def _on_done(self, result: _JobResult) -> None:
        # _running stays True while the results are emitted: a listener that
        # asks for more (a refresh, the next page) only queues it, and it
        # starts below. Otherwise it would start mid-way and busy would lie.
        self._loading_more = False  # this page has arrived; the next may be asked for
        if result.generation == self._generation:
            if result.history_complete is not None:  # before history_changed fires
                self._history_complete = result.history_complete
            for part, value in result.values:
                self._store(part, value)
            if result.error is not None:
                self.error_occurred.emit(result.error)
            self._store_forced_as_empty()  # parts a failed first load left behind
        self._running = False
        if (self._pending or self._more_pending) and self._repo is not None:
            self._start_next()
        else:
            self._pending = Refresh(0)
            self._more_pending = False
            self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        if busy != self._busy:
            self._busy = busy
            self.busy_changed.emit(busy)

    def _store(self, part: Refresh, value: object) -> None:
        forced = part in self._forced
        self._forced &= ~part
        if not forced and self._values[part] == value:
            return
        self._values[part] = value
        self._signals[part].emit(value)

    def _store_forced_as_empty(self) -> None:
        for part in _PARTS:
            if part in self._forced:
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


@dataclass(frozen=True)
class _JobResult:
    generation: int
    values: tuple[tuple[Refresh, object], ...]  # in _PARTS order
    history_complete: bool | None = None  # None: history wasn't loaded
    error: str | None = None


class _Relay(QObject):
    """Carries a job's result back to the main thread (a queued connection)."""

    done = Signal(object)  # _JobResult


def _run_job(job: _Job) -> _JobResult:
    values: list[tuple[Refresh, object]] = []
    complete: bool | None = None
    for part in _PARTS:
        wanted = part in job.parts or (part is Refresh.HISTORY and job.more)
        if not wanted:
            continue
        try:
            if part is Refresh.HISTORY:
                value, complete = _load_history(job)
            else:
                value = _load(job.repo, part)
        except GitError as exc:
            return _JobResult(job.generation, tuple(values), complete, str(exc))
        except Exception as exc:  # a bug must not leave the session busy forever
            log.exception("Loading %s failed", part)
            return _JobResult(job.generation, tuple(values), complete,
                              f"Unexpected error: {exc}")
        values.append((part, value))
    return _JobResult(job.generation, tuple(values), complete)


def _load(repo: Repo, part: Refresh) -> object:
    if part is Refresh.HEAD:
        return repo.current_branch()
    if part is Refresh.BRANCHES:
        return tuple(repo.branches())
    if part is Refresh.STATUS:
        return tuple(repo.status())
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
        new = job.repo.log(page + 1, skip=len(loaded))
        return loaded + tuple(new[:page]), len(new) <= page
    # Reload. Read a page more than was loaded, for commits that are new on
    # top, then keep everything down to the oldest commit loaded before, so
    # what the user scrolled to and selected is still there.
    pages = max(1, -(-len(loaded) // page)) * page  # loaded, in whole pages
    extra = page if job.more else 0
    want = pages + (page if loaded else 0) + extra
    commits = job.repo.log(want + 1)
    keep = pages + extra
    if loaded:
        oldest = loaded[-1].sha
        found = next((i for i, c in enumerate(commits) if c.sha == oldest), -1)
        if found >= 0:
            keep = min(want, found + 1 + extra)
    return tuple(commits[:keep]), len(commits) <= keep
