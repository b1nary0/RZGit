import shutil
import tempfile
import unittest
from pathlib import Path
import threading
from unittest import mock

from rzgit.core import git_commands
from rzgit.core.diff_patch import hunk_lines
from rzgit.core.git_commands import CommandKind
from rzgit.core.repo_session import Refresh, RepoSession
from rzgit.core.selection import FileSelection, FileSource
from rzgit.git.git_models import DiffSource, FileStatus, HistoryOrder, Ref
from rzgit.git.repo import GitError, Repo
from tests.support import (SignalRecorder, commit_file, git, make_branched_repo, make_repo, qt_app,
                           wait_idle)

DATA_SIGNALS = ("head_changed", "head_sha_changed", "branches_changed", "refs_changed",
                "history_changed", "status_changed",
                "stashes_changed", "commit_count_changed")
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

    def test_a_quiet_open_does_not_report(self):
        self.open_and_clear()
        repo = self.session.repo
        self.assertFalse(self.session.open(self.tmp / "gone", report=False))
        wait_idle(self.session)
        self.assertEqual(self.rec.names(), [])
        self.assertIs(self.session.repo, repo)

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
            ["repo_changed", "head_changed", "head_sha_changed", "branches_changed",
             "refs_changed", "error_occurred",
             "history_changed", "status_changed", "stashes_changed", "commit_count_changed"],
        )
        self.assertEqual(self.session.history, ())
        self.assertEqual(self.session.status, ())
        self.assertIsNone(self.session.commit_count)  # not the old repo's count

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

    def test_close_forgets_head_sha(self):
        self.open_and_clear()
        self.assertIsNotNone(self.session.head_sha)
        self.session.close()
        self.assertIsNone(self.session.head_sha)
        self.assertEqual(self.rec.values("head_sha_changed"), [None])

    # ---- HEAD: branch and sha (spec §4.1) -------------------------------

    def test_head_sha_is_heads_commit(self):
        self.open_and_clear()
        self.assertEqual(self.session.head_sha, git(self.path, "rev-parse", "HEAD").strip())

    def test_head_sha_is_none_before_the_first_commit(self):
        empty = make_repo(self.tmp / "empty")
        self.open_and_clear(empty)
        self.assertEqual(self.session.head, "main")
        self.assertIsNone(self.session.head_sha)

    def test_a_new_commit_fires_only_head_sha_changed_of_the_two(self):
        self.open_and_clear()
        commit_file(self.path, "b.txt", "1", "second")
        self.session.refresh(Refresh.HEAD)
        wait_idle(self.session)
        sha = git(self.path, "rev-parse", "HEAD").strip()
        self.assertEqual(self.rec.names(), ["head_sha_changed"])
        self.assertEqual(self.rec.values("head_sha_changed"), [sha])
        self.assertEqual(self.session.head_sha, sha)

    def test_switching_at_the_same_commit_fires_only_head_changed(self):
        self.open_and_clear()
        git(self.path, "switch", "-q", "feature")
        self.session.refresh(Refresh.HEAD)
        wait_idle(self.session)
        self.assertEqual(self.rec.names(), ["head_changed"])
        self.assertEqual(self.session.head, "feature")

    def test_moving_a_detached_head_fires_head_sha_changed(self):
        commit_file(self.path, "b.txt", "1", "second")
        git(self.path, "checkout", "-q", "--detach")
        self.open_and_clear()
        self.assertIsNone(self.session.head)
        git(self.path, "checkout", "-q", "--detach", "HEAD~1")
        self.session.refresh(Refresh.HEAD)
        wait_idle(self.session)
        self.assertEqual(self.rec.names(), ["head_sha_changed"])
        self.assertEqual(self.session.head_sha, git(self.path, "rev-parse", "HEAD").strip())

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
        # A new commit moves HEAD's sha and its branch's ref, and changes history,
        # status and the count; the branch and the branch list (names, which is
        # current) stay.
        self.assertEqual(self.rec.names(), ["head_sha_changed", "refs_changed", "history_changed",
                                            "status_changed", "commit_count_changed"])

    # ---- refs (RZG-77) ----------------------------------------------------

    def test_refs_are_loaded_with_their_commits(self):
        self.open_and_clear()
        head = self.session.head_sha
        self.assertEqual(self.session.refs, (Ref("refs/heads/feature", head),
                                             Ref("refs/heads/main", head)))

    def test_a_new_tag_is_read_though_head_has_not_moved(self):
        # History runs no git log while HEAD stays, so the refs are read by themselves.
        self.open_and_clear()
        git(self.path, "tag", "v1.0")
        self.session.refresh(Refresh.BRANCHES)
        wait_idle(self.session)
        self.assertEqual(self.rec.names(), ["refs_changed"])
        self.assertIn(Ref("refs/tags/v1.0", self.session.head_sha), self.session.refs)

    def test_refs_come_with_history_and_not_with_status(self):
        self.open_and_clear()
        git(self.path, "tag", "v1.0")
        self.session.refresh(Refresh.STATUS)
        wait_idle(self.session)
        self.assertEqual(self.rec.names(), [])
        self.session.refresh(Refresh.HISTORY)
        wait_idle(self.session)
        self.assertEqual(self.rec.names(), ["refs_changed"])

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
            with self.assertLogs("rzgit.core.repo_session", "ERROR"):
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



    # ---- the commit count -----------------------------------------------

    def test_count_runs_in_its_own_job_after_history_is_out(self):
        seen_when_counting = []
        real_count = Repo.commit_count

        def count(repo):
            seen_when_counting.append(list(self.rec.names()))
            return real_count(repo)

        with mock.patch.object(Repo, "commit_count", count):
            self.session.open(self.path)
            wait_idle(self.session)
        # history and status were emitted before git was asked to count
        self.assertEqual(seen_when_counting, [["repo_changed", *DATA_SIGNALS[:-1]]])
        self.assertEqual(self.session.commit_count, 1)

    def test_count_is_none_until_counted_and_after_close(self):
        self.assertIsNone(self.session.commit_count)
        self.open_and_clear()
        self.assertEqual(self.session.commit_count, 1)
        self.session.close()
        self.assertIsNone(self.session.commit_count)

    def test_count_follows_new_commits(self):
        self.open_and_clear()
        commit_file(self.path, "b.txt", "1", "second")
        self.session.refresh()
        wait_idle(self.session)
        self.assertEqual(self.session.commit_count, 2)
        self.assertEqual(self.rec.values("commit_count_changed"), [2])

    def test_count_alone_runs_in_one_job(self):
        self.open_and_clear()
        jobs = SignalRecorder(self.session, BUSY)
        self.session.refresh(Refresh.COUNT)
        wait_idle(self.session)
        self.assertEqual(jobs.events, [(BUSY, True), (BUSY, False)])

    def test_a_failed_load_reports_once_not_again_for_the_count(self):
        with mock.patch.object(Repo, "log", side_effect=GitError("log broke")):
            self.session.open(self.path)
            wait_idle(self.session)
        self.assertEqual(self.rec.values("error_occurred"), ["log broke"])
        self.assertIsNone(self.session.commit_count)


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
        page = mock.patch("rzgit.core.repo_session.HISTORY_PAGE", 2)
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


class HistoryOrderTests(unittest.TestCase):
    """History in date or topological order (RZG-58), in pages of three commits: the
    first three differ between the two."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = make_branched_repo(Path(self._tmp.name) / "repo")
        page = mock.patch("rzgit.core.repo_session.HISTORY_PAGE", 3)
        page.start()
        self.addCleanup(page.stop)
        self.session = RepoSession()
        self.addCleanup(self.session.shutdown)
        self.repo = Repo(self.path)
        self.assertNotEqual(self.expected(HistoryOrder.DATE, 3), self.expected(HistoryOrder.TOPO, 3))

    def subjects(self):
        return [c.subject for c in self.session.history]

    def expected(self, order, n):
        return [c.subject for c in self.repo.log(n, order=order)]

    def more(self):
        self.session.load_more_history()
        wait_idle(self.session)

    def test_date_order_by_default_with_parents(self):
        self.assertIs(self.session.history_order, HistoryOrder.DATE)
        self.session.open(self.path)
        wait_idle(self.session)
        self.more()
        self.assertEqual(self.subjects(), ["merge", "s2", "m1", "s1", "a"])
        self.assertEqual(len(self.session.history[0].parents), 2)

    def test_another_order_reloads_from_the_top_and_pages_in_it(self):
        self.session.open(self.path)
        wait_idle(self.session)
        self.more()  # all five, by date
        rec = SignalRecorder(self.session, "history_changed")
        self.session.set_history_order(HistoryOrder.TOPO)
        self.assertIs(self.session.history_order, HistoryOrder.TOPO)
        wait_idle(self.session)
        self.assertEqual(self.subjects(), self.expected(HistoryOrder.TOPO, 3))  # not paged on
        self.assertEqual(len(rec.events), 1)
        self.assertFalse(self.session.history_complete)
        self.more()
        self.assertEqual(self.subjects(), self.expected(HistoryOrder.TOPO, 5))
        self.session.refresh()  # HEAD hasn't moved: kept as it is
        wait_idle(self.session)
        self.assertEqual(self.subjects(), self.expected(HistoryOrder.TOPO, 5))

    def test_the_same_order_does_nothing(self):
        self.session.open(self.path)
        wait_idle(self.session)
        self.session.set_history_order(HistoryOrder.DATE)
        self.assertFalse(self.session.busy)

    def test_the_order_set_before_opening_is_the_first_loads(self):
        self.session.set_history_order(HistoryOrder.TOPO)  # no repo: nothing to load
        self.assertFalse(self.session.busy)
        self.session.open(self.path)
        wait_idle(self.session)
        self.assertEqual(self.subjects(), self.expected(HistoryOrder.TOPO, 3))


class RepoSessionDiffTests(unittest.TestCase):
    """Diffs are asked for (spec §4.1)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", "1\n", "first")
        commit_file(self.path, "b.txt", "1\n", "second")
        (self.path / "a.txt").write_text("2\n")
        self.session = RepoSession()
        self.diffs = []
        self.session.diff_changed.connect(lambda f, d: self.diffs.append((f, d)))
        self.errors = []
        self.session.error_occurred.connect(self.errors.append)
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)
        self.diffs.clear()

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    def added(self, diff):
        return [l.text for h in diff.hunks for l in h.lines if l.kind.value == "+"]

    def test_request_reads_the_diff_off_the_ui_thread(self):
        a = FileSelection("a.txt", FileSource.WORKTREE)
        self.session.request_diff(a)
        self.assertEqual(self.diffs, [])  # not yet: it runs on the worker
        self.assertTrue(self.session.busy)
        wait_idle(self.session)
        ((file, diff),) = self.diffs
        self.assertEqual(file, a)
        self.assertEqual(self.added(diff), ["2"])
        self.assertEqual((self.session.diff_file, self.session.diff), (a, diff))

    def test_a_newer_request_drops_the_older_result(self):
        a = FileSelection("a.txt", FileSource.WORKTREE)
        b = FileSelection("b.txt", FileSource.WORKTREE)
        self.session.request_diff(a)
        self.session.request_diff(b)
        wait_idle(self.session)
        self.assertEqual([f for f, _ in self.diffs], [b])

    def test_none_clears_the_target_at_once(self):
        a = FileSelection("a.txt", FileSource.WORKTREE)
        self.session.request_diff(a)
        wait_idle(self.session)
        self.session.request_diff(None)
        self.assertEqual(self.diffs[-1], (None, None))
        self.session.refresh()
        wait_idle(self.session)
        self.assertEqual(self.diffs[-1], (None, None))  # nothing read for no target

    def test_a_refresh_reads_the_diff_again_and_sends_only_changes(self):
        a = FileSelection("a.txt", FileSource.WORKTREE)
        self.session.request_diff(a)
        wait_idle(self.session)
        self.session.refresh()
        wait_idle(self.session)
        self.assertEqual(len(self.diffs), 1)  # the same diff: not sent again
        (self.path / "a.txt").write_text("3\n")  # still "modified": status doesn't change
        self.session.refresh()
        wait_idle(self.session)
        self.assertEqual(len(self.diffs), 2)
        self.assertEqual(self.added(self.diffs[-1][1]), ["3"])

    def test_a_refresh_without_status_leaves_the_diff(self):
        self.session.request_diff(FileSelection("a.txt", FileSource.WORKTREE))
        wait_idle(self.session)
        (self.path / "a.txt").write_text("3\n")
        self.session.refresh(Refresh.BRANCHES)
        wait_idle(self.session)
        self.assertEqual(len(self.diffs), 1)

    def test_a_listener_of_status_can_change_the_target_first(self):
        # The Files panel does this when the selected file disappears.
        self.session.request_diff(FileSelection("a.txt", FileSource.WORKTREE))
        wait_idle(self.session)
        self.session.status_changed.connect(lambda _s: self.session.request_diff(None))
        git(self.path, "checkout", "--", "a.txt")
        self.session.refresh()
        wait_idle(self.session)
        self.assertEqual(self.diffs[-1], (None, None))
        self.assertEqual(self.session.diff_file, None)

    def test_full_reads_a_diff_over_the_limit(self):
        a = FileSelection("a.txt", FileSource.WORKTREE)
        (self.path / "a.txt").write_text("".join(f"{n}\n" for n in range(40)))
        with mock.patch("rzgit.core.diff_request.DIFF_MAX_LINES", 10):
            self.session.request_diff(a)
            wait_idle(self.session)
            self.assertTrue(self.diffs[-1][1].too_large)
            self.session.request_diff(a, full=True)
            wait_idle(self.session)
            self.assertFalse(self.diffs[-1][1].too_large)
            self.session.refresh()  # stays full
            wait_idle(self.session)
            self.assertFalse(self.session.diff.too_large)

    def test_whole_reads_the_whole_file_and_says_so(self):
        """RZG-60: toggling whole sends a result even when the diff is the same."""
        a = FileSelection("a.txt", FileSource.WORKTREE)
        self.session.request_diff(a)
        wait_idle(self.session)
        self.assertFalse(self.session.diff_whole)
        self.session.request_diff(a, whole=True)  # a one-line file: the same diff
        wait_idle(self.session)
        self.assertEqual(len(self.diffs), 2)
        self.assertTrue(self.session.diff_whole)
        (self.path / "a.txt").write_text("".join(f"{n}\n" for n in range(30)))
        self.session.refresh()  # stays whole
        wait_idle(self.session)
        self.assertTrue(self.session.diff_whole)
        new = [l.new_no for h in self.session.diff.hunks for l in h.lines if l.new_no]
        self.assertEqual(new, list(range(1, 31)))
        self.session.request_diff(None)
        self.assertFalse(self.session.diff_whole)

    def test_context_reads_wider_and_says_so(self):
        """RZG-97: a new context count sends a result even when the diff is the same."""
        a = FileSelection("a.txt", FileSource.WORKTREE)
        self.session.request_diff(a)
        wait_idle(self.session)
        self.assertEqual(self.session.diff_context, 3)
        self.session.request_diff(a, context=10)  # a one-line file: the same diff
        wait_idle(self.session)
        self.assertEqual(len(self.diffs), 2)
        self.assertEqual(self.session.diff_context, 10)
        commit_file(self.path, "a.txt", "".join(f"{n}\n" for n in range(30)), "long")
        (self.path / "a.txt").write_text("".join(f"{n}\n" for n in range(29)))
        self.session.refresh()  # keeps the count
        wait_idle(self.session)
        self.assertEqual(self.session.diff_context, 10)
        self.assertEqual(len(self.session.diff.hunks[0].lines), 11)  # 10 above the removed line
        self.session.request_diff(a, whole=True, context=10)
        wait_idle(self.session)
        self.assertIsNone(self.session.diff_context)
        self.session.request_diff(None)
        self.assertIsNone(self.session.diff_context)

    def test_a_failed_diff_is_an_error_and_none(self):
        gone = FileSelection("gone.txt", FileSource.WORKTREE)
        (self.path / "gone.txt").write_text("x")
        self.session.refresh()
        wait_idle(self.session)
        (self.path / "gone.txt").unlink()  # status still says untracked
        self.session.request_diff(gone)
        wait_idle(self.session)
        self.assertEqual(self.diffs[-1], (gone, None))
        self.assertEqual(len(self.errors), 1)

    def test_a_part_failing_queues_the_diff_by_itself(self):
        a = FileSelection("a.txt", FileSource.WORKTREE)
        self.session.request_diff(a)
        wait_idle(self.session)
        (self.path / "a.txt").write_text("3\n")
        with mock.patch.object(Repo, "branches", side_effect=GitError("boom")):
            self.session.refresh()
            wait_idle(self.session)
        self.assertEqual(self.errors, ["boom"])
        self.assertEqual(self.added(self.session.diff), ["3"])

    def test_open_and_close_forget_the_diff(self):
        self.session.request_diff(FileSelection("a.txt", FileSource.WORKTREE))
        wait_idle(self.session)
        self.session.close()
        self.assertEqual(self.diffs[-1], (None, None))
        self.session.request_diff(FileSelection("a.txt", FileSource.WORKTREE))  # no repo
        self.assertFalse(self.session.busy)


class RepoSessionCommitFilesTests(unittest.TestCase):
    """A commit's files are asked for (spec §4.1)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", "1\n", "first")
        commit_file(self.path, "b.txt", "1\n", "second")
        self.session = RepoSession()
        self.events = []
        self.session.commit_files_changed.connect(lambda sha, f: self.events.append((sha, f)))
        self.errors = []
        self.session.error_occurred.connect(self.errors.append)
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)
        self.first, self.second = (c.sha for c in reversed(self.session.history))

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    def test_request_reads_them_off_the_ui_thread(self):
        self.session.request_commit_files(self.second)
        self.assertEqual(self.events, [])  # not yet: it runs on the worker
        wait_idle(self.session)
        files = (FileStatus("A", " ", "b.txt"),)
        self.assertEqual(self.events, [(self.second, files)])
        self.assertEqual((self.session.commit_files_sha, self.session.commit_files),
                         (self.second, files))

    def test_a_newer_request_drops_the_older_result(self):
        self.session.request_commit_files(self.second)
        self.session.request_commit_files(self.first)
        wait_idle(self.session)
        self.assertEqual(self.events, [(self.first, (FileStatus("A", " ", "a.txt"),))])

    def test_the_same_target_again_reads_nothing(self):
        self.session.request_commit_files(self.second)
        wait_idle(self.session)
        self.session.request_commit_files(self.second)
        self.assertFalse(self.session.busy)

    def test_none_clears_the_target_at_once_and_refreshes_leave_it(self):
        self.session.request_commit_files(self.second)
        wait_idle(self.session)
        self.session.request_commit_files(None)
        self.assertEqual(self.events[-1], (None, None))
        self.events.clear()
        self.session.refresh()
        wait_idle(self.session)
        self.assertEqual(self.events, [])

    def test_a_failure_is_an_error_and_none(self):
        with mock.patch.object(Repo, "commit_files", side_effect=GitError("bad commit")):
            self.session.request_commit_files(self.second)
            wait_idle(self.session)
        self.assertEqual(self.errors, ["bad commit"])
        self.assertEqual(self.events, [(self.second, None)])

    def test_open_and_close_forget_them(self):
        self.session.request_commit_files(self.second)
        wait_idle(self.session)
        self.session.close()
        self.assertEqual(self.events[-1], (None, None))
        self.session.request_commit_files(self.first)  # no repo
        self.assertFalse(self.session.busy)


class RepoSessionCommitDetailsTests(unittest.TestCase):
    """A commit's details are asked for, and follow its refs (RZG-26, spec §4.1)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", "1\n", "first")
        commit_file(self.path, "b.txt", "1\n", "second\n\nwith a body")
        self.session = RepoSession()
        self.events = []
        self.session.commit_details_changed.connect(lambda sha, d: self.events.append((sha, d)))
        self.errors = []
        self.session.error_occurred.connect(self.errors.append)
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)
        self.first, self.second = (c.sha for c in reversed(self.session.history))

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    def test_request_reads_it_off_the_ui_thread(self):
        self.session.request_commit_details(self.second)
        self.assertEqual(self.events, [])  # not yet: it runs on the worker
        wait_idle(self.session)
        [(sha, details)] = self.events
        self.assertEqual((sha, details.sha, details.parents), (self.second, self.second,
                                                               (self.first,)))
        self.assertEqual(details.body, "with a body")
        self.assertEqual((self.session.commit_details_sha, self.session.commit_details),
                         (self.second, details))

    def test_with_the_commit_files_in_one_job(self):
        files = []
        self.session.commit_files_changed.connect(lambda sha, f: files.append(sha))
        self.session.request_commit_files(self.first)
        self.session.request_commit_details(self.first)
        wait_idle(self.session)
        self.assertEqual(files, [self.first])
        self.assertEqual([sha for sha, _ in self.events], [self.first])

    def test_a_newer_request_drops_the_older_result(self):
        self.session.request_commit_details(self.second)
        self.session.request_commit_details(self.first)
        wait_idle(self.session)
        self.assertEqual([sha for sha, _ in self.events], [self.first])

    def test_a_branch_change_reads_the_refs_again(self):
        self.session.request_commit_details(self.first)
        wait_idle(self.session)
        self.assertEqual(self.events[-1][1].refs, ())
        git(self.path, "tag", "v1", self.first)
        self.session.refresh(Refresh.BRANCHES)
        wait_idle(self.session)
        self.assertEqual(self.events[-1][1].refs, ("tag: v1",))
        self.events.clear()
        self.session.refresh(Refresh.STATUS)  # refs can't change: not read
        wait_idle(self.session)
        self.session.refresh(Refresh.HISTORY)  # read, but the same: not sent
        wait_idle(self.session)
        self.assertEqual(self.events, [])

    def test_a_failure_is_an_error_and_none(self):
        with mock.patch.object(Repo, "commit_details", side_effect=GitError("bad commit")):
            self.session.request_commit_details(self.second)
            wait_idle(self.session)
        self.assertEqual(self.errors, ["bad commit"])
        self.assertEqual(self.events, [(self.second, None)])

    def test_after_an_earlier_part_fails_it_is_read_by_itself(self):
        self.session.request_commit_details(self.first)
        wait_idle(self.session)
        self.events.clear()
        git(self.path, "tag", "v2", self.first)
        with mock.patch.object(Repo, "branches", side_effect=GitError("no branches")):
            self.session.refresh(Refresh.BRANCHES)
            wait_idle(self.session)
        self.assertEqual(self.errors, ["no branches"])
        self.assertEqual(self.events[-1][1].refs, ("tag: v2",))

    def test_none_clears_the_target_and_open_and_close_forget_it(self):
        self.session.request_commit_details(self.second)
        wait_idle(self.session)
        self.session.request_commit_details(None)
        self.assertEqual(self.events[-1], (None, None))
        self.session.request_commit_details(self.second)
        wait_idle(self.session)
        self.session.close()
        self.assertEqual(self.events[-1], (None, None))
        self.session.request_commit_details(self.first)  # no repo
        self.assertFalse(self.session.busy)


class RepoSessionCommandTests(unittest.TestCase):
    """Commands change the repository (spec §4.1)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", "1", "first")
        (self.path / "a.txt").write_text("2")
        (self.path / "b.txt").write_text("new")
        self.session = RepoSession()
        self.rec = SignalRecorder(self.session, *ALL_SIGNALS)
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)
        self.rec.clear()

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    def codes(self):
        return {f.path: f.index + f.worktree for f in self.session.status}

    def test_a_command_runs_in_the_background_then_the_status_reloads(self):
        self.session.stage(["a.txt"])
        self.assertTrue(self.session.busy)
        self.assertEqual(self.codes(), {"a.txt": " M", "b.txt": "??"})  # not yet
        wait_idle(self.session)
        self.assertEqual(self.codes(), {"a.txt": "M ", "b.txt": "??"})
        self.assertEqual(self.rec.names(), ["status_changed"])  # only what it changed

    def test_commands_run_in_order(self):
        self.session.stage(["a.txt", "b.txt"])
        self.session.unstage(["b.txt"])
        self.session.stage_all()
        self.session.unstage_all()
        self.session.stage(["b.txt"])
        wait_idle(self.session)
        self.assertEqual(self.codes(), {"a.txt": " M", "b.txt": "A "})

    def test_stage_lines_reloads_the_status_and_the_diff(self):
        target = FileSelection("a.txt", FileSource.WORKTREE)
        self.session.request_diff(target)
        wait_idle(self.session)
        diff = self.session.diff
        self.rec.clear()
        command = self.session.stage_lines(diff, hunk_lines(diff, 0))
        self.assertEqual(command.kind, CommandKind.STAGE_LINES)
        self.assertEqual(command.paths, ("a.txt",))
        wait_idle(self.session)
        self.assertEqual(self.codes(), {"a.txt": "M ", "b.txt": "??"})
        self.assertEqual(self.rec.names(), ["status_changed"])  # only what it changed
        self.assertEqual(self.session.diff.hunks, ())  # nothing left unstaged

    def test_unstage_and_discard_lines(self):
        git(self.path, "add", "a.txt")
        self.session.refresh()
        wait_idle(self.session)
        index = Repo(self.path).diff("a.txt", DiffSource.INDEX)
        self.session.unstage_lines(index, hunk_lines(index, 0))
        wait_idle(self.session)
        self.assertEqual(self.codes(), {"a.txt": " M", "b.txt": "??"})
        worktree = Repo(self.path).diff("a.txt", DiffSource.WORKTREE)
        self.session.discard_lines(worktree, hunk_lines(worktree, 0))
        wait_idle(self.session)
        self.assertEqual(self.codes(), {"b.txt": "??"})

    def test_commands_are_in_flight_until_command_finished_has_gone_out(self):
        self.assertEqual(self.session.commands_in_flight, ())
        first = self.session.stage(["a.txt"])
        second = self.session.stage(["b.txt"])
        self.assertEqual(self.session.commands_in_flight, (first, second))
        seen = []
        self.session.command_finished.connect(
            lambda command, ok: seen.append(command in self.session.commands_in_flight))
        wait_idle(self.session)
        self.assertEqual(seen, [True, True])
        self.assertEqual(self.session.commands_in_flight, ())

    def test_a_failed_command_is_one_error_and_the_status_still_loads(self):
        git(self.path, "add", "a.txt")  # outside the app
        self.session.refresh()  # the worker is busy, so both commands share the next job
        self.session.stage(["missing.txt"])
        self.session.stage(["b.txt"])  # skipped after the failure
        wait_idle(self.session)
        self.assertEqual(len(self.rec.values("error_occurred")), 1)
        self.assertEqual(self.codes(), {"a.txt": "M ", "b.txt": "??"})

    def test_the_diff_target_is_read_again_after_a_command(self):
        target = FileSelection("a.txt", FileSource.INDEX)
        diffs = []
        self.session.diff_changed.connect(lambda f, d: diffs.append(d))
        self.session.request_diff(target)
        wait_idle(self.session)
        self.assertEqual(diffs[-1].hunks, ())  # nothing staged yet
        self.session.stage(["a.txt"])
        wait_idle(self.session)
        self.assertEqual(len(diffs[-1].hunks), 1)

    def test_close_drops_commands_that_have_not_run(self):
        with mock.patch("rzgit.core.repo_session.run_command") as run:
            self.session.refresh()  # keeps the worker busy, so the command waits
            self.session.stage(["a.txt"])
            self.session.close()
            wait_idle(self.session)
        run.assert_not_called()
        self.session.stage(["a.txt"])  # no repo: nothing happens
        self.assertFalse(self.session.busy)



class RepoSessionCommitTests(unittest.TestCase):
    """Committing, and how commands report back (spec §4.1)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        self.session = RepoSession()
        self.finished = []  # (command, ok)
        self.session.command_finished.connect(lambda c, ok: self.finished.append((c, ok)))

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    def open(self):
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)
        self.finished.clear()

    def test_the_first_commit_updates_head_branches_history_status_and_count(self):
        (self.path / "a.txt").write_text("a")
        git(self.path, "add", "a.txt")
        self.open()
        self.assertEqual(self.session.branches, ())
        rec = SignalRecorder(self.session, *ALL_SIGNALS)
        command = self.session.commit("First")
        wait_idle(self.session)
        self.assertEqual([c.subject for c in self.session.history], ["First"])
        self.assertEqual([b.name for b in self.session.branches], ["main"])
        self.assertEqual(self.session.status, ())
        self.assertEqual(self.session.commit_count, 1)
        self.assertNotIn("error_occurred", rec.names())
        self.assertEqual(self.finished, [(command, True)])

    def test_a_failed_commit_reports_its_error_and_not_ok(self):
        commit_file(self.path, "a.txt", "1", "first")
        self.open()
        rec = SignalRecorder(self.session, "error_occurred")
        command = self.session.commit("Nothing staged")
        wait_idle(self.session)
        self.assertEqual(len(rec.events), 1)
        self.assertEqual(self.finished, [(command, False)])
        self.assertEqual(self.session.commit_count, 1)

    def test_each_command_of_a_job_reports_in_order_and_those_after_a_failure_fail(self):
        commit_file(self.path, "a.txt", "1", "first")
        (self.path / "a.txt").write_text("2")
        self.open()
        self.session.refresh()  # the worker is busy, so the commands share the next job
        commands = [self.session.stage(["a.txt"]), self.session.stage(["missing.txt"]),
                    self.session.commit("Skipped")]
        wait_idle(self.session)
        self.assertEqual(self.finished, list(zip(commands, (True, False, False))))
        self.assertEqual(self.session.commit_count, 1)

    def test_commit_with_no_repo_does_nothing(self):
        self.assertIsNone(self.session.commit("Nowhere"))
        self.assertFalse(self.session.busy)

    def test_a_closed_repo_s_command_reports_nothing(self):
        (self.path / "a.txt").write_text("a")
        git(self.path, "add", "a.txt")
        self.open()
        self.session.commit("First")
        self.session.close()
        wait_idle(self.session)
        self.assertEqual(self.finished, [])



class RepoSessionBranchTests(unittest.TestCase):
    """Branch commands and what they reload (spec §4.1)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", "1\n", "first")
        git(self.path, "switch", "-q", "-c", "side")
        commit_file(self.path, "a.txt", "side\n", "side")
        git(self.path, "switch", "-q", "main")
        self.session = RepoSession()
        self.finished = []
        self.session.command_finished.connect(lambda c, ok: self.finished.append((c.kind, ok)))
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    def branches(self):
        return {b.name: (b.is_current, b.merged) for b in self.session.branches}

    def subjects(self):
        return [c.subject for c in self.session.history]

    def test_switch_updates_head_branches_history_status_and_count(self):
        (self.path / "new.txt").write_text("new")
        self.session.refresh()
        wait_idle(self.session)
        self.session.switch_branch("side", stash=True)
        wait_idle(self.session)
        self.assertEqual(self.session.head, "side")
        self.assertEqual(self.branches(), {"main": (False, True), "side": (True, True)})
        self.assertEqual(self.subjects(), ["side", "first"])
        self.assertEqual(self.session.status, ())
        self.assertEqual(self.session.commit_count, 2)
        self.assertEqual(self.finished, [(CommandKind.SWITCH, True)])

    def test_create_rename_delete(self):
        self.session.create_branch("topic", "side")
        wait_idle(self.session)
        self.assertEqual(self.session.head, "main")
        self.assertIn("topic", self.branches())
        self.session.create_branch("now", "main", switch=True)
        wait_idle(self.session)
        self.assertEqual(self.session.head, "now")
        self.session.rename_branch("now", "renamed")
        wait_idle(self.session)
        self.assertEqual(self.session.head, "renamed")
        self.session.delete_branch("topic", force=True)
        wait_idle(self.session)
        self.assertEqual(set(self.branches()), {"main", "renamed", "side"})

    def test_a_refused_delete_reports_its_error_and_keeps_the_branch(self):
        rec = SignalRecorder(self.session, "error_occurred")
        self.session.delete_branch("side")  # not merged into main
        wait_idle(self.session)
        self.assertEqual(len(rec.events), 1)
        self.assertIn("side", self.branches())
        self.assertEqual(self.finished, [(CommandKind.DELETE_BRANCH, False)])

    def test_merge_and_rebase_reload_history(self):
        git(self.path, "switch", "-q", "-c", "other", "main")
        commit_file(self.path, "b.txt", "b", "other")
        git(self.path, "switch", "-q", "main")
        self.session.merge("side")  # a fast-forward
        wait_idle(self.session)
        self.assertEqual(self.subjects(), ["side", "first"])
        self.session.switch_branch("other")
        self.session.rebase("main")
        wait_idle(self.session)
        self.assertEqual(self.session.head, "other")
        self.assertEqual(self.subjects(), ["other", "side", "first"])
        self.assertEqual(self.session.commit_count, 3)

    def test_a_conflicting_merge_shows_the_error_and_changes_nothing(self):
        commit_file(self.path, "a.txt", "main\n", "main")
        self.session.refresh()
        wait_idle(self.session)
        rec = SignalRecorder(self.session, "error_occurred")
        self.session.merge("side")
        wait_idle(self.session)
        self.assertEqual(len(rec.events), 1)
        self.assertIn("aborted", rec.values("error_occurred")[0])
        self.assertEqual(self.session.status, ())
        self.assertEqual(self.subjects(), ["main", "first"])

    def test_branch_commands_with_no_repo_do_nothing(self):
        self.session.close()
        wait_idle(self.session)
        for call in (lambda: self.session.switch_branch("side"),
                     lambda: self.session.create_branch("x", "main"),
                     lambda: self.session.rename_branch("a", "b"),
                     lambda: self.session.delete_branch("side"),
                     lambda: self.session.merge("side"),
                     lambda: self.session.rebase("side")):
            self.assertIsNone(call())
        self.assertFalse(self.session.busy)



class RepoSessionWorktreeTests(unittest.TestCase):
    """Worktree commands, and what they reload (RZG-49, spec §4.1)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.path = make_repo(self.tmp / "repo")
        commit_file(self.path, "a.txt", "1\n", "first")
        self.session = RepoSession()
        self.addCleanup(self.session.shutdown)
        self.finished = []
        self.session.command_finished.connect(lambda c, ok: self.finished.append((c, ok)))
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)

    def test_add_reloads_the_branches_and_reports_its_command(self):
        command = self.session.add_worktree(str(self.tmp / "wt"), "topic", "main")
        self.assertEqual((command.path, command.branch, command.start),
                         (str(self.tmp / "wt"), "topic", "main"))
        wait_idle(self.session)
        self.assertEqual(self.finished, [(command, True)])
        self.assertIn("topic", [b.name for b in self.session.branches])
        self.assertTrue((self.tmp / "wt" / "a.txt").is_file())

    def test_remove_and_prune(self):
        git(self.path, "worktree", "add", "-q", "-b", "a", str(self.tmp / "a"))
        git(self.path, "worktree", "add", "-q", "-b", "b", str(self.tmp / "b"))
        (self.tmp / "a" / "x").write_text("x")
        refused = self.session.remove_worktree(str(self.tmp / "a"))
        forced = self.session.remove_worktree(str(self.tmp / "a"), force=True)
        wait_idle(self.session)
        self.assertEqual(self.finished, [(refused, False), (forced, True)])
        self.assertFalse((self.tmp / "a").exists())
        shutil.rmtree(self.tmp / "b")
        pruned = self.session.prune_worktrees()
        wait_idle(self.session)
        self.assertEqual(self.finished[-1], (pruned, True))
        self.assertEqual(len(Repo(self.path).worktrees()), 1)

    def test_no_repository_no_command(self):
        self.session.close()
        self.assertIsNone(self.session.add_worktree("/x", "b"))
        self.assertIsNone(self.session.remove_worktree("/x"))
        self.assertIsNone(self.session.prune_worktrees())


class RepoSessionLastCommitTests(unittest.TestCase):
    """Refresh.LAST_COMMIT and amend (spec §4.1)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", "1", "First\n\nBody")
        self.session = RepoSession()
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    def test_read_only_when_asked_for(self):
        rec = SignalRecorder(self.session, "last_commit_changed")
        self.session.refresh()
        wait_idle(self.session)
        self.assertIsNone(self.session.last_commit)
        self.assertEqual(rec.events, [])
        self.session.refresh(Refresh.LAST_COMMIT)
        wait_idle(self.session)
        last = self.session.last_commit
        self.assertEqual((last.message, last.pushed), ("First\n\nBody", False))
        self.assertEqual(rec.values("last_commit_changed"), [last])
        self.session.refresh(Refresh.LAST_COMMIT)  # unchanged: not emitted again
        wait_idle(self.session)
        self.assertEqual(len(rec.events), 1)

    def test_open_and_close_reset_it(self):
        self.session.refresh(Refresh.LAST_COMMIT)
        wait_idle(self.session)
        rec = SignalRecorder(self.session, "last_commit_changed")
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)
        self.assertIsNone(self.session.last_commit)
        self.assertEqual(rec.values("last_commit_changed"), [None])
        self.session.close()
        self.assertIsNone(self.session.last_commit)

    def test_none_before_the_first_commit(self):
        self.assertTrue(self.session.open(make_repo(Path(self._tmp.name) / "empty")))
        self.session.refresh(Refresh.LAST_COMMIT)
        wait_idle(self.session)
        self.assertIsNone(self.session.last_commit)

    def test_amend_reloads_history_and_status(self):
        (self.path / "b.txt").write_text("b")
        git(self.path, "add", "b.txt")
        self.session.refresh()
        wait_idle(self.session)
        command = self.session.amend("First, amended")
        finished = []
        self.session.command_finished.connect(lambda c, ok: finished.append((c, ok)))
        wait_idle(self.session)
        self.assertEqual([c.subject for c in self.session.history], ["First, amended"])
        self.assertEqual(self.session.status, ())
        self.assertEqual(self.session.commit_count, 1)
        self.assertEqual(finished, [(command, True)])


if __name__ == "__main__":
    unittest.main()


class RepoSessionStashTests(unittest.TestCase):
    """Stashes: the part, their files and diffs, and the commands (RZG-36, spec §4.1)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", "1\n", "first")
        (self.path / "a.txt").write_text("2\n")
        (self.path / "new.txt").write_text("new\n")
        self.session = RepoSession()
        self.finished = []
        self.session.command_finished.connect(lambda c, ok: self.finished.append((c.kind, ok)))
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    def test_stash_changes_reloads_status_and_stashes(self):
        rec = SignalRecorder(self.session, "stashes_changed", "status_changed")
        self.session.stash_changes("saved")
        wait_idle(self.session)
        [stash] = self.session.stashes
        self.assertEqual((stash.message, stash.branch, stash.untracked), ("saved", "main", True))
        self.assertEqual(self.session.status, ())
        self.assertEqual(rec.names(), ["status_changed", "stashes_changed"])
        self.assertEqual(self.finished, [(CommandKind.STASH_PUSH, True)])

    def test_a_stash_s_files_and_the_diff_of_its_untracked_file(self):
        self.session.stash_changes("saved")
        wait_idle(self.session)
        [stash] = self.session.stashes
        self.session.request_commit_files(stash.sha)
        wait_idle(self.session)
        self.assertEqual([(f.index, f.path) for f in self.session.commit_files],
                         [("M", "a.txt"), ("?", "new.txt")])
        self.session.request_diff(FileSelection("new.txt", FileSource.COMMIT, stash.sha))
        wait_idle(self.session)
        self.assertEqual([line.text for h in self.session.diff.hunks for line in h.lines],
                         ["new"])

    def test_apply_pop_and_drop(self):
        self.session.stash_changes("saved")
        wait_idle(self.session)
        [stash] = self.session.stashes
        self.session.apply_stash(stash.sha)
        wait_idle(self.session)
        self.assertEqual({f.path for f in self.session.status}, {"a.txt", "new.txt"})
        self.assertEqual(self.session.stashes, (stash,))
        self.session.stash_changes("again")
        wait_idle(self.session)
        again = self.session.stashes[0]
        self.session.drop_stash(stash.sha)  # stash@{1} now
        wait_idle(self.session)
        self.assertEqual(self.session.stashes, (again,))  # still stash@{0}
        self.session.apply_stash(again.sha, pop=True)
        wait_idle(self.session)
        self.assertEqual(self.session.stashes, ())
        self.assertEqual(len(self.session.status), 2)

    def test_a_notice_comes_before_its_command_finished(self):
        (self.path / "a.txt").write_text("1\n")  # only new.txt is left: untracked
        self.session.refresh()
        wait_idle(self.session)
        events = []
        self.session.notice_occurred.connect(lambda text: events.append(text))
        self.session.command_finished.connect(lambda c, ok: events.append(ok))
        self.session.stash_changes("m", untracked=False)
        wait_idle(self.session)
        self.assertEqual(events, [git_commands.NOTHING_TO_STASH, True])
        self.assertEqual(self.session.stashes, ())

    def test_a_refused_apply_is_an_error_and_keeps_the_stash(self):
        self.session.stash_changes("saved")
        wait_idle(self.session)
        (self.path / "a.txt").write_text("dirty\n")
        self.session.refresh()
        wait_idle(self.session)
        rec = SignalRecorder(self.session, "error_occurred")
        self.session.apply_stash(self.session.stashes[0].sha, pop=True)
        wait_idle(self.session)
        self.assertEqual(rec.values("error_occurred"), [git_commands.NOT_CLEAN])
        self.assertEqual(len(self.session.stashes), 1)
        self.assertEqual(self.finished[-1], (CommandKind.STASH_APPLY, False))
