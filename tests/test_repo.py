import subprocess
import tempfile
import unittest
from pathlib import Path

from rzgitclient.git.repo import GitError, Repo


def sh(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class RepoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        sh(self.path, "init", "-b", "main")
        sh(self.path, "config", "user.name", "Test User")
        sh(self.path, "config", "user.email", "test@example.com")
        sh(self.path, "config", "commit.gpgsign", "false")

    def tearDown(self):
        self._tmp.cleanup()

    def commit_file(self, name, content, message):
        (self.path / name).write_text(content)
        sh(self.path, "add", name)
        sh(self.path, "commit", "-m", message)

    def test_not_a_repo(self):
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(GitError):
                Repo(other)

    def test_empty_repo(self):
        repo = Repo(self.path)
        self.assertFalse(repo.has_commits())
        self.assertEqual(repo.log(), [])
        self.assertEqual(repo.branches(), [])
        self.assertEqual(repo.current_branch(), "main")

    def test_opens_from_subdirectory(self):
        sub = self.path / "a" / "b"
        sub.mkdir(parents=True)
        self.assertEqual(Repo(sub).root.resolve(), self.path.resolve())

    def test_log_and_branches(self):
        self.commit_file("a.txt", "1", "first")
        self.commit_file("a.txt", "2", "second: with | odd chars")
        sh(self.path, "branch", "feature")
        repo = Repo(self.path)

        log = repo.log()
        self.assertEqual([c.subject for c in log], ["second: with | odd chars", "first"])
        self.assertEqual(log[0].author, "Test User")
        self.assertEqual(len(log[0].sha), 40)

        branches = {b.name: b.is_current for b in repo.branches()}
        self.assertEqual(branches, {"main": True, "feature": False})

    def test_status(self):
        self.commit_file("tracked.txt", "x", "init")
        self.commit_file("old.txt", "rename me", "add old")
        (self.path / "tracked.txt").write_text("changed")
        (self.path / "new file.txt").write_text("hi")
        sh(self.path, "mv", "old.txt", "renamed.txt")

        status = {f.path: f for f in Repo(self.path).status()}

        self.assertTrue(status["tracked.txt"].has_unstaged)
        self.assertFalse(status["tracked.txt"].is_staged)
        self.assertTrue(status["new file.txt"].is_untracked)
        self.assertEqual(status["renamed.txt"].index, "R")
        self.assertEqual(status["renamed.txt"].orig_path, "old.txt")


if __name__ == "__main__":
    unittest.main()
