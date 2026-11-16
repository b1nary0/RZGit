import shutil
import tempfile
import unittest
from pathlib import Path
import threading
from unittest import mock

from rzgitclient.core.repo_session import Refresh, RepoSession
from rzgitclient.git.repo import GitError, Repo
from tests.support import SignalRecorder, commit_file, git, make_repo, qt_app, wait_idle

DATA_SIGNALS = ("head_changed", "branches_changed", "history_changed", "status_changed")
ALL_SIGNALS = ("repo_changed", *DATA_SIGNALS, "error_occurred")
BUSY = "busy_changed"


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
        wait_idle(self.session)
        self.rec.clear()

    # ---- open -----------------------------------------------------------

    def test_open_emits_everything_in_order(self):
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)
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
        wait_idle(self.session)
        self.assertEqual(self.rec.names(), ["error_occurred"])
        self.assertIs(self.session.repo, repo)

    def test_switching_to_equal_data_still_emits(self):
        empty_a = make_repo(self.tmp / "empty_a")
        empty_b = make_repo(self.tmp / "empty_b")
        self.open_and_clear(empty_a)
        # Both repos have identical data (main, nothing else); the second
        # one's must still reach the panels.
        self.assertTrue(self.session.open(empty_b))
        wait_idle(self.session)
        self.assertEqual(self.rec.names(), ["repo_changed", *DATA_SIGNALS])

    def test_failed_load_during_open_leaves_parts_empty(self):
        self.open_and_clear()
        other = make_repo(self.tmp / "other")
        commit_file(other, "b.txt", "1", "other first")
        with mock.patch.object(Repo, "log", side_effect=GitError("boom")):
            self.assertTrue(self.session.open(other))
            wait_idle(self.session)
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
        wait_idle(self.session)
        self.assertEqual(self.rec.events, [])

    def test_refresh_only_the_parts_asked_for(self):
        self.open_and_clear()
        (self.path / "new.txt").write_text("x")
        git(self.path, "checkout", "-q", "feature")
        self.session.refresh(Refresh.STATUS)
        wait_idle(self.session)
        self.assertEqual(self.rec.names(), ["status_changed"])
        self.assertEqual(self.session.head, "main")  # HEAD not reloaded

    def test_refresh_emits_only_what_changed(self):
        self.open_and_clear()
        commit_file(self.path, "a.txt", "2", "second")
        self.session.refresh()
        wait_idle(self.session)
        # A new commit changes history and status; HEAD and the branch list
        # (names and which is current) stay the same.
        self.assertEqual(self.rec.names(), ["history_changed", "status_changed"])

    def test_refresh_without_repo_does_nothing(self):
        self.session.refresh()
        wait_idle(self.session)
        self.assertEqual(self.rec.events, [])

    def test_refresh_error_reports_once_and_keeps_values(self):
        self.open_and_clear()
        before = (self.session.head, self.session.branches, self.session.history)
        shutil.rmtree(self.path)
        self.session.refresh()
        wait_idle(self.session)
        self.assertEqual(self.rec.names(), ["error_occurred"])
        self.assertEqual(
            (self.session.head, self.session.branches, self.session.history), before
        )


    # ---- background loading ----------------------------------------------

    def test_open_returns_before_the_data_arrives(self):
        self.assertTrue(self.session.open(self.path))
        self.assertEqual(self.rec.names(), ["repo_changed"])  # data not loaded yet
        self.assertEqual(self.session.history, ())
        self.assertTrue(self.session.busy)
        wait_idle(self.session)
        self.assertEqual([c.subject for c in self.session.history], ["first"])

    def test_git_runs_off_the_main_thread_and_signals_arrive_on_it(self):
        threads = []
        real_log = Repo.log

        def log(repo, *args, **kwargs):
            threads.append(threading.current_thread())
            return real_log(repo, *args, **kwargs)

        emitted_on = []
        self.session.history_changed.connect(lambda _: emitted_on.append(threading.current_thread()))
        with mock.patch.object(Repo, "log", log):
            self.session.open(self.path)
            wait_idle(self.session)
        self.assertNotEqual(threads, [threading.main_thread()])
        self.assertEqual(emitted_on, [threading.main_thread()])

    def test_refreshes_requested_while_busy_merge_into_one_job(self):
        self.open_and_clear()
        calls = []
        real_status = Repo.status
        with mock.patch.object(Repo, "status", lambda r: calls.append(1) or real_status(r)):
            self.session.refresh(Refresh.STATUS)  # starts a job
            self.session.refresh(Refresh.HISTORY)  # these two wait,
            self.session.refresh(Refresh.STATUS)  # merged into one job
            wait_idle(self.session)
        self.assertEqual(len(calls), 2)

    def test_busy_changes_once_per_busy_period(self):
        rec = SignalRecorder(self.session, BUSY)
        self.session.open(self.path)
        self.session.refresh()
        wait_idle(self.session)
        self.assertEqual(rec.events, [(BUSY, True), (BUSY, False)])

    def test_switching_repos_mid_load_drops_the_old_results(self):
        other = make_repo(self.tmp / "other")
        commit_file(other, "b.txt", "1", "other first")
        self.session.open(self.path)
        self.session.open(other)  # before the first repo's data arrived
        wait_idle(self.session)
        histories = self.rec.values("history_changed")
        self.assertEqual([[c.subject for c in h] for h in histories], [["other first"]])
        self.assertEqual(self.rec.names().count("repo_changed"), 2)

    def test_close_mid_load_drops_the_results(self):
        self.session.open(self.path)
        self.session.close()
        wait_idle(self.session)
        self.assertEqual(self.rec.names(), ["repo_changed", "repo_changed", *DATA_SIGNALS])
        self.assertEqual(self.session.history, ())

    def test_unexpected_exception_is_reported_and_not_left_busy(self):
        self.open_and_clear()
        with mock.patch.object(Repo, "status", side_effect=RuntimeError("bug")):
            with self.assertLogs("rzgitclient.core.repo_session", "ERROR"):
                self.session.refresh(Refresh.STATUS)
                wait_idle(self.session)
        self.assertEqual(self.rec.names(), ["error_occurred"])
        self.assertIn("bug", self.rec.values("error_occurred")[0])
        self.assertFalse(self.session.busy)

    def test_shutdown_drops_queued_work_and_waits(self):
        self.open_and_clear()
        self.session.refresh()
        self.session.shutdown()
        wait_idle(self.session, timeout=1)  # nothing left running
        self.assertEqual(self.rec.events, [])  # the result was dropped



class HistoryPagingTests(unittest.TestCase):
    """History in pages of two commits (spec §4.1)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = make_repo(Path(self._tmp.name) / "repo")
        for n in range(1, 6):
            commit_file(self.path, "a.txt", str(n), f"c{n}")
        page = mock.patch("rzgitclient.core.repo_session.HISTORY_PAGE", 2)
        page.start()
        self.addCleanup(page.stop)
        self.session = RepoSession()
        self.addCleanup(self.session.shutdown)
        self.rec = SignalRecorder(self.session, "history_changed", "error_occurred")

    def subjects(self):
        return [c.subject for c in self.session.history]

    def open(self):
        self.session.open(self.path)
        wait_idle(self.session)

    def more(self):
        self.session.load_more_history()
        wait_idle(self.session)

    def test_complete_with_no_repo_or_no_commits(self):
        self.assertTrue(self.session.history_complete)
        self.session.load_more_history()  # nothing to load from
        self.assertFalse(self.session.busy)
        self.session.open(make_repo(Path(self._tmp.name) / "empty"))
        wait_idle(self.session)
        self.assertTrue(self.session.history_complete)

    def test_pages_append_until_complete(self):
        self.open()
        self.assertEqual(self.subjects(), ["c5", "c4"])
        self.assertFalse(self.session.history_complete)
        first = self.session.history

        self.more()
        self.assertEqual(self.subjects(), ["c5", "c4", "c3", "c2"])
        # The loaded part is the very same objects, so a listener can tell it's an append.
        self.assertTrue(all(a is b for a, b in zip(first, self.session.history)))
        self.more()
        self.assertEqual(self.subjects(), ["c5", "c4", "c3", "c2", "c1"])
        self.assertTrue(self.session.history_complete)

        self.rec.clear()
        self.session.load_more_history()  # complete: does nothing
        self.assertFalse(self.session.busy)
        self.assertEqual(self.rec.events, [])

    def test_a_full_last_page_is_known_to_be_the_last(self):
        commit_file(self.path, "a.txt", "6", "c6")  # six commits: exactly three pages
        self.open()
        self.more()
        self.more()
        self.assertEqual(len(self.session.history), 6)
        self.assertTrue(self.session.history_complete)  # no empty fourth page needed

    def test_a_page_already_on_its_way_is_not_asked_for_twice(self):
        self.open()
        self.session.load_more_history()  # starts a job
        self.session.load_more_history()  # loading already
        self.session.refresh(Refresh.STATUS)
        self.session.load_more_history()  # still the same page
        wait_idle(self.session)
        self.assertEqual(len(self.session.history), 4)

    def test_refresh_keeps_the_loaded_pages(self):
        self.open()
        self.more()
        commit_file(self.path, "a.txt", "6", "c6")
        self.session.refresh()
        wait_idle(self.session)
        # Everything loaded before is still there, plus the new commit.
        self.assertEqual(self.subjects(), ["c6", "c5", "c4", "c3", "c2"])
        self.assertFalse(self.session.history_complete)

    def test_refresh_with_head_unchanged_runs_no_log(self):
        self.open()
        self.more()
        with mock.patch.object(Repo, "log", side_effect=AssertionError("no log")):
            self.session.refresh()
            wait_idle(self.session)
        self.assertEqual(len(self.session.history), 4)
        self.assertEqual(self.rec.names(), ["history_changed", "history_changed"])

    def test_refresh_of_complete_history_stays_complete(self):
        self.open()
        self.more()
        self.more()
        commit_file(self.path, "a.txt", "6", "c6")
        self.session.refresh()
        wait_idle(self.session)
        self.assertEqual(self.subjects(), ["c6", "c5", "c4", "c3", "c2", "c1"])
        self.assertTrue(self.session.history_complete)

    def test_a_listener_asking_for_more_while_handling_a_result(self):
        # The view does this: laying out new rows can call fetchMore at once.
        busy = SignalRecorder(self.session, BUSY)
        asked = []

        def on_history(history):
            if len(history) < 4:
                asked.append(len(history))
                self.session.load_more_history()
                self.assertTrue(self.session.busy)

        self.session.history_changed.connect(on_history)
        self.open()
        self.assertEqual(asked, [2])
        self.assertEqual(len(self.session.history), 4)  # the page asked for mid-signal loaded
        self.assertEqual(busy.events, [(BUSY, True), (BUSY, False)])  # one busy period

    def test_a_page_asked_for_with_a_refresh_joins_it(self):
        self.open()
        self.session.refresh(Refresh.STATUS)  # running, so the next two wait together
        self.session.refresh(Refresh.HISTORY)
        self.session.load_more_history()
        wait_idle(self.session)
        self.assertEqual(self.subjects(), ["c5", "c4", "c3", "c2"])

    def test_head_moved_outside_the_app_reloads_instead_of_skipping(self):
        self.open()
        commit_file(self.path, "a.txt", "6", "c6")  # e.g. in a terminal
        self.more()
        # --skip=2 from the new HEAD would repeat c5; a reload gets it right:
        # what was loaded, the new commit, and the page asked for.
        self.assertEqual(self.subjects(), ["c6", "c5", "c4", "c3", "c2"])
        self.assertEqual(self.rec.names(), ["history_changed", "history_changed"])

    def test_switching_repos_starts_from_the_first_page(self):
        self.open()
        self.more()
        other = make_repo(Path(self._tmp.name) / "other")
        for n in range(1, 6):
            commit_file(other, "b.txt", str(n), f"o{n}")
        self.session.open(other)
        self.assertTrue(self.session.history_complete)  # until the new repo loads
        wait_idle(self.session)
        self.assertEqual(self.subjects(), ["o5", "o4"])
        self.assertFalse(self.session.history_complete)

    def test_close_marks_history_complete(self):
        self.open()
        self.session.close()
        self.assertTrue(self.session.history_complete)
        self.assertEqual(self.session.history, ())


if __name__ == "__main__":
    unittest.main()
