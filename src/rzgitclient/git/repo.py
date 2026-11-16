"""Thin wrapper around the git command-line tool.

Using the CLI (rather than libgit2) means the user's existing config,
credential helpers, SSH keys and hooks all behave exactly as in a terminal.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Collection, Sequence
from datetime import datetime
from pathlib import Path

from rzgitclient.git.git_models import (
    Branch,
    Commit,
    DiffLine,
    DiffSource,
    FileDiff,
    FileStatus,
    Hunk,
    LineKind,
)


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


def run_git(cwd: str | Path, *args: str, ok_codes: Collection[int] = (0,)) -> str:
    """git's output as text (newlines normalised). Raises GitError for any
    exit code not in ``ok_codes``."""
    return _run(cwd, args, ok_codes, text=True)


def run_git_bytes(cwd: str | Path, *args: str, ok_codes: Collection[int] = (0,)) -> bytes:
    """git's output exactly as written, e.g. a diff whose CRLF line ends matter."""
    return _run(cwd, args, ok_codes, text=False)


def _run(cwd: str | Path, args: tuple[str, ...], ok_codes: Collection[int], *, text: bool):
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=_git_env(),
            capture_output=True,
            text=text,
        )
    except OSError as exc:  # missing cwd, git not installed, …
        raise GitError(f"Can't run git in {cwd}: {exc.strerror or exc}") from exc
    if proc.returncode not in ok_codes:
        stderr = proc.stderr if text else proc.stderr.decode("utf-8", "replace")
        raise GitError(
            stderr.strip() or f"git {' '.join(args)} failed",
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
        return self.head_sha() is not None

    def head_sha(self) -> str | None:
        """The commit HEAD points at, or None before the first commit."""
        try:
            return self.git("rev-parse", "--verify", "-q", "HEAD").strip()
        except GitError as exc:
            if exc.returncode == 1:  # -q: HEAD doesn't resolve yet
                return None
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

    def commit_count(self) -> int:
        """How many commits HEAD reaches (0 before the first commit)."""
        if not self.has_commits():
            return 0
        return int(self.git("rev-list", "--count", "HEAD"))

    def log(self, limit: int = 500, skip: int = 0) -> list[Commit]:
        """Up to ``limit`` commits reachable from HEAD, newest first, after ``skip``."""
        if not self.has_commits():
            return []
        fmt = "%H%x1f%an%x1f%aI%x1f%s%x1e"
        out = self.git("log", f"--format={fmt}", f"-n{limit}", f"--skip={skip}")
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

    # ---- commands: they change the repository ------------------------------

    def stage(self, paths: Sequence[str]) -> None:
        """Stage ``paths`` as they are now: changes, new files and deletions."""
        if paths:
            self._literal("add", "-A", "--", *paths)

    def unstage(self, paths: Sequence[str]) -> None:
        """Take ``paths`` out of the index, back to HEAD (or untracked, before
        the first commit). The working tree is left alone."""
        if not paths:
            return
        if self.has_commits():
            self._literal("restore", "--staged", "--", *paths)
        else:
            self._literal("rm", "--cached", "-r", "-q", "--ignore-unmatch", "--", *paths)

    def stage_all(self) -> None:
        self.git("add", "-A")

    def unstage_all(self) -> None:
        if self.has_commits():
            self.git("reset", "-q")
        else:
            self.git("rm", "--cached", "-r", "-q", "--ignore-unmatch", "--", ".")

    def restore(self, paths: Sequence[str]) -> None:
        """Throw away the unstaged changes to tracked ``paths``: the working
        tree goes back to the index, so staged changes are kept."""
        if paths:
            self._literal("restore", "--worktree", "--", *paths)

    def _literal(self, *args: str) -> str:
        """Run git with paths that mean themselves: ":x" or "*.c" aren't patterns."""
        return self.git("--literal-pathspecs", *args)

    def diff(
        self,
        path: str,
        source: DiffSource,
        *,
        commit: str | None = None,
        old_path: str | None = None,
        max_bytes: int | None = None,
        max_lines: int | None = None,
    ) -> FileDiff:
        """The changes to ``path`` that ``source`` names.

        ``commit`` is needed for ``DiffSource.COMMIT``. ``old_path`` (a staged
        or committed rename's source) lets git show the change as a rename.
        Output over ``max_bytes`` or ``max_lines`` isn't parsed: the result
        is ``too_large`` and has no hunks.
        """
        paths = (old_path, path) if old_path else (path,)
        ok_codes = (0,)
        match source:
            case DiffSource.WORKTREE:
                args = ["diff", *_DIFF_OPTIONS, "--", path]
            case DiffSource.INDEX:
                args = ["diff", "--cached", *_DIFF_OPTIONS, "--", *paths]
            case DiffSource.UNTRACKED:
                # --no-index exits with 1 when the files differ, which they do.
                args = ["diff", "--no-index", *_DIFF_OPTIONS, "--", "/dev/null", path]
                ok_codes = (0, 1)
            case DiffSource.COMMIT:
                if commit is None:
                    raise ValueError("a commit diff needs the commit")
                args = ["show", "--format=", "--diff-merges=first-parent", *_DIFF_OPTIONS,
                        commit, "--", *paths]
            case _:
                raise ValueError(f"not a diff source: {source!r}")
        raw = run_git_bytes(self.root, *args, ok_codes=ok_codes)
        if source is DiffSource.UNTRACKED and not raw:
            # Exit code 1 also means "can't read it"; a readable file always has a header.
            raise GitError(f"Can't read {path}", 1)
        return parse_diff(raw, path, max_bytes=max_bytes, max_lines=max_lines)


# Plain output whatever the user's config says; -M: show renames as renames.
_DIFF_OPTIONS = ("--no-color", "--no-ext-diff", "-M")
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_diff(
    raw: bytes, path: str, *, max_bytes: int | None = None, max_lines: int | None = None
) -> FileDiff:
    """Parse ``git diff`` output for one file (several file sections are merged).

    Hunks are read by their line counts, so a removed line that starts with
    "--" can't be taken for a header. Text is UTF-8; bytes that aren't are
    replaced.
    """
    line_count = raw.count(b"\n")
    if (max_bytes is not None and len(raw) > max_bytes) or (
        max_lines is not None and line_count > max_lines
    ):
        return FileDiff(path, too_large=True, line_count=line_count)

    hunks: list[Hunk] = []
    old_path: str | None = None
    binary = False
    lines: list[DiffLine] = []
    header = ""
    old_start = new_start = old_no = new_no = old_left = new_left = 0

    def end_hunk() -> None:
        if header:
            hunks.append(Hunk(header, old_start, new_start, tuple(lines)))

    for line in raw.decode("utf-8", "replace").split("\n"):
        if old_left > 0 or new_left > 0:  # inside a hunk
            kind = line[:1]
            if kind == "+":
                lines.append(DiffLine(LineKind.ADDED, None, new_no, line[1:]))
                new_no, new_left = new_no + 1, new_left - 1
                continue
            if kind == "-":
                lines.append(DiffLine(LineKind.REMOVED, old_no, None, line[1:]))
                old_no, old_left = old_no + 1, old_left - 1
                continue
            if kind == " " or line == "":  # some tools strip the space of a blank line
                lines.append(DiffLine(LineKind.CONTEXT, old_no, new_no, line[1:]))
                old_no, new_no = old_no + 1, new_no + 1
                old_left, new_left = old_left - 1, new_left - 1
                continue
        if line.startswith("\\"):  # "\ No newline at end of file"
            if lines:
                last = lines[-1]
                lines[-1] = DiffLine(last.kind, last.old_no, last.new_no, last.text, True)
            continue
        if m := _HUNK_HEADER.match(line):
            end_hunk()
            lines = []
            header = line
            old_start, new_start = int(m[1]), int(m[3])
            old_left = int(m[2]) if m[2] is not None else 1
            new_left = int(m[4]) if m[4] is not None else 1
            old_no, new_no = old_start, new_start
            continue
        if line.startswith("diff "):  # another file section: close this one's hunk
            end_hunk()
            header, lines = "", []
        elif line.startswith(("rename from ", "copy from ")):
            old_path = _unquote(line.split(" ", 2)[2])
        elif line.startswith("Binary files ") or line == "GIT binary patch":
            binary = True
    end_hunk()
    return FileDiff(path, tuple(hunks), old_path, binary, line_count=line_count)


def _unquote(name: str) -> str:
    """A path as git prints it in headers: C-quoted if it has unusual characters."""
    if not (len(name) >= 2 and name[0] == name[-1] == '"'):
        return name
    raw = name[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
    return raw.encode("latin-1", "replace").decode("utf-8", "replace")
