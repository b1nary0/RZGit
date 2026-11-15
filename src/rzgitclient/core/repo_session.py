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

HISTORY_LIMIT = 500

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
        self._forced = Refresh.ALL
        self.repo_changed.emit(None)
        self._store_forced_as_empty()

    def shutdown(self) -> None:
        """Drop queued work and wait for the running git command (on quit)."""
        self._generation += 1
        self._pending = Refresh(0)
        self._pool.waitForDone()

    # ---- loading ------------------------------------------------------------

    def refresh(self, parts: Refresh = Refresh.ALL) -> None:
        """Reload ``parts`` from git in the background; changed ones are emitted.

        A request made while another job is waiting merges into it. A job
        stops at the first part that fails and sends one ``error_occurred``.
        Parts already loaded keep their new values; the rest keep the old.
        """
        if self._repo is None:
            return
        self._pending |= parts
        if not self._running:
            self._start_next()

    def _start_next(self) -> None:
        parts, self._pending = self._pending, Refresh(0)
        repo, generation, relay = self._repo, self._generation, self._relay
        self._running = True
        self._set_busy(True)
        self._pool.start(lambda: relay.done.emit(_run_job(repo, parts, generation)))

    def _on_done(self, result: _JobResult) -> None:
        self._running = False
        if result.generation == self._generation:
            for part, value in result.values:
                self._store(part, value)
            if result.error is not None:
                self.error_occurred.emit(result.error)
            self._store_forced_as_empty()  # parts a failed first load left behind
        if self._pending and self._repo is not None:
            self._start_next()
        else:
            self._pending = Refresh(0)
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
                self._store(part, _EMPTY[part])


# ---- the worker side (runs on the session's pool thread; no Qt objects) -----


@dataclass(frozen=True)
class _JobResult:
    generation: int
    values: tuple[tuple[Refresh, object], ...]  # in _PARTS order
    error: str | None = None


class _Relay(QObject):
    """Carries a job's result back to the main thread (a queued connection)."""

    done = Signal(object)  # _JobResult


def _run_job(repo: Repo, parts: Refresh, generation: int) -> _JobResult:
    values: list[tuple[Refresh, object]] = []
    for part in _PARTS:
        if part not in parts:
            continue
        try:
            values.append((part, _load(repo, part)))
        except GitError as exc:
            return _JobResult(generation, tuple(values), str(exc))
        except Exception as exc:  # a bug must not leave the session busy forever
            log.exception("Loading %s failed", part)
            return _JobResult(generation, tuple(values), f"Unexpected error: {exc}")
    return _JobResult(generation, tuple(values))


def _load(repo: Repo, part: Refresh) -> object:
    if part is Refresh.HEAD:
        return repo.current_branch()
    if part is Refresh.BRANCHES:
        return tuple(repo.branches())
    if part is Refresh.HISTORY:
        return tuple(repo.log(HISTORY_LIMIT))
    if part is Refresh.STATUS:
        return tuple(repo.status())
    raise ValueError(f"not a single part: {part!r}")
