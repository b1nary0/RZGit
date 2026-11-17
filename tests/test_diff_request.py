import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rzgitclient.core import diff_request
from rzgitclient.core.diff_patch import build_patch, hunk_lines
from rzgitclient.core.diff_request import DiffRequest, load_diff, split_whole
from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.git.git_models import DiffLine, DiffSource, FileDiff, Hunk, LineKind
from rzgitclient.git.repo import Repo
from rzgitclient.ui.panels.diff_rows import whole_file
from tests.support import commit_file, git, make_repo


class LoadDiffTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", "1\n", "first")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, path, source, commit=None, full=False):
        request = DiffRequest(FileSelection(path, source, commit), full)
        return load_diff(self.repo, request, tuple(self.repo.status()))

    def test_untracked_worktree_file_is_diffed_against_nothing(self):
        (self.path / "new.txt").write_text("x\n")
        diff = self.load("new.txt", FileSource.WORKTREE)
        self.assertEqual([l.text for l in diff.hunks[0].lines], ["x"])

    def test_a_committed_rename_passes_its_old_path_from_the_commit_files(self):
        git(self.path, "mv", "a.txt", "b.txt")
        git(self.path, "commit", "-q", "-m", "rename")
        sha = git(self.path, "rev-parse", "HEAD").strip()
        request = DiffRequest(FileSelection("b.txt", FileSource.COMMIT, sha))
        diff = load_diff(self.repo, request, (), tuple(self.repo.commit_files(sha)))
        self.assertEqual(diff.old_path, "a.txt")
        self.assertEqual(diff.hunks, ())  # renamed, not changed

    def test_staged_rename_passes_its_old_path(self):
        git(self.path, "mv", "a.txt", "b.txt")
        with mock.patch.object(Repo, "diff", wraps=self.repo.diff) as spy:
            diff = self.load("b.txt", FileSource.INDEX)
        self.assertEqual(spy.call_args.kwargs["old_path"], "a.txt")
        self.assertEqual(spy.call_args.args[1], DiffSource.INDEX)
        self.assertEqual(diff.old_path, "a.txt")

    def test_commit_source(self):
        diff = self.load("a.txt", FileSource.COMMIT, commit="HEAD")
        self.assertEqual(len(diff.hunks), 1)

    def test_size_limit_unless_full(self):
        (self.path / "a.txt").write_text("".join(f"{n}\n" for n in range(50)))
        with mock.patch.object(diff_request, "DIFF_MAX_LINES", 20):
            self.assertTrue(self.load("a.txt", FileSource.WORKTREE).too_large)
            full = self.load("a.txt", FileSource.WORKTREE, full=True)
        self.assertFalse(full.too_large)
        self.assertEqual(sum(l.kind.value == "+" for l in full.hunks[0].lines), 49)

    def test_follows_status_except_for_a_commit(self):
        self.assertTrue(DiffRequest(FileSelection("a", FileSource.WORKTREE)).follows_status)
        self.assertTrue(DiffRequest(FileSelection("a", FileSource.INDEX)).follows_status)
        self.assertFalse(DiffRequest(FileSelection("a", FileSource.COMMIT, "abc")).follows_status)


if __name__ == "__main__":
    unittest.main()


def _lines(n, changed=(), prefix="line"):
    """n lines, those numbered in ``changed`` (from 0) upper-cased."""
    return "".join((f"{prefix} {i}".upper() if i in changed else f"{prefix} {i}") + "\n"
                   for i in range(n))


class WholeFileTests(unittest.TestCase):
    """RZG-60: the whole file, cut into hunks of one change each."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", _lines(60), "first")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, path, source, commit=None, whole=True, full=False):
        request = DiffRequest(FileSelection(path, source, commit), full, whole)
        return load_diff(self.repo, request, tuple(self.repo.status()))

    def every_line(self, diff):
        return [l for h in diff.hunks for l in h.lines]

    def test_every_line_of_the_file_cut_into_one_hunk_per_change(self):
        (self.path / "a.txt").write_text(_lines(60, changed=(10, 40)))
        diff = self.load("a.txt", FileSource.WORKTREE)
        lines = self.every_line(diff)
        self.assertEqual([l.new_no for l in lines if l.new_no], list(range(1, 61)))
        self.assertEqual([l.old_no for l in lines if l.old_no], list(range(1, 61)))
        self.assertEqual(len(diff.hunks), 2)
        # Cut in the middle of the 29 unchanged lines between the changes (lines 12 to 40).
        first, second = diff.hunks
        self.assertEqual(first.header, "@@ -1,26 +1,26 @@")
        self.assertEqual(second.header, "@@ -27,34 +27,34 @@")
        self.assertEqual((second.old_start, second.new_start), (27, 27))
        hunks_view = self.load("a.txt", FileSource.WORKTREE, whole=False)
        self.assertEqual(len(hunks_view.hunks), 2)

    def test_headers_agree_with_the_lines_and_git_takes_them(self):
        (self.path / "a.txt").write_text(_lines(58, changed=(0, 20, 21, 57)) + "extra\n")
        diff = self.load("a.txt", FileSource.WORKTREE)
        self.assertEqual(len(diff.hunks), 3)
        for hunk in diff.hunks:
            old = [l.old_no for l in hunk.lines if l.old_no is not None]
            new = [l.new_no for l in hunk.lines if l.new_no is not None]
            self.assertEqual(hunk.header,
                             f"@@ -{old[0]},{len(old)} +{new[0]},{len(new)} @@")
        # Staging each hunk of the whole file leaves the index as staging each of the hunks view.
        for h in range(3):
            self.repo.apply(build_patch(diff, hunk_lines(diff, h)), cached=True)
        self.assertEqual(git(self.path, "diff").strip(), "")  # nothing left unstaged

    def test_staging_one_hunk_of_the_whole_file_is_staging_it_in_the_hunks_view(self):
        (self.path / "a.txt").write_text(_lines(60, changed=(5, 30, 55)))
        whole = self.load("a.txt", FileSource.WORKTREE)
        self.repo.apply(build_patch(whole, hunk_lines(whole, 1)), cached=True)
        from_whole = git(self.path, "diff", "--cached")
        git(self.path, "reset", "-q")
        hunks = self.load("a.txt", FileSource.WORKTREE, whole=False)
        self.repo.apply(build_patch(hunks, hunk_lines(hunks, 1)), cached=True)
        self.assertEqual(from_whole, git(self.path, "diff", "--cached"))
        self.assertIn("+LINE 30", from_whole)
        self.assertNotIn("LINE 5\n", from_whole)

    def test_every_source(self):
        (self.path / "a.txt").write_text(_lines(60, changed=(30,)))
        git(self.path, "add", "a.txt")
        self.assertEqual(len(self.every_line(self.load("a.txt", FileSource.INDEX))), 61)
        git(self.path, "commit", "-q", "-m", "second")
        self.assertEqual(len(self.every_line(self.load("a.txt", FileSource.COMMIT, "HEAD"))), 61)
        (self.path / "new.txt").write_text("x\ny\n")
        untracked = self.load("new.txt", FileSource.WORKTREE)
        self.assertEqual([l.text for l in self.every_line(untracked)], ["x", "y"])
        (self.path / "a.txt").write_text(_lines(60, changed=(30, 31)))
        self.assertEqual(len(self.every_line(self.load("a.txt", FileSource.WORKTREE))), 61)

    def test_a_whole_added_file_is_still_all_added_from_nothing(self):
        # RZG-61's rule (whole_file in diff_rows) holds for the whole-file view.
        (self.path / "new.txt").write_text(_lines(40))
        diff = self.load("new.txt", FileSource.WORKTREE)
        self.assertEqual(whole_file(diff), "added")
        self.assertEqual(len(diff.hunks), 1)

    def test_the_size_limit_holds_for_the_whole_file(self):
        (self.path / "a.txt").write_text(_lines(60, changed=(30,)))
        with mock.patch.object(diff_request, "DIFF_MAX_LINES", 30):
            self.assertFalse(self.load("a.txt", FileSource.WORKTREE, whole=False).too_large)
            self.assertTrue(self.load("a.txt", FileSource.WORKTREE).too_large)
            self.assertFalse(self.load("a.txt", FileSource.WORKTREE, full=True).too_large)


class SplitWholeTests(unittest.TestCase):
    def test_a_diff_with_git_s_context_is_left_as_it_is(self):
        C, A = LineKind.CONTEXT, LineKind.ADDED
        lines = (DiffLine(C, 1, 1, "a"), DiffLine(A, None, 2, "b"),
                 *(DiffLine(C, n, n + 1, "c") for n in range(2, 8)), DiffLine(A, None, 9, "d"))
        diff = FileDiff("f", (Hunk("@@ -1,7 +1,9 @@ f()", 1, 1, lines),))
        self.assertIs(split_whole(diff), diff)  # a run of 6 isn't cut

    def test_a_run_of_seven_is_cut_with_the_larger_half_first(self):
        C, A = LineKind.CONTEXT, LineKind.ADDED
        lines = (DiffLine(A, None, 1, "b"), *(DiffLine(C, n, n + 1, "c") for n in range(1, 8)),
                 DiffLine(A, None, 9, "d"))
        first, second = split_whole(FileDiff("f", (Hunk("@@ -1,7 +1,9 @@", 1, 1, lines),))).hunks
        self.assertEqual(len(first.lines), 5)  # the added line and 4 of the 7
        self.assertEqual((first.header, second.header), ("@@ -1,4 +1,5 @@", "@@ -5,3 +6,4 @@"))
