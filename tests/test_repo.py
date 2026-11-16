import shutil
import tempfile
import unittest
from pathlib import Path

from rzgitclient.git.git_models import DiffLine, DiffSource, LineKind
from rzgitclient.git.repo import GitError, Repo, parse_diff
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


class RepoDiffTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", "one\ntwo\nthree\n", "first")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, data):
        target = self.path / name
        if isinstance(data, bytes):
            target.write_bytes(data)
        else:
            target.write_text(data)

    def test_unstaged_change_with_line_numbers(self):
        self.write("a.txt", "one\n2\nthree\nfour\n")
        diff = self.repo.diff("a.txt", DiffSource.WORKTREE)
        self.assertEqual(diff.path, "a.txt")
        self.assertFalse(diff.binary or diff.too_large)
        (hunk,) = diff.hunks
        self.assertEqual((hunk.old_start, hunk.new_start), (1, 1))
        self.assertTrue(hunk.header.startswith("@@ -1,3 +1,4 @@"))
        self.assertEqual(hunk.lines, (
            DiffLine(LineKind.CONTEXT, 1, 1, "one"),
            DiffLine(LineKind.REMOVED, 2, None, "two"),
            DiffLine(LineKind.ADDED, None, 2, "2"),
            DiffLine(LineKind.CONTEXT, 3, 3, "three"),
            DiffLine(LineKind.ADDED, None, 4, "four"),
        ))

    def test_staged_and_unstaged_are_separate(self):
        self.write("a.txt", "one\ntwo\nthree\nstaged\n")
        git(self.path, "add", "a.txt")
        self.write("a.txt", "one\ntwo\nthree\nstaged\nunstaged\n")
        staged = self.repo.diff("a.txt", DiffSource.INDEX)
        unstaged = self.repo.diff("a.txt", DiffSource.WORKTREE)
        self.assertEqual([l.text for l in staged.hunks[0].lines if l.kind is LineKind.ADDED],
                         ["staged"])
        self.assertEqual([l.text for l in unstaged.hunks[0].lines if l.kind is LineKind.ADDED],
                         ["unstaged"])

    def test_no_changes_gives_no_hunks(self):
        diff = self.repo.diff("a.txt", DiffSource.WORKTREE)
        self.assertEqual(diff.hunks, ())
        self.assertFalse(diff.binary)

    def test_untracked_file_is_all_added(self):
        self.write("new.txt", "x\ny\n")
        diff = self.repo.diff("new.txt", DiffSource.UNTRACKED)  # git exits 1: not an error
        (hunk,) = diff.hunks
        self.assertEqual(hunk.lines, (DiffLine(LineKind.ADDED, None, 1, "x"),
                                      DiffLine(LineKind.ADDED, None, 2, "y")))

    def test_staged_rename(self):
        commit_file(self.path, "old.txt", "a\nb\nc\nd\ne\n", "add old")
        git(self.path, "mv", "old.txt", "new.txt")
        self.write("new.txt", "a\nb\nc\nd\nE\n")
        git(self.path, "add", "new.txt")
        diff = self.repo.diff("new.txt", DiffSource.INDEX, old_path="old.txt")
        self.assertEqual(diff.old_path, "old.txt")
        self.assertEqual(len(diff.hunks), 1)

    def test_binary_file(self):
        commit_file(self.path, "b.bin", "x", "add binary")
        self.write("b.bin", b"\x00\x01\x02 binary")
        diff = self.repo.diff("b.bin", DiffSource.WORKTREE)
        self.assertTrue(diff.binary)
        self.assertEqual(diff.hunks, ())

    def test_no_newline_at_end_of_file(self):
        self.write("a.txt", "one\ntwo\nthree")
        lines = self.repo.diff("a.txt", DiffSource.WORKTREE).hunks[0].lines
        self.assertEqual(lines[-2], DiffLine(LineKind.REMOVED, 3, None, "three"))
        self.assertEqual(lines[-1], DiffLine(LineKind.ADDED, None, 3, "three", no_newline=True))

    def test_crlf_line_ends_are_kept(self):
        self.write("a.txt", b"one\r\ntwo\nthree\n")
        lines = self.repo.diff("a.txt", DiffSource.WORKTREE).hunks[0].lines
        self.assertIn(DiffLine(LineKind.ADDED, None, 1, "one\r"), lines)
        self.assertIn(DiffLine(LineKind.REMOVED, 1, None, "one"), lines)

    def test_removed_lines_that_look_like_headers(self):
        commit_file(self.path, "h.txt", "-- x\n++ y\nkeep\n", "dashes")
        self.write("h.txt", "keep\n")
        lines = self.repo.diff("h.txt", DiffSource.WORKTREE).hunks[0].lines
        self.assertEqual([(l.kind, l.text) for l in lines], [
            (LineKind.REMOVED, "-- x"), (LineKind.REMOVED, "++ y"), (LineKind.CONTEXT, "keep")])

    def test_commit_diff_including_the_root_commit(self):
        commit_file(self.path, "a.txt", "one\ntwo\n", "second")
        root = git(self.path, "rev-list", "--max-parents=0", "HEAD").strip()
        first = self.repo.diff("a.txt", DiffSource.COMMIT, commit=root)
        self.assertEqual([l.kind for l in first.hunks[0].lines], [LineKind.ADDED] * 3)
        second = self.repo.diff("a.txt", DiffSource.COMMIT, commit="HEAD")
        self.assertEqual([l.text for l in second.hunks[0].lines if l.kind is LineKind.REMOVED],
                         ["three"])
        with self.assertRaises(ValueError):
            self.repo.diff("a.txt", DiffSource.COMMIT)

    def test_limits_skip_parsing(self):
        self.write("a.txt", "".join(f"{n}\n" for n in range(100)))
        diff = self.repo.diff("a.txt", DiffSource.WORKTREE, max_lines=50)
        self.assertTrue(diff.too_large)
        self.assertEqual(diff.hunks, ())
        self.assertGreater(diff.line_count, 100)
        self.assertTrue(self.repo.diff("a.txt", DiffSource.WORKTREE, max_bytes=100).too_large)
        self.assertFalse(self.repo.diff("a.txt", DiffSource.WORKTREE, max_lines=1000).too_large)

    def test_git_failure_raises(self):
        with self.assertRaises(GitError):
            self.repo.diff("missing.txt", DiffSource.UNTRACKED)


class ParseDiffTests(unittest.TestCase):
    def test_hunk_without_counts_and_invalid_utf8(self):
        raw = b"diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+caf\xe9\n"
        (hunk,) = parse_diff(raw, "f").hunks
        self.assertEqual(hunk.lines, (DiffLine(LineKind.REMOVED, 1, None, "old"),
                                      DiffLine(LineKind.ADDED, None, 1, "caf\ufffd")))

    def test_quoted_rename_source(self):
        raw = (b"diff --git a/x b/y\nsimilarity index 90%\n"
               b'rename from "sp\\303\\251cial.txt"\nrename to y\n')
        self.assertEqual(parse_diff(raw, "y").old_path, "sp\u00e9cial.txt")


class RepoCommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def status(self):
        return {f.path: f.index + f.worktree for f in self.repo.status()}

    def test_before_the_first_commit(self):
        (self.path / "a.txt").write_text("a")
        (self.path / "b.txt").write_text("b")
        self.repo.stage(["a.txt"])
        self.assertEqual(self.status(), {"a.txt": "A ", "b.txt": "??"})
        self.repo.unstage(["a.txt"])
        self.assertEqual(self.status(), {"a.txt": "??", "b.txt": "??"})
        self.repo.stage_all()
        self.assertEqual(self.status(), {"a.txt": "A ", "b.txt": "A "})
        self.repo.unstage_all()
        self.assertEqual(self.status(), {"a.txt": "??", "b.txt": "??"})
        self.repo.unstage_all()  # nothing staged: still fine

    def test_modified_deleted_and_untracked(self):
        commit_file(self.path, "m.txt", "1", "first")
        commit_file(self.path, "d.txt", "1", "second")
        (self.path / "m.txt").write_text("2")
        (self.path / "d.txt").unlink()
        (self.path / "u.txt").write_text("new")
        self.repo.stage(["m.txt", "d.txt", "u.txt"])
        self.assertEqual(self.status(), {"m.txt": "M ", "d.txt": "D ", "u.txt": "A "})
        self.repo.unstage(["m.txt", "d.txt", "u.txt"])
        self.assertEqual(self.status(), {"m.txt": " M", "d.txt": " D", "u.txt": "??"})
        self.repo.stage_all()
        self.repo.unstage_all()
        self.assertEqual(self.status(), {"m.txt": " M", "d.txt": " D", "u.txt": "??"})

    def test_restore_keeps_staged_changes(self):
        commit_file(self.path, "a.txt", "1", "first")
        commit_file(self.path, "d.txt", "1", "second")
        (self.path / "a.txt").write_text("staged")
        self.repo.stage(["a.txt"])
        (self.path / "a.txt").write_text("unstaged")
        (self.path / "d.txt").unlink()
        self.repo.restore(["a.txt", "d.txt"])
        self.assertEqual((self.path / "a.txt").read_text(), "staged")
        self.assertEqual((self.path / "d.txt").read_text(), "1")
        self.assertEqual(self.status(), {"a.txt": "M "})

    def test_paths_mean_themselves(self):
        commit_file(self.path, "base.txt", "1", "first")
        for name in (":x", "*.c", "b.c"):
            (self.path / name).write_text(name)
        self.repo.stage([":x", "*.c"])
        self.assertEqual(self.status(), {":x": "A ", "*.c": "A ", "b.c": "??"})

    def test_failure_raises(self):
        commit_file(self.path, "a.txt", "1", "first")
        with self.assertRaises(GitError):
            self.repo.stage(["no-such-file"])


if __name__ == "__main__":
    unittest.main()
