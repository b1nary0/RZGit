"""Thin wrapper around the git command-line tool.

Using the CLI (rather than libgit2) means the user's existing config,
credential helpers, SSH keys and hooks all behave exactly as in a terminal.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

from rzgitclient.git.git_models import Branch, Commit, FileStatus


class GitError(Exception):
    """Raised when a git command fails, or can't be run at all.

    ``returncode`` is git's exit code, or None when git never ran (for
    example, the repository folder no longer exists).
    """

    def __init__(self, message: str, returncode: int | None = None):
        super().__init__(message)
        self.returncode = returncode


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["LC_ALL"] = "C"  # stable, parseable output
    env["GIT_TERMINAL_PROMPT"] = "0"  # never block on a hidden prompt
    return env


def run_git(cwd: str | Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=_git_env(),
            capture_output=True,
            text=True,
        )
    except OSError as exc:  # missing cwd, git not installed, …
        raise GitError(f"Can't run git in {cwd}: {exc.strerror or exc}") from exc
    if proc.returncode != 0:
        raise GitError(
            proc.stderr.strip() or f"git {' '.join(args)} failed",
            proc.returncode,
        )
    return proc.stdout


class Repo:
    def __init__(self, path: str | Path):
        path = Path(path).expanduser()
        if not path.is_dir():
            raise GitError(f"Not a directory: {path}")
        top = run_git(path, "rev-parse", "--show-toplevel").strip()
        self.root = Path(top)

    def git(self, *args: str) -> str:
        return run_git(self.root, *args)

    @property
    def name(self) -> str:
        return self.root.name

    def has_commits(self) -> bool:
        try:
            self.git("rev-parse", "--verify", "-q", "HEAD")
            return True
        except GitError as exc:
            if exc.returncode == 1:  # -q: HEAD doesn't resolve yet
                return False
            raise

    def current_branch(self) -> str | None:
        """Branch name, or None if HEAD is detached."""
        try:
            return self.git("symbolic-ref", "--short", "-q", "HEAD").strip() or None
        except GitError as exc:
            if exc.returncode == 1:  # -q: HEAD isn't a symbolic ref (detached)
                return None
            raise

    def branches(self) -> list[Branch]:
        out = self.git(
            "for-each-ref", "--format=%(HEAD)%00%(refname:short)", "refs/heads"
        )
        result = []
        for line in out.splitlines():
            head, _, name = line.partition("\0")
            result.append(Branch(name=name, is_current=head == "*"))
        return result

    def log(self, limit: int = 500) -> list[Commit]:
        if not self.has_commits():
            return []
        fmt = "%H%x1f%an%x1f%aI%x1f%s%x1e"
        out = self.git("log", f"--format={fmt}", f"-n{limit}")
        commits = []
        for record in out.split("\x1e"):
            record = record.strip("\n")
            if not record:
                continue
            sha, author, date, subject = record.split("\x1f", 3)
            commits.append(
                Commit(sha, author, datetime.fromisoformat(date), subject)
            )
        return commits

    def status(self) -> list[FileStatus]:
        out = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        entries = out.split("\0")
        result = []
        i = 0
        while i < len(entries):
            entry = entries[i]
            i += 1
            if len(entry) < 4:
                continue
            x, y, path = entry[0], entry[1], entry[3:]
            orig = None
            if x in "RC":  # rename/copy: next NUL field is the source path
                orig = entries[i]
                i += 1
            result.append(FileStatus(x, y, path, orig))
        return result
