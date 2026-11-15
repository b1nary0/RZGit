"""Value types the git layer returns. Plain, frozen data: no Qt, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    date: datetime
    subject: str

    @property
    def short_sha(self) -> str:
        return self.sha[:8]


@dataclass(frozen=True)
class Branch:
    name: str
    is_current: bool


@dataclass(frozen=True)
class FileStatus:
    index: str  # staged status code (X in porcelain XY)
    worktree: str  # unstaged status code (Y in porcelain XY)
    path: str
    orig_path: str | None = None  # source path for renames/copies

    @property
    def is_untracked(self) -> bool:
        return self.index == "?"

    @property
    def is_staged(self) -> bool:
        return self.index not in (" ", "?")

    @property
    def has_unstaged(self) -> bool:
        return self.worktree != " " and not self.is_untracked
