"""Reading one file's diff, on the session's worker (spec §4.1).

RepoSession keeps the diff target and queues it; this module is the worker
side: it picks the git source, applies the size limit and, for the whole
file, cuts git's one hunk into hunks of one change each. No Qt objects.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from rzgit.core.selection import FileSelection, FileSource
from rzgit.git.git_models import DiffSource, FileDiff, FileStatus, Hunk, LineKind
from rzgit.git.repo import Repo

DIFF_MAX_BYTES = 1_000_000  # larger diffs come back too_large, unless asked for in full
DIFF_MAX_LINES = 20_000
WHOLE_CONTEXT = 1_000_000_000  # lines of context that take in any file whole
SPLIT_RUN = 6  # an unchanged run longer than this (git's context, twice) parts two hunks


@dataclass(frozen=True)
class DiffRequest:
    file: FileSelection
    full: bool = False  # read it however large
    whole: bool = False  # the whole file, not only the changes with some context
    max_lines: int | None = None  # longer comes back too_large unless full; None: DIFF_MAX_LINES

    @property
    def follows_status(self) -> bool:
        """Its diff can change when the status does (it isn't a commit's)."""
        return self.file.source is not FileSource.COMMIT


def load_diff(repo: Repo, request: DiffRequest, status: tuple[FileStatus, ...],
              commit_files: tuple[FileStatus, ...] = ()) -> FileDiff:
    """The diff ``request`` asks for. ``status`` is the latest status: it
    tells an untracked file and a staged rename's old path. ``commit_files``,
    the files of the commit a commit file is from, tell its rename's old path."""
    file = request.file
    changes = commit_files if file.source is FileSource.COMMIT else status
    entry = next((f for f in changes if f.path == file.path), None)
    old_path = None
    commit = file.commit
    match file.source:
        case FileSource.WORKTREE:
            untracked = entry is not None and entry.is_untracked
            source = DiffSource.UNTRACKED if untracked else DiffSource.WORKTREE
        case FileSource.INDEX:
            source = DiffSource.INDEX
            if entry is not None and entry.index in "RC":
                old_path = entry.orig_path
        case FileSource.COMMIT:
            source = DiffSource.COMMIT
            if entry is not None and entry.index in "RC":
                old_path = entry.orig_path
            elif entry is not None and entry.is_untracked:
                commit = f"{file.commit}^3"  # a stash's untracked part (spec §4.1)
        case _:
            raise ValueError(f"not a file source: {file.source!r}")
    limit = {} if request.full else {"max_bytes": DIFF_MAX_BYTES,
                                       "max_lines": request.max_lines or DIFF_MAX_LINES}
    if not request.whole:
        return repo.diff(file.path, source, commit=commit, old_path=old_path, **limit)
    diff = repo.diff(file.path, source, commit=commit, old_path=old_path,
                     context=WHOLE_CONTEXT, **limit)
    return split_whole(diff)


def split_whole(diff: FileDiff) -> FileDiff:
    """``diff`` with each hunk cut in the middle of every unchanged run longer
    than ``SPLIT_RUN`` lines: the pieces cover the same lines without gaps, each
    with its own header. A diff with git's usual context comes back as it is."""
    hunks = tuple(piece for hunk in diff.hunks for piece in _split(hunk))
    if len(hunks) == len(diff.hunks):
        return diff
    return replace(diff, hunks=hunks)


def _split(hunk: Hunk) -> list[Hunk]:
    lines = hunk.lines
    changed = [i for i, line in enumerate(lines) if line.kind is not LineKind.CONTEXT]
    # Between changes a and b: the first half of the run (the larger) stays with a.
    cuts = [a + 1 + (b - a) // 2 for a, b in zip(changed, changed[1:]) if b - a - 1 > SPLIT_RUN]
    if not cuts:
        return [hunk]
    pieces: list[Hunk] = []
    old_no, new_no = hunk.old_start, hunk.new_start  # the next line's numbers on each side
    for start, end in zip([0, *cuts], [*cuts, len(lines)]):
        part = lines[start:end]
        old_count = sum(line.kind is not LineKind.ADDED for line in part)
        new_count = sum(line.kind is not LineKind.REMOVED for line in part)
        # Every piece has unchanged lines, where it was cut, so neither side is empty.
        header = f"@@ -{old_no},{old_count} +{new_no},{new_count} @@"
        pieces.append(Hunk(header, old_no, new_no, part))
        old_no, new_no = old_no + old_count, new_no + new_count
    return pieces
