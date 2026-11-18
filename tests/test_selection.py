import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from rzgit.core.repo_session import RepoSession
from rzgit.core.selection import FileSelection, FileSource, Selection, file_rows
from rzgit.git.git_models import Commit, FileStatus, Stash
from tests.support import SignalRecorder, commit_file, git, make_repo, qt_app, wait_idle

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

    def test_only_the_selected_commit_s_rows_are_kept(self):
        worktree = FileSelection("w.py", FileSource.WORKTREE)
        self.selection.select([worktree], worktree)
        self.selection.commit = COMMIT  # Unstaged Files collapses: its rows go
        self.assertEqual((self.selection.files, self.selection.file), ((), None))
        mine = FileSelection("a.py", FileSource.COMMIT, COMMIT.sha)
        self.selection.select([mine], mine)
        other = Commit("b" * 40, "Test User", COMMIT.date, "second")
        self.selection.commit = other
        self.assertEqual((self.selection.files, self.selection.file), ((), None))
        theirs = FileSelection("b.py", FileSource.COMMIT, other.sha)
        self.selection.select([theirs], theirs)
        self.selection.commit = None  # Uncommitted changes
        self.assertEqual(self.selection.files, ())

    def test_a_commit_the_history_no_longer_has_goes_back_to_uncommitted(self):
        repo = make_repo(self.tmp / "a")
        commit_file(repo, "a.txt", "1", "first")
        self.assertTrue(self.session.open(repo))
        wait_idle(self.session)
        (first,) = self.session.history
        self.selection.commit = first
        self.session.refresh()  # the same history: kept
        wait_idle(self.session)
        self.assertEqual(self.selection.commit, first)
        git(repo, "commit", "-q", "--amend", "-m", "amended")
        self.session.refresh()
        wait_idle(self.session)
        self.assertIsNone(self.selection.commit)

    def test_a_selected_stash_follows_the_stashes(self):
        """RZG-36: kept through history reloads, moved to its new stash@{n}, and back
        to Uncommitted changes once it's gone."""
        repo = make_repo(self.tmp / "s")
        commit_file(repo, "a.txt", "1", "first")
        (repo / "a.txt").write_text("older")
        git(repo, "stash", "push", "-q", "-m", "older")
        self.assertTrue(self.session.open(repo))
        wait_idle(self.session)
        [older] = self.session.stashes
        mine = FileSelection("a.txt", FileSource.COMMIT, older.sha)
        self.selection.commit = older
        self.selection.select([mine], mine)
        commit_file(repo, "b.txt", "2", "second")  # history changes; the stash isn't in it
        (repo / "a.txt").write_text("newer")
        git(repo, "stash", "push", "-q", "-m", "newer")
        self.session.refresh()
        wait_idle(self.session)
        self.assertIsInstance(self.selection.commit, Stash)
        self.assertEqual((self.selection.commit.sha, self.selection.commit.ref),
                         (older.sha, "stash@{1}"))
        self.assertEqual(self.selection.file, mine)  # its files stay selected
        git(repo, "stash", "drop", "-q", "stash@{1}")
        self.session.refresh()
        wait_idle(self.session)
        self.assertIsNone(self.selection.commit)
        self.assertIsNone(self.selection.file)

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

    def test_files_emit_on_change_and_clear_with_the_repo(self):
        rec = SignalRecorder(self.selection, "files_changed")
        files = (FileSelection("a.py", FileSource.WORKTREE), FileSelection("b.py", FileSource.INDEX))
        self.selection.files = files
        self.selection.files = list(files)  # equal
        self.assertEqual(rec.events, [("files_changed", files)])
        self.selection.clear()
        self.assertEqual(self.selection.files, ())

    def test_clear_when_empty_emits_nothing(self):
        self.selection.clear()
        self.assertEqual(self.rec.events, [])


W, I = FileSource.WORKTREE, FileSource.INDEX


def status(*entries):
    """FileStatus from "XY path" strings."""
    return tuple(FileStatus(e[0], e[1], e[3:]) for e in entries)


class FileRowsTests(unittest.TestCase):
    def test_unstaged_and_untracked_rows_then_staged_rows(self):
        rows = file_rows(status("MM a.txt", "?? new.txt", "A  b.txt", " D gone.txt"))
        self.assertEqual(rows, [FileSelection("a.txt", W), FileSelection("new.txt", W),
                                FileSelection("gone.txt", W),
                                FileSelection("a.txt", I), FileSelection("b.txt", I)])


class SelectionFollowsTheStatusTests(unittest.TestCase):
    """What stays selected after a status change (spec §4.2)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.session = RepoSession()
        self.selection = Selection(self.session)

    def change(self, *entries):
        self.session.status_changed.emit(status(*entries))

    def selected(self):
        return self.selection.files, self.selection.file

    def test_a_row_that_is_still_there_stays(self):
        a = FileSelection("a.txt", W)
        self.selection.select([a], a)
        rec = SignalRecorder(self.selection, "files_changed", "file_changed")
        self.change(" M a.txt", "?? b.txt")
        self.assertEqual(self.selected(), ((a,), a))
        self.assertEqual(rec.events, [])

    def test_a_row_moved_outside_the_app_takes_the_other_side(self):
        self.selection.select([FileSelection("a.txt", W)], FileSelection("a.txt", W))
        self.change("M  a.txt")
        self.assertEqual(self.selected(), ((FileSelection("a.txt", I),), FileSelection("a.txt", I)))

    def test_a_row_that_is_gone_is_dropped(self):
        a, b = FileSelection("a.txt", W), FileSelection("b.txt", W)
        self.selection.select([a, b], b)
        self.change(" M a.txt")
        self.assertEqual(self.selected(), ((a,), None))

    def test_follow_waits_for_a_status_with_the_new_row(self):
        a = FileSelection("a.txt", W)
        self.selection.select([a], a)
        self.selection.follow(["a.txt"], I)
        self.change(" M a.txt")  # an older refresh: not staged yet
        self.assertEqual(self.selected(), ((a,), a))
        self.change("MM a.txt")  # staged, with changes left: still follows
        self.assertEqual(self.selected(), ((FileSelection("a.txt", I),), FileSelection("a.txt", I)))
        self.change("MM a.txt")
        self.assertEqual(self.selection.file, FileSelection("a.txt", I))  # done following

    def test_selecting_cancels_the_follow(self):
        a, b = FileSelection("a.txt", W), FileSelection("b.txt", W)
        self.selection.select([a], a)
        self.selection.follow(["a.txt"], I)
        self.selection.select([b], b)
        self.selection.select([a], a)
        self.change("MM a.txt", "?? b.txt")
        self.assertEqual(self.selection.file, a)

    def test_several_rows_follow_in_list_order(self):
        x, y = FileSelection("x.txt", W), FileSelection("y.txt", W)
        self.selection.select([y, x], y)
        self.selection.follow(["x.txt", "y.txt"], I)
        self.change("A  x.txt", "A  y.txt")
        self.assertEqual(self.selected(), ((FileSelection("x.txt", I), FileSelection("y.txt", I)),
                                           FileSelection("y.txt", I)))


if __name__ == "__main__":
    unittest.main()
