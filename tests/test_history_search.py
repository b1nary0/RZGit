"""History search: every commit from HEAD, matched for History's filter (spec §4.1, RZG-88)."""

import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QThread

from rzgit.core.history_search import HistoryMatches, commit_matches
from rzgit.core.repo_session import RepoSession
from rzgit.git.git_models import HistoryOrder
from rzgit.git.repo import Repo
from tests.support import SignalRecorder, commit_file, git, make_repo, qt_app, wait_idle


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        QCoreApplication.processEvents()
        QThread.msleep(1)


class SearchLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = make_repo(Path(self._tmp.name) / "repo")

    def test_records_carry_email_refs_and_whole_message(self):
        commit_file(self.path, "a.txt", "1", "first")
        commit_file(self.path, "a.txt", "2", "second line\n\nthe body\nmore")
        git(self.path, "tag", "v1.0")
        records = list(Repo(self.path).search_log())
        self.assertEqual([c.subject for c, *_ in records], ["second line", "first"])
        commit, email, refs, message = records[0]
        self.assertEqual(email, "test@example.com")
        self.assertIn("tag: v1.0", refs)
        self.assertEqual(message, "second line\n\nthe body\nmore")
        self.assertEqual(commit.parents, (records[1][0].sha,))
        self.assertEqual([c for c, *_ in records], Repo(self.path).log())  # the same commits

    def test_empty_repo_yields_nothing(self):
        self.assertEqual(list(Repo(self.path).search_log(HistoryOrder.TOPO)), [])

    def test_closing_early_ends_git(self):
        for n in range(3):
            commit_file(self.path, "a.txt", str(n), f"c{n}")
        started = []
        real = subprocess.Popen

        def popen(*args, **kwargs):
            started.append(real(*args, **kwargs))
            return started[-1]

        records = Repo(self.path).search_log()
        with mock.patch("subprocess.Popen", side_effect=popen):
            next(records)
        records.close()
        log = started[-1]  # after has_commits' rev-parse
        self.assertIn("log", log.args)
        self.assertIsNotNone(log.poll())  # killed, or already done and waited for
        self.assertTrue(log.stdout.closed)


class CommitMatchesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(path, "a.txt", "1", "Fix the parser\n\nIt crashed on tabs.")
        git(path, "branch", "feature/tabs")
        self.record = next(Repo(path).search_log())

    def matches(self, *texts, flags=re.IGNORECASE, prefix=None):
        patterns = [re.compile(re.escape(text), flags) for text in texts if text]
        return commit_matches(patterns, prefix, *self.record)

    def test_fields(self):
        self.assertTrue(self.matches("parser"))  # the subject
        self.assertTrue(self.matches("crashed"))  # the body
        self.assertTrue(self.matches("test user"))  # the author
        self.assertTrue(self.matches("example.com"))  # the email
        self.assertTrue(self.matches("feature/tabs"))  # a ref
        self.assertFalse(self.matches("nowhere"))
        self.assertFalse(self.matches("PARSER", flags=0))  # match case

    def test_every_pattern_somewhere(self):
        """RZG-88: each word of the query on its own, in any order and any field."""
        self.assertTrue(self.matches("tabs", "parser"))  # the body and the subject
        self.assertTrue(self.matches("parser", "test user", "feature/tabs"))
        self.assertFalse(self.matches("parser", "nowhere"))  # every one, not any

    def test_sha_only_as_a_prefix(self):
        sha = self.record[0].sha
        self.assertTrue(self.matches("", prefix=sha[:6]))
        self.assertFalse(self.matches("", prefix=sha[2:8]))
        self.assertFalse(self.matches(sha[2:8]))  # the pattern never looks at the SHA


class SessionSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = make_repo(Path(self._tmp.name) / "repo")
        for n in range(1, 7):
            commit_file(self.path, "a.txt", str(n), f"{'odd' if n % 2 else 'even'} {n}")
        self.session = RepoSession()
        self.addCleanup(self.session.shutdown)
        self.session.open(self.path)
        wait_idle(self.session)
        self.rec = SignalRecorder(self.session, "history_matches", "error_occurred")

    def done(self) -> HistoryMatches:
        wait_for(lambda: any(m.done for m in self.rec.values("history_matches")))
        return self.rec.values("history_matches")[-1]

    def test_finds_every_match_newest_first(self):
        search_id = self.session.search_history([re.compile("odd")])
        result = self.done()
        self.assertEqual(result.search_id, search_id)
        self.assertEqual([c.subject for c in result.commits], ["odd 5", "odd 3", "odd 1"])
        self.assertFalse(result.capped)
        self.assertEqual(self.rec.values("error_occurred"), [])

    def test_every_pattern_must_match(self):
        self.session.search_history([re.compile("odd"), re.compile(r"\b[13]$")])
        self.assertEqual([c.subject for c in self.done().commits], ["odd 3", "odd 1"])

    def test_sha_prefix(self):
        target = self.session.history[2]
        self.session.search_history(None, target.sha[:7])
        self.assertEqual(self.done().commits, (target,))

    def test_not_only_the_loaded_page(self):
        with mock.patch("rzgit.core.repo_session.HISTORY_PAGE", 2):
            self.session.close()
            self.session.open(self.path)
            wait_idle(self.session)
        self.assertEqual(len(self.session.history), 2)
        self.session.search_history([re.compile(r"\b1$")])
        self.assertEqual([c.subject for c in self.done().commits], ["odd 1"])

    def test_cap(self):
        with mock.patch("rzgit.core.history_search.MAX_MATCHES", 2):
            self.session.search_history([re.compile(".")])
            result = self.done()
        self.assertEqual(len(result.commits), 2)
        self.assertTrue(result.capped)

    def test_a_new_search_drops_the_last(self):
        first = self.session.search_history([re.compile("odd")])
        second = self.session.search_history([re.compile("even")])
        result = self.done()
        self.assertEqual(result.search_id, second)
        self.assertNotIn(first, [m.search_id for m in self.rec.values("history_matches")])
        self.assertEqual([c.subject for c in result.commits], ["even 6", "even 4", "even 2"])

    def test_a_late_result_of_a_stopped_search_is_dropped(self):
        search = self.session._search
        self.session.search_history([re.compile("odd")])
        old = search._token
        self.session.search_history([re.compile("even")])
        search._on_found(old, HistoryMatches(old.search_id, (), True))  # arrives late
        search._on_failed(old, "late error")
        self.done()
        self.assertNotIn(old.search_id, [m.search_id for m in self.rec.values("history_matches")])
        self.assertEqual(self.rec.values("error_occurred"), [])

    def test_stop_and_close_emit_nothing_more(self):
        self.session.search_history([re.compile("odd")])
        self.session.search_history(None)  # stops it
        self.session.search_history([re.compile("even")])
        self.session.close()
        self.session.search_history([re.compile("odd")])  # no repo: nothing starts
        QThread.msleep(200)
        self.app.processEvents()
        self.assertEqual(self.rec.values("history_matches"), [])

    def test_batches_append_the_same_objects(self):
        with mock.patch("rzgit.core.history_search.BATCH_INTERVAL", 0):
            self.session.search_history([re.compile(".")])
            self.done()
        emits = self.rec.values("history_matches")
        self.assertGreater(len(emits), 1)
        for before, after in zip(emits, emits[1:]):
            self.assertTrue(all(a is b for a, b in zip(before.commits, after.commits)))
        self.assertEqual(len(emits[-1].commits), 6)

    def test_history_order(self):
        git(self.path, "checkout", "-q", "-b", "side", "HEAD~3")
        commit_file(self.path, "b.txt", "x", "side commit")
        git(self.path, "checkout", "-q", "main")
        git(self.path, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        self.session.set_history_order(HistoryOrder.TOPO)
        wait_idle(self.session)
        self.session.search_history([re.compile(".")])
        found = [c.sha for c in self.done().commits]
        self.assertEqual(found, [c.sha for c in Repo(self.path).log(100, order=HistoryOrder.TOPO)])


if __name__ == "__main__":
    unittest.main()
