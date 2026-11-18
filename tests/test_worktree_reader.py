import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QThread

from rzgit.core import worktree_reader
from rzgit.core.repo_session import RepoSession
from rzgit.core.inotify_watcher import IN_CLOSE_WRITE, IN_CREATE, InotifyEvent
from rzgit.core.worktree_reader import ALL, WorktreeReader
from rzgit.git.git_models import WorktreeState
from rzgit.git.repo import canonical_path
from tests.support import commit_file, git, make_repo, pump, qt_app, wait_idle


def wait_until(condition, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"not true after {timeout}s")
        QCoreApplication.processEvents()
        QThread.msleep(2)


class WorktreeReaderTests(unittest.TestCase):
    """The Worktrees window's reader (RZG-48, spec §4.4)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = canonical_path(self._tmp.name)
        self.main = make_repo(self.tmp / "main")
        commit_file(self.main, "a.txt", "1\n", "first")
        self.linked = self.add("linked", "-b", "linked")
        self.reader = self.make_reader()

    def make_reader(self, attach=True, **kwargs):
        """A reader on a session showing ``self.main``; attached and read, unless not."""
        self.session = RepoSession()
        self.addCleanup(self.session.shutdown)
        self.session.open(self.main)
        wait_idle(self.session)
        reader = WorktreeReader(self.session, delay_ms=kwargs.pop("delay_ms", 20), **kwargs)
        self.addCleanup(reader.shutdown)
        if attach:
            reader.attach()
            wait_until(lambda: not reader.busy)
        self.lists, self.states, self.errors = [], [], []
        reader.worktrees_changed.connect(self.lists.append)
        reader.state_changed.connect(lambda path, state: self.states.append((path, state)))
        reader.error_occurred.connect(self.errors.append)
        return reader

    def add(self, name, *args):
        git(self.main, "worktree", "add", "-q", *args, str(self.tmp / name))
        return self.tmp / name

    def settle(self):
        wait_until(lambda: not self.reader.busy)

    def names(self):
        return [w.name for w in self.reader.worktrees]

    # ---- reading ---------------------------------------------------------------

    def test_attaching_lists_the_worktrees_then_reads_each_state(self):
        self.reader = self.make_reader(attach=False)
        self.reader.refresh()  # detached: nothing
        self.assertFalse(self.reader.busy)
        self.assertEqual(self.reader.worktrees, ())
        self.reader.attach()
        self.assertTrue(self.reader.attached)
        self.assertTrue(self.reader.busy)
        self.settle()
        self.assertEqual(self.names(), ["main", "linked"])
        self.assertEqual(len(self.lists), 1)
        self.assertEqual(self.reader.state(str(self.main)), WorktreeState(0))
        self.assertEqual(sorted(p for p, _ in self.states), sorted([str(self.main),
                                                                    str(self.linked)]))

    def test_only_what_changed_is_emitted(self):
        self.reader.refresh()
        self.settle()
        self.lists.clear()
        self.states.clear()
        (self.linked / "new.txt").write_text("n\n")
        self.reader.refresh()
        self.settle()
        self.assertEqual(self.lists, [])  # the same worktrees
        self.assertEqual(self.states, [(str(self.linked), WorktreeState(1))])

    def test_a_missing_worktree_s_state_isn_t_read(self):
        gone = self.add("gone", "-b", "gone")
        shutil.rmtree(gone)
        with mock.patch.object(worktree_reader, "worktree_state",
                               wraps=worktree_reader.worktree_state) as read:
            self.reader.refresh()
            self.settle()
        self.assertGreater(read.call_count, 0)
        self.assertEqual({c.args[0] for c in read.call_args_list},
                         {str(self.main), str(self.linked)})  # never the missing one
        self.assertTrue(next(w for w in self.reader.worktrees if w.name == "gone").missing)
        self.assertIsNone(self.reader.state(str(gone)))

    def test_refreshes_asked_for_while_one_runs_merge_into_one_more(self):
        with mock.patch.object(worktree_reader, "worktree_state",
                               wraps=worktree_reader.worktree_state) as read:
            for _ in range(3):
                self.reader.refresh()
            self.settle()
        self.assertEqual(read.call_count, 4)  # two jobs of two worktrees

    def test_a_failed_listing_empties_the_list_and_says_why(self):
        self.reader.refresh()
        self.settle()
        shutil.rmtree(self.main / ".git")
        self.reader.refresh()
        self.settle()
        self.assertEqual(self.reader.worktrees, ())
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.reader.error, self.errors[0])
        self.assertIsNone(self.reader.state(str(self.main)))

    def test_detaching_stops_and_forgets(self):
        self.reader.attach()  # a second panel
        self.reader.detach()
        self.assertTrue(self.reader.attached)
        self.assertEqual(self.names(), ["main", "linked"])
        self.reader.refresh()
        self.reader.detach()  # the last one
        self.assertFalse(self.reader.attached)
        self.assertEqual(self.reader.worktrees, ())
        self.assertEqual(self.reader.watched, set())
        self.assertIsNone(self.reader.state(str(self.main)))
        self.settle()  # the read already running finishes; its results are dropped
        self.assertEqual(self.lists, [])
        self.assertEqual(self.states, [])
        self.reader.refresh()
        self.assertFalse(self.reader.busy)  # detached: nothing

    def test_it_follows_the_session_s_repository(self):
        other = make_repo(self.tmp / "other")
        commit_file(other, "a.txt", "1\n", "first")
        self.session.open(other)
        self.assertEqual(self.lists[0], ())  # the old list goes at once
        self.settle()
        self.assertEqual(self.names(), ["other"])
        self.assertIn(str(other / ".git"), self.reader.watched)
        self.assertNotIn(str(self.main / ".git"), self.reader.watched)
        self.session.close()
        self.settle()
        self.assertEqual(self.reader.worktrees, ())
        self.assertFalse(self.reader.busy)

    def test_another_repository_while_a_read_runs(self):
        other = make_repo(self.tmp / "other")
        commit_file(other, "a.txt", "1\n", "first")
        self.reader.refresh()
        self.assertTrue(self.reader.busy)
        self.session.open(other)  # the running read's results are dropped
        self.settle()
        self.assertEqual(self.names(), ["other"])

    def test_the_same_repository_from_a_linked_worktree_is_the_same_list(self):
        before = self.reader.worktrees
        self.session.open(self.linked)
        wait_idle(self.session)
        self.settle()
        self.assertEqual(self.reader.worktrees, before)

    def test_results_after_shutdown_are_dropped(self):
        self.reader.refresh()
        self.reader.shutdown()
        pump(50)
        self.assertEqual(self.lists, [])
        self.assertFalse(self.reader.busy)

    # ---- activation --------------------------------------------------------------

    def test_becoming_active_again_reads_everything(self):
        self.reader.refresh()
        self.settle()
        self.reader.set_active(True)  # the first activation: just opened
        self.assertFalse(self.reader.busy)
        (self.main / "a.txt").write_text("edited\n")  # no git dir changes
        self.reader.set_active(False)
        self.reader.set_active(True)
        self.settle()
        self.assertEqual(self.reader.state(str(self.main)), WorktreeState(1))

    # ---- watching ------------------------------------------------------------------

    def test_watches_the_git_dirs_and_refs(self):
        self.reader.refresh()
        self.settle()
        common = str(self.main / ".git")
        watched = self.reader.watched
        for folder in (common, f"{common}/worktrees", f"{common}/worktrees/linked",
                       f"{common}/refs", f"{common}/refs/heads"):
            self.assertIn(folder, watched)
        self.assertNotIn(f"{common}/objects", watched)
        self.assertNotIn(str(self.main), watched)  # never the files

    def test_what_a_change_reads_again(self):
        self.reader.refresh()
        self.settle()
        common = str(self.main / ".git")

        def affected(folder, name):
            return self.reader.affected(InotifyEvent(folder, name, IN_CLOSE_WRITE))

        self.assertEqual(affected(f"{common}/worktrees/linked", "index"), {str(self.linked)})
        self.assertEqual(affected(f"{common}/worktrees/linked", "HEAD"), {str(self.linked)})
        self.assertEqual(affected(f"{common}/worktrees/linked", "index.lock"), set())
        self.assertEqual(affected(f"{common}/worktrees/linked", "ORIG_HEAD"), set())
        self.assertEqual(affected(common, "index"), {str(self.main)})
        self.assertEqual(affected(common, "FETCH_HEAD"), set())
        self.assertIs(affected(common, "packed-refs"), ALL)
        self.assertIs(affected(f"{common}/worktrees", "new"), ALL)
        self.assertIs(affected(f"{common}/refs/heads", "main"), ALL)
        self.assertEqual(affected(f"{common}/refs/heads", "main.lock"), set())
        self.assertEqual(affected(common, "index.lock"), set())

    @unittest.skipUnless(os.path.exists("/proc/sys/fs/inotify"), "needs inotify")
    def test_a_worktree_added_outside_the_app_shows_up(self):
        self.reader.refresh()
        self.settle()
        self.add("later", "-b", "later")
        wait_until(lambda: "later" in self.names())
        self.settle()
        self.assertEqual(self.reader.state(str(self.tmp / "later")), WorktreeState(0))

    @unittest.skipUnless(os.path.exists("/proc/sys/fs/inotify"), "needs inotify")
    def test_a_worktree_removed_outside_the_app_goes(self):
        self.reader.refresh()
        self.settle()
        git(self.main, "worktree", "remove", str(self.linked))
        wait_until(lambda: self.names() == ["main"])
        self.assertIsNone(self.reader.state(str(self.linked)))

    @unittest.skipUnless(os.path.exists("/proc/sys/fs/inotify"), "needs inotify")
    def test_staging_in_a_worktree_reads_only_its_state(self):
        self.reader.refresh()
        self.settle()
        (self.linked / "b.txt").write_text("b\n")
        with mock.patch.object(worktree_reader, "worktree_state",
                               wraps=worktree_reader.worktree_state) as read:
            git(self.linked, "add", "b.txt")
            wait_until(lambda: self.reader.state(str(self.linked)) == WorktreeState(1))
            self.settle()
        self.assertEqual({c.args[0] for c in read.call_args_list}, {str(self.linked)})

    @unittest.skipUnless(os.path.exists("/proc/sys/fs/inotify"), "needs inotify")
    def test_a_commit_in_one_worktree_reads_them_all(self):
        clone = self.tmp / "clone"
        git(self.tmp, "clone", "-q", str(self.main), str(clone))
        self.reader.shutdown()
        self.main = clone
        git(clone, "config", "user.name", "T")
        git(clone, "config", "user.email", "t@example.com")
        self.reader = self.make_reader()
        self.assertEqual(self.reader.state(str(clone)), WorktreeState(0, "origin/main", 0, 0))
        commit_file(clone, "b.txt", "b", "ahead")  # refs/heads/main moves
        wait_until(lambda: self.reader.state(str(clone)) == WorktreeState(0, "origin/main",
                                                                          1, 0))

    def test_changes_are_merged(self):
        reader = self.make_reader(delay_ms=50, max_delay_ms=1000)
        self.reader = reader
        common = str(self.main / ".git")
        with mock.patch.object(reader, "_request") as request:
            for _ in range(3):
                reader._on_events((InotifyEvent(f"{common}/worktrees/linked", "index",
                                                IN_CREATE),))
                pump(10)
            self.assertEqual(request.call_count, 0)
            wait_until(lambda: request.call_count == 1)
        request.assert_called_once_with({str(self.linked)})


if __name__ == "__main__":
    unittest.main()
