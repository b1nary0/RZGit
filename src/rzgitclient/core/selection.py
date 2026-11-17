"""What the user has selected, shared by every panel (spec §4.2).

Panels talk to each other through Selection instead of holding references to
one another: History sets the commit, and whatever shows a commit listens.
Selected files follow the status (§4.2), so the Unstaged and Staged panels
need not know each other.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from rzgitclient.git.git_models import Commit, FileStatus, Stash

if TYPE_CHECKING:  # repo_session imports FileSelection from here
    from rzgitclient.core.repo_session import RepoSession


class FileSource(StrEnum):
    """Which version of a file is selected."""

    WORKTREE = "worktree"
    INDEX = "index"
    COMMIT = "commit"


@dataclass(frozen=True)
class FileSelection:
    path: str
    source: FileSource
    commit: str | None = None  # set only when source is COMMIT

    def __post_init__(self) -> None:
        if (self.source is FileSource.COMMIT) != (self.commit is not None):
            raise ValueError("commit must be set when, and only when, source is COMMIT")


def file_rows(status: Iterable[FileStatus]) -> list[FileSelection]:
    """The rows the file panels show for ``status``, top to bottom: a worktree
    row for each unstaged or untracked file, then an index row for each staged one."""
    status = tuple(status)
    unstaged = [FileSelection(f.path, FileSource.WORKTREE)
                for f in status if f.is_untracked or f.has_unstaged]
    staged = [FileSelection(f.path, FileSource.INDEX) for f in status if f.is_staged]
    return unstaged + staged


def other_source(source: FileSource) -> FileSource:
    """Staged for unstaged and back (a commit's files have no other side)."""
    return {FileSource.WORKTREE: FileSource.INDEX,
            FileSource.INDEX: FileSource.WORKTREE}.get(source, source)


class Selection(QObject):
    commit_changed = Signal(object)  # Commit | None
    file_changed = Signal(object)  # FileSelection | None: the current row, what Diff shows
    files_changed = Signal(object)  # tuple[FileSelection, ...]: every selected row

    def __init__(self, session: RepoSession, parent: QObject | None = None):
        super().__init__(parent)
        self._commit: Commit | None = None
        self._file: FileSelection | None = None
        self._files: tuple[FileSelection, ...] = ()
        # Paths that Stage or Unstage is moving, and where to (see follow).
        self._follow: dict[str, FileSource] = {}
        session.repo_changed.connect(self._on_repo_changed)
        # Before any panel connects: they render what this decides.
        session.status_changed.connect(self._on_status)
        session.history_changed.connect(self._on_history)
        session.stashes_changed.connect(self._on_stashes)

    @property
    def commit(self) -> Commit | None:
        return self._commit

    @commit.setter
    def commit(self, commit: Commit | None) -> None:
        """The commit selected in History, or the Stash selected in the Stash
        panel; None for Uncommitted changes (§4.2).
        Only the selected files that belong to it are kept: its own, or the
        worktree and index rows for Uncommitted changes."""
        if commit == self._commit:
            return
        sha = commit.sha if commit is not None else None

        def kept(f: FileSelection | None) -> bool:
            return f is not None and f.commit == sha

        self._commit = commit
        self.files = tuple(f for f in self._files if kept(f))
        if self._file is not None and not kept(self._file):
            self.file = None
        self.commit_changed.emit(commit)

    @property
    def file(self) -> FileSelection | None:
        return self._file

    @file.setter
    def file(self, file: FileSelection | None) -> None:
        if file == self._file:
            return
        self._file = file
        self.file_changed.emit(file)

    @property
    def files(self) -> tuple[FileSelection, ...]:
        """Every selected file, in list order; what the stage actions act on."""
        return self._files

    @files.setter
    def files(self, files: tuple[FileSelection, ...]) -> None:
        files = tuple(files)
        if files == self._files:
            return
        self._files = files
        self.files_changed.emit(files)

    def select(self, files: Iterable[FileSelection], current: FileSelection | None) -> None:
        """The user selected ``files``, ``current`` among them: what file panels call.
        Cancels any follow (the user chose)."""
        self._follow.clear()
        self.files = tuple(files)
        self.file = current

    def follow(self, paths: Iterable[str], source: FileSource) -> None:
        """``paths`` are being moved to ``source`` (Stage, Unstage): once a status
        has their rows there, those rows are selected instead of the old ones."""
        self._follow.update(dict.fromkeys(paths, source))

    def clear(self) -> None:
        self._follow.clear()
        self.commit = None
        self.files = ()
        self.file = None

    def _on_repo_changed(self, _repo: object) -> None:
        self.clear()

    def _on_history(self, history: tuple[Commit, ...]) -> None:
        """A selected commit the history no longer has is gone (amend, reset,
        rebase): back to Uncommitted changes. A stash isn't in the history."""
        commit = self._commit
        if (commit is not None and not isinstance(commit, Stash)
                and all(c.sha != commit.sha for c in history)):
            self.commit = None

    def _on_stashes(self, stashes: tuple[Stash, ...]) -> None:
        """The selected stash, as the new list has it (its stash@{n} may have
        moved); back to Uncommitted changes if it's gone (popped, dropped)."""
        if isinstance(self._commit, Stash):
            sha = self._commit.sha
            self.commit = next((s for s in stashes if s.sha == sha), None)

    def _on_status(self, status: tuple[FileStatus, ...]) -> None:
        """Each selected file stays, moves where it was sent, moves to the same
        path's other side (changed outside the app), or is dropped."""
        if not self._files and self._file is None:
            return
        order = {row: i for i, row in enumerate(file_rows(status))}
        arrived: set[str] = set()

        def place(f: FileSelection) -> FileSelection | None:
            if f.source is FileSource.COMMIT:
                return f
            target = self._follow.get(f.path)
            if target is not None and (moved := FileSelection(f.path, target)) in order:
                arrived.add(f.path)
                return moved
            if f in order:
                return f
            other = FileSelection(f.path, other_source(f.source))
            return other if other in order else None

        placed = {p for f in self._files if (p := place(f)) is not None}
        current = place(self._file) if self._file is not None else None
        for path in arrived:
            del self._follow[path]
        self.files = tuple(sorted(placed, key=lambda f: order.get(f, -1)))
        self.file = current
