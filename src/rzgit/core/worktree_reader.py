"""The worktrees of a window's repository and their state, kept up to date (spec §4.4).

Each main window has one, for its Worktrees panel. It follows the window's
session, but only reads, on a worker thread of its own, so it can't clash
with any session. While a panel is attached it watches the worktrees' git
dirs (not their files) and reads again what a change affects. QtCore only.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal

from rzgit.core.inotify_watcher import InotifyEvent, InotifyWatcher
from rzgit.core.repo_session import RepoSession
from rzgit.git.git_models import Worktree, WorktreeState
from rzgit.git.repo import GitError, Repo, canonical_path, list_worktrees, worktree_state

# The files of a git dir whose change can change a worktree's list entry or state.
_STATE_FILES = frozenset({"HEAD", "index", "locked", "gitdir"})
ALL = None  # every worktree: the paths of a request that reads them all


@dataclass(frozen=True)
class _Listing:
    """What a job found, before reading the states."""

    worktrees: tuple[Worktree, ...]
    folders: tuple[str, ...]  # to watch
    admin: dict[str, str]  # a linked worktree's git dir -> its worktree's path


class _Relay(QObject):
    done = Signal(object)  # (generation, kind, payload)


class WorktreeReader(QObject):
    worktrees_changed = Signal(object)  # tuple[Worktree, ...], the main worktree first
    state_changed = Signal(str, object)  # path, WorktreeState | None
    error_occurred = Signal(str)  # listing failed (once per new error); the list is empty

    def __init__(self, session: RepoSession, parent: QObject | None = None, *,
                 delay_ms: int = 300, max_delay_ms: int = 2000):
        super().__init__(parent)
        self._session = session
        self._repo: Repo | None = None
        self._common = ""
        self._users = 0  # attached panels
        self._worktrees: tuple[Worktree, ...] = ()
        self._states: dict[str, WorktreeState | None] = {}
        self._error: str | None = None
        # Bumped by another repository, detach() and shutdown(); older results are dropped.
        self._generation = 0
        self._running = False
        self._pending = False
        self._pending_paths: set[str] | None = set()  # ALL (None): every worktree
        self._active = False
        self._was_active = False
        self._admin: dict[str, str] = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._relay = _Relay()  # no parent: a job still running may emit on it
        self._relay.done.connect(self._on_done)
        # The change being collected (spec §4.3's merging).
        self._delay_ms, self._max_delay_ms = delay_ms, max_delay_ms
        self._changed: set[str] | None = set()
        self._first_change = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush)
        self._inotify = InotifyWatcher(self)
        self._inotify.events_read.connect(self._on_events)
        self._inotify.overflowed.connect(self.refresh)
        session.repo_changed.connect(self._on_repo_changed)
        self._set_repo(session.repo)

    # ---- state ---------------------------------------------------------------

    @property
    def repo(self) -> Repo | None:
        return self._repo

    @property
    def attached(self) -> bool:
        return self._users > 0

    @property
    def worktrees(self) -> tuple[Worktree, ...]:
        return self._worktrees

    @property
    def error(self) -> str | None:
        return self._error

    def state(self, path: str) -> WorktreeState | None:
        """None: not read yet, or the worktree is missing."""
        return self._states.get(path)

    @property
    def busy(self) -> bool:
        """Work is queued or running, or a change waits to be read."""
        return self._running or self._pending or self._timer.isActive()

    @property
    def watched(self) -> set[str]:
        return self._inotify.folders

    # ---- requests ------------------------------------------------------------

    def attach(self) -> None:
        """A panel shows the list: read it, and watch for changes."""
        self._users += 1
        if self._users == 1:
            self.refresh()

    def detach(self) -> None:
        """A panel that attached is gone. With none left, stop and forget."""
        self._users = max(0, self._users - 1)
        if self._users == 0:
            self._forget()

    def refresh(self) -> None:
        """Read the list, then every worktree's state, in the background. Does
        nothing while detached or with no repository open."""
        self._request(ALL)

    def set_active(self, active: bool) -> None:
        """The app became active or inactive. Becoming active again reads
        everything: unstaged edits change no git dir (spec §4.4)."""
        if active and self._was_active and not self._active:
            self.refresh()
        self._was_active |= active
        self._active = active

    def shutdown(self) -> None:
        """Drop queued work and wait for the running git command. Reads nothing after."""
        self._users = 0
        self._generation += 1
        self._pending = False
        self._timer.stop()
        self._inotify.close()
        self._pool.waitForDone()
        self._running = False

    def _on_repo_changed(self, repo: Repo | None) -> None:
        had = bool(self._worktrees) or self._error is not None
        self._forget()
        self._set_repo(repo)
        if had:
            self.worktrees_changed.emit(())  # the old repository's list is gone
        self.refresh()

    def _set_repo(self, repo: Repo | None) -> None:
        self._repo = repo
        self._common = str(repo.common_dir) if repo is not None else ""

    def _forget(self) -> None:
        """Drop the work, the list and the watches (quietly)."""
        self._generation += 1
        self._pending, self._pending_paths = False, set()
        self._timer.stop()
        self._changed = set()
        self._inotify.clear()
        self._worktrees, self._states, self._admin, self._error = (), {}, {}, None

    def _request(self, paths: set[str] | None) -> None:
        if self._users == 0 or self._repo is None:
            return
        self._pending = True
        if paths is ALL or self._pending_paths is ALL:
            self._pending_paths = ALL
        else:
            self._pending_paths |= paths
        if not self._running:
            self._start_next()

    def _start_next(self) -> None:
        if not self._pending:
            return
        paths, self._pending, self._pending_paths = self._pending_paths, False, set()
        known = frozenset(self._states)
        generation, relay, repo, common = self._generation, self._relay, self._repo, self._common
        assert repo is not None  # _request() checked
        self._running = True

        def run() -> None:
            for kind, payload in _run_job(repo, common, paths, known):
                relay.done.emit((generation, kind, payload))

        self._pool.start(run)

    # ---- results (on the main thread) -------------------------------------------

    def _on_done(self, result: tuple[int, str, object]) -> None:
        generation, kind, payload = result
        if kind == "done":  # whatever its generation: the worker is free
            self._running = False
            self._start_next()
            return
        if generation != self._generation:
            return
        if kind == "list":
            self._take_listing(payload)  # type: ignore[arg-type]
        elif kind == "error":
            new = payload != self._error
            self._error = str(payload)
            self._set_worktrees(())
            if new:  # not again for each change while it lasts
                self.error_occurred.emit(self._error)
        elif kind == "state":
            path, state = payload  # type: ignore[misc]
            if not any(w.path == path for w in self._worktrees):
                return
            changed = self._states.get(path) != state
            self._states[path] = state  # read, even if it failed (None)
            if changed:
                self.state_changed.emit(path, state)

    def _take_listing(self, listing: _Listing) -> None:
        self._error = None
        self._admin = listing.admin
        self._watch(listing.folders)
        self._set_worktrees(listing.worktrees)

    def _set_worktrees(self, worktrees: tuple[Worktree, ...]) -> None:
        listed = {w.path for w in worktrees if not w.missing}
        for path in [p for p in self._states if p not in listed]:
            old = self._states.pop(path)
            if old is not None and any(w.path == path for w in worktrees):
                self.state_changed.emit(path, None)  # it went missing
        if worktrees != self._worktrees:
            self._worktrees = worktrees
            self.worktrees_changed.emit(worktrees)

    # ---- watching (spec §4.4) -----------------------------------------------------

    def _watch(self, folders: tuple[str, ...]) -> None:
        wanted = set(folders)
        for folder in self._inotify.folders - wanted:
            self._inotify.remove(folder)
        for folder in wanted:
            self._inotify.add(folder)

    def affected(self, event: InotifyEvent) -> set[str] | None:
        """The worktrees whose state ``event`` may change: a set of paths, ALL,
        or an empty set for a change that changes nothing."""
        folder, name = event.folder, event.name
        if name.endswith(".lock"):
            return set()
        if folder == self._common:
            if name in ("HEAD", "index"):
                main = next((w.path for w in self._worktrees if w.main), None)
                return {main} if main is not None else ALL
            return ALL if name in ("packed-refs", "worktrees") else set()
        if folder == os.path.join(self._common, "worktrees"):
            return ALL
        if folder in self._admin:
            return {self._admin[folder]} if name in _STATE_FILES else set()
        return ALL  # a folder under refs/

    def _on_events(self, events: tuple[InotifyEvent, ...]) -> None:
        for event in events:
            paths = self.affected(event)
            if paths is ALL or self._changed is ALL:
                self._changed = ALL
            else:
                self._changed |= paths
        if self._changed is not ALL and not self._changed:
            return
        now = time.monotonic()
        if not self._timer.isActive():
            self._first_change = now
        left_ms = self._max_delay_ms - (now - self._first_change) * 1000
        self._timer.start(int(max(0, min(self._delay_ms, left_ms))))

    def _flush(self) -> None:
        changed, self._changed = self._changed, set()
        self._request(changed)


def _run_job(repo: Repo, common: str, paths: set[str] | None, known: frozenset[str]):
    """The worker side: list, then read the states asked for, one at a time.
    Yields ``(kind, payload)``; always ends with ``("done", None)``."""
    try:
        worktrees = tuple(list_worktrees(common))
    except GitError as exc:
        yield "error", str(exc)
        yield "done", None
        return
    yield "list", _Listing(worktrees, _watch_folders(common), _admin_dirs(common))
    for worktree in worktrees:
        if worktree.missing:
            continue
        if paths is not ALL and worktree.path not in paths and worktree.path in known:
            continue
        try:
            state: WorktreeState | None = worktree_state(worktree.path)
        except GitError:
            state = None  # gone meanwhile: the next listing says so
        yield "state", (worktree.path, state)
    yield "done", None


def _watch_folders(common: str) -> tuple[str, ...]:
    folders = [common]
    admin = os.path.join(common, "worktrees")
    if os.path.isdir(admin):
        folders.append(admin)
        folders.extend(e.path for e in os.scandir(admin) if e.is_dir(follow_symlinks=False))
    for top, _dirs, _files in os.walk(os.path.join(common, "refs")):
        folders.append(top)
    return tuple(folders)


def _admin_dirs(common: str) -> dict[str, str]:
    """Each linked worktree's git dir, and the path of the worktree it belongs
    to, from its ``gitdir`` file (``<worktree>/.git``)."""
    found: dict[str, str] = {}
    admin = os.path.join(common, "worktrees")
    if not os.path.isdir(admin):
        return found
    for entry in os.scandir(admin):
        try:
            gitdir = Path(entry.path, "gitdir").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        found[entry.path] = str(canonical_path(Path(gitdir).parent))
    return found
