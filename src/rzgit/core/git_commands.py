"""Commands that change the repository, run on the session's worker (spec §4.1).

RepoSession queues a GitCommand; ``run_command`` is the worker side. Where a
command depends on the status it reads it itself, so a stale status in the
window can't make it act on the wrong file. No Qt objects besides QFile.
A command that worked, but not quite as asked, returns a notice; a pull that
found its branch diverged returns ``PullDiverged``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QFile

from rzgit.core.diff_patch import Line, build_patch
from rzgit.git.git_models import FileDiff, FileStatus, Stash
from rzgit.git.repo import GitError, Repo


class CommandKind(StrEnum):
    STAGE = "stage"
    UNSTAGE = "unstage"
    STAGE_ALL = "stage_all"
    UNSTAGE_ALL = "unstage_all"
    DISCARD = "discard"  # unstaged changes; untracked files go to the trash
    COMMIT = "commit"  # the staged changes, with ``message``
    AMEND = "amend"  # HEAD's commit replaced: the staged changes, if any, and ``message``
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
    # ``branch`` (the current one when asked) from its upstream, as ``mode`` says;
    # with ``stash``, the changes are stashed first and put back after (spec §4.1).
    PULL = "pull"
    # Stashes (``sha`` is the stash acted on):
    STASH_PUSH = "stash_push"  # with ``message``; ``untracked`` files too
    STASH_APPLY = "stash_apply"  # rolled back if it fails; ``pop``: dropped if it worked
    STASH_DROP = "stash_drop"
    # Worktrees (``path`` is the worktree's folder; spec §4.1):
    WORKTREE_ADD = "worktree_add"  # on ``branch``: new from ``start``, or existing without
    WORKTREE_REMOVE = "worktree_remove"  # ``force``: even with changes
    WORKTREE_PRUNE = "worktree_prune"  # the records of missing worktrees


class PullMode(StrEnum):
    """How a pull integrates the upstream (spec §4.1)."""

    FF_ONLY = "ff_only"  # the first try: diverged, it changes nothing
    MERGE = "merge"
    REBASE = "rebase"


@dataclass(frozen=True)
class PullDiverged:
    """A pull's result when the branch and its upstream have diverged: nothing changed."""

    ahead: int  # the branch's commits not in the upstream
    behind: int  # the upstream's commits not in the branch
    rebase: bool  # the rebase default: branch.<name>.rebase, else pull.rebase


WORKTREE_COMMANDS = frozenset({CommandKind.WORKTREE_ADD, CommandKind.WORKTREE_REMOVE,
                               CommandKind.WORKTREE_PRUNE})


@dataclass(frozen=True, eq=False)
class GitCommand:
    """One queued command. Compared by identity: two commits with the same
    message are still two commands, and the session reports each."""

    kind: CommandKind
    paths: tuple[str, ...] = ()
    message: str = ""  # COMMIT's and AMEND's
    diff: FileDiff | None = None  # the *_LINES commands': the diff the lines are from
    lines: frozenset[Line] = frozenset()  # (hunk, line) indices into ``diff``
    branch: str = ""  # the branch commands'
    new_name: str = ""  # CREATE_BRANCH's and RENAME_BRANCH's
    stash: bool = False  # SWITCH's and PULL's
    mode: PullMode = PullMode.FF_ONLY  # PULL's
    switch: bool = False  # CREATE_BRANCH's
    force: bool = False  # DELETE_BRANCH's and WORKTREE_REMOVE's
    sha: str = ""  # the stash commands'
    untracked: bool = False  # STASH_PUSH's
    pop: bool = False  # STASH_APPLY's
    path: str = ""  # the worktree commands': the worktree's folder
    start: str | None = None  # WORKTREE_ADD's: a new ``branch`` from here; None: existing


def run_command(repo: Repo, command: GitCommand) -> str | PullDiverged | None:
    """Run ``command``; a notice if it worked, but not quite as asked, or
    ``PullDiverged``. Raises GitError if git (or the trash) fails."""
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
        case CommandKind.AMEND:
            repo.amend(command.message)
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
        case CommandKind.PULL:
            return _pull(repo, command)
        case CommandKind.STASH_PUSH:
            if not repo.stash_push(command.message, untracked=command.untracked):
                return NOTHING_TO_STASH
        case CommandKind.STASH_APPLY:
            return _apply_stash(repo, command.sha, command.pop)
        case CommandKind.STASH_DROP:
            repo.stash_drop(_stash(repo, command.sha).ref)
        case CommandKind.WORKTREE_ADD:
            repo.worktree_add(command.path, command.branch, command.start)
        case CommandKind.WORKTREE_REMOVE:
            repo.worktree_remove(command.path, force=command.force)
        case CommandKind.WORKTREE_PRUNE:
            repo.worktree_prune()
        case _:
            raise ValueError(f"not a command: {command.kind!r}")
    return None


NOTHING_TO_STASH = "There was nothing to stash: the only changes are untracked files."
UNSTAGED = ("The stash was applied, but its staged changes couldn't be staged again, "
            "so they are unstaged.")
NOT_CLEAN = ("Commit or stash your changes first: a stash is applied only to a clean "
             "working tree, so it can be undone if it doesn't fit.")


def _stash(repo: Repo, sha: str) -> Stash:
    """The stash ``sha`` as it is now: its ``stash@{n}`` moves when stashes are made."""
    stash = next((s for s in repo.stashes() if s.sha == sha), None)
    if stash is None:
        raise GitError("That stash is gone: it was popped or dropped meanwhile.")
    return stash


def _apply_stash(repo: Repo, sha: str, pop: bool) -> str | None:
    """Apply (and with ``pop``, drop) stash ``sha``, never leaving it half-applied:
    on a clean working tree, so a failure is rolled back to exactly that (spec §4.1)."""
    if repo.status():
        raise GitError(NOT_CLEAN)
    stash = _stash(repo, sha)
    try:
        notice = _apply_in_full(repo, stash)
    except GitError as exc:
        raise GitError(f"{exc}\n\nNothing was changed, and the stash is kept.",
                       exc.returncode) from exc
    if pop:
        repo.stash_drop(_stash(repo, sha).ref)
    return notice


def _apply_in_full(repo: Repo, stash: Stash) -> str | None:
    """Apply ``stash`` to the clean working tree, staged changes staged again if
    they fit, else unstaged (the notice says so); rolled back if neither works."""
    try:
        _apply_or_roll_back(repo, stash, index=True)
    except GitError:
        # Its staged changes may not fit HEAD as staged changes: try them unstaged.
        _apply_or_roll_back(repo, stash, index=False)
        return UNSTAGED
    return None


def _apply_or_roll_back(repo: Repo, stash: Stash, *, index: bool) -> None:
    try:
        repo.stash_apply(stash.sha, index=index)
    except GitError:
        repo.reset_hard()
        if stash.untracked:
            restored = set(repo.stash_untracked(stash.sha))
            repo.clean([f.path for f in repo.status() if f.is_untracked and f.path in restored])
        raise


def stash_message(branch: str) -> str:
    """The message of the stash Switch makes before switching to ``branch``."""
    return f"RZGit: before switching to {branch}"


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


def pull_stash_message(branch: str) -> str:
    """The message of the stash Pull makes before pulling into ``branch``."""
    return f"RZGit: before pulling {branch}"


NOT_ON_BRANCH = "{branch} is no longer the current branch, so nothing was pulled."
CHANGES_KEPT = ("Pulled into {branch}, but your changes couldn't be put back on top:\n\n"
                "{error}\n\nThey are kept in the stash “{stash}”, and the working tree "
                "is clean.")


def _pull(repo: Repo, command: GitCommand) -> str | PullDiverged | None:
    """Bring the upstream's commits into ``command.branch`` (spec §4.1). The
    checks change nothing; with ``stash``, the changes are stashed only after
    them, popped back if the integration fails, and put back as Pop does if it
    worked (kept, with a notice, if that fails)."""
    branch = command.branch
    if repo.current_branch() != branch:
        raise GitError(NOT_ON_BRANCH.format(branch=branch))
    ahead, behind = repo.upstream_counts()
    if behind == 0:
        return None  # already up to date
    if command.mode is PullMode.FF_ONLY and ahead:
        return PullDiverged(ahead, behind, repo.pull_rebases(branch))
    message = pull_stash_message(branch)
    stashed = command.stash and repo.stash_push(message)
    stash = repo.stashes()[0] if stashed else None
    try:
        match command.mode:
            case PullMode.FF_ONLY:
                repo.merge_ff_only("@{u}")
            case PullMode.MERGE:
                repo.merge("@{u}")
            case PullMode.REBASE:
                repo.rebase("@{u}")
    except GitError as exc:
        if not stashed:
            raise
        try:
            repo.stash_pop()
        except GitError as pop_exc:
            raise GitError(f"{exc}\n\nYour changes are in the stash (\"{message}\") but "
                           f"couldn't be put back: {pop_exc}", exc.returncode) from exc
        raise
    if stash is None:
        return None
    try:
        notice = _apply_in_full(repo, stash)
    except GitError as exc:
        return CHANGES_KEPT.format(branch=branch, error=exc, stash=message)
    repo.stash_drop(_stash(repo, stash.sha).ref)
    return notice


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
