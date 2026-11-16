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
    env["GIT_EDITOR"] = "false"  # a command that wants an editor fails instead of waiting
    # Reads write nothing: `git status` would otherwise refresh .git/index, and the
    # repo watcher would see that and refresh again (spec §4.1, §4.3).
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def run_git(
    cwd: str | Path, *args: str, ok_codes: Collection[int] = (0,), input: str | None = None
) -> str:
    """git's output as text (newlines normalised). Raises GitError for any
    exit code not in ``ok_codes``. ``input`` is written to git's stdin, which
    is otherwise closed."""
    return _run(cwd, args, ok_codes, text=True, input=input)


def run_git_bytes(cwd: str | Path, *args: str, ok_codes: Collection[int] = (0,),
                  input: bytes | None = None) -> bytes:
    """git's output exactly as written, e.g. a diff whose CRLF line ends matter.
    ``input`` is written to git's stdin as it is."""
    return _run(cwd, args, ok_codes, text=False, input=input)


def _run(cwd: str | Path, args: tuple[str, ...], ok_codes: Collection[int], *, text: bool,
         input: str | bytes | None = None):
    # With no input, stdin is closed: git can never wait on the app's terminal.
    stdin = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=_git_env(),
            capture_output=True,
            text=text,
            **stdin,
        )
    except OSError as exc:  # missing cwd, git not installed, …
        raise GitError(f"Can't run git in {cwd}: {exc.strerror or exc}") from exc
    if proc.returncode not in ok_codes:
        out, err = (proc.stdout, proc.stderr) if text else (
            proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace"))
        # Some failures (a hook, "nothing to commit") explain themselves on stdout.
        raise GitError(
            err.strip() or out.strip() or f"git {' '.join(args)} failed",
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
        rows = [line.partition("\0") for line in out.splitlines()]
        merged: set[str] = set()
        if rows:
            try:
                merged = set(self.git("for-each-ref", "--merged", "HEAD",
                                      "--format=%(refname:short)", "refs/heads").splitlines())
            except GitError:  # HEAD has no commit yet: nothing is merged into it
                pass
        return [Branch(name=name, is_current=head == "*", merged=name in merged)
                for head, _, name in rows]

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

    def commit_files(self, sha: str) -> list[FileStatus]:
        """The files commit ``sha`` changed, compared with its first parent (with
        nothing, for the first commit). Each is a ``FileStatus`` whose ``index``
        is the change's code and whose ``worktree`` is " ", as for a staged
        change; ``orig_path`` is a rename's or copy's source (spec §4.1)."""
        out = self.git("show", "--format=", "--name-status", "-z", "--no-color", "-M",
                       "--diff-merges=first-parent", sha, "--")
        fields = out.split("\0")
        result = []
        i = 0
        while i < len(fields):
            code = fields[i].strip()
            i += 1
            if not code:
                continue
            if code[0] in "RC":  # R100 old new
                orig, path = fields[i], fields[i + 1]
                i += 2
            else:
                orig, path = None, fields[i]
                i += 1
            result.append(FileStatus(code[0], " ", path, orig))
        return result

    # ---- for the repo watcher (spec §4.3) ----------------------------------

    def git_dirs(self) -> tuple[Path, Path]:
        """The git dir (HEAD, index) and the common dir (refs, packed-refs).
        They differ in a linked worktree."""
        out = self.git("rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir")
        git_dir, common_dir = out.splitlines()[:2]
        return Path(git_dir), Path(common_dir)

    def listed_files(self) -> list[str]:
        """Tracked files and untracked ones git doesn't ignore, relative to the root."""
        out = self.git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        return [p for p in out.split("\0") if p]

    def untracked_folders(self) -> list[str]:
        """Untracked folders git doesn't ignore, empty ones too, relative to the
        root and without the trailing slash. Only the top one of an untracked tree."""
        out = self.git("ls-files", "-z", "--others", "--exclude-standard", "--directory")
        return [p.rstrip("/") for p in out.split("\0") if p.endswith("/")]

    def ignored(self, paths: Sequence[str]) -> set[str]:
        """Those of ``paths`` (relative to the root) that git ignores. Tracked
        files are never ignored."""
        if not paths:
            return set()
        out = run_git(self.root, "check-ignore", "-z", "--stdin", ok_codes=(0, 1),
                      input="\0".join(paths) + "\0")
        return {p for p in out.split("\0") if p}

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

    def commit(self, message: str) -> None:
        """Commit the staged changes with ``message``, passed on stdin: no
        editor opens. Hooks run; one that fails raises GitError with its output.
        Only surrounding blank lines and trailing spaces are cleaned up, so a
        line starting with "#" is kept."""
        run_git(self.root, "commit", "-q", "--cleanup=whitespace", "-F", "-", input=message)

    # Branches (spec §4.1, "Branch commands"). Names are git's to check.

    def switch(self, name: str) -> None:
        self.git("switch", "-q", name)

    def create_branch(self, name: str, start: str, *, switch: bool = False) -> None:
        if switch:
            self.git("switch", "-q", "-c", name, start)
        else:
            self.git("branch", name, start)

    def rename_branch(self, old: str, new: str) -> None:
        self.git("branch", "-m", old, new)

    def delete_branch(self, name: str, *, force: bool = False) -> None:
        """``git branch -d``, which refuses an unmerged branch; ``-D`` with ``force``."""
        self.git("branch", "-D" if force else "-d", name)

    def stash_push(self, message: str) -> bool:
        """Stash staged, unstaged and untracked changes (not ignored files).
        True if a stash was made: with nothing to stash, git makes none."""
        before = self._resolve("refs/stash")
        self.git("stash", "push", "-q", "--include-untracked", "-m", message)
        return self._resolve("refs/stash") != before

    def stash_pop(self) -> None:
        """Put the newest stash back, staged changes staged again."""
        self.git("stash", "pop", "-q", "--index")

    def merge(self, name: str) -> None:
        """Merge ``name`` into the current branch. If it fails part-way (a
        conflict), the merge is aborted, so the repository is as it was."""
        try:
            self.git("merge", "--no-edit", name)
        except GitError as exc:
            if self._resolve("MERGE_HEAD") is None:
                raise
            self.git("merge", "--abort")
            raise _aborted(exc, "merge") from exc

    def rebase(self, onto: str) -> None:
        """Rebase the current branch onto ``onto``. If it stops part-way (a
        conflict), the rebase is aborted, so the repository is as it was."""
        try:
            self.git("rebase", onto)
        except GitError as exc:
            if not self._rebasing():
                raise
            self.git("rebase", "--abort")
            raise _aborted(exc, "rebase") from exc

    def _resolve(self, ref: str) -> str | None:
        """The object ``ref`` names, or None if it doesn't exist."""
        try:
            return self.git("rev-parse", "-q", "--verify", ref).strip()
        except GitError as exc:
            if exc.returncode == 1:
                return None
            raise

    def _rebasing(self) -> bool:
        out = self.git("rev-parse", "--git-path", "rebase-merge", "--git-path", "rebase-apply")
        return any((self.root / p).exists() for p in out.splitlines())

    def apply(self, patch: str, *, cached: bool = False, reverse: bool = False) -> None:
        """Apply ``patch`` (a ``core.diff_patch`` patch): to the index with
        ``cached``, else to the working tree; backwards with ``reverse``. git
        checks it all first, so a patch that doesn't fit changes nothing."""
        args = ["apply", "--whitespace=nowarn"]
        if cached:
            args.append("--cached")
        if reverse:
            args.append("--reverse")
        run_git_bytes(self.root, *args, "-", input=patch.encode("utf-8"))

    def staged_as_empty_new_file(self, path: str) -> bool:
        """The index holds ``path`` as a new file (not in HEAD) with nothing in it."""
        status = self._literal("diff", "--cached", "--name-status", "-z", "--", path)
        if not status.startswith("A\0"):
            return False
        return self.git("cat-file", "-s", f":{path}").strip() == "0"

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


def _aborted(exc: GitError, what: str) -> GitError:
    """``exc``, after its merge or rebase was aborted. git's hints (resolve the
    conflicts, then continue) no longer apply, so they are left out."""
    lines = [line for line in str(exc).splitlines() if not line.startswith("hint:")]
    return GitError("\n".join(lines) + f"\n\nThe {what} was aborted, so nothing changed.",
                    exc.returncode)


def _unquote(name: str) -> str:
    """A path as git prints it in headers: C-quoted if it has unusual characters."""
    if not (len(name) >= 2 and name[0] == name[-1] == '"'):
        return name
    raw = name[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
    return raw.encode("latin-1", "replace").decode("utf-8", "replace")
