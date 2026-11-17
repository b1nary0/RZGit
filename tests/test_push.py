"""Push: the command, through the session, the action and its box (spec §4.1, §8, RZG-72)."""

import unittest
from unittest import mock

from PySide6.QtWidgets import QWidget

from rzgitclient.core.network_worker import (
    NetworkCommand,
    NetworkKind,
    push_rejected,
    pushes_to_upstream,
)
from rzgitclient.core.repo_session import RepoSession
from rzgitclient.core.selection import Selection
from rzgitclient.git.git_models import Branch
from rzgitclient.git.repo import GitError, Repo
from rzgitclient.ui.app_actions import AppActions
from rzgitclient.ui.push_dialogs import PushDialogs, default_remote
from tests.support import SignalRecorder, commit_file, git, qt_app
from tests.test_network_worker import wait_network
from tests.test_pull import Clones


class PushArgsTests(Clones, unittest.TestCase):
    """What a push runs (no Qt): an explicit refspec, never forced, no tags."""

    def setUp(self):
        self.make_clones()
        self.repo = Repo(self.path)

    def test_to_the_upstream_by_its_merge_ref(self):
        git(self.path, "config", "branch.main.merge", "refs/heads/trunk")
        git(self.path, "config", "push.default", "nothing")  # not read
        command = NetworkCommand(NetworkKind.PUSH, remote="origin", branch="main")
        self.assertEqual(command.git_args(self.repo),
                         ("push", "--progress", "--", "origin", "refs/heads/main:refs/heads/trunk"))

    def test_to_track_it(self):
        command = NetworkCommand(NetworkKind.PUSH, remote="fork", branch="topic", track=True)
        self.assertEqual(command.git_args(self.repo),
                         ("push", "-u", "--progress", "--", "fork",
                          "refs/heads/topic:refs/heads/topic"))

    def test_no_merge_ref_is_an_error(self):
        command = NetworkCommand(NetworkKind.PUSH, remote="origin", branch="nowhere")
        with self.assertRaises(GitError):
            command.git_args(self.repo)

    def test_which_branches_push_to_their_upstream(self):
        self.assertTrue(pushes_to_upstream(Branch("main", True, upstream="origin/main", ahead=0,
                                                  behind=0, remote="origin")))
        self.assertFalse(pushes_to_upstream(Branch("main", True)))  # none
        self.assertFalse(pushes_to_upstream(Branch("main", True, upstream="origin/main",
                                                   remote="origin")))  # gone
        self.assertFalse(pushes_to_upstream(Branch("main", True, upstream="other", ahead=1,
                                                   behind=0, remote=".")))  # local

    def test_rejections_that_mean_pull_first(self):
        self.assertTrue(push_rejected([" ! [rejected]        main -> main (fetch first)"]))
        self.assertTrue(push_rejected([" ! [rejected]        main -> main (non-fast-forward)"]))
        self.assertFalse(push_rejected([" ! [remote rejected] main -> main (pre-receive hook "
                                        "declined)"]))
        self.assertFalse(push_rejected(["Everything up-to-date"]))

    def test_the_push_default_is_read(self):
        self.assertIsNone(self.repo.push_default())
        git(self.path, "config", "remote.pushDefault", "fork")
        self.assertEqual(self.repo.push_default(), "fork")


class SessionPushTests(Clones, unittest.TestCase):
    """Push through the session, on the network worker, against a bare origin."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.make_clones()
        self.session = RepoSession()
        self.addCleanup(self.session.shutdown)
        self.rec = SignalRecorder(self.session, "network_finished", "error_occurred",
                                  "remotes_changed")
        self.finished = []  # (command, ok): the recorder keeps only the first argument
        self.session.network_finished.connect(lambda c, ok: self.finished.append((c, ok)))

    def open(self):
        self.assertTrue(self.session.open(self.path))
        wait_network(self.session)
        self.rec.clear()
        self.finished.clear()

    def origin(self, rev="main"):
        return git(self.tmp / "origin.git", "rev-parse", rev).strip()

    def current(self):
        return next(b for b in self.session.branches if b.is_current)

    def test_push_sends_the_branch_to_its_upstream_and_the_count_follows(self):
        commit_file(self.path, "c.txt", "c", "ours")
        self.open()
        self.assertEqual(self.current().ahead, 1)
        command = self.session.push()
        self.assertEqual((command.kind, command.remote, command.branch, command.track),
                         (NetworkKind.PUSH, "origin", "main", False))
        self.assertIsNone(self.session.push())  # one at a time
        self.assertIsNone(self.session.fetch())
        self.assertIsNone(self.session.pull())
        wait_network(self.session)
        self.assertEqual(self.origin(), self.head())
        self.assertEqual(self.finished, [(command, True)])
        self.assertEqual(self.rec.values("error_occurred"), [])
        self.assertEqual(self.current().ahead, 0)  # the branches reloaded

    def test_an_upstream_with_another_name_is_pushed_to_by_its_merge_ref(self):
        git(self.tmp / "origin.git", "branch", "trunk", "main")
        git(self.path, "fetch", "-q")
        git(self.path, "branch", "-q", "--set-upstream-to", "origin/trunk")
        commit_file(self.path, "c.txt", "c", "ours")
        self.open()
        self.session.push()
        wait_network(self.session)
        self.assertEqual(self.origin("trunk"), self.head())
        self.assertNotEqual(self.origin("main"), self.head())

    def test_up_to_date_pushes_without_an_error(self):
        self.open()
        command = self.session.push()
        wait_network(self.session)
        self.assertEqual(self.finished, [(command, True)])
        self.assertEqual(self.rec.values("error_occurred"), [])

    def test_a_branch_with_no_upstream_needs_a_remote_and_then_tracks_it(self):
        git(self.path, "switch", "-q", "-c", "topic")
        commit_file(self.path, "t.txt", "t", "topic")
        self.open()
        self.assertIsNone(self.session.push())  # no upstream, no remote given
        self.assertIsNone(self.session.push("nowhere"))  # not a remote
        command = self.session.push("origin")
        self.assertTrue(command.track)
        wait_network(self.session)
        self.assertEqual(self.origin("topic"), self.head())
        self.assertEqual(git(self.path, "rev-parse", "--abbrev-ref", "topic@{u}").strip(),
                         "origin/topic")
        self.assertEqual((self.current().upstream, self.current().ahead),
                         ("origin/topic", 0))

    def test_a_local_upstream_is_replaced_by_tracking(self):
        git(self.path, "switch", "-q", "-c", "topic", "--track", "main")
        commit_file(self.path, "t.txt", "t", "topic")
        self.open()
        self.assertIsNone(self.session.push())  # its upstream is on "."
        self.session.push("origin")
        wait_network(self.session)
        self.assertEqual(self.current().upstream, "origin/topic")

    def test_a_rejected_push_says_to_pull_first_and_never_forces(self):
        self.push_from_other()
        commit_file(self.path, "c.txt", "c", "ours")
        git(self.path, "fetch", "-q")
        self.open()
        theirs = self.origin()
        command = self.session.push()
        wait_network(self.session)
        self.assertEqual(self.origin(), theirs)
        self.assertEqual(self.finished, [(command, False)])
        [error] = self.rec.values("error_occurred")
        self.assertTrue(error.startswith("origin has commits that main doesn't. "
                                         "Pull first, then push.\n\n"), error)
        self.assertIn("[rejected]", error)

    def test_a_rejection_by_a_hook_is_gits_output(self):
        hook = self.tmp / "origin.git" / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'protected branch'\nexit 1\n")
        hook.chmod(0o755)
        commit_file(self.path, "c.txt", "c", "ours")
        self.open()
        self.session.push()
        wait_network(self.session)
        [error] = self.rec.values("error_occurred")
        self.assertNotIn("Pull first", error)
        self.assertIn("protected branch", error)

    def test_no_push_on_a_detached_head_before_a_commit_or_without_a_remote(self):
        self.open()
        git(self.path, "switch", "-q", "--detach")
        self.session.refresh()
        wait_network(self.session)
        self.assertIsNone(self.session.push())
        self.assertIsNone(self.session.push("origin"))
        git(self.path, "switch", "-q", "--orphan", "fresh")
        self.session.refresh()
        wait_network(self.session)
        self.assertIsNone(self.session.push("origin"))  # no commits: not in branches
        git(self.path, "switch", "-q", "main")
        git(self.path, "remote", "remove", "origin")
        self.session.refresh()
        wait_network(self.session)
        self.assertIsNone(self.session.push())

    def test_a_cancelled_push_reports_nothing(self):
        commit_file(self.path, "c.txt", "c", "ours")
        self.open()
        before = self.origin()
        git(self.path, "remote", "set-url", "origin", "ssh://example.invalid/x.git")
        script = self.tmp / "fake-ssh"
        script.write_text("#!/bin/sh\nexec sleep 30\n")
        script.chmod(0o755)
        with mock.patch.dict("os.environ", {"GIT_SSH_COMMAND": str(script)}):
            self.assertIsNotNone(self.session.push())
            self.session.cancel_network()
            wait_network(self.session)
        self.assertEqual(self.origin(), before)
        self.assertEqual(self.rec.values("error_occurred"), [])
        self.assertEqual(self.finished, [])

    def test_the_push_default_loads_with_the_remotes(self):
        self.open()
        self.assertIsNone(self.session.push_default)
        git(self.path, "config", "remote.pushDefault", "origin")
        self.session.refresh()
        wait_network(self.session)
        self.assertEqual(self.session.push_default, "origin")
        self.assertEqual(self.session.remotes, ("origin",))
        self.assertEqual(self.rec.values("remotes_changed"), [("origin",)])  # the names


class PushActionTests(unittest.TestCase):
    """The Push action: its text, its state, and cancelling (spec §8)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.session = RepoSession()
        self.addCleanup(self.session.shutdown)
        self.actions = AppActions(self.session, Selection(self.session))
        self.session_type = type(self.session)

    def show(self, branches, remotes=("origin",), head="main", network=None):
        with mock.patch.object(self.session_type, "branches", branches), \
                mock.patch.object(self.session_type, "remotes", remotes), \
                mock.patch.object(self.session_type, "head", head), \
                mock.patch.object(self.session_type, "repo", object()), \
                mock.patch.object(self.session_type, "network", network):
            self.session.network_changed.emit(network)
        push = self.actions.push
        return push.text(), push.toolTip(), push.isEnabled()

    def test_push_shows_how_far_ahead(self):
        self.assertFalse(self.actions.push.isEnabled())  # no repository
        ahead = Branch("main", True, upstream="origin/main", ahead=2, behind=0, remote="origin")
        self.assertEqual(self.show((ahead,)), ("Pus&h ↑2", "Push main to origin/main", True))
        even = Branch("main", True, upstream="origin/main", ahead=0, behind=1, remote="origin")
        self.assertEqual(self.show((even,))[0], "Pus&h")

    def test_without_an_upstream_it_pushes_to_track_it(self):
        for branch in (Branch("main", True),
                       Branch("main", True, upstream="origin/main", remote="origin"),  # gone
                       Branch("main", True, upstream="dev", ahead=4, behind=0, remote=".")):
            self.assertEqual(self.show((branch,)),
                             ("Pus&h", "Push main to a remote and track it", True), branch)

    def test_disabled_without_a_remote_a_branch_or_commits(self):
        branch = Branch("main", True)
        self.assertFalse(self.show((branch,), remotes=())[2])
        self.assertFalse(self.show((branch,), head=None)[2])  # detached
        self.assertFalse(self.show((), head="main")[2])  # before the first commit

    def test_pushing_reads_pushing_and_cancels_and_fetch_and_pull_wait(self):
        branch = Branch("main", True, upstream="origin/main", ahead=1, behind=1, remote="origin")
        requested = []
        self.actions.push_requested.connect(lambda: requested.append(True))
        pushing = NetworkCommand(NetworkKind.PUSH, remote="origin", branch="main")
        fetching = NetworkCommand(NetworkKind.FETCH)
        with mock.patch.object(self.session_type, "branches", (branch,)), \
                mock.patch.object(self.session_type, "remotes", ("origin",)), \
                mock.patch.object(self.session_type, "head", "main"), \
                mock.patch.object(self.session_type, "repo", object()), \
                mock.patch.object(self.session, "cancel_network") as cancel:
            push, pull, fetch = self.actions.push, self.actions.pull, self.actions.fetch
            with mock.patch.object(self.session_type, "network", pushing):
                self.session.network_changed.emit(pushing)
                self.assertEqual((push.text(), push.toolTip(), push.isEnabled()),
                                 ("Pushing…", "Pushing… Click to cancel", True))
                self.assertFalse(pull.isEnabled() or fetch.isEnabled())
                push.trigger()
                cancel.assert_called_once_with()
            with mock.patch.object(self.session_type, "network", fetching):
                self.session.network_changed.emit(fetching)
                self.assertEqual((push.text(), push.isEnabled()), ("Pus&h ↑1", False))
            with mock.patch.object(self.session_type, "network", None):
                self.session.network_changed.emit(None)
                self.assertTrue(push.isEnabled())
                push.trigger()
            self.assertEqual(requested, [True])
            cancel.assert_called_once_with()


class PushDialogsTests(unittest.TestCase):
    """What Push asks, and what it then runs (spec §8)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.real_session = RepoSession()
        self.addCleanup(self.real_session.shutdown)
        self.actions = AppActions(self.real_session, Selection(self.real_session))
        self.session = mock.Mock(spec=RepoSession)
        self.session.head = "main"
        self.session.remotes = ("origin",)
        self.session.push_default = None
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.dialogs = PushDialogs(self.window, self.session, self.actions)
        self.asked = []  # (branch, remote, remotes, info)

    def run_push(self, branch, answer=None):
        self.session.branches = (branch,)

        def fake_ask(name, remote, remotes, info):
            self.asked.append((name, remote, remotes, info))
            return answer

        with mock.patch.object(self.dialogs, "ask_track", side_effect=fake_ask):
            self.actions.push_requested.emit()

    def test_with_an_upstream_it_pushes_at_once(self):
        self.run_push(Branch("main", True, upstream="origin/main", ahead=0, behind=0,
                             remote="origin"))
        self.assertEqual(self.asked, [])
        self.session.push.assert_called_once_with()

    def test_without_one_it_asks_then_pushes_to_track(self):
        self.run_push(Branch("main", True), answer="origin")
        [(name, remote, remotes, info)] = self.asked
        self.assertEqual((name, remote, remotes), ("main", "origin", ("origin",)))
        self.assertIn("no upstream", info)
        self.session.push.assert_called_once_with("origin")

    def test_cancel_pushes_nothing(self):
        self.run_push(Branch("main", True), answer=None)
        self.session.push.assert_not_called()

    def test_a_local_or_gone_upstream_is_named_in_the_box(self):
        self.run_push(Branch("main", True, upstream="dev", ahead=1, behind=0, remote="."))
        self.run_push(Branch("main", True, upstream="origin/main", remote="origin"))
        self.assertIn("tracks the local branch dev", self.asked[0][3])
        self.assertIn("origin/main is gone", self.asked[1][3])
        self.session.push.assert_not_called()

    def test_the_remote_offered(self):
        self.assertEqual(default_remote(("fork",), "origin"), "fork")  # the only one
        self.assertEqual(default_remote(("origin", "fork"), "fork"), "fork")
        self.assertEqual(default_remote(("fork", "origin"), None), "origin")
        self.assertEqual(default_remote(("fork", "origin"), "gone"), "origin")
        self.assertIsNone(default_remote(("fork", "mine"), None))  # the box lists them
        self.session.remotes = ("fork", "mine")
        self.run_push(Branch("main", True), answer="mine")
        self.assertEqual(self.asked[0][1:3], (None, ("fork", "mine")))
        self.session.push.assert_called_once_with("mine")

    def test_detached_asks_nothing(self):
        self.session.head = None
        self.run_push(Branch("main", True))
        self.assertEqual(self.asked, [])
        self.session.push.assert_not_called()


if __name__ == "__main__":
    unittest.main()
