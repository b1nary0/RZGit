import sys
import tempfile
import time
import unittest
from pathlib import Path

from rzgit.core.repo_session import Refresh, RepoSession
from rzgit.core import repo_watcher
from rzgit.core.repo_watcher import REF_PARTS, RepoWatcher
from tests.support import SignalRecorder, commit_file, git, make_repo, pump, qt_app, wait_idle

SETTLE_MS = 80  # quiet time after which nothing more is on its way (delay is 30 ms)


class RepoWatcherTests(unittest.TestCase):
    """Real file changes in a temp repo (spec §4.3)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        (self.path / ".gitignore").write_text("build/\n*.o\n")
        (self.path / "src").mkdir()
        (self.path / "src" / "b.txt").write_text("b")
        (self.path / "build").mkdir()
        git(self.path, "add", ".")
        commit_file(self.path, "a.txt", "a", "first")
        self.session = RepoSession()
        self.watcher = self.make_watcher()
        self.rec = SignalRecorder(self.session, "head_changed", "branches_changed",
                                  "history_changed", "status_changed")
        self.open(self.path)

    def tearDown(self):
        self.watcher.shutdown()
        self.session.shutdown()
        self._tmp.cleanup()

    def make_watcher(self, **kwargs):
        options = {"delay_ms": 30, "max_delay_ms": 300, **kwargs}
        watcher = RepoWatcher(self.session, **options)
        if not watcher._inotify.available:
            self.skipTest("inotify unavailable")
        return watcher

    def open(self, path):
        self.assertTrue(self.session.open(path))
        self.settle()
        self.rec.clear()
        self.calls = []
        if "refresh" not in vars(self.session):
            real = self.session.refresh

            def spy(parts=Refresh.ALL):
                # Only the watcher's calls: commands call refresh() themselves.
                if sys._getframe(1).f_code.co_filename == repo_watcher.__file__:
                    self.calls.append(parts)
                real(parts)

            self.session.refresh = spy

    def settle(self, timeout=10.0):
        """Wait until the watcher and the session have been idle a while."""
        deadline = time.monotonic() + timeout
        quiet_since = None
        while True:
            pump(5)
            if self.watcher.busy or self.session.busy:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since > SETTLE_MS / 1000:
                return
            if time.monotonic() > deadline:
                raise AssertionError("watcher still busy")

    def status_paths(self):
        return sorted(f.path for f in self.session.status)

    # ---- the working tree ------------------------------------------------------

    def test_watches_the_folders_git_doesnt_ignore(self):
        self.assertEqual(self.watcher.folder_count, 2)  # the root and src
        self.assertFalse(self.watcher.polling)

    def test_a_file_changed_in_place(self):
        with open(self.path / "a.txt", "a") as f:
            f.write("more")
        self.settle()
        self.assertEqual(self.calls, [Refresh.STATUS])
        self.assertEqual(self.status_paths(), ["a.txt"])

    def test_new_and_deleted_files(self):
        (self.path / "src" / "new.txt").write_text("n")
        self.settle()
        self.assertEqual(self.status_paths(), ["src/new.txt"])
        (self.path / "src" / "b.txt").unlink()
        self.settle()
        self.assertEqual(self.status_paths(), ["src/b.txt", "src/new.txt"])
        self.assertEqual(self.calls, [Refresh.STATUS, Refresh.STATUS])

    def test_ignored_files_cause_no_refresh(self):
        (self.path / "x.o").write_text("o")
        (self.path / "build" / "out").write_text("o")
        (self.path / "src" / "y.o").write_text("o")
        self.settle()
        self.assertEqual(self.calls, [])

    def test_a_burst_is_one_refresh(self):
        for i in range(300):
            (self.path / "src" / f"f{i}.txt").write_text(str(i))
        for i in range(20):
            with open(self.path / "a.txt", "a") as f:
                f.write(str(i))
        self.settle()
        self.assertEqual(self.calls, [Refresh.STATUS])
        self.assertEqual(len(self.session.status), 301)

    def test_a_long_burst_refreshes_at_most_every_max_delay(self):
        end = time.monotonic() + 0.9
        while time.monotonic() < end:
            with open(self.path / "a.txt", "a") as f:
                f.write("x")
            pump(10)
        self.settle()
        self.assertIn(len(self.calls), (2, 3, 4))

    def test_new_folders_are_watched(self):
        (self.path / "n" / "m").mkdir(parents=True)
        self.settle()
        self.assertEqual(self.watcher.folder_count, 4)
        (self.path / "n" / "m" / "f.txt").write_text("f")
        self.settle()
        self.assertEqual(self.status_paths(), ["n/m/f.txt"])

    def test_new_ignored_folders_arent(self):
        (self.path / "src" / "build" / "deep").mkdir(parents=True)
        self.settle()
        self.assertEqual(self.watcher.folder_count, 2)
        self.assertEqual(self.calls, [])

    def test_a_deleted_folder(self):
        (self.path / "src" / "b.txt").unlink()
        (self.path / "src").rmdir()
        self.settle()
        self.assertEqual(self.watcher.folder_count, 1)
        self.assertEqual(self.status_paths(), ["src/b.txt"])

    def test_a_changed_gitignore_rebuilds_the_watch_set(self):
        (self.path / "tmp").mkdir()
        self.settle()
        self.assertEqual(self.watcher.folder_count, 3)
        with open(self.path / ".gitignore", "a") as f:
            f.write("tmp/\n")
        self.settle()
        self.assertEqual(self.watcher.folder_count, 2)

    # ---- the git dir -------------------------------------------------------------

    def test_a_commit_outside_the_app(self):
        commit_file(self.path, "a.txt", "2", "second")
        self.settle()
        self.assertEqual([c.subject for c in self.session.history], ["second", "first"])
        self.assertEqual(self.session.commit_count, 2)
        self.assertTrue(all(part in REF_PARTS | Refresh.STATUS for part in self.calls))
        self.assertLessEqual(len(self.calls), 2)

    def test_a_remote_or_upstream_set_outside_the_app(self):
        git(self.path, "remote", "add", "origin", "https://example.invalid/x.git")
        self.settle()
        self.assertEqual(self.calls, [Refresh.BRANCHES | Refresh.REMOTES])
        self.assertEqual(self.session.remotes, ("origin",))

    def test_stashes_made_and_dropped_outside_the_app(self):
        """RZG-36: refs/stash changes, dropping an older stash too."""
        (self.path / "a.txt").write_text("older")
        git(self.path, "stash", "push", "-q", "-m", "older")
        (self.path / "a.txt").write_text("newer")
        git(self.path, "stash", "push", "-q", "-m", "newer")
        self.settle()
        self.assertEqual([s.message for s in self.session.stashes], ["newer", "older"])
        git(self.path, "stash", "drop", "-q", "stash@{1}")
        self.settle()
        self.assertEqual([s.message for s in self.session.stashes], ["newer"])

    def test_branches_and_checkout_outside_the_app(self):
        git(self.path, "branch", "feature/x")
        self.settle()
        self.assertEqual({b.name for b in self.session.branches}, {"main", "feature/x"})
        git(self.path, "checkout", "-q", "feature/x")
        self.settle()
        self.assertEqual(self.session.head, "feature/x")
        git(self.path, "branch", "feature/y")  # in the refs folder made meanwhile
        self.settle()
        self.assertIn("feature/y", {b.name for b in self.session.branches})

    def test_a_linked_worktree(self):
        other = Path(self._tmp.name) / "linked"
        git(self.path, "worktree", "add", "-q", "-b", "side", str(other))
        self.open(other)
        commit_file(other, "a.txt", "side", "on side")
        self.settle()
        self.assertEqual(self.session.history[0].subject, "on side")

    # ---- the app's own work ------------------------------------------------------

    def test_the_apps_own_commands_dont_refresh_again(self):
        (self.path / "a.txt").write_text("changed")
        (self.path / "new.txt").write_text("n")
        self.settle()
        self.calls.clear()
        self.session.stage(["a.txt"])
        self.settle()
        self.session.unstage(["a.txt"])
        self.settle()
        self.session.stage_all()
        self.settle()
        self.session.commit("second")
        self.settle()
        self.assertEqual(self.session.history[0].subject, "second")
        self.assertEqual(self.calls, [])

    def test_a_discard_doesnt_refresh_again(self):
        (self.path / "a.txt").write_text("changed")
        self.settle()
        self.calls.clear()
        self.session.discard(["a.txt"])
        self.settle()
        self.assertEqual(self.session.status, ())
        self.assertEqual(self.calls, [])

    def test_a_refresh_causes_no_refresh(self):
        (self.path / "a.txt").write_text("a")  # same content: the index's stat info is stale
        self.settle()
        self.calls.clear()
        self.session.refresh()
        self.settle()
        self.assertEqual(self.calls, [])

    def test_an_edit_during_a_command_still_counts(self):
        (self.path / "a.txt").write_text("changed")
        self.settle()
        self.session.stage(["a.txt"])
        (self.path / "src" / "b.txt").write_text("edited")  # while the stage is in flight
        self.settle()
        self.assertIn("src/b.txt", self.status_paths())

    # ---- focus, the cap, closing ---------------------------------------------------

    def test_becoming_active_again_refreshes_everything(self):
        self.watcher.set_active(True)  # the first time: open() just loaded everything
        self.assertEqual(self.calls, [])
        self.watcher.set_active(False)
        self.watcher.set_active(True)
        self.assertEqual(self.calls, [Refresh.ALL])
        self.settle()

    def test_over_the_cap_the_status_is_polled_while_active(self):
        self.watcher.shutdown()
        self.watcher = self.make_watcher(max_folders=1, poll_ms=40)
        self.watcher._on_repo_changed(self.session.repo)
        self.settle()
        self.assertTrue(self.watcher.polling)
        self.assertEqual(self.watcher.folder_count, 0)
        pump(150)
        self.assertEqual(self.calls, [])  # not active yet
        self.watcher.set_active(True)
        (self.path / "a.txt").write_text("changed")
        pump(150)
        self.watcher.set_active(False)
        self.settle()
        self.assertIn(Refresh.STATUS, self.calls)
        self.assertEqual(self.status_paths(), ["a.txt"])

    def test_closing_stops_watching(self):
        self.session.close()
        self.assertEqual(self.watcher.folder_count, 0)
        (self.path / "a.txt").write_text("changed")
        self.settle()
        self.assertEqual(self.calls, [])
