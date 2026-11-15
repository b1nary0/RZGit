"""The open repository and its latest data (spec §4.1).

RepoSession is the single owner of "the open repository". It pushes each kind
of data to whoever listens through its own signal, emitted only when the value
changes. Nothing else runs git for the UI, so moving git off the UI thread
later only changes this class.
"""

from __future__ import annotations

from enum import Flag, auto
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from rzgitclient.git.git_models import Branch, Commit, FileStatus
from rzgitclient.git.repo import GitError, Repo

HISTORY_LIMIT = 500


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

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._repo: Repo | None = None
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

        On failure, reports through ``error_occurred``, keeps the current
        repository open and returns False.
        """
        try:
            repo = Repo(path)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return False
        self._repo = repo
        self._forced = Refresh.ALL
        self.repo_changed.emit(repo)
        self.refresh()
        self._store_forced_as_empty()  # parts a failed load left behind
        return True

    def close(self) -> None:
        if self._repo is None:
            return
        self._repo = None
        self._forced = Refresh.ALL
        self.repo_changed.emit(None)
        self._store_forced_as_empty()

    # ---- loading ------------------------------------------------------------

    def refresh(self, parts: Refresh = Refresh.ALL) -> None:
        """Reload ``parts`` from git and emit the ones that changed.

        Stops at the first part that fails and sends one ``error_occurred``.
        Parts already loaded keep their new values; the rest keep the old.
        """
        if self._repo is None:
            return
        for part in _PARTS:
            if part not in parts:
                continue
            try:
                value = self._load(self._repo, part)
            except GitError as exc:
                self.error_occurred.emit(str(exc))
                return
            self._store(part, value)

    @staticmethod
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
