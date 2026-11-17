"""Keeps the session up to date with changes on disk (spec §4.3).

RepoWatcher watches the git dir, its refs and the working tree's folders git
doesn't ignore, and turns what changed into one ``session.refresh(parts)``
per burst. Changes the app's own commands make are left to them. QtCore only.
"""

from __future__ import annotations

import errno
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal

from rzgitclient.core.git_commands import CommandKind, GitCommand
from rzgitclient.core.inotify_watcher import InotifyEvent, InotifyWatcher
from rzgitclient.core.repo_session import Refresh, RepoSession
from rzgitclient.git.repo import GitError, Repo

log = logging.getLogger(__name__)

# refs/stash is rewritten by every stash change, dropping an older one too.
REF_PARTS = Refresh.HEAD | Refresh.BRANCHES | Refresh.HISTORY | Refresh.STASHES | Refresh.COUNT
_REF_FILES = frozenset({"HEAD", "packed-refs"})  # in the git dir or the common dir
_WRITES_WORKTREE = frozenset({CommandKind.DISCARD, CommandKind.DISCARD_LINES})


@dataclass(frozen=True)
class _WatchSet:
    """What to watch, as a build job found it."""

    git_dir: str
    common_dir: str
    git_folders: tuple[str, ...]  # absolute: the git dir, the common dir, refs/**
    worktree: tuple[str, ...] | None  # relative ("" is the root); None: over the cap
    known: frozenset[str]  # relative files and folders git doesn't ignore


@dataclass(frozen=True)
class _CheckResult:
    kept: frozenset[str]  # new names git doesn't ignore
    folders: tuple[str, ...]  # new folders git doesn't ignore, with their subfolders
    watch_set: _WatchSet | None  # rebuilt, if asked


class _Relay(QObject):
    done = Signal(object)  # (generation, callback, result, error)


class RepoWatcher(QObject):
    def __init__(self, session: RepoSession, parent: QObject | None = None, *,
                 delay_ms: int = 300, max_delay_ms: int = 2000,
                 max_folders: int = 10_000, poll_ms: int = 3000):
        super().__init__(parent)
        self._session = session
        self._delay_ms = delay_ms
        self._max_delay_ms = max_delay_ms
        self._max_folders = max_folders
        self._inotify = InotifyWatcher(self)
        self._inotify.events_read.connect(self._on_events)
        self._inotify.overflowed.connect(self._on_overflow)
        self._repo: Repo | None = None
        self._generation = 0  # bumped per repository; older task results are dropped
        self._root = ""
        self._git_dir = ""
        self._common_dir = ""
        self._refs = ""
        self._git_folders: set[str] = set()  # absolute
        self._worktree: set[str] = set()  # relative folders watched
        self._known: set[str] = set()
        self._polling = False
        self._active = False
        self._was_active = False
        # The change being collected.
        self._parts = Refresh(0)
        self._unknown: set[str] = set()  # names to check with check-ignore
        self._new_folders: set[str] = set()
        self._rebuild = False
        self._first_change = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush)
        self._poll = QTimer(self)
        self._poll.setInterval(poll_ms)
        self._poll.timeout.connect(lambda: self._session.refresh(Refresh.STATUS))
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._relay = _Relay()  # no parent, like the session's
        self._relay.done.connect(self._on_task_done)
        self._tasks = 0  # queued or running
        self._flush_waiting = False  # the timer fired while a task ran
        session.repo_changed.connect(self._on_repo_changed)
        session.command_finished.connect(self._on_command_finished)
        if session.repo is not None:
            self._on_repo_changed(session.repo)

    # ---- state ---------------------------------------------------------------

    @property
    def busy(self) -> bool:
        """A change waits to be sent, or a background task runs."""
        return self._timer.isActive() or self._tasks > 0

    @property
    def polling(self) -> bool:
        """Over the cap: the working tree isn't watched, its status is polled."""
        return self._polling

    @property
    def folder_count(self) -> int:
        return len(self._worktree)

    def set_active(self, active: bool) -> None:
        """The window became active or inactive. Becoming active again
        refreshes everything, catching whatever the watcher missed."""
        if active and self._was_active and not self._active:
            self._session.refresh(Refresh.ALL)
        self._was_active |= active
        self._active = active
        self._update_poll()

    def shutdown(self) -> None:
        self._generation += 1
        self._timer.stop()
        self._poll.stop()
        self._inotify.close()
        self._pool.waitForDone()

    # ---- the watch set ---------------------------------------------------------

    def _on_repo_changed(self, repo: Repo | None) -> None:
        self._generation += 1
        self._inotify.clear()
        self._repo = repo
        self._git_folders.clear()
        self._worktree.clear()
        self._known.clear()
        self._parts = Refresh(0)
        self._unknown.clear()
        self._new_folders.clear()
        self._rebuild = False
        self._flush_waiting = False
        self._timer.stop()
        self._set_polling(False)
        if repo is None or not self._inotify.available:
            return
        self._root = str(repo.root)
        max_folders = self._max_folders
        self._start_task(lambda: _build(repo, max_folders), self._apply_watch_set)

    def _apply_watch_set(self, ws: _WatchSet) -> None:
        self._git_dir, self._common_dir = ws.git_dir, ws.common_dir
        self._refs = os.path.join(ws.common_dir, "refs")
        for folder in self._git_folders - set(ws.git_folders):
            self._inotify.remove(folder)
        self._git_folders = {f for f in ws.git_folders if self._inotify.add(f)}
        self._known = set(ws.known)
        wanted = set(ws.worktree or ())
        for rel in self._worktree - wanted:
            self._inotify.remove(self._abs(rel))
        self._worktree &= wanted
        if ws.worktree is None:
            self._over_cap()
            return
        self._set_polling(False)
        for rel in sorted(wanted - self._worktree):
            if not self._watch(rel):
                return

    def _watch(self, rel: str) -> bool:
        """Watch a working tree folder. False once over the cap."""
        if self._polling or len(self._worktree) >= self._max_folders:
            self._over_cap()
            return False
        if self._inotify.add(self._abs(rel)):
            self._worktree.add(rel)
        elif self._inotify.last_error == errno.ENOSPC:  # out of inotify watches
            self._over_cap()
            return False
        return True

    def _unwatch_tree(self, rel: str) -> None:
        for folder in [f for f in self._worktree if f == rel or f.startswith(rel + "/")]:
            self._inotify.remove(self._abs(folder))
            self._worktree.discard(folder)

    def _over_cap(self) -> None:
        for rel in self._worktree:
            self._inotify.remove(self._abs(rel))
        self._worktree.clear()
        self._set_polling(True)

    def _set_polling(self, polling: bool) -> None:
        self._polling = polling
        self._update_poll()

    def _update_poll(self) -> None:
        if self._polling and self._active and self._repo is not None:
            if not self._poll.isActive():
                self._poll.start()
        else:
            self._poll.stop()

    def _abs(self, rel: str) -> str:
        return os.path.join(self._root, rel) if rel else self._root

    def _rel(self, folder: str) -> str | None:
        if folder == self._root:
            return ""
        if folder.startswith(self._root + "/"):
            return folder[len(self._root) + 1:]
        return None

    # ---- events ----------------------------------------------------------------

    def _on_command_finished(self, _command: GitCommand, _ok: bool) -> None:
        # Its changes are in the kernel's queue by now: read them while the
        # command still counts as in flight, so they are dropped as its own.
        self._inotify.read_now()

    def _on_overflow(self) -> None:
        self._rebuild = True
        self._parts |= Refresh.ALL
        self._schedule()

    def _on_events(self, events: tuple[InotifyEvent, ...]) -> None:
        if self._repo is None:
            return
        in_flight = self._session.commands_in_flight
        own_paths = {p for c in in_flight if c.kind in _WRITES_WORKTREE for p in c.paths}
        changed = False
        for event in events:
            if not event.name:
                continue  # the folder itself: its parent reports it
            if event.folder in self._git_folders:
                changed |= self._on_git_event(event, bool(in_flight))
            else:
                changed |= self._on_worktree_event(event, own_paths)
        if changed:
            self._schedule()

    def _on_git_event(self, event: InotifyEvent, own: bool) -> bool:
        name, folder = event.name, event.folder
        if folder == self._refs or folder.startswith(self._refs + "/"):
            if event.is_dir and event.appeared:
                self._watch_refs(os.path.join(folder, name))
            parts = REF_PARTS
        elif name == "index":
            parts = Refresh.STATUS
        elif name in _REF_FILES:
            parts = REF_PARTS
        else:
            return False  # locks, logs, FETCH_HEAD, objects/…
        if name.endswith(".lock") or own:
            return False
        self._parts |= parts
        return True

    def _watch_refs(self, path: str) -> None:
        for folder, _dirs, _files in os.walk(path):
            if self._inotify.add(folder):
                self._git_folders.add(folder)

    def _on_worktree_event(self, event: InotifyEvent, own_paths: set[str]) -> bool:
        base = self._rel(event.folder)
        if base is None:
            return False
        name = event.name
        if base == "" and name == ".git":
            return False
        rel = f"{base}/{name}" if base else name
        if rel in own_paths:
            return False
        if event.is_dir:
            if event.vanished:
                if rel not in self._worktree and rel not in self._known:
                    return False  # it was ignored
                self._unwatch_tree(rel)
                self._parts |= Refresh.STATUS
                return True
            if event.appeared:
                self._new_folders.add(rel)
                return True
            return False
        if name == ".gitignore":
            self._rebuild = True
        if rel in self._known:
            self._parts |= Refresh.STATUS
        else:
            self._unknown.add(rel)
        return True

    # ---- sending one refresh per burst ------------------------------------------

    def _schedule(self) -> None:
        now = time.monotonic()
        if not self._timer.isActive():
            self._first_change = now
        left = self._max_delay_ms - (now - self._first_change) * 1000
        self._timer.start(int(max(0, min(self._delay_ms, left))))

    def _flush(self) -> None:
        if self._tasks:
            self._flush_waiting = True  # sent when the task is done
            return
        parts, self._parts = self._parts, Refresh(0)
        unknown, self._unknown = tuple(sorted(self._unknown)), set()
        new, self._new_folders = tuple(sorted(self._new_folders)), set()
        rebuild, self._rebuild = self._rebuild, False
        if not (unknown or new or rebuild):
            if parts:
                self._session.refresh(parts)
            return
        repo, max_folders = self._repo, self._max_folders
        self._start_task(lambda: _check(repo, unknown, new, rebuild, max_folders),
                         lambda result: self._apply_check(result, parts))

    def _apply_check(self, result: _CheckResult, parts: Refresh) -> None:
        if result.watch_set is not None:
            self._apply_watch_set(result.watch_set)
        elif not self._polling:
            for rel in result.folders:
                if not self._watch(rel):
                    break
        self._known |= result.kept | set(result.folders)
        if result.kept or result.folders or result.watch_set is not None:
            parts |= Refresh.STATUS
        if parts:
            self._session.refresh(parts)

    # ---- background tasks (read-only git, so they can't clash with the session) --

    def _start_task(self, work: Callable[[], object], done: Callable[[object], None]) -> None:
        generation, relay = self._generation, self._relay
        self._tasks += 1

        def run() -> None:
            try:
                result, error = work(), None
            except (GitError, OSError) as exc:
                result, error = None, exc
            relay.done.emit((generation, done, result, error))

        self._pool.start(run)

    def _on_task_done(self, payload: tuple) -> None:
        generation, done, result, error = payload
        self._tasks -= 1
        if generation == self._generation:
            if error is not None:
                log.warning("repo watcher: %s", error)
                self._session.refresh(Refresh.ALL)  # it can't tell what changed
            else:
                done(result)
        if self._flush_waiting and not self._tasks:
            self._flush_waiting = False
            self._flush()


# ---- the worker side --------------------------------------------------------------


def _build(repo: Repo, max_folders: int) -> _WatchSet:
    git_dir, common_dir = repo.git_dirs()
    git_folders = [str(git_dir), str(common_dir)]
    git_folders += [folder for folder, _dirs, _files in os.walk(common_dir / "refs")]
    files = repo.listed_files()
    folders = {""}
    for path in [*files, *(f + "/x" for f in repo.untracked_folders())]:
        folder = path.rpartition("/")[0]
        while folder and folder not in folders:
            folders.add(folder)
            folder = folder.rpartition("/")[0]
    worktree = tuple(sorted(folders)) if len(folders) <= max_folders else None
    return _WatchSet(str(git_dir), str(common_dir), tuple(dict.fromkeys(git_folders)),
                     worktree, frozenset(files) | frozenset(folders))


def _check(repo: Repo | None, unknown: tuple[str, ...], new: tuple[str, ...],
           rebuild: bool, max_folders: int) -> _CheckResult:
    if repo is None:
        return _CheckResult(frozenset(), (), None)
    ignored = repo.ignored(sorted({*unknown, *new}))
    kept = frozenset(p for p in unknown if p not in ignored)
    # Walk the new folders a level at a time, leaving out ignored ones.
    folders: list[str] = []
    level = [p for p in new if p not in ignored and (repo.root / p).is_dir()]
    while level and len(folders) <= max_folders:
        folders += level
        children = []
        for folder in level:
            children += _subfolders(repo.root, folder)
        skip = repo.ignored(children)
        level = [c for c in children if c not in skip]
    watch_set = _build(repo, max_folders) if rebuild else None
    return _CheckResult(kept, tuple(folders), watch_set)


def _subfolders(root: Path, folder: str) -> list[str]:
    try:
        with os.scandir(root / folder) as entries:
            return [f"{folder}/{e.name}" for e in entries
                    if e.name != ".git" and e.is_dir(follow_symlinks=False)]
    except OSError:
        return []
