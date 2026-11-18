import shutil
import tempfile
import unittest
from pathlib import Path

from rzgit.git.git_models import (Commit, DiffLine, DiffSource, FileStatus, HistoryOrder,
                                        LineKind, Ref)
from rzgit.git.git_models import Worktree, WorktreeState
from rzgit.git.repo import (GitError, Repo, canonical_path, parse_diff, parse_worktree_status,
                                  parse_worktrees, worktree_state)
from tests.support import commit_file, git, make_branched_repo, make_repo


class CanonicalPathTests(unittest.TestCase):
    """One path per folder, whichever way it was reached (spec §6.3, RZG-29)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()

    def test_symlinks_are_resolved(self):
        real = self.tmp / "real"
        real.mkdir()
        (self.tmp / "link").symlink_to(real)
        self.assertEqual(canonical_path(self.tmp / "link"), real)
        self.assertEqual(canonical_path(str(self.tmp / "link" / ".." / "real")), real)

    def test_home_and_relative_paths_become_absolute(self):
        self.assertEqual(canonical_path("~"), Path.home().resolve())
        self.assertEqual(canonical_path("."), Path.cwd().resolve())

    def test_a_missing_folder_still_has_one(self):
        self.assertEqual(canonical_path(self.tmp / "gone" / "x"), self.tmp / "gone" / "x")

    def test_a_repo_reached_through_a_link_has_its_real_root(self):
        real = make_repo(self.tmp / "repo")
        (self.tmp / "link").symlink_to(real)
        self.assertEqual(Repo(self.tmp / "link").root, real)
        self.assertEqual(Repo(self.tmp / "link").root, Repo(real).root)

    def test_a_non_ascii_folder_name(self):
        real = make_repo(self.tmp / "café 日本")
        commit_file(real, "a.txt", "1", "first")
        repo = Repo(real)
        self.assertEqual(repo.root, real)
        self.assertEqual(repo.name, "café 日本")
        self.assertEqual(len(repo.log()), 1)


class RepoWorktreeNameTests(unittest.TestCase):
    """A window's title and the last repository, for linked worktrees (spec §6.3, RZG-47)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.main = make_repo(self.tmp / "main-repo")
        commit_file(self.main, "a.txt", "1\n", "first")

    def test_the_main_worktree_is_its_own_main_root(self):
        repo = Repo(self.main)
        self.assertEqual(repo.worktree_name, "main-repo")
        self.assertEqual(repo.main_root, self.main)
        sub = self.main / "sub"
        sub.mkdir()
        self.assertEqual(Repo(sub).main_root, self.main)  # opened from a subfolder

    def test_a_linked_worktree_has_gits_name_and_the_main_root(self):
        git(self.main, "worktree", "add", "-b", "lane", str(self.tmp / "lanes" / "lane-1"))
        # git names it after its folder, made unique: a second "lane-1" folder is "lane-11".
        git(self.main, "worktree", "add", "-b", "lane2", str(self.tmp / "other" / "lane-1"))
        first, second = Repo(self.tmp / "lanes" / "lane-1"), Repo(self.tmp / "other" / "lane-1")
        self.assertEqual(first.root, self.tmp / "lanes" / "lane-1")
        self.assertEqual(first.worktree_name, "lane-1")
        self.assertEqual(second.worktree_name, "lane-11")
        self.assertEqual(first.main_root, self.main)
        self.assertEqual(second.main_root, self.main)

    def test_a_linked_worktree_reached_through_a_link(self):
        git(self.main, "worktree", "add", "-b", "lane", str(self.tmp / "lane"))
        (self.tmp / "link").symlink_to(self.main)
        (self.tmp / "wt-link").symlink_to(self.tmp / "lane")
        self.assertEqual(Repo(self.tmp / "wt-link").main_root, self.main)
        self.assertEqual(Repo(self.tmp / "link").main_root, self.main)


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

    def test_log_reads_parents_first_parent_first(self):
        repo = Repo(make_branched_repo(Path(self._tmp.name) / "branched"))
        log = {c.subject: c for c in repo.log()}
        self.assertEqual(log["merge"].parents, (log["m1"].sha, log["s2"].sha))
        self.assertEqual(log["s1"].parents, (log["a"].sha,))
        self.assertEqual(log["a"].parents, ())

    def test_log_in_date_or_topological_order(self):
        repo = Repo(make_branched_repo(Path(self._tmp.name) / "branched"))
        date = [c.subject for c in repo.log()]
        self.assertEqual(date, ["merge", "s2", "m1", "s1", "a"])  # the default
        self.assertEqual([c.subject for c in repo.log(order=HistoryOrder.DATE)], date)
        topo = [c.subject for c in repo.log(order=HistoryOrder.TOPO)]
        self.assertNotEqual(topo, date)
        self.assertEqual(abs(topo.index("s1") - topo.index("s2")), 1)  # a branch kept together
        self.assertEqual([c.subject for c in repo.log(2, skip=2, order=HistoryOrder.TOPO)],
                         topo[2:4])

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


class RepoCommitFilesTests(unittest.TestCase):
    """A commit's changed files (spec §4.1)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def head(self):
        return git(self.path, "rev-parse", "HEAD").strip()

    def test_the_first_commit_is_compared_with_nothing(self):
        commit_file(self.path, "a.txt", "1\n", "first")
        self.assertEqual(self.repo.commit_files(self.head()), [FileStatus("A", " ", "a.txt")])

    def test_modified_deleted_added_and_renamed(self):
        commit_file(self.path, "a.txt", "1\n", "first")
        commit_file(self.path, "gone.txt", "x\n", "second")
        commit_file(self.path, "old name.txt", "keep\n" * 20, "third")
        (self.path / "a.txt").write_text("2\n")
        git(self.path, "rm", "-q", "gone.txt")
        git(self.path, "mv", "old name.txt", "new name.txt")
        (self.path / "n.txt").write_text("n\n")
        git(self.path, "add", "-A")
        git(self.path, "commit", "-m", "many")
        self.assertEqual(sorted(self.repo.commit_files(self.head()), key=lambda f: f.path), [
            FileStatus("M", " ", "a.txt"),
            FileStatus("D", " ", "gone.txt"),
            FileStatus("A", " ", "n.txt"),
            FileStatus("R", " ", "new name.txt", "old name.txt"),
        ])

    def test_a_merge_is_compared_with_its_first_parent(self):
        commit_file(self.path, "a.txt", "1\n", "first")
        git(self.path, "checkout", "-q", "-b", "side")
        commit_file(self.path, "side.txt", "s\n", "on side")
        git(self.path, "checkout", "-q", "main")
        commit_file(self.path, "main.txt", "m\n", "on main")
        git(self.path, "merge", "-q", "--no-edit", "side")
        self.assertEqual(self.repo.commit_files(self.head()), [FileStatus("A", " ", "side.txt")])

    def test_an_empty_commit_changed_nothing(self):
        commit_file(self.path, "a.txt", "1\n", "first")
        git(self.path, "commit", "-q", "--allow-empty", "-m", "empty")
        self.assertEqual(self.repo.commit_files(self.head()), [])


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


class RepoCommitDetailsTests(unittest.TestCase):
    """One commit in full (RZG-26, spec §4.1)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def sha(self, rev="HEAD"):
        return git(self.path, "rev-parse", rev).strip()

    def test_the_first_commit(self):
        commit_file(self.path, "a.txt", "1", "First line\n\nThe body,\nover two lines.\n")
        details = self.repo.commit_details(self.sha())
        self.assertEqual(details.sha, self.sha())
        self.assertEqual(details.parents, ())
        self.assertEqual((details.author, details.author_email), ("Test User", "test@example.com"))
        self.assertEqual(details.committer, "Test User")
        self.assertIsNotNone(details.author_date.tzinfo)
        self.assertEqual(details.summary, "First line")
        self.assertEqual(details.body, "The body,\nover two lines.")
        self.assertEqual(details.refs, ("HEAD -> main",))

    def test_parents_refs_and_a_different_committer(self):
        commit_file(self.path, "a.txt", "1", "one")
        git(self.path, "tag", "v1.0")
        git(self.path, "branch", "side")
        git(self.path, "-c", "user.name=Someone Else", "-c", "user.email=else@example.com",
            "commit", "--allow-empty", "-m", "two", "--author", "Author <a@example.com>")
        details = self.repo.commit_details(self.sha())
        self.assertEqual(details.parents, (self.sha("HEAD~1"),))
        self.assertEqual((details.author, details.author_email), ("Author", "a@example.com"))
        self.assertEqual((details.committer, details.committer_email),
                         ("Someone Else", "else@example.com"))
        self.assertEqual(details.body, "")
        old = self.repo.commit_details(self.sha("HEAD~1"))
        self.assertEqual(set(old.refs), {"tag: v1.0", "side"})

    def test_a_merge_has_both_parents(self):
        commit_file(self.path, "a.txt", "1", "one")
        git(self.path, "switch", "-q", "-c", "side")
        commit_file(self.path, "b.txt", "2", "side")
        git(self.path, "switch", "-q", "main")
        commit_file(self.path, "c.txt", "3", "main")
        git(self.path, "merge", "-q", "--no-edit", "side")
        details = self.repo.commit_details(self.sha())
        self.assertEqual(details.parents, (self.sha("HEAD^1"), self.sha("HEAD^2")))
        self.assertTrue(details.summary.startswith("Merge branch"))

    def test_an_unknown_commit_raises(self):
        commit_file(self.path, "a.txt", "1", "one")
        with self.assertRaises(GitError):
            self.repo.commit_details("0" * 40)



def add_hook(repo: Path, name: str, script: str) -> None:
    hook = repo / ".git" / "hooks" / name
    hook.write_text("#!/bin/sh\n" + script)
    hook.chmod(0o755)


class RepoRefsTests(unittest.TestCase):
    """The branches and tags, for History's labels (RZG-77, spec §4.1)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def sha(self, rev="HEAD"):
        return git(self.path, "rev-parse", rev).strip()

    def test_no_commits_no_refs(self):
        self.assertEqual(self.repo.refs(), [])

    def test_branches_remotes_and_tags_with_their_commits(self):
        commit_file(self.path, "a.txt", "1", "one")
        first = self.sha()
        git(self.path, "tag", "light")
        git(self.path, "tag", "-a", "annotated", "-m", "a tag object")
        git(self.path, "update-ref", "refs/remotes/origin/main", first)
        git(self.path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        commit_file(self.path, "a.txt", "2", "two")
        git(self.path, "branch", "side", first)
        git(self.path, "stash", "list")  # no stash: refs/stash is never read anyway
        refs = {r.name: r.sha for r in self.repo.refs()}
        self.assertEqual(refs, {
            "refs/heads/main": self.sha(),
            "refs/heads/side": first,
            "refs/remotes/origin/HEAD": first,  # left for the labels to drop
            "refs/remotes/origin/main": first,
            "refs/tags/annotated": first,  # peeled: the commit, not the tag object
            "refs/tags/light": first,
        })
        self.assertNotEqual(self.sha("annotated"), first)  # a tag object of its own

    def test_a_stash_is_not_a_ref_here(self):
        commit_file(self.path, "a.txt", "1", "one")
        (self.path / "a.txt").write_text("changed")
        git(self.path, "stash")
        self.assertEqual(self.repo.refs(), [Ref("refs/heads/main", self.sha())])


class RepoCommitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def message(self):
        return git(self.path, "log", "-1", "--format=%B")

    def test_the_first_commit_in_an_empty_repo(self):
        (self.path / "a.txt").write_text("a")
        self.repo.stage(["a.txt"])
        self.repo.commit("First")
        self.assertEqual(self.repo.commit_count(), 1)
        self.assertEqual(self.repo.current_branch(), "main")
        self.assertEqual(self.repo.status(), [])

    def test_a_multi_line_message_is_kept_as_written(self):
        commit_file(self.path, "a.txt", "1", "first")
        (self.path / "a.txt").write_text("2")
        self.repo.stage(["a.txt"])
        self.repo.commit("\nSubject  \n\nBody line one\n#42 is not a comment\n\n\n")
        # Only surrounding blank lines and trailing spaces go.
        self.assertEqual(self.message(), "Subject\n\nBody line one\n#42 is not a comment\n\n")

    def test_a_failing_hook_stops_the_commit_with_its_output(self):
        commit_file(self.path, "a.txt", "1", "first")
        add_hook(self.path, "pre-commit", "echo 'lint failed: a.txt'\nexit 1\n")
        (self.path / "a.txt").write_text("2")
        self.repo.stage(["a.txt"])
        with self.assertRaisesRegex(GitError, "lint failed: a.txt"):
            self.repo.commit("Change")
        self.assertEqual(self.repo.commit_count(), 1)

    def test_hooks_see_the_message(self):
        add_hook(self.path, "commit-msg", "grep -q JIRA- \"$1\" || { echo 'no ticket' >&2; exit 1; }\n")
        (self.path / "a.txt").write_text("a")
        self.repo.stage(["a.txt"])
        with self.assertRaisesRegex(GitError, "no ticket"):
            self.repo.commit("No ticket here")
        self.repo.commit("JIRA-1 With a ticket")
        self.assertEqual(self.message(), "JIRA-1 With a ticket\n\n")

    def test_nothing_staged_is_an_error(self):
        commit_file(self.path, "a.txt", "1", "first")
        with self.assertRaises(GitError):
            self.repo.commit("Nothing")

    def test_git_never_waits_for_an_editor_or_the_terminal(self):
        commit_file(self.path, "a.txt", "1", "first")
        (self.path / "a.txt").write_text("2")
        self.repo.stage(["a.txt"])
        # No message: git would open an editor. It fails at once instead.
        with self.assertRaises(GitError):
            self.repo.git("commit")
        add_hook(self.path, "pre-commit", "read answer\n[ -z \"$answer\" ] || exit 1\n")
        self.repo.commit("Hook read nothing")  # stdin is closed, not the test's terminal



class WorktreeCommandTests(unittest.TestCase):
    """Adding, removing and pruning worktrees (RZG-49, spec §4.1)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = canonical_path(self._tmp.name)
        self.main = make_repo(self.tmp / "main")
        commit_file(self.main, "a.txt", "1\n", "first")
        git(self.main, "branch", "side")
        self.repo = Repo(self.main)

    def listed(self):
        return {w.path: w.branch for w in self.repo.worktrees()}

    def test_add_on_a_new_branch_from_a_start_point(self):
        git(self.main, "switch", "-q", "-c", "later")
        commit_file(self.main, "b.txt", "2\n", "second")
        self.repo.worktree_add("-odd", "topic", "side")  # like an option; in the repo's folder
        self.assertEqual(self.listed()[str(self.main / "-odd")], "topic")
        self.assertEqual(git(self.main, "rev-parse", "topic").strip(),
                         git(self.main, "rev-parse", "side").strip())

    def test_add_on_an_existing_branch(self):
        self.repo.worktree_add(self.tmp / "side", "side")
        self.assertEqual(self.listed()[str(self.tmp / "side")], "side")
        self.assertTrue((self.tmp / "side" / "a.txt").is_file())

    def test_add_refuses_a_branch_checked_out_elsewhere_and_a_folder_in_use(self):
        with self.assertRaises(GitError):
            self.repo.worktree_add(self.tmp / "again", "main")
        (self.tmp / "full").mkdir()
        (self.tmp / "full" / "x").write_text("x")
        with self.assertRaises(GitError):
            self.repo.worktree_add(self.tmp / "full", "side")
        self.assertEqual(set(self.listed()), {str(self.main)})

    def test_remove_refuses_changes_unless_forced_and_keeps_the_branch(self):
        self.repo.worktree_add(self.tmp / "side", "side")
        (self.tmp / "side" / "new.txt").write_text("n")
        with self.assertRaises(GitError):
            self.repo.worktree_remove(self.tmp / "side")
        self.assertTrue((self.tmp / "side" / "new.txt").exists())
        self.repo.worktree_remove(self.tmp / "side", force=True)
        self.assertFalse((self.tmp / "side").exists())
        self.assertEqual(set(self.listed()), {str(self.main)})
        self.assertIn("side", git(self.main, "branch", "--list", "side"))

    def test_remove_refuses_a_locked_worktree_even_forced(self):
        self.repo.worktree_add(self.tmp / "side", "side")
        git(self.main, "worktree", "lock", str(self.tmp / "side"))
        with self.assertRaises(GitError):
            self.repo.worktree_remove(self.tmp / "side", force=True)
        self.assertTrue((self.tmp / "side").is_dir())

    def test_remove_a_missing_worktree_drops_its_record(self):
        self.repo.worktree_add(self.tmp / "side", "side")
        shutil.rmtree(self.tmp / "side")
        self.repo.worktree_remove(self.tmp / "side")
        self.assertEqual(set(self.listed()), {str(self.main)})

    def test_prune_drops_missing_worktrees_but_not_locked_ones(self):
        self.repo.worktree_add(self.tmp / "side", "side")
        self.repo.worktree_add(self.tmp / "kept", "kept", "main")
        git(self.main, "worktree", "lock", str(self.tmp / "kept"))
        shutil.rmtree(self.tmp / "side")
        shutil.rmtree(self.tmp / "kept")
        self.repo.worktree_prune()
        self.assertEqual(set(self.listed()), {str(self.main), str(self.tmp / "kept")})


if __name__ == "__main__":
    unittest.main()


class RepoWatchListingTests(unittest.TestCase):
    """What the repo watcher asks git for (spec §4.3)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        (self.path / ".gitignore").write_text("build/\n*.o\n")
        (self.path / "src" / "a").mkdir(parents=True)
        (self.path / "src" / "a" / "y.c").write_text("c")
        (self.path / "src" / "a" / "y.o").write_text("o")
        (self.path / "build").mkdir()
        (self.path / "build" / "out").write_text("x")
        (self.path / "empty").mkdir()
        commit_file(self.path, "t.txt", "t", "first")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_git_dirs(self):
        git_dir, common = self.repo.git_dirs()
        self.assertEqual(git_dir, self.repo.root / ".git")
        self.assertEqual(common, git_dir)

    def test_git_dirs_of_a_linked_worktree(self):
        other = Path(self._tmp.name) / "linked"
        git(self.path, "worktree", "add", "-q", str(other))
        git_dir, common = Repo(other).git_dirs()
        self.assertEqual(common, self.repo.root / ".git")
        self.assertEqual(git_dir.parent, common / "worktrees")

    def test_listed_files_leave_out_ignored_ones(self):
        self.assertEqual(sorted(self.repo.listed_files()), [".gitignore", "src/a/y.c", "t.txt"])

    def test_untracked_folders_include_empty_ones(self):
        self.assertEqual(sorted(self.repo.untracked_folders()), ["empty", "src"])

    def test_ignored(self):
        names = ["build", "build/out", "src/a/y.o", "src/a/y.c", "t.txt", "gone.o", "a b/c.o"]
        self.assertEqual(self.repo.ignored(names),
                         {"build", "build/out", "src/a/y.o", "gone.o", "a b/c.o"})
        self.assertEqual(self.repo.ignored([]), set())

    def test_tracked_files_are_never_ignored(self):
        (self.path / "kept.o").write_text("o")
        git(self.path, "add", "-f", "kept.o")
        self.assertEqual(self.repo.ignored(["kept.o"]), set())

    def test_status_doesnt_write_the_index(self):
        index = self.path / ".git" / "index"
        (self.path / "t.txt").write_text("t")  # same content, newer mtime: stat info is stale
        before = index.stat().st_mtime_ns
        self.repo.status()
        self.assertEqual(index.stat().st_mtime_ns, before)


class RepoBranchTests(unittest.TestCase):
    """Branch commands (spec §4.1, "Branch commands")."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.path, "a.txt", "1\n", "first")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def names(self):
        return {b.name: (b.is_current, b.merged) for b in self.repo.branches()}

    def diverge(self):
        """``side`` and ``main`` both change a.txt: merging them conflicts."""
        git(self.path, "switch", "-q", "-c", "side")
        commit_file(self.path, "a.txt", "side\n", "side")
        git(self.path, "switch", "-q", "main")
        commit_file(self.path, "a.txt", "main\n", "main")

    def assert_clean(self, head="main"):
        self.assertEqual(self.repo.current_branch(), head)
        self.assertEqual(self.repo.status(), [])
        self.assertIsNone(self.repo._resolve("MERGE_HEAD"))
        self.assertFalse(self.repo._rebasing())

    def test_branches_say_whether_they_are_merged_into_head(self):
        git(self.path, "branch", "old")
        git(self.path, "switch", "-q", "-c", "ahead")
        commit_file(self.path, "b.txt", "b", "second")
        git(self.path, "switch", "-q", "main")
        self.assertEqual(self.names(), {"ahead": (False, False), "main": (True, True),
                                        "old": (False, True)})

    def test_branches_know_their_upstream_and_how_far_from_it_they_are(self):
        remote = Path(self._tmp.name) / "remote.git"
        git(self.path, "clone", "-q", "--bare", str(self.path), str(remote))
        git(self.path, "remote", "add", "origin", str(remote))
        git(self.path, "fetch", "-q", "origin")
        git(self.path, "branch", "-q", "--set-upstream-to", "origin/main", "main")
        git(self.path, "branch", "-q", "--track", "even", "origin/main")
        git(self.path, "branch", "-q", "--track", "gone", "origin/main")
        git(self.path, "config", "branch.gone.merge", "refs/heads/deleted")
        git(self.path, "branch", "-q", "local")
        commit_file(self.path, "b.txt", "b", "second")  # main: 1 ahead
        git(self.path, "update-ref", "refs/remotes/origin/main", "HEAD")  # ... then even,
        git(self.path, "reset", "-q", "--hard", "HEAD~")  # ... then 1 behind
        commit_file(self.path, "c.txt", "c", "third")  # ... and 1 ahead: diverged
        tracking = {b.name: (b.upstream, b.ahead, b.behind) for b in self.repo.branches()}
        self.assertEqual(tracking, {
            "main": ("origin/main", 1, 1),
            "even": ("origin/main", 0, 1),
            "gone": ("origin/deleted", None, None),
            "local": (None, None, None),
        })
        self.assertEqual(self.repo.remotes(), ["origin"])

    def test_no_remotes(self):
        self.assertEqual(self.repo.remotes(), [])

    def test_branches_have_their_tip_s_committer_date(self):
        """RZG-43: Branches' Last Commit sort."""
        import os
        import subprocess
        from datetime import datetime
        env = {**os.environ, "GIT_COMMITTER_DATE": "2026-01-02T03:04:05+01:00"}
        git(self.path, "switch", "-q", "-c", "dated")
        (self.path / "d.txt").write_text("d")
        git(self.path, "add", "d.txt")
        subprocess.run(["git", "commit", "-q", "-m", "dated"], cwd=self.path, check=True, env=env)
        dates = {b.name: b.date for b in self.repo.branches()}
        self.assertEqual(dates["dated"], datetime.fromisoformat("2026-01-02T03:04:05+01:00"))
        self.assertIsNotNone(dates["main"])

    def test_no_branch_is_merged_into_an_unborn_head(self):
        git(self.path, "switch", "-q", "--orphan", "fresh")
        self.assertEqual(self.names(), {"main": (False, False)})

    def test_create_switch_rename_delete(self):
        self.repo.create_branch("topic", "main")
        self.assertEqual(self.repo.current_branch(), "main")
        self.repo.create_branch("other", "main", switch=True)
        self.assertEqual(self.repo.current_branch(), "other")
        self.repo.switch("topic")
        self.repo.rename_branch("topic", "renamed")  # the current branch
        self.assertEqual(self.repo.current_branch(), "renamed")
        self.repo.delete_branch("other")
        self.assertEqual(set(self.names()), {"main", "renamed"})

    def test_an_unmerged_branch_is_deleted_only_by_force(self):
        self.diverge()
        with self.assertRaises(GitError):
            self.repo.delete_branch("side")
        self.assertIn("side", self.names())
        self.repo.delete_branch("side", force=True)
        self.assertNotIn("side", self.names())

    def test_bad_names_are_git_s_errors(self):
        for bad in ("has space", "main", "a..b"):
            with self.subTest(bad), self.assertRaises(GitError):
                self.repo.create_branch(bad, "main")

    def test_merge(self):
        git(self.path, "switch", "-q", "-c", "side")
        commit_file(self.path, "b.txt", "b", "side")
        git(self.path, "switch", "-q", "main")
        commit_file(self.path, "c.txt", "c", "main")
        self.repo.merge("side")
        self.assertTrue((self.path / "b.txt").exists())
        self.assertEqual(git(self.path, "log", "-1", "--format=%P").count(" "), 1)  # a merge
        self.assert_clean()

    def test_a_conflicting_merge_is_aborted(self):
        self.diverge()
        head = self.repo.head_sha()
        with self.assertRaises(GitError) as caught:
            self.repo.merge("side")
        self.assertIn("CONFLICT", str(caught.exception))
        self.assertIn("aborted", str(caught.exception))
        self.assertEqual(self.repo.head_sha(), head)
        self.assertEqual((self.path / "a.txt").read_text(), "main\n")
        self.assert_clean()

    def test_a_merge_refused_before_it_starts_needs_no_abort(self):
        self.diverge()
        (self.path / "a.txt").write_text("dirty\n")
        with self.assertRaises(GitError) as caught:
            self.repo.merge("side")
        self.assertNotIn("aborted", str(caught.exception))
        self.assertEqual((self.path / "a.txt").read_text(), "dirty\n")

    def test_rebase(self):
        git(self.path, "switch", "-q", "-c", "side")
        commit_file(self.path, "b.txt", "b", "side")
        git(self.path, "switch", "-q", "main")
        commit_file(self.path, "c.txt", "c", "main")
        self.repo.rebase("side")
        self.assertEqual(git(self.path, "log", "--format=%s").split(), ["main", "side", "first"])
        self.assert_clean()

    def test_a_conflicting_rebase_is_aborted(self):
        self.diverge()
        head = self.repo.head_sha()
        with self.assertRaises(GitError) as caught:
            self.repo.rebase("side")
        self.assertIn("aborted", str(caught.exception))
        self.assertNotIn("hint:", str(caught.exception))  # "resolve them" no longer applies
        self.assertEqual(self.repo.head_sha(), head)
        self.assert_clean()

    def test_stash_push_takes_untracked_files_and_pop_restores_the_index(self):
        commit_file(self.path, ".gitignore", "*.log\n", "ignore")
        (self.path / "a.txt").write_text("staged\n")
        git(self.path, "add", "a.txt")
        (self.path / "new.txt").write_text("new")
        (self.path / "x.log").write_text("ignored")
        self.assertTrue(self.repo.stash_push("msg"))
        self.assertEqual(self.repo.status(), [])
        self.assertTrue((self.path / "x.log").exists())  # ignored files are left alone
        self.assertEqual(git(self.path, "stash", "list", "--format=%gs"), "On main: msg\n")
        self.repo.stash_pop()
        status = {f.path: f.index + f.worktree for f in self.repo.status()}
        self.assertEqual(status, {"a.txt": "M ", "new.txt": "??"})

    def test_stash_push_with_nothing_to_stash_says_so(self):
        self.assertFalse(self.repo.stash_push("msg"))

    def test_stash_push_without_untracked_files_and_with_git_s_message(self):
        (self.path / "a.txt").write_text("changed\n")
        (self.path / "new.txt").write_text("new")
        self.assertTrue(self.repo.stash_push("", untracked=False))
        self.assertEqual([f.path for f in self.repo.status()], ["new.txt"])  # left behind
        [stash] = self.repo.stashes()
        self.assertTrue(stash.message.endswith(" first"))  # "<sha> first": git's own
        self.assertEqual(stash.branch, "main")
        self.assertFalse(stash.untracked)

    def test_stashes_newest_first_with_their_branch_message_and_parts(self):
        (self.path / "a.txt").write_text("one\n")
        (self.path / "new.txt").write_text("new")
        self.repo.stash_push("first: with a colon")
        git(self.path, "switch", "-q", "-c", "side")
        (self.path / "a.txt").write_text("two\n")
        self.repo.stash_push("second", untracked=False)
        newest, older = self.repo.stashes()
        self.assertEqual((newest.index, newest.ref, newest.branch, newest.message),
                         (0, "stash@{0}", "side", "second"))
        self.assertEqual((older.ref, older.branch, older.message, older.subject),
                         ("stash@{1}", "main", "first: with a colon", "first: with a colon"))
        self.assertEqual(older.sha, git(self.path, "rev-parse", "stash@{1}").strip())
        self.assertTrue(older.untracked)
        self.assertFalse(newest.untracked)
        # Its parents, base commit first (History's labels, RZG-77).
        self.assertEqual(older.parents[0], git(self.path, "rev-parse", "main").strip())
        self.assertEqual(len(older.parents), 3)  # base, index, untracked
        self.assertEqual(len(newest.parents), 2)
        self.assertEqual(newest.author, "Test User")
        self.assertIsNotNone(newest.date.tzinfo)
        self.assertNotEqual(newest, Commit(newest.sha, newest.author, newest.date, newest.subject))

    def test_no_stashes(self):
        self.assertEqual(self.repo.stashes(), [])

    def test_stash_files_are_its_changes_then_its_untracked_files(self):
        (self.path / "a.txt").write_text("changed\n")
        (self.path / "new.txt").write_text("new")
        self.repo.stash_push("msg")
        [stash] = self.repo.stashes()
        self.assertEqual(self.repo.stash_files(stash),
                         [FileStatus("M", " ", "a.txt"), FileStatus("?", "?", "new.txt")])
        diff = self.repo.diff("new.txt", DiffSource.COMMIT, commit=f"{stash.sha}^3")
        self.assertEqual([line.text for h in diff.hunks for line in h.lines], ["new"])

    def test_stash_apply_and_drop(self):
        (self.path / "a.txt").write_text("staged\n")
        git(self.path, "add", "a.txt")
        self.repo.stash_push("msg")
        [stash] = self.repo.stashes()
        self.repo.stash_apply(stash.sha)
        self.assertEqual({f.path: f.index + f.worktree for f in self.repo.status()},
                         {"a.txt": "M "})
        self.repo.stash_drop(stash.ref)
        self.assertEqual(self.repo.stashes(), [])


class RepoAmendTests(unittest.TestCase):
    """The last commit and amending it (spec §4.1)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def message(self):
        return git(self.path, "log", "-1", "--format=%B").rstrip("\n")

    def test_no_last_commit_before_the_first(self):
        self.assertIsNone(self.repo.last_commit())

    def test_last_commit_has_the_full_message_and_whether_it_is_pushed(self):
        (self.path / "a.txt").write_text("a")
        git(self.path, "add", "a.txt")
        git(self.path, "commit", "-q", "-m", "Summary", "-m", "Body line\n\n# kept")
        last = self.repo.last_commit()
        self.assertEqual(last.sha, self.repo.head_sha())
        self.assertEqual(last.message, "Summary\n\nBody line\n\n# kept")
        self.assertFalse(last.pushed)
        git(self.path, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertTrue(self.repo.last_commit().pushed)
        commit_file(self.path, "b.txt", "b", "Not pushed yet")
        self.assertFalse(self.repo.last_commit().pushed)

    def test_amend_the_message_only(self):
        commit_file(self.path, "a.txt", "a", "first")
        commit_file(self.path, "b.txt", "b", "Tpyo")
        git(self.path, "commit", "-q", "--amend", "--author=Someone <s@example.com>",
            "--no-edit", "--date=2001-02-03T04:05:06")
        self.repo.amend("Typo fixed\n\n# a heading, kept\n\n")
        self.assertEqual(self.message(), "Typo fixed\n\n# a heading, kept")
        self.assertEqual(git(self.path, "rev-list", "--count", "HEAD").strip(), "2")
        self.assertEqual(git(self.path, "log", "-1", "--format=%an %ad", "--date=short"),
                         "Someone 2001-02-03\n")  # the author and author date stay
        self.assertEqual(git(self.path, "show", "--format=", "--name-only", "HEAD"), "b.txt\n")

    def test_amend_with_staged_changes(self):
        commit_file(self.path, "a.txt", "a", "first")
        (self.path / "c.txt").write_text("c")
        git(self.path, "add", "c.txt")
        self.repo.amend("first, and c")
        self.assertEqual(git(self.path, "rev-list", "--count", "HEAD").strip(), "1")
        self.assertEqual(sorted(git(self.path, "ls-tree", "--name-only", "HEAD").split()),
                         ["a.txt", "c.txt"])
        self.assertEqual(self.repo.status(), [])

    def test_amend_before_the_first_commit_is_git_s_error(self):
        with self.assertRaises(GitError):
            self.repo.amend("nothing to amend")


class WorktreeTests(unittest.TestCase):
    """Listing worktrees and reading their state (RZG-48, spec §4.4)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = canonical_path(self._tmp.name)
        self.main = make_repo(self.tmp / "main")
        commit_file(self.main, "a.txt", "1\n", "first")
        self.repo = Repo(self.main)

    def add(self, name, *args):
        git(self.main, "worktree", "add", "-q", *args, str(self.tmp / name))
        return self.tmp / name

    def test_lists_the_main_worktree_first_then_the_linked_ones(self):
        self.add("feature", "-b", "feature")
        self.add("loose", "--detach")
        head = git(self.main, "rev-parse", "HEAD").strip()
        self.assertEqual(self.repo.worktrees(), [
            Worktree(str(self.main), head, "main", main=True),
            Worktree(str(self.tmp / "feature"), head, "feature"),
            Worktree(str(self.tmp / "loose"), head, None),
        ])
        self.assertEqual(self.repo.worktrees()[1].name, "feature")

    def test_the_same_list_from_a_linked_worktree(self):
        linked = self.add("feature", "-b", "feature")
        self.assertEqual(Repo(linked).worktrees(), self.repo.worktrees())
        self.assertEqual(Repo(linked).common_dir, self.repo.common_dir)
        self.assertEqual(self.repo.common_dir, self.main / ".git")

    def test_locked_and_missing_worktrees(self):
        self.add("usb", "-b", "usb")
        git(self.main, "worktree", "lock", "--reason", "on a usb stick", str(self.tmp / "usb"))
        gone = self.add("gone", "-b", "gone")
        gone.rename(self.tmp / "moved")
        listed = {w.name: w for w in self.repo.worktrees()}
        self.assertTrue(listed["usb"].locked)
        self.assertEqual(listed["usb"].lock_reason, "on a usb stick")
        self.assertFalse(listed["usb"].missing)
        self.assertTrue(listed["gone"].missing)
        self.assertFalse(listed["main"].missing)

    def test_a_bare_main_repository_is_left_out(self):
        bare = self.tmp / "bare.git"
        git(self.tmp, "clone", "-q", "--bare", str(self.main), str(bare))
        git(bare, "worktree", "add", "-q", str(self.tmp / "work"), "main")
        listed = Repo(self.tmp / "work").worktrees()
        self.assertEqual([w.name for w in listed], ["work"])
        self.assertFalse(listed[0].main)

    def test_parsing_before_the_first_commit_and_odd_names(self):
        out = ("worktree /x/new\0HEAD " + "0" * 40 + "\0branch refs/heads/main\0\0"
               "worktree /x/b\0HEAD abc\0branch refs/heads/feat/a b\0locked\0\0")
        first, second = parse_worktrees(out)
        self.assertIsNone(first.head)
        self.assertTrue(first.main)
        self.assertEqual(second.branch, "feat/a b")
        self.assertTrue(second.locked)
        self.assertEqual(second.lock_reason, "")

    def test_state_counts_each_changed_file_once(self):
        (self.main / "a.txt").write_text("2\n")
        git(self.main, "add", "a.txt")
        (self.main / "a.txt").write_text("3\n")  # staged and unstaged: one file
        (self.main / "new.txt").write_text("n\n")
        self.assertEqual(worktree_state(self.main), WorktreeState(2))
        git(self.main, "reset", "-q", "--hard")
        git(self.main, "clean", "-fq")
        self.assertEqual(worktree_state(self.main), WorktreeState(0))

    def test_state_counts_ahead_and_behind_its_upstream(self):
        clone = self.tmp / "clone"
        git(self.tmp, "clone", "-q", str(self.main), str(clone))
        git(clone, "config", "user.name", "T")
        git(clone, "config", "user.email", "t@example.com")
        self.assertEqual(worktree_state(clone), WorktreeState(0, "origin/main", 0, 0))
        commit_file(clone, "b.txt", "b", "mine")
        commit_file(clone, "c.txt", "c", "mine too")
        commit_file(self.main, "d.txt", "d", "theirs")
        git(clone, "fetch", "-q")
        self.assertEqual(worktree_state(clone), WorktreeState(0, "origin/main", 2, 1))
        self.assertEqual(worktree_state(self.main), WorktreeState(0))  # no upstream

    def test_parsing_a_rename_an_unmerged_file_and_a_gone_upstream(self):
        out = ("# branch.oid abc\0# branch.head main\0# branch.upstream origin/old\0"
               "2 R. N... 100644 100644 100644 a b R100 new name\0? old name\0"
               "u UU N... 1 2 3 4 a b c conflict\0? untracked\0")
        self.assertEqual(parse_worktree_status(out), WorktreeState(3, "origin/old", None, None))

    def test_the_state_of_a_missing_folder_is_git_s_error(self):
        with self.assertRaises(GitError):
            worktree_state(self.tmp / "nowhere")
