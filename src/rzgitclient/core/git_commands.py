"""Commands that change the repository, run on the session's worker (spec §4.1).

RepoSession queues a GitCommand; ``run_command`` is the worker side. Where a
command depends on the status it reads it itself, so a stale status in the
window can't make it act on the wrong file. No Qt objects besides QFile.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QFile

from rzgitclient.git.git_models import FileStatus
from rzgitclient.git.repo import GitError, Repo


class CommandKind(StrEnum):
    STAGE = "stage"
    UNSTAGE = "unstage"
    STAGE_ALL = "stage_all"
    UNSTAGE_ALL = "unstage_all"
    DISCARD = "discard"  # unstaged changes; untracked files go to the trash


@dataclass(frozen=True)
class GitCommand:
    kind: CommandKind
    paths: tuple[str, ...] = ()


def run_command(repo: Repo, command: GitCommand) -> None:
    """Run ``command``. Raises GitError if git (or the trash) fails."""
    paths = command.paths
    match command.kind:
        case CommandKind.STAGE:
            repo.stage(paths)
        case CommandKind.UNSTAGE:
            repo.unstage(_with_rename_sources(paths, repo.status()))
        case CommandKind.STAGE_ALL:
            repo.stage_all()
        case CommandKind.UNSTAGE_ALL:
            repo.unstage_all()
        case CommandKind.DISCARD:
            _discard(repo, paths)
        case _:
            raise ValueError(f"not a command: {command.kind!r}")


def _with_rename_sources(paths: tuple[str, ...], status: list[FileStatus]) -> list[str]:
    """``paths``, plus the old path of each staged rename among them: unstaging
    only the new path would leave the old one staged as deleted."""
    renamed = {f.path: f.orig_path for f in status if f.index == "R" and f.orig_path}
    result = list(paths)
    for path in paths:
        if (old := renamed.get(path)) is not None and old not in result:
            result.append(old)
    return result


def _discard(repo: Repo, paths: tuple[str, ...]) -> None:
    untracked = {f.path for f in repo.status() if f.is_untracked}
    repo.restore([p for p in paths if p not in untracked])
    for path in paths:
        if path in untracked:
            moved, _where = QFile.moveToTrash(str(repo.root / path))
            if not moved:
                raise GitError(f"Couldn't move {path} to the trash, so it was kept.")
