"""Reading one file's diff, on the session's worker (spec §4.1).

RepoSession keeps the diff target and queues it; this module is the worker
side: it picks the git source and applies the size limit. No Qt objects.
"""

from __future__ import annotations

from dataclasses import dataclass

from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.git.git_models import DiffSource, FileDiff, FileStatus
from rzgitclient.git.repo import Repo

DIFF_MAX_BYTES = 1_000_000  # larger diffs come back too_large, unless asked for in full
DIFF_MAX_LINES = 20_000


@dataclass(frozen=True)
class DiffRequest:
    file: FileSelection
    full: bool = False  # read it whole, however large

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
    limit = {} if request.full else {"max_bytes": DIFF_MAX_BYTES, "max_lines": DIFF_MAX_LINES}
    return repo.diff(file.path, source, commit=commit, old_path=old_path, **limit)
