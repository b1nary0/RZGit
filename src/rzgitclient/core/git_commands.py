"""Commands that change the repository, run on the session's worker (spec §4.1).

RepoSession queues a GitCommand; ``run_command`` is the worker side. Where a
command depends on the status it reads it itself, so a stale status in the
window can't make it act on the wrong file. No Qt objects besides QFile.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QFile

from rzgitclient.core.diff_patch import Line, build_patch
from rzgitclient.git.git_models import FileDiff, FileStatus
from rzgitclient.git.repo import GitError, Repo


class CommandKind(StrEnum):
    STAGE = "stage"
    UNSTAGE = "unstage"
    STAGE_ALL = "stage_all"
    UNSTAGE_ALL = "unstage_all"
    DISCARD = "discard"  # unstaged changes; untracked files go to the trash
    COMMIT = "commit"  # the staged changes, with ``message``
    # Chosen lines of ``diff`` (a hunk is all of its changed lines):
    STAGE_LINES = "stage_lines"
    UNSTAGE_LINES = "unstage_lines"
    DISCARD_LINES = "discard_lines"  # from the working tree; no undo
    # Branches (``branch`` is the one acted on):
    SWITCH = "switch"  # with ``stash``, the changes are stashed first
    CREATE_BRANCH = "create_branch"  # ``new_name`` from ``branch``; ``switch`` to it
    RENAME_BRANCH = "rename_branch"  # ``branch`` to ``new_name``
    DELETE_BRANCH = "delete_branch"  # ``force``: even if unmerged
    MERGE = "merge"  # ``branch`` into the current branch
    REBASE = "rebase"  # the current branch onto ``branch``


@dataclass(frozen=True, eq=False)
class GitCommand:
    """One queued command. Compared by identity: two commits with the same
    message are still two commands, and the session reports each."""

    kind: CommandKind
    paths: tuple[str, ...] = ()
    message: str = ""  # COMMIT's
    diff: FileDiff | None = None  # the *_LINES commands': the diff the lines are from
    lines: frozenset[Line] = frozenset()  # (hunk, line) indices into ``diff``
    branch: str = ""  # the branch commands'
    new_name: str = ""  # CREATE_BRANCH's and RENAME_BRANCH's
    stash: bool = False  # SWITCH's
    switch: bool = False  # CREATE_BRANCH's
    force: bool = False  # DELETE_BRANCH's


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
        case CommandKind.COMMIT:
            repo.commit(command.message)
        case CommandKind.STAGE_LINES | CommandKind.UNSTAGE_LINES | CommandKind.DISCARD_LINES:
            _apply_lines(repo, command)
        case CommandKind.SWITCH:
            _switch(repo, command.branch, command.stash)
        case CommandKind.CREATE_BRANCH:
            repo.create_branch(command.new_name, command.branch, switch=command.switch)
        case CommandKind.RENAME_BRANCH:
            repo.rename_branch(command.branch, command.new_name)
        case CommandKind.DELETE_BRANCH:
            repo.delete_branch(command.branch, force=command.force)
        case CommandKind.MERGE:
            repo.merge(command.branch)
        case CommandKind.REBASE:
            repo.rebase(command.branch)
        case _:
            raise ValueError(f"not a command: {command.kind!r}")


def stash_message(branch: str) -> str:
    """The message of the stash Switch makes before switching to ``branch``."""
    return f"RZGitClient: before switching to {branch}"


def _switch(repo: Repo, branch: str, stash: bool) -> None:
    """Switch to ``branch``; with ``stash``, stash the changes first, and pop
    them back if the switch fails, so the repository is as it was."""
    stashed = stash and repo.stash_push(stash_message(branch))
    try:
        repo.switch(branch)
    except GitError as exc:
        if not stashed:
            raise
        try:
            repo.stash_pop()
        except GitError as pop_exc:
            raise GitError(f"{exc}\n\nYour changes are in the stash "
                           f"(\"{stash_message(branch)}\") but couldn't be put back: "
                           f"{pop_exc}", exc.returncode) from exc
        raise


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


def _apply_lines(repo: Repo, command: GitCommand) -> None:
    """Stage, unstage or discard the chosen lines with a patch (spec §4.1)."""
    diff = command.diff
    if diff is None:
        raise ValueError("a lines command needs the diff")
    staging = command.kind is CommandKind.STAGE_LINES
    new_file = False
    mode = "100644"
    if command.kind is not CommandKind.UNSTAGE_LINES:  # an untracked file: a new-file patch
        new_file = any(f.path == diff.path and f.is_untracked for f in repo.status())
    if new_file and not staging:
        raise GitError(f"{diff.path} is a new file: discard it whole, to the trash.")
    if new_file and os.access(repo.root / diff.path, os.X_OK):
        mode = "100755"
    patch = build_patch(diff, command.lines, reverse=not staging, new_file=new_file, mode=mode)
    if patch is None:
        return
    repo.apply(patch, cached=command.kind is not CommandKind.DISCARD_LINES, reverse=not staging)
    if command.kind is CommandKind.UNSTAGE_LINES and repo.staged_as_empty_new_file(diff.path):
        # Its last staged lines are gone: it goes back to untracked, as it was,
        # rather than staying staged as an empty new file.
        repo.unstage([diff.path])
