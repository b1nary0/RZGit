import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rzgitclient.core.repo_session import Refresh, RepoSession
from rzgitclient.git.repo import GitError, Repo
from tests.support import SignalRecorder, commit_file, git, make_repo, qt_app

DATA_SIGNALS = ("head_changed", "branches_changed", "history_changed", "status_changed")
ALL_SIGNALS = ("repo_changed", *DATA_SIGNALS, "error_occurred")


class RepoSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.path = make_repo(self.tmp / "repo")
        commit_file(self.path, "a.txt", "1", "first")
        git(self.path, "branch", "feature")
        (self.path / "a.txt").write_text("changed")
        self.session = RepoSession()
        self.rec = SignalRecorder(self.session, *ALL_SIGNALS)

    def tearDown(self):
        self._tmp.cleanup()

    def open_and_clear(self, path=None):
        self.assertTrue(self.session.open(path or self.path))
        self.rec.clear()

    # ---- open -----------------------------------------------------------

    def test_open_emits_everything_in_order(self):
        self.assertTrue(self.session.open(self.path))
        self.assertEqual(self.rec.names(), ["repo_changed", *DATA_SIGNALS])

        s = self.session
        self.assertEqual(s.repo.root.resolve(), self.path.resolve())
        self.assertEqual(s.head, "main")
        self.assertEqual({b.name for b in s.branches}, {"main", "feature"})
        self.assertEqual([c.subject for c in s.history], ["first"])
        self.assertEqual([f.path for f in s.status], ["a.txt"])
        # Values are tuples, and the signal carried the same object.
        for value in (s.branches, s.history, s.status):
            self.assertIsInstance(value, tuple)
        self.assertIs(self.rec.values("history_changed")[0], s.history)

    def test_failed_open_keeps_current_repo(self):
        self.open_and_clear()
        repo = self.session.repo
        self.assertFalse(self.session.open(self.tmp))  # not a repository
        self.assertEqual(self.rec.names(), ["error_occurred"])
        self.assertIs(self.session.repo, repo)

    def test_switching_to_equal_data_still_emits(self):
        empty_a = make_repo(self.tmp / "empty_a")
        empty_b = make_repo(self.tmp / "empty_b")
        self.open_and_clear(empty_a)
        # Both repos have identical data (main, nothing else); the second
        # one's must still reach the panels.
        self.assertTrue(self.session.open(empty_b))
        self.assertEqual(self.rec.names(), ["repo_changed", *DATA_SIGNALS])

    def test_failed_load_during_open_leaves_parts_empty(self):
        self.open_and_clear()
        other = make_repo(self.tmp / "other")
        commit_file(other, "b.txt", "1", "other first")
        with mock.patch.object(Repo, "log", side_effect=GitError("boom")):
            self.assertTrue(self.session.open(other))
        # Head and branches loaded; history failed, so loading stopped. The
        # parts never loaded must not keep the previous repo's data.
        self.assertEqual(
            self.rec.names(),
            ["repo_changed", "head_changed", "branches_changed", "error_occurred",
             "history_changed", "status_changed"],
        )
        self.assertEqual(self.session.history, ())
        self.assertEqual(self.session.status, ())

    # ---- close ----------------------------------------------------------

    def test_close_empties_everything(self):
        self.open_and_clear()
        self.session.close()
        self.assertEqual(self.rec.names(), ["repo_changed", *DATA_SIGNALS])
        self.assertIsNone(self.rec.values("repo_changed")[0])
        s = self.session
        self.assertIsNone(s.repo)
        self.assertIsNone(s.head)
        self.assertEqual((s.branches, s.history, s.status), ((), (), ()))

    def test_close_without_repo_does_nothing(self):
        self.session.close()
        self.assertEqual(self.rec.events, [])

    # ---- refresh --------------------------------------------------------

    def test_unchanged_refresh_emits_nothing(self):
        self.open_and_clear()
        self.session.refresh()
        self.assertEqual(self.rec.events, [])

    def test_refresh_only_the_parts_asked_for(self):
        self.open_and_clear()
        (self.path / "new.txt").write_text("x")
        git(self.path, "checkout", "-q", "feature")
        self.session.refresh(Refresh.STATUS)
        self.assertEqual(self.rec.names(), ["status_changed"])
        self.assertEqual(self.session.head, "main")  # HEAD not reloaded

    def test_refresh_emits_only_what_changed(self):
        self.open_and_clear()
        commit_file(self.path, "a.txt", "2", "second")
        self.session.refresh()
        # A new commit changes history and status; HEAD and the branch list
        # (names and which is current) stay the same.
        self.assertEqual(self.rec.names(), ["history_changed", "status_changed"])

    def test_refresh_without_repo_does_nothing(self):
        self.session.refresh()
        self.assertEqual(self.rec.events, [])

    def test_refresh_error_reports_once_and_keeps_values(self):
        self.open_and_clear()
        before = (self.session.head, self.session.branches, self.session.history)
        shutil.rmtree(self.path)
        self.session.refresh()
        self.assertEqual(self.rec.names(), ["error_occurred"])
        self.assertEqual(
            (self.session.head, self.session.branches, self.session.history), before
        )


if __name__ == "__main__":
    unittest.main()
