"""Network commands: the worker, Fetch through the session (spec §4.1, RZG-62)."""

import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QThread

from rzgit.core import network_worker
from rzgit.core.network_worker import (
    CREDENTIALS_HINT,
    NetworkKind,
    error_message,
    progress_text,
)
from rzgit.core.repo_session import RepoSession
from rzgit.git.repo import network_env
from tests.support import SignalRecorder, commit_file, git, make_repo, qt_app, wait_idle

NETWORK_SIGNALS = ("network_changed", "network_progress", "network_finished", "error_occurred")


def wait_network(session: RepoSession, timeout: float = 10.0) -> None:
    """Process events until no network command runs, then until the session is idle."""
    deadline = time.monotonic() + timeout
    while session.network is not None:
        if time.monotonic() > deadline:
            raise AssertionError(f"network command still running after {timeout}s")
        QCoreApplication.processEvents()
        QThread.msleep(1)
    wait_idle(session)


def wait_for(condition, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        QCoreApplication.processEvents()
        QThread.msleep(1)


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    # A zombie still answers until it's reaped; it no longer runs.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1].startswith("Z")
    except (FileNotFoundError, IndexError):
        return True


class OutputTests(unittest.TestCase):
    """Reading git's output as it comes (no Qt, no git)."""

    def read(self, data: bytes):
        r, w = os.pipe()
        os.write(w, data)
        os.close(w)
        popen = mock.Mock()
        popen.stdout = os.fdopen(r, "rb")
        self.addCleanup(popen.stdout.close)
        sent = []
        return network_worker._read(popen, sent.append), sent

    def test_progress_lines_are_sent_and_left_out_of_the_lines(self):
        lines, sent = self.read(b"Fetching origin\nremote: Counting objects:  50% (1/2)\r"
                                b"remote: Counting objects: 100% (2/2), done.\n"
                                b"Receiving objects: 100% (2/2), done.\nfatal: broken")
        self.assertEqual(lines, ["Fetching origin", "remote: Counting objects: 100% (2/2), done.",
                                 "Receiving objects: 100% (2/2), done.", "fatal: broken"])
        self.assertEqual(sent[:1], ["Receiving objects: 100% (2/2), done."])  # one chunk: its latest
        self.assertEqual(error_message(lines, "fallback"), "Fetching origin\nfatal: broken")

    def test_progress_is_throttled(self):
        with mock.patch.object(network_worker.os, "read",
                               side_effect=[b"A: 1\r", b"A: 2\r", b"A: 3\r", b""]):
            popen = mock.Mock()
            sent = []
            network_worker._read(popen, sent.append)
        self.assertEqual(sent, ["A: 1"])  # the others came within PROGRESS_INTERVAL

    def test_a_split_utf8_character_is_kept_whole(self):
        lines, _ = self.read("error: café\n".encode()[:10] + "error: café\n".encode()[10:])
        self.assertEqual(lines, ["error: café"])

    def test_progress_text_drops_remote_and_spaces(self):
        self.assertEqual(progress_text("remote: Counting objects:  50% (1/2)  "),
                         "Counting objects:  50% (1/2)")

    def test_an_empty_error_says_what_failed(self):
        self.assertEqual(error_message(["Receiving objects: 100% (2/2), done."], "git fetch failed (1)"),
                         "git fetch failed (1)")

    def test_a_prompt_that_couldnt_be_answered_gets_the_credentials_hint(self):
        for line in ("fatal: could not read Username for 'https://x': terminal prompts disabled",
                     "Host key verification failed.",
                     "git@x: Permission denied (publickey).",
                     "ssh_askpass: exec(/usr/libexec/ssh-askpass): No such file or directory"):
            with self.subTest(line=line):
                self.assertEqual(error_message([line], ""), f"{CREDENTIALS_HINT}\n\n{line}")
        self.assertEqual(error_message(["fatal: no such remote"], ""), "fatal: no such remote")


class NetworkEnvTests(unittest.TestCase):
    def test_no_prompt_and_an_askpass_preferred_when_set(self):
        with mock.patch.dict(os.environ, {"SSH_ASKPASS": "/usr/bin/ksshaskpass"}):
            os.environ.pop("SSH_ASKPASS_REQUIRE", None)
            env = network_env()
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["SSH_ASKPASS_REQUIRE"], "prefer")

    def test_the_users_askpass_choice_is_kept(self):
        with mock.patch.dict(os.environ, {"SSH_ASKPASS": "/x", "SSH_ASKPASS_REQUIRE": "never"}):
            self.assertEqual(network_env()["SSH_ASKPASS_REQUIRE"], "never")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SSH_ASKPASS", None)
            os.environ.pop("SSH_ASKPASS_REQUIRE", None)
            self.assertNotIn("SSH_ASKPASS_REQUIRE", network_env())


class FetchTests(unittest.TestCase):
    """Fetch through the session, against a local bare remote (or a fake ssh one)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        seed = make_repo(self.tmp / "seed")
        commit_file(seed, "a.txt", "1", "first")
        git(self.tmp, "clone", "-q", "--bare", str(seed), "origin.git")
        git(self.tmp, "clone", "-q", "origin.git", "repo")
        self.path = self.tmp / "repo"
        self.other = self.tmp / "other"  # another clone, that pushes
        git(self.tmp, "clone", "-q", "origin.git", "other")
        for repo in (self.path, self.other):
            git(repo, "config", "user.name", "Test User")
            git(repo, "config", "user.email", "test@example.com")
        self.session = RepoSession()
        self.addCleanup(self.session.shutdown)
        self.rec = SignalRecorder(self.session, *NETWORK_SIGNALS)

    def open(self):
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)
        self.rec.clear()

    def push_from_other(self):
        commit_file(self.other, "b.txt", "2", "second")
        git(self.other, "push", "-q", "origin", "main")

    def fake_ssh(self, body: str) -> None:
        """Point origin at an ssh URL whose 'ssh' is the script ``body``."""
        script = self.tmp / "fake-ssh"
        script.write_text(f"#!/bin/sh\n{body}\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        git(self.path, "remote", "set-url", "origin", "ssh://example.invalid/x.git")
        patcher = mock.patch.dict(os.environ, {"GIT_SSH_COMMAND": str(script)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_remotes_and_upstreams_are_read(self):
        self.open()
        self.assertEqual(self.session.remotes, ("origin",))
        main = next(b for b in self.session.branches if b.name == "main")
        self.assertEqual((main.upstream, main.ahead, main.behind), ("origin/main", 0, 0))

    def test_fetch_brings_the_remotes_commits_and_the_counts_follow(self):
        self.open()
        self.push_from_other()
        finished = []
        self.session.network_finished.connect(lambda command, ok: finished.append(ok))
        command = self.session.fetch()
        self.assertEqual(command.kind, NetworkKind.FETCH)
        self.assertIs(self.session.network, command)
        self.assertIsNone(self.session.fetch())  # one at a time
        wait_network(self.session)
        self.assertEqual(self.rec.values("network_changed"), [command, None])
        self.assertEqual(self.rec.values("network_finished"), [command])
        self.assertEqual(finished, [True])
        self.assertEqual(self.rec.values("error_occurred"), [])
        progress = self.rec.values("network_progress")
        self.assertTrue(progress and all(p == p.strip() for p in progress), progress)
        self.assertFalse(any(p.startswith("remote:") for p in progress), progress)
        main = next(b for b in self.session.branches if b.name == "main")
        self.assertEqual((main.ahead, main.behind), (0, 1))

    def test_fetch_prunes_remote_branches_that_are_gone(self):
        git(self.other, "push", "-q", "origin", "main:topic")
        git(self.path, "fetch", "-q")
        git(self.other, "push", "-q", "origin", ":topic")
        self.open()
        self.session.fetch()
        wait_network(self.session)
        self.assertNotIn("origin/topic", git(self.path, "branch", "-r"))

    def test_no_fetch_without_a_repository(self):
        self.assertIsNone(self.session.fetch())
        self.assertIsNone(self.session.network)

    def test_loads_go_on_while_a_fetch_runs(self):
        self.fake_ssh("exec sleep 30")
        self.open()
        self.session.fetch()
        (self.path / "a.txt").write_text("changed")
        self.session.refresh()
        wait_idle(self.session)  # the session's worker isn't held up by the fetch
        self.assertEqual([f.path for f in self.session.status], ["a.txt"])
        self.assertIsNotNone(self.session.network)
        self.session.cancel_network()
        wait_network(self.session)

    def test_cancel_kills_the_process_group_without_an_error(self):
        # (git passes ssh's own output on only when ssh ends: progress comes from git.)
        pid_file = self.tmp / "ssh.pid"
        self.fake_ssh(f"echo $$ > {pid_file}.new\nmv {pid_file}.new {pid_file}\nexec sleep 30")
        self.open()
        command = self.session.fetch()
        wait_for(pid_file.exists)
        pid = int(pid_file.read_text())
        started = time.monotonic()
        self.session.cancel_network()
        wait_network(self.session)
        self.assertLess(time.monotonic() - started, 5)
        wait_for(lambda: gone(pid), timeout=5)  # ssh too, not only git
        self.assertEqual(self.rec.values("network_changed"), [command, None])
        self.assertEqual(self.rec.values("network_finished"), [])
        self.assertEqual(self.rec.values("error_occurred"), [])
        self.assertIsNotNone(self.session.fetch())  # free for the next one
        self.session.cancel_network()
        wait_network(self.session)

    def test_closing_the_repository_cancels_the_fetch(self):
        self.fake_ssh("exec sleep 30")
        self.open()
        self.session.fetch()
        self.session.close()
        wait_network(self.session, timeout=5)
        self.assertEqual(self.rec.values("error_occurred"), [])
        self.assertEqual(self.rec.values("network_finished"), [])

    def test_git_runs_without_a_terminal(self):
        sid_file = self.tmp / "ssh.sid"
        self.fake_ssh(f"ps -o sid= -p $$ > {sid_file}\nexit 1")
        self.open()
        self.session.fetch()
        wait_network(self.session)
        self.assertNotEqual(int(sid_file.read_text()), os.getsid(0))  # a session of its own

    def test_a_prompt_that_cant_be_answered_fails_with_the_hint(self):
        self.fake_ssh("echo 'Host key verification failed.' >&2\nexit 255")
        self.open()
        command = self.session.fetch()
        wait_network(self.session)
        errors = self.rec.values("error_occurred")
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith(CREDENTIALS_HINT), errors[0])
        self.assertIn("Host key verification failed.", errors[0])
        self.assertEqual(self.rec.events[-1], ("network_finished", command))
        self.assertEqual(self.rec.names()[-2:], ["error_occurred", "network_finished"])


if __name__ == "__main__":
    unittest.main()
