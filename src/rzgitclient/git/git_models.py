"""Value types the git layer returns. Plain, frozen data: no Qt, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    date: datetime
    subject: str
    # The parents' shas, first parent first (History's graph, spec §4.1). Keyword-only,
    # so subclasses add positional fields after it.
    parents: tuple[str, ...] = field(default=(), kw_only=True)

    @property
    def short_sha(self) -> str:
        return self.sha[:8]


class HistoryOrder(StrEnum):
    """The order History lists commits in (spec §4.1); the value is git log's option."""

    DATE = "--date-order"
    TOPO = "--topo-order"


@dataclass(frozen=True)
class Stash(Commit):
    """A stash (spec §4.1). A Commit, so ``selection.commit`` can hold one; it never
    equals a Commit of the same sha (dataclass equality compares the class)."""

    index: int  # its place in the list: stash@{index}
    branch: str  # the branch it was made on; "(no branch)" when HEAD was detached
    message: str  # what follows "On <branch>: " (or "WIP on <branch>: ")
    untracked: bool  # it has an untracked part (a third parent)

    @property
    def ref(self) -> str:
        return f"stash@{{{self.index}}}"


@dataclass(frozen=True)
class LastCommit:
    """HEAD's commit, for amending it (spec §4.1)."""

    sha: str
    message: str  # in full, without trailing newlines
    pushed: bool  # on a remote-tracking branch: amending it diverges from the remote


@dataclass(frozen=True)
class CommitDetails:
    """One commit in full, for Commit Details (spec §4.1)."""

    sha: str
    parents: tuple[str, ...]  # none for the first commit, two or more for a merge
    author: str
    author_email: str
    author_date: datetime
    committer: str
    committer_email: str
    committer_date: datetime
    refs: tuple[str, ...]  # as git's %D: "HEAD -> main", "origin/main", "tag: v1.0"
    message: str  # the full message, trailing newlines stripped

    @property
    def summary(self) -> str:
        return self.message.split("\n", 1)[0]

    @property
    def body(self) -> str:
        """The message after the summary, without the blank line between."""
        rest = self.message.split("\n", 1)[1] if "\n" in self.message else ""
        return rest.lstrip("\n")


@dataclass(frozen=True)
class Branch:
    name: str
    is_current: bool
    merged: bool = False  # its tip is in HEAD, so `git branch -d` can delete it
    upstream: str | None = None  # e.g. "origin/main"; None without one (spec §4.1)
    ahead: int | None = None  # commits not in the upstream; None without one, or when it's gone
    behind: int | None = None  # the upstream's commits not in it
    date: datetime | None = None  # its tip's committer date (Branches' Last commit sort)


@dataclass(frozen=True)
class Ref:
    """A branch or tag, for History's labels (spec §4.1, RZG-77)."""

    name: str  # in full: "refs/heads/main", "refs/remotes/origin/main", "refs/tags/v1.0"
    sha: str  # the commit it points at; an annotated tag's, peeled


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


@dataclass(frozen=True)
class Worktree:
    """One worktree of a repository, as ``git worktree list`` gives it (spec §4.4)."""

    path: str  # canonical
    head: str | None  # its commit; None before the first
    branch: str | None  # the short name; None when detached
    main: bool = False  # the main worktree (listed first)
    locked: bool = False
    lock_reason: str = ""
    missing: bool = False  # git calls it prunable, or its folder isn't there

    @property
    def name(self) -> str:
        return self.path.rstrip("/").rsplit("/", 1)[-1]


@dataclass(frozen=True)
class WorktreeState:
    """What ``git status`` says about a worktree (spec §4.4)."""

    changed: int  # files with staged, unstaged or untracked changes, each once
    upstream: str | None = None  # e.g. "origin/main"; None without one
    ahead: int | None = None  # None without an upstream, or when it's gone
    behind: int | None = None
