import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rzgitclient.core import git_commands
from rzgitclient.core.git_commands import CommandKind, GitCommand, run_command
from rzgitclient.git.repo import GitError, Repo
from tests.support import commit_file, git, make_repo


class RunCommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", "1", "first")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def status(self):
        return {f.path: f.index + f.worktree for f in self.repo.status()}

    def run_(self, kind, *paths):
        run_command(self.repo, GitCommand(kind, paths))

    def test_unstaging_a_rename_unstages_its_old_path_too(self):
        git(self.path, "mv", "a.txt", "b.txt")
        self.run_(CommandKind.UNSTAGE, "b.txt")
        self.assertEqual(self.status(), {"a.txt": " D", "b.txt": "??"})

    def test_discard_restores_tracked_files_and_trashes_untracked_ones(self):
        (self.path / "a.txt").write_text("changed")
        (self.path / "new.txt").write_text("new")
        trashed = []
        def trash(name):
            trashed.append(name)
            Path(name).unlink()
            return True, "/trash/" + Path(name).name
        with mock.patch.object(git_commands.QFile, "moveToTrash", side_effect=trash):
            self.run_(CommandKind.DISCARD, "a.txt", "new.txt")
        self.assertEqual(trashed, [str(self.repo.root / "new.txt")])
        self.assertEqual(self.status(), {})
        self.assertEqual((self.path / "a.txt").read_text(), "1")

    def test_a_trash_that_fails_keeps_the_file_and_is_an_error(self):
        (self.path / "new.txt").write_text("new")
        with mock.patch.object(git_commands.QFile, "moveToTrash", return_value=(False, "")):
            with self.assertRaises(GitError):
                self.run_(CommandKind.DISCARD, "new.txt")
        self.assertTrue((self.path / "new.txt").exists())

    def test_all_kinds_run(self):
        (self.path / "a.txt").write_text("2")
        self.run_(CommandKind.STAGE_ALL)
        self.assertEqual(self.status(), {"a.txt": "M "})
        self.run_(CommandKind.UNSTAGE_ALL)
        self.run_(CommandKind.STAGE, "a.txt")
        self.assertEqual(self.status(), {"a.txt": "M "})



class CommitCommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_commit_commits_the_staged_changes_with_the_message(self):
        (self.path / "a.txt").write_text("a")
        (self.path / "b.txt").write_text("b")
        git(self.path, "add", "a.txt")
        run_command(self.repo, GitCommand(CommandKind.COMMIT, message="Add a"))
        self.assertEqual(git(self.path, "log", "--format=%s"), "Add a\n")
        self.assertEqual({f.path: f.index + f.worktree for f in self.repo.status()},
                         {"b.txt": "??"})

    def test_commands_are_compared_by_identity(self):
        one, two = (GitCommand(CommandKind.COMMIT, message="Same") for _ in range(2))
        self.assertNotEqual(one, two)
        self.assertEqual(one, one)


if __name__ == "__main__":
    unittest.main()
