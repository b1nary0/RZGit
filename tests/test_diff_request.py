import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rzgitclient.core import diff_request
from rzgitclient.core.diff_request import DiffRequest, load_diff
from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.git.git_models import DiffSource
from rzgitclient.git.repo import Repo
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
