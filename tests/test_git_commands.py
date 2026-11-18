import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rzgit.core import git_commands
from rzgit.core.diff_patch import hunk_lines
from rzgit.core.git_commands import CommandKind, GitCommand, run_command
from rzgit.git.git_models import DiffSource
from rzgit.git.repo import GitError, Repo
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
        self.assertEqual(self.stashes(), ["On main: RZGit: before switching to other"])

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
        self.assertIn("RZGit: before switching to other", str(caught.exception))
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



class WorktreeCommandTests(unittest.TestCase):
    """The worktree commands' worker side (RZG-49, spec §4.1)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.path = make_repo(self.tmp / "repo")
        commit_file(self.path, "a.txt", "1", "first")
        git(self.path, "branch", "side")
        self.repo = Repo(self.path)

    def listed(self):
        return {w.path: w.branch for w in self.repo.worktrees()}

    def test_add_remove_and_prune(self):
        new, side = str(self.tmp / "new"), str(self.tmp / "side")
        run_command(self.repo, GitCommand(CommandKind.WORKTREE_ADD, path=new, branch="topic",
                                          start="side"))
        run_command(self.repo, GitCommand(CommandKind.WORKTREE_ADD, path=side, branch="side"))
        self.assertEqual(self.listed(), {str(self.path): "main", new: "topic", side: "side"})

        (self.tmp / "new" / "x.txt").write_text("x")
        with self.assertRaises(GitError):  # changes, and no force
            run_command(self.repo, GitCommand(CommandKind.WORKTREE_REMOVE, path=new))
        run_command(self.repo, GitCommand(CommandKind.WORKTREE_REMOVE, path=new, force=True))
        self.assertNotIn(new, self.listed())

        shutil.rmtree(side)
        run_command(self.repo, GitCommand(CommandKind.WORKTREE_PRUNE))
        self.assertEqual(self.listed(), {str(self.path): "main"})


if __name__ == "__main__":
    unittest.main()


class StashCommandTests(unittest.TestCase):
    """Stash Changes, Apply, Pop and Drop, by sha (RZG-36, spec §4.1)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", "1\n2\n3\n4\n5\n6\n", "first")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def run_(self, kind, **fields):
        return run_command(self.repo, GitCommand(kind, **fields))

    def status(self):
        return {f.path: f.index + f.worktree for f in self.repo.status()}

    def stash(self, message="saved", untracked=True):
        self.assertIsNone(self.run_(CommandKind.STASH_PUSH, message=message,
                                    untracked=untracked))
        return self.repo.stashes()[0]

    def test_stash_changes_with_or_without_untracked_files(self):
        (self.path / "a.txt").write_text("changed\n")
        (self.path / "new.txt").write_text("new")
        self.assertEqual(self.stash(untracked=False).message, "saved")
        self.assertEqual(self.status(), {"new.txt": "??"})
        self.assertTrue(self.stash("rest").untracked)
        self.assertEqual(self.status(), {})

    def test_nothing_to_stash_is_a_notice(self):
        (self.path / "new.txt").write_text("new")
        notice = self.run_(CommandKind.STASH_PUSH, message="m", untracked=False)
        self.assertEqual(notice, git_commands.NOTHING_TO_STASH)

    def test_apply_restores_staged_unstaged_and_untracked_and_keeps_the_stash(self):
        (self.path / "a.txt").write_text("staged\n")
        git(self.path, "add", "a.txt")
        (self.path / "new.txt").write_text("new")
        stash = self.stash()
        self.assertIsNone(self.run_(CommandKind.STASH_APPLY, sha=stash.sha))
        self.assertEqual(self.status(), {"a.txt": "M ", "new.txt": "??"})
        self.assertEqual(self.repo.stashes(), [stash])

    def test_pop_drops_it_by_sha_after_another_stash_was_made(self):
        (self.path / "a.txt").write_text("older\n")
        older = self.stash("older")
        (self.path / "b.txt").write_text("newer")
        newer = self.stash("newer")  # older is stash@{1} now
        self.run_(CommandKind.STASH_APPLY, sha=older.sha, pop=True)
        self.assertEqual((self.path / "a.txt").read_text(), "older\n")
        self.assertEqual([s.sha for s in self.repo.stashes()], [newer.sha])

    def test_drop_by_sha(self):
        (self.path / "a.txt").write_text("older\n")
        older = self.stash("older")
        (self.path / "a.txt").write_text("newer\n")
        newer = self.stash("newer")
        self.run_(CommandKind.STASH_DROP, sha=older.sha)
        self.assertEqual([s.sha for s in self.repo.stashes()], [newer.sha])
        with self.assertRaises(GitError) as caught:
            self.run_(CommandKind.STASH_DROP, sha=older.sha)
        self.assertIn("gone", str(caught.exception))

    def test_apply_needs_a_clean_working_tree(self):
        (self.path / "a.txt").write_text("saved\n")
        stash = self.stash()
        (self.path / "new.txt").write_text("in the way")  # even untracked
        with self.assertRaises(GitError) as caught:
            self.run_(CommandKind.STASH_APPLY, sha=stash.sha, pop=True)
        self.assertEqual(str(caught.exception), git_commands.NOT_CLEAN)
        self.assertEqual(self.status(), {"new.txt": "??"})
        self.assertEqual(self.repo.stashes(), [stash])

    def test_a_conflict_is_rolled_back_and_the_stash_kept(self):
        (self.path / "a.txt").write_text("stashed\n")
        (self.path / "new.txt").write_text("restored, then cleaned")
        (self.path / "dir").mkdir()
        (self.path / "dir" / "deep.txt").write_text("also")
        stash = self.stash()
        commit_file(self.path, "a.txt", "committed\n", "conflicting")
        commit_file(self.path, ".gitignore", "*.log\n", "ignore")
        (self.path / "keep.log").write_text("ignored, so kept")
        with self.assertRaises(GitError) as caught:
            self.run_(CommandKind.STASH_APPLY, sha=stash.sha, pop=True)
        self.assertIn("CONFLICT", str(caught.exception))
        self.assertIn("the stash is kept", str(caught.exception))
        self.assertEqual(self.status(), {})  # as before: nothing half-applied
        self.assertEqual((self.path / "a.txt").read_text(), "committed\n")
        self.assertFalse((self.path / "new.txt").exists())
        self.assertTrue((self.path / "keep.log").exists())
        self.assertEqual(self.repo.stashes(), [stash])

    def test_staged_changes_that_dont_fit_as_staged_come_back_unstaged(self):
        # The staged change no longer applies as a patch (its context moved), but
        # merges: git refuses --index ("conflicts in index").
        (self.path / "a.txt").write_text("ONE\n2\n3\n4\n5\n6\n")
        git(self.path, "add", "a.txt")
        stash = self.stash()
        commit_file(self.path, "a.txt", "1\n2\nTHREE\n4\n5\n6\n", "three")
        notice = self.run_(CommandKind.STASH_APPLY, sha=stash.sha, pop=True)
        self.assertEqual(notice, git_commands.UNSTAGED)
        self.assertEqual((self.path / "a.txt").read_text(), "ONE\n2\nTHREE\n4\n5\n6\n")
        self.assertEqual(self.status(), {"a.txt": " M"})
        self.assertEqual(self.repo.stashes(), [])
