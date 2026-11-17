"""Remote commands on a worker of their own (spec §4.1, "Network commands").

A fetch can take minutes, so it doesn't run on the session's worker: status,
diffs and history keep loading meanwhile. One command runs at a time. Git
runs in a session of its own, so it has no terminal to prompt on, and
cancelling kills its whole process group (git and the ssh or https helper it
started). Signals are emitted on the main thread.
"""

from __future__ import annotations

import codecs
import logging
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QObject, QThreadPool, Signal

from rzgitclient.git.git_models import Branch
from rzgitclient.git.repo import GitError, Repo

PROGRESS_INTERVAL = 0.1  # s: at most this often a progress line goes out

CREDENTIALS_HINT = ("RZGitClient can't ask for credentials: use an SSH agent, a credential "
                    "helper or an askpass program.")
# In git's or ssh's output: a prompt that had no terminal and no askpass to answer it.
_PROMPT_FAILED = ("terminal prompts disabled", "could not read Username",
                  "could not read Password", "ssh_askpass", "Host key verification failed",
                  "Permission denied (publickey")
# git's progress lines, also when they end with ", done." on a line of their own.
_PROGRESS = re.compile(r"^(remote: )?[A-Z][\w ]*: +(\d+% \(\d+/\d+\)|\d+)(, .*)?$")

log = logging.getLogger(__name__)


class NetworkKind(StrEnum):
    """What a network command does."""

    FETCH = "fetch"
    PULL = "pull"  # its fetch: the upstream's remote; the session then integrates
    PUSH = "push"  # the current branch: to its upstream, or to a remote it then tracks


_ARGS = {
    NetworkKind.FETCH: ("fetch", "--all", "--prune", "--progress"),
}


@dataclass(frozen=True, eq=False)
class NetworkCommand:
    """One network command; compared by identity, like ``GitCommand``."""

    kind: NetworkKind
    remote: str = ""  # PULL's: the remote to fetch; PUSH's: the one to push to
    branch: str = ""  # PULL's: the branch to pull into, once fetched; PUSH's: the one pushed
    stash: bool = False  # PULL's: stash the changes while integrating
    track: bool = False  # PUSH's: push to remote/branch and make it the upstream (-u)

    def git_args(self, repo: Repo) -> tuple[str, ...]:
        """Git's arguments; a push to an upstream reads where it goes (on the
        worker thread). Raises GitError."""
        if self.kind is NetworkKind.PULL:
            return ("fetch", "--prune", "--progress", "--", self.remote)
        if self.kind is NetworkKind.PUSH:
            source = f"refs/heads/{self.branch}"
            if self.track:
                return ("push", "-u", "--progress", "--", self.remote, f"{source}:{source}")
            merge = repo.config_value(f"branch.{self.branch}.merge")
            if not merge:
                raise GitError(f"{self.branch} has no upstream to push to.")
            return ("push", "--progress", "--", self.remote, f"{source}:{merge}")
        return _ARGS[self.kind]


def pushes_to_upstream(branch: Branch) -> bool:
    """Whether Push sends ``branch`` to its upstream (spec §4.1): one on a remote
    (not ``.``) that isn't gone. Otherwise it is pushed to a remote it then tracks."""
    return bool(branch.remote) and branch.remote != "." and branch.ahead is not None


@dataclass(frozen=True)
class NetworkResult:
    command: NetworkCommand
    ok: bool
    cancelled: bool = False
    error: str | None = None  # a failed command's output, without its progress lines


class NetworkWorker(QObject):
    started = Signal(object)  # NetworkCommand
    progress = Signal(object, str)  # NetworkCommand, its latest progress line
    finished = Signal(object)  # NetworkResult

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        # No parent: a command still running when the worker is deleted can emit on it.
        self._relay = _Relay()
        self._relay.progress.connect(self._on_progress)
        self._relay.done.connect(self._on_done)
        self._running: NetworkCommand | None = None
        self._process: _Process | None = None

    @property
    def running(self) -> NetworkCommand | None:
        """The command running, until its ``finished`` goes out."""
        return self._running

    def start(self, repo: Repo, command: NetworkCommand) -> bool:
        """Run ``command`` in ``repo`` in the background; False while another runs."""
        if self._running is not None:
            return False
        self._running, self._process = command, _Process()
        process, relay = self._process, self._relay
        self.started.emit(command)
        self._pool.start(lambda: relay.done.emit(_run(repo, command, process, relay)))
        return True

    def cancel(self) -> None:
        """Kill the running command, if any; it finishes as cancelled."""
        if self._process is not None:
            self._process.cancel()

    def shutdown(self) -> None:
        """Cancel the running command and wait for it to end (on quit)."""
        self.cancel()
        self._pool.waitForDone()

    def _on_progress(self, command: NetworkCommand, line: str) -> None:
        if command is self._running:
            self.progress.emit(command, line)

    def _on_done(self, result: NetworkResult) -> None:
        if result.command is not self._running:
            return
        self._running, self._process = None, None
        self.finished.emit(result)


class _Relay(QObject):
    """Carries progress and results back to the main thread (queued connections)."""

    progress = Signal(object, str)
    done = Signal(object)  # NetworkResult


class _Process:
    """The running git process, shared by the worker thread and ``cancel()``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._popen: subprocess.Popen[bytes] | None = None
        self._ended = False
        self.cancelled = False

    def attach(self, popen: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._popen = popen
            if self.cancelled:  # cancelled before git started
                self._kill()

    def end(self) -> None:
        with self._lock:
            self._ended = True

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            if self._popen is not None and not self._ended:
                self._kill()

    def _kill(self) -> None:
        try:  # the group: git, and the ssh or https helper it started
            os.killpg(self._popen.pid, signal.SIGTERM)  # type: ignore[union-attr]
        except (ProcessLookupError, PermissionError):
            pass  # already gone


def _run(repo: Repo, command: NetworkCommand, process: _Process,
         relay: _Relay) -> NetworkResult:
    """The worker side: run ``command``, sending its progress as it comes."""
    try:
        popen = repo.start_network_git(*command.git_args(repo))
    except GitError as exc:
        return NetworkResult(command, False, error=str(exc))
    process.attach(popen)
    try:
        lines = _read(popen, lambda line: relay.progress.emit(command, line))
        code = popen.wait()
    except Exception as exc:  # a bug must not leave the command running for ever
        log.exception("Running %s failed", command.kind, exc_info=exc)
        popen.kill()
        popen.wait()
        return NetworkResult(command, False, error=f"Unexpected error: {exc}")
    finally:
        process.end()
        popen.stdout.close()  # type: ignore[union-attr]
    if process.cancelled:
        return NetworkResult(command, False, cancelled=True)
    if code == 0:
        return NetworkResult(command, True)
    error = error_message(lines, f"git {command.kind} failed ({code})")
    if command.kind is NetworkKind.PUSH and push_rejected(lines):
        error = (f"{command.remote} has commits that {command.branch} doesn't. "
                 f"Pull first, then push.\n\n{error}")
    return NetworkResult(command, False, error=error)


def push_rejected(lines: list[str]) -> bool:
    """Whether git's push output says the remote has commits the branch doesn't."""
    return any("[rejected]" in line and ("(fetch first)" in line or "(non-fast-forward)" in line)
               for line in lines)


def _read(popen: subprocess.Popen[bytes], progress) -> list[str]:
    """Read git's output to its end; send its latest line through ``progress``,
    at most every PROGRESS_INTERVAL. Returns its lines that end in a newline
    (and the last one, if it doesn't), not those ending in ``\\r``."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    fd = popen.stdout.fileno()  # type: ignore[union-attr]
    lines: list[str] = []
    buffer = ""
    sent = 0.0
    while chunk := os.read(fd, 4096):
        buffer += decoder.decode(chunk)
        latest = None
        while (end := _line_end(buffer)) >= 0:
            segment, ending, buffer = buffer[:end], buffer[end], buffer[end + 1:]
            if ending == "\n":
                lines.append(segment)
            if segment.strip():
                latest = segment
        if latest is not None and time.monotonic() - sent >= PROGRESS_INTERVAL:
            sent = time.monotonic()
            progress(progress_text(latest))
    buffer += decoder.decode(b"", final=True)
    if buffer.strip():
        lines.append(buffer)
    return lines


def _line_end(text: str) -> int:
    ends = [i for i in (text.find("\r"), text.find("\n")) if i >= 0]
    return min(ends) if ends else -1


def progress_text(line: str) -> str:
    """A line of git's output as the status bar shows it."""
    return line.strip().removeprefix("remote:").strip()


def error_message(lines: list[str], fallback: str) -> str:
    """A failed command's output, without its progress lines; with the
    credentials hint first when a prompt couldn't be answered."""
    kept = [line.rstrip() for line in lines if line.strip() and not _PROGRESS.match(line.strip())]
    message = "\n".join(kept) or fallback
    if any(marker in message for marker in _PROMPT_FAILED):
        message = f"{CREDENTIALS_HINT}\n\n{message}"
    return message
