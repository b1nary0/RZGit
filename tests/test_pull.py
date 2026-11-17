"""Pull: the command, through the session, the action and its boxes (spec §4.1, §8, RZG-71)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtWidgets import QWidget

from rzgitclient.core.git_commands import (
    CHANGES_KEPT,
    CommandKind,
    GitCommand,
    PullDiverged,
    PullMode,
    pull_stash_message,
    run_command,
)
from rzgitclient.core.network_worker import NetworkCommand, NetworkKind
from rzgitclient.core.repo_session import RepoSession
from rzgitclient.core.selection import Selection
from rzgitclient.git.git_models import Branch, FileStatus
from rzgitclient.git.repo import GitError, Repo
from rzgitclient.ui.app_actions import AppActions
from rzgitclient.ui.pull_dialogs import MERGE, REBASE, STASH, WITH_CHANGES, PullDialogs
from tests.support import SignalRecorder, commit_file, git, make_repo, qt_app, wait_idle
from tests.test_network_worker import wait_network


class Clones:
    """``repo`` and ``other``, clones of a bare ``origin``; ``other`` pushes."""

    def make_clones(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        seed = make_repo(self.tmp / "seed")
        commit_file(seed, "a.txt", "1\n2\n3\n", "first")
        git(self.tmp, "clone", "-q", "--bare", str(seed), "origin.git")
        for name in ("repo", "other"):
            git(self.tmp, "clone", "-q", "origin.git", name)
            git(self.tmp / name, "config", "user.name", "Test User")
            git(self.tmp / name, "config", "user.email", "test@example.com")
            git(self.tmp / name, "config", "commit.gpgsign", "false")
        self.path, self.other = self.tmp / "repo", self.tmp / "other"

    def push_from_other(self, name="b.txt", content="b", message="theirs"):
        commit_file(self.other, name, content, message)
        git(self.other, "push", "-q", "origin", "main")

    def head(self, rev="HEAD"):
        return git(self.path, "rev-parse", rev).strip()


class PullCommandTests(Clones, unittest.TestCase):
    """The worker side: ``run_command`` with a PULL, fetched beforehand (no Qt)."""

    def setUp(self):
        self.make_clones()
        self.repo = Repo(self.path)

    def pull(self, mode=PullMode.FF_ONLY, stash=False, branch="main"):
        git(self.path, "fetch", "-q")
        return run_command(self.repo, GitCommand(CommandKind.PULL, branch=branch, mode=mode,
                                                 stash=stash))

    def diverge(self):
        self.push_from_other()
        commit_file(self.path, "c.txt", "c", "ours")

    def test_the_upstreams_remote_is_read(self):
        git(self.path, "branch", "local", "main")
        git(self.path, "branch", "-q", "--track", "follows", "main")
        branches = {b.name: b for b in self.repo.branches()}
        self.assertEqual(branches["main"].remote, "origin")
        self.assertEqual(branches["follows"].remote, ".")
        self.assertIsNone(branches["local"].remote)

    def test_up_to_date_changes_nothing(self):
        before = self.head()
        self.assertIsNone(self.pull())
        self.assertEqual(self.head(), before)

    def test_only_ahead_has_nothing_to_pull(self):
        commit_file(self.path, "c.txt", "c", "ours")
        before = self.head()
        self.assertIsNone(self.pull())
        self.assertEqual(self.head(), before)

    def test_behind_fast_forwards(self):
        self.push_from_other()
        self.assertIsNone(self.pull())
        self.assertEqual(self.head(), self.head("origin/main"))

    def test_diverged_changes_nothing_and_says_so(self):
        self.diverge()
        before = self.head()
        self.assertEqual(self.pull(), PullDiverged(1, 1, False))
        self.assertEqual(self.head(), before)

    def test_diverged_with_stash_stashes_nothing(self):
        self.diverge()
        (self.path / "a.txt").write_text("mine\n")
        self.assertIsInstance(self.pull(stash=True), PullDiverged)
        self.assertEqual(git(self.path, "stash", "list"), "")
        self.assertEqual((self.path / "a.txt").read_text(), "mine\n")

    def test_merge_makes_a_merge_commit(self):
        self.diverge()
        ours = self.head()
        self.assertIsNone(self.pull(PullMode.MERGE))
        parents = git(self.path, "rev-list", "--parents", "-n1", "HEAD").split()[1:]
        self.assertEqual(parents, [ours, self.head("origin/main")])

    def test_rebase_replays_the_branch_on_its_upstream(self):
        self.diverge()
        self.assertIsNone(self.pull(PullMode.REBASE))
        self.assertEqual(self.head("HEAD~1"), self.head("origin/main"))
        self.assertEqual(git(self.path, "log", "-1", "--format=%s").strip(), "ours")

    def test_a_conflict_aborts_and_changes_nothing(self):
        self.push_from_other("a.txt", "theirs\n", "theirs")
        commit_file(self.path, "a.txt", "ours\n", "ours")
        before = self.head()
        for mode in (PullMode.MERGE, PullMode.REBASE):
            with self.assertRaises(GitError) as caught:
                self.pull(mode)
            self.assertIn("aborted, so nothing changed", str(caught.exception))
            self.assertEqual(self.head(), before)
            self.assertEqual(git(self.path, "status", "--porcelain"), "")

    def test_another_current_branch_is_an_error(self):
        self.push_from_other()
        git(self.path, "switch", "-q", "-c", "other", "--track", "origin/main")
        git(self.path, "reset", "-q", "--hard", "main")  # behind its upstream too
        with self.assertRaises(GitError):
            self.pull(branch="main")
        self.assertNotEqual(self.head("main"), self.head("origin/main"))
        self.assertNotEqual(self.head("other"), self.head("origin/main"))

    def test_stash_and_pull_puts_the_changes_back(self):
        self.push_from_other()
        (self.path / "a.txt").write_text("1\n2\n3\nmine\n")
        (self.path / "new.txt").write_text("new")
        git(self.path, "add", "new.txt")
        (self.path / "untracked.txt").write_text("u")
        self.assertIsNone(self.pull(stash=True))
        self.assertEqual(self.head(), self.head("origin/main"))
        self.assertEqual(git(self.path, "stash", "list"), "")
        self.assertEqual(sorted(git(self.path, "status", "--porcelain").splitlines()),
                         [" M a.txt", "?? untracked.txt", "A  new.txt"])

    def test_a_put_back_that_conflicts_keeps_the_stash_with_a_notice(self):
        self.push_from_other("a.txt", "theirs\n", "theirs")
        (self.path / "a.txt").write_text("mine\n")
        notice = self.pull(stash=True)
        self.assertEqual(self.head(), self.head("origin/main"))  # the pull stays
        self.assertTrue(notice.startswith("Pulled into main, but your changes couldn't"), notice)
        self.assertIn(pull_stash_message("main"), notice)
        self.assertIn(pull_stash_message("main"), git(self.path, "stash", "list"))
        self.assertEqual(git(self.path, "status", "--porcelain"), "")  # clean, not half-done
        self.assertEqual(CHANGES_KEPT.split("{")[0], "Pulled into ")

    def test_a_failed_integration_pops_the_changes_back(self):
        self.push_from_other("a.txt", "theirs\n", "theirs")
        commit_file(self.path, "a.txt", "ours\n", "ours")
        (self.path / "b.txt").write_text("mine")
        with self.assertRaises(GitError):
            self.pull(PullMode.MERGE, stash=True)
        self.assertEqual(git(self.path, "stash", "list"), "")
        self.assertEqual(git(self.path, "status", "--porcelain"), "?? b.txt\n")

    def test_rebase_with_changes_left_in_place_is_refused(self):
        self.diverge()
        (self.path / "a.txt").write_text("mine\n")
        before = self.head()
        with self.assertRaises(GitError):
            self.pull(PullMode.REBASE)
        self.assertEqual(self.head(), before)
        self.assertEqual((self.path / "a.txt").read_text(), "mine\n")

    def test_the_rebase_default(self):
        self.assertFalse(self.repo.pull_rebases("main"))
        for value, rebases in (("true", True), ("merges", True), ("interactive", True),
                               ("false", False)):
            git(self.path, "config", "pull.rebase", value)
            self.assertEqual(self.repo.pull_rebases("main"), rebases, value)
        git(self.path, "config", "pull.rebase", "true")
        git(self.path, "config", "branch.main.rebase", "false")  # the branch's wins
        self.assertFalse(self.repo.pull_rebases("main"))
        git(self.path, "config", "pull.rebase", "false")
        git(self.path, "config", "branch.main.rebase", "true")
        self.assertTrue(self.repo.pull_rebases("main"))

    def test_the_diverged_result_carries_the_rebase_default(self):
        self.diverge()
        git(self.path, "config", "pull.rebase", "true")
        self.assertEqual(self.pull(), PullDiverged(1, 1, True))


class SessionPullTests(Clones, unittest.TestCase):
    """Pull through the session: the fetch on the network worker, then the command."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.make_clones()
        self.session = RepoSession()
        self.addCleanup(self.session.shutdown)
        self.rec = SignalRecorder(self.session, "network_changed", "network_finished",
                                  "pull_diverged", "error_occurred", "notice_occurred",
                                  "command_finished")

    def open(self):
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)
        self.rec.clear()

    def test_pull_fetches_the_upstreams_remote_then_fast_forwards(self):
        self.open()
        self.push_from_other()
        command = self.session.pull()
        self.assertEqual((command.kind, command.remote, command.branch, command.stash),
                         (NetworkKind.PULL, "origin", "main", False))
        self.assertEqual(command.git_args(self.session.repo),
                         ("fetch", "--prune", "--progress", "--", "origin"))
        self.assertIsNone(self.session.pull())  # one at a time
        self.assertIsNone(self.session.fetch())
        wait_network(self.session)
        self.assertEqual(self.head(), self.head("origin/main"))
        self.assertEqual(self.rec.values("error_occurred"), [])
        pulls = [c for c in self.rec.values("command_finished") if c.kind is CommandKind.PULL]
        self.assertEqual(len(pulls), 1)
        main = next(b for b in self.session.branches if b.is_current)
        self.assertEqual((main.ahead, main.behind), (0, 0))

    def test_diverged_asks_then_integrates_without_fetching_again(self):
        self.open()
        self.push_from_other()
        commit_file(self.path, "c.txt", "c", "ours")
        self.session.refresh()
        wait_idle(self.session)
        self.session.pull(stash=True)
        wait_network(self.session)
        [pull] = self.rec.values("pull_diverged")
        self.assertEqual((pull.kind, pull.mode, pull.stash), (CommandKind.PULL,
                                                              PullMode.FF_ONLY, True))
        names = self.rec.names()
        self.assertLess(names.index("pull_diverged"), names.index("command_finished"))
        command = self.session.pull_integrate(PullMode.MERGE, stash=True)
        self.assertEqual((command.mode, command.stash, command.branch),
                         (PullMode.MERGE, True, "main"))
        wait_idle(self.session)
        self.assertIsNone(self.session.network)
        self.assertEqual(len(git(self.path, "rev-list", "--parents", "-n1", "HEAD").split()), 3)

    def test_the_diverged_signal_carries_the_counts(self):
        self.open()
        self.push_from_other()
        commit_file(self.path, "c.txt", "c", "ours")
        got = []
        self.session.pull_diverged.connect(lambda command, diverged: got.append(diverged))
        self.session.refresh()
        wait_idle(self.session)
        self.session.pull()
        wait_network(self.session)
        self.assertEqual(got, [PullDiverged(1, 1, False)])

    def test_no_pull_without_an_upstream_or_on_a_detached_head(self):
        self.assertIsNone(self.session.pull())  # no repository
        self.open()
        git(self.path, "switch", "-q", "-c", "local")
        self.session.refresh()
        wait_idle(self.session)
        self.assertIsNone(self.session.pull())
        git(self.path, "switch", "-q", "--detach", "main")
        self.session.refresh()
        wait_idle(self.session)
        self.assertIsNone(self.session.pull())
        self.assertIsNone(self.session.network)

    def test_no_pull_when_the_upstream_is_gone(self):
        git(self.other, "push", "-q", "origin", "main:topic")
        git(self.path, "fetch", "-q")
        git(self.path, "switch", "-q", "-c", "topic", "--track", "origin/topic")
        git(self.other, "push", "-q", "origin", ":topic")
        git(self.path, "fetch", "-q", "--prune")
        self.open()
        current = next(b for b in self.session.branches if b.is_current)
        self.assertEqual((current.upstream, current.behind), ("origin/topic", None))
        self.assertIsNone(self.session.pull())
        self.assertIsNone(self.session.network)

    def test_a_local_upstream_skips_the_fetch(self):
        git(self.path, "branch", "-q", "--track", "follows", "main")
        commit_file(self.path, "m.txt", "m", "on main")
        git(self.path, "switch", "-q", "follows")
        self.open()
        command = self.session.pull()
        self.assertIsInstance(command, GitCommand)
        self.assertEqual(self.rec.values("network_changed"), [])
        wait_idle(self.session)
        self.assertEqual(self.head(), self.head("main"))

    def test_a_cancelled_pull_integrates_nothing(self):
        self.open()
        self.push_from_other()
        git(self.path, "remote", "set-url", "origin", "ssh://example.invalid/x.git")
        script = self.tmp / "fake-ssh"
        script.write_text("#!/bin/sh\nexec sleep 30\n")
        script.chmod(0o755)
        with mock.patch.dict("os.environ", {"GIT_SSH_COMMAND": str(script)}):
            (self.path / "a.txt").write_text("mine\n")
            before = self.head()
            self.session.pull(stash=True)
            self.session.cancel_network()
            wait_network(self.session)
        self.assertEqual(self.head(), before)
        self.assertEqual(git(self.path, "stash", "list"), "")
        self.assertEqual(self.rec.values("error_occurred"), [])
        self.assertFalse([c for c in self.rec.values("command_finished")
                          if c.kind is CommandKind.PULL])

    def test_a_failed_fetch_integrates_nothing(self):
        self.open()
        git(self.path, "remote", "set-url", "origin", str(self.tmp / "missing.git"))
        self.session.pull()
        wait_network(self.session)
        self.assertEqual(len(self.rec.values("error_occurred")), 1)
        self.assertFalse([c for c in self.rec.values("command_finished")
                          if c.kind is CommandKind.PULL])


class PullActionTests(unittest.TestCase):
    """The Pull action: its text, its state, and cancelling (spec §8)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.repo, "a.txt", "1", "first")
        self.session = RepoSession()
        self.addCleanup(self.session.shutdown)
        self.actions = AppActions(self.session, Selection(self.session))

    def set_branches(self, *branches):
        with mock.patch.object(type(self.session), "branches", branches):
            self.session.branches_changed.emit(branches)

    def test_pull_needs_an_upstream_and_shows_how_far_behind(self):
        pull = self.actions.pull
        self.assertFalse(pull.isEnabled())  # no repository
        self.session.open(self.repo)
        wait_idle(self.session)
        self.assertFalse(pull.isEnabled())  # no upstream
        with mock.patch.object(type(self.session), "branches",
                               (Branch("main", True, upstream="origin/main", ahead=0,
                                       behind=3, remote="origin"),)):
            self.session.branches_changed.emit(())
            self.assertTrue(pull.isEnabled())
            self.assertEqual((pull.text(), pull.toolTip()),
                             ("&Pull ↓3", "Pull origin/main into main"))
        with mock.patch.object(type(self.session), "branches",
                               (Branch("main", True, upstream="origin/main", ahead=1,
                                       behind=0, remote="origin"),)):
            self.session.branches_changed.emit(())
            self.assertEqual(pull.text(), "&Pull")
        with mock.patch.object(type(self.session), "branches",  # its upstream is gone
                               (Branch("main", True, upstream="origin/main", remote="origin"),)):
            self.session.branches_changed.emit(())
            self.assertFalse(pull.isEnabled())

    def test_pull_asks_cancels_its_own_fetch_and_waits_for_a_fetch(self):
        self.session.open(self.repo)
        wait_idle(self.session)
        branches = (Branch("main", True, upstream="origin/main", ahead=0, behind=1,
                           remote="origin"),)
        requested = []
        self.actions.pull_requested.connect(lambda: requested.append(True))
        pull, fetch = self.actions.pull, self.actions.fetch
        with mock.patch.object(type(self.session), "branches", branches), \
                mock.patch.object(type(self.session), "remotes", ("origin",)), \
                mock.patch.object(self.session, "cancel_network") as cancel:
            self.session.branches_changed.emit(branches)
            pull.trigger()
            self.assertEqual(requested, [True])
            pulling = NetworkCommand(NetworkKind.PULL, remote="origin", branch="main")
            with mock.patch.object(type(self.session), "network", pulling):
                self.session.network_changed.emit(pulling)
                self.assertEqual((pull.text(), pull.toolTip(), pull.isEnabled()),
                                 ("Pulling…", "Pulling… Click to cancel", True))
                self.assertFalse(fetch.isEnabled())
                pull.trigger()
                cancel.assert_called_once_with()
                fetch.trigger()  # disabled: nothing
                cancel.assert_called_once_with()
            fetching = NetworkCommand(NetworkKind.FETCH)
            with mock.patch.object(type(self.session), "network", fetching):
                self.session.network_changed.emit(fetching)
                self.assertFalse(pull.isEnabled())
                self.assertEqual(pull.text(), "&Pull ↓1")
                self.assertTrue(fetch.isEnabled())
            self.assertEqual(requested, [True])
            self.session.network_changed.emit(None)
            self.assertTrue(pull.isEnabled() and fetch.isEnabled())


class PullDialogsTests(unittest.TestCase):
    """What Pull asks, and what it then runs (spec §8)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.real_session = RepoSession()
        self.addCleanup(self.real_session.shutdown)
        self.actions = AppActions(self.real_session, Selection(self.real_session))
        self.session = mock.Mock(spec=RepoSession)
        self.session.head = "main"
        self.session.status = ()
        self.session.branches = (Branch("main", True, upstream="origin/main", ahead=2,
                                        behind=3, remote="origin"),)
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.dialogs = PullDialogs(self.window, self.session, self.actions)
        self.asked = []  # (title, text, info, labels, default)
        self.answers = []

    def fake_ask(self, title, text, info, buttons, *, default):
        self.asked.append((title, text, info, [b for b, _ in buttons], default))
        return self.answers.pop(0) if self.answers else None

    def run_pull(self, *answers):
        self.answers = list(answers)
        with mock.patch.object(self.dialogs, "ask", side_effect=self.fake_ask):
            self.actions.pull_requested.emit()

    def run_diverged(self, command, diverged, *answers):
        self.answers = list(answers)
        with mock.patch.object(self.dialogs, "ask", side_effect=self.fake_ask):
            self.dialogs.diverged(command, diverged)

    def test_no_changes_pulls_at_once(self):
        self.run_pull()
        self.assertEqual(self.asked, [])
        self.session.pull.assert_called_once_with()

    def test_changes_offer_to_stash_them_first(self):
        self.session.status = (FileStatus(" ", "M", "a.txt"),)
        self.run_pull(None)
        self.session.pull.assert_not_called()
        self.assertEqual(self.asked[0][1], "Pull into main with uncommitted changes?")
        self.assertEqual(self.asked[0][3:], ([STASH, WITH_CHANGES], STASH))
        self.assertIn(pull_stash_message("main"), self.asked[0][2])
        self.run_pull(STASH)
        self.session.pull.assert_called_once_with(stash=True)
        self.run_pull(WITH_CHANGES)
        self.session.pull.assert_called_with(stash=False)

    def test_diverged_asks_rebase_or_merge_with_the_default(self):
        command = GitCommand(CommandKind.PULL, branch="main", stash=True)
        self.run_diverged(command, PullDiverged(2, 3, False), None)
        self.session.pull_integrate.assert_not_called()
        title, text, info, labels, default = self.asked[0]
        self.assertEqual(text, "main and origin/main have diverged.")
        self.assertIn("2 commits that origin/main doesn't", info)
        self.assertIn("3 commits that main doesn't", info)
        self.assertEqual((labels, default), ([REBASE, MERGE], MERGE))
        self.run_diverged(command, PullDiverged(2, 3, True), REBASE)
        self.assertEqual(self.asked[1][4], REBASE)
        self.session.pull_integrate.assert_called_once_with(PullMode.REBASE, stash=True)
        self.run_diverged(command, PullDiverged(2, 3, True), MERGE)
        self.session.pull_integrate.assert_called_with(PullMode.MERGE, stash=True)

    def test_diverged_after_a_switch_asks_nothing(self):
        self.session.head = "other"
        self.run_diverged(GitCommand(CommandKind.PULL, branch="main"),
                          PullDiverged(1, 1, False), MERGE)
        self.assertEqual(self.asked, [])
        self.session.pull_integrate.assert_not_called()

    def test_the_sessions_signal_reaches_the_box(self):
        session = RepoSession()
        self.addCleanup(session.shutdown)
        with mock.patch.object(PullDialogs, "diverged") as diverged:
            PullDialogs(self.window, session, self.actions)
            command, result = GitCommand(CommandKind.PULL, branch="main"), PullDiverged(1, 1, False)
            session.pull_diverged.emit(command, result)
        diverged.assert_called_once_with(command, result)


if __name__ == "__main__":
    unittest.main()
