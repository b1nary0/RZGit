"""Value types the git layer returns. Plain, frozen data: no Qt, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


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
    merged: bool = False  # its tip is in HEAD, so `git branch -d` can delete it


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


class DiffSource(StrEnum):
    """Which two versions of a file a diff compares."""

    WORKTREE = "worktree"  # the index against the working tree (unstaged changes)
    INDEX = "index"  # HEAD against the index (staged changes)
    UNTRACKED = "untracked"  # nothing against a new, untracked file
    COMMIT = "commit"  # a commit against its first parent (nothing, for the root)


class LineKind(StrEnum):
    """A diff line, by its first character in git's output."""

    CONTEXT = " "
    ADDED = "+"
    REMOVED = "-"


@dataclass(frozen=True)
class DiffLine:
    kind: LineKind
    old_no: int | None  # line number in the old file; None for an added line
    new_no: int | None  # line number in the new file; None for a removed line
    text: str  # without the kind character and the newline; a CR is kept
    no_newline: bool = False  # "\ No newline at end of file" follows this line


@dataclass(frozen=True)
class Hunk:
    header: str  # the whole "@@ -1,4 +1,5 @@ def f():" line
    old_start: int
    new_start: int
    lines: tuple[DiffLine, ...]


@dataclass(frozen=True)
class FileDiff:
    path: str
    hunks: tuple[Hunk, ...] = ()
    old_path: str | None = None  # set for a rename or copy
    binary: bool = False
    too_large: bool = False  # over the size limit: not parsed, no hunks
    line_count: int = 0  # lines of git's output; set even when too_large
