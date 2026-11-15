import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from rzgitclient.core.repo_session import RepoSession
from rzgitclient.core.selection import FileSelection, FileSource, Selection
from rzgitclient.git.git_models import Commit
from tests.support import SignalRecorder, make_repo, qt_app, wait_idle

COMMIT = Commit("a" * 40, "Test User", datetime(2026, 1, 1, tzinfo=timezone.utc), "first")


class FileSelectionTests(unittest.TestCase):
    def test_commit_only_with_commit_source(self):
        FileSelection("a.py", FileSource.WORKTREE)
        FileSelection("a.py", FileSource.INDEX)
        FileSelection("a.py", FileSource.COMMIT, "a" * 40)
        with self.assertRaises(ValueError):
            FileSelection("a.py", FileSource.COMMIT)
        with self.assertRaises(ValueError):
            FileSelection("a.py", FileSource.INDEX, "a" * 40)


class SelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.session = RepoSession()
        self.selection = Selection(self.session)
        self.rec = SignalRecorder(self.selection, "commit_changed", "file_changed")

    def tearDown(self):
        self._tmp.cleanup()

    def test_setters_emit_only_on_change(self):
        file = FileSelection("a.py", FileSource.WORKTREE)
        self.selection.commit = COMMIT
        self.selection.commit = COMMIT
        self.selection.file = file
        self.selection.file = FileSelection("a.py", FileSource.WORKTREE)  # equal
        self.assertEqual(self.rec.events, [("commit_changed", COMMIT), ("file_changed", file)])

    def test_cleared_when_repo_changes(self):
        self.assertTrue(self.session.open(make_repo(self.tmp / "a")))
        wait_idle(self.session)
        self.selection.commit = COMMIT
        self.selection.file = FileSelection("a.py", FileSource.INDEX)
        self.rec.clear()

        self.assertTrue(self.session.open(make_repo(self.tmp / "b")))
        wait_idle(self.session)
        self.assertIsNone(self.selection.commit)
        self.assertIsNone(self.selection.file)
        self.assertEqual(self.rec.events, [("commit_changed", None), ("file_changed", None)])

    def test_clear_when_empty_emits_nothing(self):
        self.selection.clear()
        self.assertEqual(self.rec.events, [])


if __name__ == "__main__":
    unittest.main()
