"""What the user has selected, shared by every panel (spec §4.2).

Panels talk to each other through Selection instead of holding references to
one another: History sets the commit, and whatever shows a commit listens.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from rzgitclient.git.git_models import Commit

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


class Selection(QObject):
    commit_changed = Signal(object)  # Commit | None
    file_changed = Signal(object)  # FileSelection | None

    def __init__(self, session: RepoSession, parent: QObject | None = None):
        super().__init__(parent)
        self._commit: Commit | None = None
        self._file: FileSelection | None = None
        session.repo_changed.connect(self._on_repo_changed)

    @property
    def commit(self) -> Commit | None:
        return self._commit

    @commit.setter
    def commit(self, commit: Commit | None) -> None:
        if commit == self._commit:
            return
        self._commit = commit
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

    def clear(self) -> None:
        self.commit = None
        self.file = None

    def _on_repo_changed(self, _repo: object) -> None:
        self.clear()
