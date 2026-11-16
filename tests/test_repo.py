import shutil
import tempfile
import unittest
from pathlib import Path

from rzgitclient.git.repo import GitError, Repo
from tests.support import commit_file, git, make_repo


class RepoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")

    def tearDown(self):
        self._tmp.cleanup()

    def test_not_a_repo(self):
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(GitError):
                Repo(other)

    def test_empty_repo(self):
        repo = Repo(self.path)
        self.assertFalse(repo.has_commits())
        self.assertIsNone(repo.head_sha())
        self.assertEqual(repo.commit_count(), 0)
        self.assertEqual(repo.log(), [])
        self.assertEqual(repo.branches(), [])
        self.assertEqual(repo.current_branch(), "main")

    def test_opens_from_subdirectory(self):
        sub = self.path / "a" / "b"
        sub.mkdir(parents=True)
        self.assertEqual(Repo(sub).root.resolve(), self.path.resolve())

    def test_log_and_branches(self):
        commit_file(self.path, "a.txt", "1", "first")
        commit_file(self.path, "a.txt", "2", "second: with | odd chars")
        git(self.path, "branch", "feature")
        repo = Repo(self.path)

        log = repo.log()
        self.assertEqual([c.subject for c in log], ["second: with | odd chars", "first"])
        self.assertEqual(log[0].author, "Test User")
        self.assertEqual(len(log[0].sha), 40)

        branches = {b.name: b.is_current for b in repo.branches()}
        self.assertEqual(branches, {"main": True, "feature": False})

    def test_log_pages_and_head_sha(self):
        for n in range(1, 6):
            commit_file(self.path, "a.txt", str(n), f"c{n}")
        repo = Repo(self.path)
        self.assertEqual([c.subject for c in repo.log(2)], ["c5", "c4"])
        self.assertEqual([c.subject for c in repo.log(2, skip=2)], ["c3", "c2"])
        self.assertEqual([c.subject for c in repo.log(2, skip=4)], ["c1"])
        self.assertEqual(repo.log(2, skip=5), [])
        self.assertEqual(repo.head_sha(), repo.log(1)[0].sha)
        self.assertEqual(repo.commit_count(), 5)

    def test_detached_head(self):
        commit_file(self.path, "a.txt", "1", "first")
        git(self.path, "checkout", "-q", "--detach")
        self.assertIsNone(Repo(self.path).current_branch())

    def test_deleted_folder_raises_git_error(self):
        commit_file(self.path, "a.txt", "1", "first")
        repo = Repo(self.path)
        shutil.rmtree(self.path)
        # Every call fails with GitError, never an OSError and never a
        # silent "detached"/"no commits" answer.
        for call in (repo.current_branch, repo.has_commits, repo.head_sha,
                     repo.branches, repo.log, repo.status, repo.commit_count):
            with self.subTest(call.__name__), self.assertRaises(GitError) as ctx:
                call()
            self.assertIsNone(ctx.exception.returncode)

    def test_status(self):
        commit_file(self.path, "tracked.txt", "x", "init")
        commit_file(self.path, "old.txt", "rename me", "add old")
        (self.path / "tracked.txt").write_text("changed")
        (self.path / "new file.txt").write_text("hi")
        git(self.path, "mv", "old.txt", "renamed.txt")

        status = {f.path: f for f in Repo(self.path).status()}

        self.assertTrue(status["tracked.txt"].has_unstaged)
        self.assertFalse(status["tracked.txt"].is_staged)
        self.assertTrue(status["new file.txt"].is_untracked)
        self.assertEqual(status["renamed.txt"].index, "R")
        self.assertEqual(status["renamed.txt"].orig_path, "old.txt")


if __name__ == "__main__":
    unittest.main()
