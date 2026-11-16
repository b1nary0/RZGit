import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rzgitclient.core import git_commands
from rzgitclient.core.diff_patch import hunk_lines
from rzgitclient.core.git_commands import CommandKind, GitCommand, run_command
from rzgitclient.git.git_models import DiffSource
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



class LinesCommandTests(unittest.TestCase):
    """Stage, Unstage and Discard Lines (spec §4.1); the patches themselves are
    tested in test_diff_patch."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", "a\nb\nc\n", "first")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def run_lines(self, kind, path, source, lines=None):
        diff = self.repo.diff(path, source)
        if lines is None:
            lines = hunk_lines(diff, 0)
        run_command(self.repo, GitCommand(kind, (path,), diff=diff, lines=frozenset(lines)))

    def index(self, path):
        return git(self.path, "show", f":{path}")

    def status(self):
        return {f.path: f.index + f.worktree for f in self.repo.status()}

    def test_stage_and_unstage_a_hunk(self):
        (self.path / "a.txt").write_text("a\nB\nc\n")
        self.run_lines(CommandKind.STAGE_LINES, "a.txt", DiffSource.WORKTREE)
        self.assertEqual(self.index("a.txt"), "a\nB\nc\n")
        self.run_lines(CommandKind.UNSTAGE_LINES, "a.txt", DiffSource.INDEX)
        self.assertEqual(self.index("a.txt"), "a\nb\nc\n")
        self.assertEqual((self.path / "a.txt").read_text(), "a\nB\nc\n")

    def test_discard_lines_changes_the_working_tree_only(self):
        (self.path / "a.txt").write_text("a\nB\nc\n")
        self.run_lines(CommandKind.DISCARD_LINES, "a.txt", DiffSource.WORKTREE)
        self.assertEqual((self.path / "a.txt").read_text(), "a\nb\nc\n")

    def test_lines_of_an_untracked_file_are_a_new_file_patch(self):
        (self.path / "new.sh").write_text("one\ntwo\n")
        (self.path / "new.sh").chmod(0o755)
        diff = self.repo.diff("new.sh", DiffSource.UNTRACKED)
        first = {(0, i) for i, line in enumerate(diff.hunks[0].lines) if line.text == "one"}
        run_command(self.repo, GitCommand(CommandKind.STAGE_LINES, ("new.sh",), diff=diff,
                                          lines=frozenset(first)))
        self.assertEqual(self.index("new.sh"), "one\n")
        self.assertEqual(git(self.path, "ls-files", "-s", "new.sh").split()[0], "100755")

    def test_unstaging_a_new_files_last_lines_makes_it_untracked_again(self):
        (self.path / "new.txt").write_text("one\ntwo\n")
        diff = self.repo.diff("new.txt", DiffSource.UNTRACKED)
        first = frozenset({(0, 0)})
        run_command(self.repo, GitCommand(CommandKind.STAGE_LINES, ("new.txt",), diff=diff,
                                          lines=first))
        self.assertEqual(self.status()["new.txt"], "AM")
        staged = self.repo.diff("new.txt", DiffSource.INDEX)
        run_command(self.repo, GitCommand(CommandKind.UNSTAGE_LINES, ("new.txt",), diff=staged,
                                          lines=hunk_lines(staged, 0)))
        self.assertEqual(self.status()["new.txt"], "??")
        self.assertEqual((self.path / "new.txt").read_text(), "one\ntwo\n")

    def test_unstaging_some_lines_of_a_new_file_keeps_it_staged(self):
        (self.path / "new.txt").write_text("one\ntwo\n")
        diff = self.repo.diff("new.txt", DiffSource.UNTRACKED)
        run_command(self.repo, GitCommand(CommandKind.STAGE_LINES, ("new.txt",), diff=diff,
                                          lines=hunk_lines(diff, 0)))
        staged = self.repo.diff("new.txt", DiffSource.INDEX)
        run_command(self.repo, GitCommand(CommandKind.UNSTAGE_LINES, ("new.txt",), diff=staged,
                                          lines=frozenset({(0, 1)})))
        self.assertEqual(self.status()["new.txt"], "AM")
        self.assertEqual(self.index("new.txt"), "one\n")

    def test_unstaging_lines_never_untracks_a_file_head_has(self):
        (self.path / "a.txt").write_text("")
        git(self.path, "add", "a.txt")  # staged: every line removed
        staged = self.repo.diff("a.txt", DiffSource.INDEX)
        run_command(self.repo, GitCommand(CommandKind.UNSTAGE_LINES, ("a.txt",), diff=staged,
                                          lines=frozenset({(0, 0)})))
        self.assertIn("a.txt", git(self.path, "ls-files"))

    def test_lines_of_an_untracked_file_cant_be_discarded(self):
        (self.path / "new.txt").write_text("one\n")
        with self.assertRaises(GitError):
            self.run_lines(CommandKind.DISCARD_LINES, "new.txt", DiffSource.UNTRACKED)
        self.assertTrue((self.path / "new.txt").exists())

    def test_a_stale_diff_is_an_error_and_changes_nothing(self):
        (self.path / "a.txt").write_text("a\nB\nc\n")
        diff = self.repo.diff("a.txt", DiffSource.WORKTREE)
        (self.path / "a.txt").write_text("A\nb\nc\n")
        git(self.path, "add", "a.txt")
        with self.assertRaises(GitError):
            run_command(self.repo, GitCommand(CommandKind.STAGE_LINES, ("a.txt",), diff=diff,
                                              lines=hunk_lines(diff, 0)))
        self.assertEqual(self.index("a.txt"), "A\nb\nc\n")



class SwitchCommandTests(unittest.TestCase):
    """Switch, with and without a stash (spec §4.1)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", "1\n", "first")
        git(self.path, "branch", "other")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def switch(self, stash):
        run_command(self.repo, GitCommand(CommandKind.SWITCH, branch="other", stash=stash))

    def stashes(self):
        return git(self.path, "stash", "list", "--format=%gs").splitlines()

    def test_stash_and_switch_takes_every_change_along(self):
        (self.path / "a.txt").write_text("2\n")
        (self.path / "new.txt").write_text("new")
        self.switch(stash=True)
        self.assertEqual(self.repo.current_branch(), "other")
        self.assertEqual(self.repo.status(), [])
        self.assertEqual(self.stashes(), ["On main: RZGitClient: before switching to other"])

    def test_switch_without_a_stash_carries_changes_over(self):
        (self.path / "new.txt").write_text("new")
        self.switch(stash=False)
        self.assertEqual(self.repo.current_branch(), "other")
        self.assertEqual([f.path for f in self.repo.status()], ["new.txt"])
        self.assertEqual(self.stashes(), [])

    def test_a_failed_switch_pops_the_stash_back(self):
        (self.path / "a.txt").write_text("staged\n")
        git(self.path, "add", "a.txt")
        (self.path / "new.txt").write_text("new")
        with mock.patch.object(Repo, "switch", side_effect=GitError("refused", 1)):
            with self.assertRaises(GitError):
                self.switch(stash=True)
        self.assertEqual(self.repo.current_branch(), "main")
        status = {f.path: f.index + f.worktree for f in self.repo.status()}
        self.assertEqual(status, {"a.txt": "M ", "new.txt": "??"})
        self.assertEqual(self.stashes(), [])

    def test_a_failed_switch_with_nothing_stashed_pops_nothing(self):
        (self.path / "b.txt").write_text("older stash")
        git(self.path, "stash", "push", "-u", "-m", "older")
        with mock.patch.object(Repo, "switch", side_effect=GitError("refused", 1)):
            with self.assertRaises(GitError):
                self.switch(stash=True)
        self.assertEqual(self.stashes(), ["On main: older"])  # not popped

    def test_if_the_pop_fails_too_the_error_names_the_stash(self):
        (self.path / "a.txt").write_text("2\n")
        with mock.patch.object(Repo, "switch", side_effect=GitError("refused", 1)), \
                mock.patch.object(Repo, "stash_pop", side_effect=GitError("pop failed", 1)):
            with self.assertRaises(GitError) as caught:
                self.switch(stash=True)
        self.assertIn("RZGitClient: before switching to other", str(caught.exception))
        self.assertIn("pop failed", str(caught.exception))

    def test_branch_commands_reach_the_repo(self):
        for kind, kwargs, call, args, options in (
            (CommandKind.CREATE_BRANCH, dict(branch="main", new_name="n", switch=True),
             "create_branch", ("n", "main"), {"switch": True}),
            (CommandKind.RENAME_BRANCH, dict(branch="a", new_name="b"),
             "rename_branch", ("a", "b"), {}),
            (CommandKind.DELETE_BRANCH, dict(branch="a", force=True),
             "delete_branch", ("a",), {"force": True}),
            (CommandKind.MERGE, dict(branch="a"), "merge", ("a",), {}),
            (CommandKind.REBASE, dict(branch="a"), "rebase", ("a",), {}),
            (CommandKind.AMEND, dict(message="m"), "amend", ("m",), {}),
        ):
            with self.subTest(kind), mock.patch.object(Repo, call) as method:
                run_command(self.repo, GitCommand(kind, **kwargs))
                method.assert_called_once_with(*args, **options)


if __name__ == "__main__":
    unittest.main()
