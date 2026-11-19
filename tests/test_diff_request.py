import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rzgit.core import diff_request
from rzgit.core.diff_patch import build_patch, hunk_lines
from rzgit.core.diff_request import DiffRequest, load_diff, split_hunks
from rzgit.core.selection import FileSelection, FileSource
from rzgit.git.git_models import DiffLine, DiffSource, FileDiff, Hunk, LineKind
from rzgit.git.repo import Repo
from rzgit.ui.panels.diff_rows import whole_file
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
        self.assertIs(split_hunks(diff), diff)  # a run of 6 isn't cut

    def test_a_run_of_seven_is_cut_with_the_larger_half_first(self):
        C, A = LineKind.CONTEXT, LineKind.ADDED
        lines = (DiffLine(A, None, 1, "b"), *(DiffLine(C, n, n + 1, "c") for n in range(1, 8)),
                 DiffLine(A, None, 9, "d"))
        first, second = split_hunks(FileDiff("f", (Hunk("@@ -1,7 +1,9 @@", 1, 1, lines),))).hunks
        self.assertEqual(len(first.lines), 5)  # the added line and 4 of the 7
        self.assertEqual((first.header, second.header), ("@@ -1,4 +1,5 @@", "@@ -5,3 +6,4 @@"))


class ContextLinesTests(unittest.TestCase):
    """RZG-97: wider context, with git's hunks at 3 lines and each line once."""

    N = (3, 4, 10, 50, 1000)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", _lines(300), "first")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, context, source=FileSource.WORKTREE, commit=None):
        request = DiffRequest(FileSelection("a.txt", source, commit), context=context)
        return load_diff(self.repo, request, tuple(self.repo.status()))

    def changes(self, diff):
        """Each hunk's changed lines, as (kind, old number, new number)."""
        return [[(l.kind, l.old_no, l.new_no) for l in h.lines if l.kind is not LineKind.CONTEXT]
                for h in diff.hunks]

    def context_runs(self, hunk):
        """The unchanged lines above and below the hunk's changes."""
        kinds = [l.kind is LineKind.CONTEXT for l in hunk.lines]
        above = kinds.index(False)
        below = kinds[::-1].index(False)
        return above, below

    def test_the_hunks_are_git_s_at_3_and_each_line_shows_once(self):
        # Gaps of 6 (one hunk at 3), 7, 9, 20 and 150 unchanged lines; changes at both ends.
        changed = (1, 8, 16, 26, 47, 198, 298)
        (self.path / "a.txt").write_text(_lines(300, changed=changed))
        at_three = self.changes(self.load(3))
        self.assertEqual(len(at_three), 6)  # the changes 6 apart share a hunk
        for n in self.N:
            with self.subTest(n=n):
                diff = self.load(n)
                self.assertEqual(self.changes(diff), at_three)
                new = [l.new_no for h in diff.hunks for l in h.lines if l.new_no]
                self.assertEqual(new, sorted(set(new)))  # in order, none twice
                for hunk in diff.hunks:
                    old = [l.old_no for l in hunk.lines if l.old_no is not None]
                    news = [l.new_no for l in hunk.lines if l.new_no is not None]
                    self.assertTrue(hunk.header.startswith(  # git may add a function's name
                        f"@@ -{old[0]},{len(old)} +{news[0]},{len(news)} @@"), hunk.header)
                    self.assertLessEqual(max(self.context_runs(hunk)), n)

    def test_overlapping_context_is_split_in_the_middle_far_apart_gets_n_each(self):
        # Changes at lines 10 and 20 (9 unchanged between), and at 200 (179 between).
        (self.path / "a.txt").write_text(_lines(300, changed=(10, 20, 200)))
        first, second, third = self.load(50).hunks
        self.assertEqual(self.context_runs(first), (10, 5))  # line 0 to 9 above: the file's start
        self.assertEqual(self.context_runs(second), (4, 50))  # the larger half (5) goes above
        self.assertEqual(self.context_runs(third), (50, 50))
        # At 5 git merges the 9 lines between (2 × 5 ≥ 9) and they are split; at 4 it
        # doesn't, and the middle line isn't shown.
        first, second, _ = self.load(5).hunks
        self.assertEqual((self.context_runs(first)[1], self.context_runs(second)[0]), (5, 4))
        first, second, _ = self.load(4).hunks
        self.assertEqual((self.context_runs(first)[1], self.context_runs(second)[0]), (4, 4))
        self.assertEqual(second.lines[0].new_no - first.lines[-1].new_no, 2)

    def test_touching_context_is_not_cut(self):
        # 8 unchanged between: at 4 git's two hunks just meet, 4 and 4.
        (self.path / "a.txt").write_text(_lines(300, changed=(100, 109)))
        first, second = self.load(4).hunks
        self.assertEqual((self.context_runs(first)[1], self.context_runs(second)[0]), (4, 4))
        self.assertEqual(first.lines[-1].new_no + 1, second.lines[0].new_no)

    def test_context_is_read_wider_for_every_source(self):
        (self.path / "a.txt").write_text(_lines(300, changed=(150,)))
        git(self.path, "add", "a.txt")
        self.assertEqual(len(self.load(20, FileSource.INDEX).hunks[0].lines), 42)
        git(self.path, "commit", "-q", "-m", "second")
        self.assertEqual(len(self.load(20, FileSource.COMMIT, "HEAD").hunks[0].lines), 42)

    def test_at_3_git_s_diff_is_used_as_it_is(self):
        (self.path / "a.txt").write_text(_lines(300, changed=(150,)))
        with mock.patch.object(Repo, "diff", wraps=self.repo.diff) as spy:
            self.load(3)
        self.assertIsNone(spy.call_args.kwargs.get("context"))

    def test_stage_unstage_and_discard_at_50_are_as_at_3(self):
        changed = (20, 26, 40, 120, 200)  # 20 and 26 share a hunk at 3
        (self.path / "a.txt").write_text(_lines(300, changed=changed))

        def staged_after(n, lines_of):
            git(self.path, "reset", "-q")
            diff = self.load(n)
            self.repo.apply(build_patch(diff, lines_of(diff)), cached=True)
            return git(self.path, "diff", "--cached")

        def one_line(diff, hunk=1):  # a hunk's first changed line: a removed one
            return frozenset([sorted(hunk_lines(diff, hunk))[0]])

        for lines_of in (lambda d: hunk_lines(d, 1), one_line):
            self.assertEqual(staged_after(50, lines_of), staged_after(3, lines_of))
        # Unstage a hunk of the staged diff at 50.
        git(self.path, "add", "a.txt")
        staged = self.load(50, FileSource.INDEX)
        self.assertEqual(len(staged.hunks), 4)
        self.repo.apply(build_patch(staged, hunk_lines(staged, 2), reverse=True),
                        cached=True, reverse=True)
        left = git(self.path, "diff", "--cached")
        self.assertNotIn("+LINE 120", left)
        self.assertIn("+LINE 200", left)
        # Discard a hunk, then some lines, of the unstaged diff at 50.
        git(self.path, "reset", "-q")
        diff = self.load(50)
        self.repo.apply(build_patch(diff, hunk_lines(diff, 3), reverse=True), reverse=True)
        self.assertNotIn("LINE 200", (self.path / "a.txt").read_text())
        diff = self.load(50)
        self.repo.apply(build_patch(diff, one_line(diff, 2), reverse=True), reverse=True)
        text = (self.path / "a.txt").read_text()
        self.assertIn("line 120\n", text)  # the removed line is back
        self.assertIn("LINE 120\n", text)  # the added one stays
