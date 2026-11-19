import random
import subprocess
import tempfile
import unittest
from pathlib import Path

from rzgit.core.diff_patch import all_lines, build_patch, can_patch, copy_patch, hunk_lines
from rzgit.core.diff_request import WHOLE_CONTEXT, split_hunks
from rzgit.git.git_models import DiffSource, LineKind
from rzgit.git.repo import GitError, Repo
from tests.support import git, make_repo


def lines_of(n: int, prefix: str = "line") -> str:
    return "".join(f"{prefix} {i}\n" for i in range(1, n + 1))


class PatchCase(unittest.TestCase):
    """A temp repo, and helpers for patches of its diffs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        self.repo = Repo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    # ---- helpers -------------------------------------------------------------

    def commit(self, name: str, content: bytes | str) -> None:
        data = content.encode() if isinstance(content, str) else content
        (self.path / name).write_bytes(data)
        git(self.path, "add", "--", name)
        git(self.path, "commit", "-q", "-m", f"add {name}")

    def write(self, name: str, content: bytes | str) -> None:
        data = content.encode() if isinstance(content, str) else content
        (self.path / name).write_bytes(data)

    def index(self, name: str) -> bytes:
        return subprocess.run(["git", "show", f":{name}"], cwd=self.path, check=True,
                              capture_output=True).stdout

    def diff(self, name: str, source: DiffSource = DiffSource.WORKTREE):
        return self.repo.diff(name, source)

    def pick(self, diff, *wanted: tuple[LineKind, str]) -> set[tuple[int, int]]:
        """The (hunk, line) pairs of the changed lines with these kinds and texts."""
        found = {(h, i) for h, hunk in enumerate(diff.hunks)
                 for i, line in enumerate(hunk.lines) if (line.kind, line.text) in wanted}
        self.assertEqual(len(found), len(wanted), wanted)
        return found

    def stage(self, diff, lines, **kwargs) -> None:
        self.repo.apply(build_patch(diff, lines, **kwargs), cached=True)



class DiffPatchTests(PatchCase):
    """Patches of chosen lines, applied by git to temp repos (spec §4.1)."""

    # ---- hunks -----------------------------------------------------------------

    def test_stage_one_hunk_of_two(self):
        self.commit("f.txt", lines_of(20))
        self.write("f.txt", lines_of(20).replace("line 2\n", "two\n").replace("line 18\n", "x\n"))
        diff = self.diff("f.txt")
        self.assertEqual(len(diff.hunks), 2)
        self.stage(diff, hunk_lines(diff, 1))
        self.assertEqual(self.index("f.txt"), lines_of(20).replace("line 18\n", "x\n").encode())
        rest = self.diff("f.txt")  # what's left unstaged: the first hunk
        self.stage(rest, hunk_lines(rest, 0))
        self.assertEqual(self.index("f.txt"), (self.path / "f.txt").read_bytes())

    def test_hunks_after_a_skipped_or_growing_hunk_still_fit(self):
        self.commit("f.txt", lines_of(30))
        new = lines_of(30).replace("line 3\n", "line 3\nadd a\nadd b\n").replace(
            "line 25\n", "changed 25\n")
        self.write("f.txt", new)
        diff = self.diff("f.txt")
        self.stage(diff, hunk_lines(diff, 0) | hunk_lines(diff, 1))
        self.assertEqual(self.index("f.txt"), new.encode())

    # ---- lines -----------------------------------------------------------------

    def test_stage_chosen_lines(self):
        self.commit("f.txt", "a\nb\nc\nd\n")
        self.write("f.txt", "a\nB\nC\nd\nE\n")
        diff = self.diff("f.txt")
        # Replace b with B only; c stays (an unselected removed line is context),
        # C and E aren't added.
        self.stage(diff, self.pick(diff, (LineKind.REMOVED, "b"), (LineKind.ADDED, "B")))
        self.assertEqual(self.index("f.txt"), b"a\nB\nc\nd\n")

    def test_a_chosen_replacement_lands_where_its_old_line_was(self):
        self.commit("f.txt", "a\nb\nc\nd\n")
        self.write("f.txt", "a\nB\nC\nd\n")
        diff = self.diff("f.txt")
        self.stage(diff, self.pick(diff, (LineKind.REMOVED, "c"), (LineKind.ADDED, "C")))
        self.assertEqual(self.index("f.txt"), b"a\nb\nC\nd\n")

    def test_stage_only_an_added_line_or_only_a_removed_one(self):
        self.commit("f.txt", "a\nb\nc\n")
        self.write("f.txt", "a\nnew\nc\n")
        diff = self.diff("f.txt")
        self.stage(diff, self.pick(diff, (LineKind.ADDED, "new")))
        self.assertEqual(self.index("f.txt"), b"a\nb\nnew\nc\n")
        git(self.path, "reset", "-q")
        diff = self.diff("f.txt")
        self.stage(diff, self.pick(diff, (LineKind.REMOVED, "b")))
        self.assertEqual(self.index("f.txt"), b"a\nc\n")

    def test_unstage_chosen_lines(self):
        self.commit("f.txt", "a\nb\nc\nd\n")
        self.write("f.txt", "a\nB\nC\nd\n")
        git(self.path, "add", "f.txt")
        diff = self.diff("f.txt", DiffSource.INDEX)
        # Unstage the C change only: an unselected added line (B) stays as context.
        lines = self.pick(diff, (LineKind.REMOVED, "c"), (LineKind.ADDED, "C"))
        self.repo.apply(build_patch(diff, lines, reverse=True), cached=True, reverse=True)
        self.assertEqual(self.index("f.txt"), b"a\nB\nc\nd\n")
        self.assertEqual((self.path / "f.txt").read_bytes(), b"a\nB\nC\nd\n")  # untouched

    def test_discard_chosen_lines(self):
        self.commit("f.txt", "a\nb\nc\nd\n")
        self.write("f.txt", "a\nB\nC\nd\nE\n")
        diff = self.diff("f.txt")
        lines = self.pick(diff, (LineKind.ADDED, "E"), (LineKind.REMOVED, "c"),
                          (LineKind.ADDED, "C"))
        self.repo.apply(build_patch(diff, lines, reverse=True), reverse=True)
        self.assertEqual((self.path / "f.txt").read_bytes(), b"a\nB\nc\nd\n")
        self.assertEqual(self.index("f.txt"), b"a\nb\nc\nd\n")  # untouched

    def test_nothing_chosen_is_no_patch(self):
        self.commit("f.txt", "a\n")
        self.write("f.txt", "b\n")
        self.assertIsNone(build_patch(self.diff("f.txt"), set()))

    def test_any_choice_then_the_rest_gives_the_whole_change(self):
        rng = random.Random(23)
        words = ["alpha", "beta", "gamma", "delta", "eps", ""]
        for round_ in range(40):
            with self.subTest(round=round_):
                base = [rng.choice(words) + str(rng.randrange(5)) for _ in range(rng.randrange(1, 25))]
                new = list(base)
                for _ in range(rng.randrange(1, 6)):  # random edits
                    at = rng.randrange(len(new) + 1)
                    match rng.randrange(3):
                        case 0 if new and at < len(new):
                            del new[at]
                        case 1 if new and at < len(new):
                            new[at] = new[at] + "!"
                        case _:
                            new.insert(at, rng.choice(words) + "+")
                name = f"r{round_}.txt"
                self.commit(name, "".join(f"{x}\n" for x in base))
                self.write(name, "".join(f"{x}\n" for x in new))
                diff = self.diff(name)
                if not diff.hunks:
                    continue
                changed = [(h, i) for h, hunk in enumerate(diff.hunks)
                           for i, line in enumerate(hunk.lines) if line.kind is not LineKind.CONTEXT]
                some = {x for x in changed if rng.random() < 0.5}
                if some:
                    self.stage(diff, some)
                rest = self.diff(name)  # unstaged: what the first patch left
                if rest.hunks:
                    self.stage(rest, {(h, i) for h, hunk in enumerate(rest.hunks)
                                      for i, line in enumerate(hunk.lines)
                                      if line.kind is not LineKind.CONTEXT})
                self.assertEqual(self.index(name), (self.path / name).read_bytes())
                # And back: unstage some, then the rest, gives HEAD again.
                staged = self.diff(name, DiffSource.INDEX)
                changed = [(h, i) for h, hunk in enumerate(staged.hunks)
                           for i, line in enumerate(hunk.lines) if line.kind is not LineKind.CONTEXT]
                some = {x for x in changed if rng.random() < 0.5}
                if some:
                    self.repo.apply(build_patch(staged, some, reverse=True), cached=True,
                                    reverse=True)
                left = self.diff(name, DiffSource.INDEX)
                if left.hunks:
                    everything = {(h, i) for h, hunk in enumerate(left.hunks)
                                  for i, line in enumerate(hunk.lines)
                                  if line.kind is not LineKind.CONTEXT}
                    self.repo.apply(build_patch(left, everything, reverse=True), cached=True,
                                    reverse=True)
                self.assertEqual(self.index(name), "".join(f"{x}\n" for x in base).encode())

    # ---- exact bytes -------------------------------------------------------------

    def test_crlf_is_kept(self):
        self.commit("w.txt", b"one\r\ntwo\r\nthree\r\n")
        self.write("w.txt", b"one\r\nTWO\r\nthree\r\nfour\r\n")
        diff = self.diff("w.txt")
        self.stage(diff, self.pick(diff, (LineKind.REMOVED, "two\r"), (LineKind.ADDED, "TWO\r")))
        self.assertEqual(self.index("w.txt"), b"one\r\nTWO\r\nthree\r\n")

    def test_no_newline_at_end_of_file(self):
        self.commit("n.txt", "a\nb")
        self.write("n.txt", "a\nc")
        diff = self.diff("n.txt")
        self.stage(diff, hunk_lines(diff, 0))
        self.assertEqual(self.index("n.txt"), b"a\nc")
        self.write("n.txt", "a\nc\n")  # now add the newline
        diff = self.diff("n.txt")
        self.stage(diff, hunk_lines(diff, 0))
        self.assertEqual(self.index("n.txt"), b"a\nc\n")

    def test_a_one_line_file_with_no_context(self):
        self.commit("o.txt", "old\n")
        self.write("o.txt", "new\n")
        diff = self.diff("o.txt")
        self.stage(diff, hunk_lines(diff, 0))
        self.assertEqual(self.index("o.txt"), b"new\n")

    def test_removing_every_line(self):
        self.commit("e.txt", "a\nb\n")
        self.write("e.txt", "")
        diff = self.diff("e.txt")
        self.stage(diff, hunk_lines(diff, 0))
        self.assertEqual(self.index("e.txt"), b"")

    def test_awkward_paths(self):
        for name in ("with space.txt", "ünï cödé.txt", 'quote"d.txt', "tab\tname.txt"):
            with self.subTest(name=name):
                self.commit(name, "a\nb\n")
                self.write(name, "a\nB\n")
                diff = self.diff(name)
                self.stage(diff, hunk_lines(diff, 0))
                self.assertEqual(self.index(name), b"a\nB\n")

    # ---- new files ---------------------------------------------------------------

    def test_part_of_a_new_file(self):
        self.commit("keep.txt", "x\n")
        self.write("new.txt", "one\ntwo\nthree\n")
        diff = self.repo.diff("new.txt", DiffSource.UNTRACKED)
        self.stage(diff, self.pick(diff, (LineKind.ADDED, "one"), (LineKind.ADDED, "three")),
                   new_file=True)
        self.assertEqual(self.index("new.txt"), b"one\nthree\n")
        mode = git(self.path, "ls-files", "-s", "new.txt").split()[0]
        self.assertEqual(mode, "100644")

    def test_a_new_executable_file_keeps_its_mode(self):
        self.commit("keep.txt", "x\n")
        self.write("run.sh", "#!/bin/sh\necho hi\n")
        diff = self.repo.diff("run.sh", DiffSource.UNTRACKED)
        self.stage(diff, hunk_lines(diff, 0), new_file=True, mode="100755")
        self.assertEqual(git(self.path, "ls-files", "-s", "run.sh").split()[0], "100755")

    # ---- refusals ----------------------------------------------------------------

    def test_a_stale_patch_changes_nothing(self):
        self.commit("f.txt", "a\nb\nc\n")
        self.write("f.txt", "a\nB\nc\n")
        diff = self.diff("f.txt")
        patch = build_patch(diff, hunk_lines(diff, 0))
        self.write("f.txt", "A\nb\nc\n")  # the index changes meanwhile
        git(self.path, "add", "f.txt")
        with self.assertRaises(GitError):
            self.repo.apply(patch, cached=True)
        self.assertEqual(self.index("f.txt"), b"A\nb\nc\n")

    def test_text_that_isnt_utf8_is_refused(self):
        self.commit("l.txt", "caf\xe9\n".encode("latin-1"))
        self.write("l.txt", "caf\xe9!\n".encode("latin-1"))
        diff = self.diff("l.txt")
        self.assertIsNotNone(can_patch(diff))
        with self.assertRaises(GitError):
            build_patch(diff, hunk_lines(diff, 0))

    def test_a_rename_is_refused(self):
        self.commit("old.txt", "a\nb\nc\nd\n")
        git(self.path, "mv", "old.txt", "new.txt")
        (self.path / "new.txt").write_text("a\nb\nc\nD\n")
        git(self.path, "add", "new.txt")
        diff = self.repo.diff("new.txt", DiffSource.INDEX, old_path="old.txt")
        self.assertEqual(diff.old_path, "old.txt")
        self.assertIn("renamed", can_patch(diff))


class CopyPatchTests(PatchCase):
    """Copy as Patch: forwards patches with git's context, applied by git (RZG-76, §4.1)."""

    def git_apply(self, patch: str, cwd: Path | None = None) -> None:
        subprocess.run(["git", "apply", "-"], cwd=cwd or self.path, check=True,
                       input=patch.encode(), capture_output=True)

    def whole(self, name: str, source: DiffSource = DiffSource.WORKTREE, **kwargs):
        return split_hunks(self.repo.diff(name, source, context=WHOLE_CONTEXT, **kwargs))

    def hunk_heads(self, patch: str) -> list[str]:
        return [line for line in patch.splitlines() if line.startswith("@@")]

    def test_the_whole_file_view_copies_what_the_hunks_view_does(self):
        self.commit("f.txt", lines_of(60))
        new = lines_of(60).replace("line 10\n", "ten\n").replace("line 40\n", "forty\n")
        self.write("f.txt", new)
        diff, whole = self.diff("f.txt"), self.whole("f.txt")
        self.assertEqual(len(whole.hunks), 2)
        self.assertGreater(len(whole.hunks[0].lines), 20)  # the piece runs from the top
        for h in (0, 1):
            self.assertEqual(copy_patch(whole, hunk_lines(whole, h)),
                             copy_patch(diff, hunk_lines(diff, h)))
        patch = copy_patch(whole, all_lines(whole))
        self.assertEqual(self.hunk_heads(patch), ["@@ -7,7 +7,7 @@", "@@ -37,7 +37,7 @@"])
        self.assertEqual(patch.count("\n"), 3 + 2 * 9)  # 3 header lines, then @@ and 3 + 2 + 3 each
        git(self.path, "checkout", "--", "f.txt")
        self.git_apply(patch)
        self.assertEqual((self.path / "f.txt").read_text(), new)

    def test_unchosen_removed_lines_become_context_and_long_context_splits(self):
        self.commit("f.txt", lines_of(20))
        new = lines_of(20).replace("line 2\n", "two\n").replace("line 19\n", "nineteen\n")
        for n in range(5, 15):
            new = new.replace(f"line {n}\n", "")
        self.write("f.txt", new)
        diff = self.diff("f.txt")
        self.assertEqual(len(diff.hunks), 1)  # git joins the changes
        chosen = self.pick(diff, (LineKind.REMOVED, "line 2"), (LineKind.ADDED, "two"),
                           (LineKind.REMOVED, "line 19"), (LineKind.ADDED, "nineteen"))
        patch = copy_patch(diff, chosen)
        self.assertEqual(self.hunk_heads(patch), ["@@ -1,5 +1,5 @@", "@@ -16,5 +16,5 @@"])
        self.assertIsNotNone(build_patch(diff, chosen))  # staging keeps one hunk
        self.assertEqual(len(self.hunk_heads(build_patch(diff, chosen))), 1)
        git(self.path, "checkout", "--", "f.txt")
        self.git_apply(patch)
        self.assertEqual((self.path / "f.txt").read_text(), lines_of(20).replace(
            "line 2\n", "two\n").replace("line 19\n", "nineteen\n"))

    def test_a_split_hunks_numbers_count_what_came_before_it(self):
        # git applies a hunk whose numbers are off, so these check the headers themselves.
        self.commit("f.txt", lines_of(30))
        new = lines_of(30).replace("line 2\n", "line 2\na\nb\nc\n").replace(
            "line 20\n", "twenty\n")
        for n in range(6, 16):
            new = new.replace(f"line {n}\n", "")
        self.write("f.txt", new)
        diff = self.diff("f.txt")
        self.assertEqual(len(diff.hunks), 1)
        chosen = self.pick(diff, (LineKind.ADDED, "a"), (LineKind.ADDED, "b"),
                           (LineKind.ADDED, "c"), (LineKind.REMOVED, "line 20"),
                           (LineKind.ADDED, "twenty"))
        patch = copy_patch(diff, chosen)  # the removed 6 to 15 stay, as context
        self.assertEqual(self.hunk_heads(patch), ["@@ -1,5 +1,8 @@", "@@ -17,7 +20,7 @@"])
        git(self.path, "checkout", "--", "f.txt")
        self.git_apply(patch)
        self.assertEqual((self.path / "f.txt").read_text(), lines_of(30).replace(
            "line 2\n", "line 2\na\nb\nc\n").replace("line 20\n", "twenty\n"))
        self.assertEqual([h.split(" @@")[0] + " @@" for h in self.hunk_heads(git(
                              self.path, "diff", "f.txt"))],  # as git numbers them
                         ["@@ -1,5 +1,8 @@", "@@ -17,7 +20,7 @@"])

    def test_seven_context_lines_split_a_hunk_six_do_not(self):
        self.commit("f.txt", lines_of(30))
        for gap, heads in ((7, ["@@ -2,7 +2,7 @@", "@@ -10,7 +10,7 @@"]),
                           (6, ["@@ -2,14 +2,14 @@"])):
            with self.subTest(gap=gap):
                last = 5 + gap + 1  # the second change, after ``gap`` lines of context
                new = lines_of(30).replace("line 5\n", "five\n").replace(
                    f"line {last}\n", "changed\n")
                for n in range(6, last - 2):  # removed, then context once unchosen
                    new = new.replace(f"line {n}\n", "")
                self.write("f.txt", new)
                diff = self.diff("f.txt")
                self.assertEqual(len(diff.hunks), 1)
                chosen = self.pick(diff, (LineKind.REMOVED, "line 5"), (LineKind.ADDED, "five"),
                                   (LineKind.REMOVED, f"line {last}"),
                                   (LineKind.ADDED, "changed"))
                self.assertEqual(self.hunk_heads(copy_patch(diff, chosen)), heads)

    def test_any_choice_copies_what_staging_it_gives(self):
        rng = random.Random(76)
        words = ["alpha", "beta", "gamma", ""]
        for round_ in range(40):
            with self.subTest(round=round_):
                base = [rng.choice(words) + str(rng.randrange(4)) for _ in range(rng.randrange(1, 40))]
                new = list(base)
                for _ in range(rng.randrange(1, 7)):
                    at = rng.randrange(len(new) + 1)
                    match rng.randrange(3):
                        case 0 if new and at < len(new):
                            del new[at]
                        case 1 if new and at < len(new):
                            new[at] = new[at] + "!"
                        case _:
                            new.insert(at, rng.choice(words) + "+")
                name = f"c{round_}.txt"
                self.commit(name, "".join(f"{x}\n" for x in base))
                self.write(name, "".join(f"{x}\n" for x in new))
                diff = self.whole(name) if round_ % 2 else self.diff(name)
                if not diff.hunks:
                    continue
                some = {x for x in all_lines(diff) if rng.random() < 0.5} or all_lines(diff)
                patch = copy_patch(diff, some)
                self.stage(diff, some)  # what staging the same lines makes of HEAD
                expected = self.index(name)
                git(self.path, "reset", "-q", "--", name)
                git(self.path, "checkout", "--", name)
                self.git_apply(patch)
                self.assertEqual((self.path / name).read_bytes(), expected)

    def test_an_added_file_is_created_with_its_mode(self):
        self.write("tool.sh", "#!/bin/sh\necho a\necho b\n")
        (self.path / "tool.sh").chmod(0o755)
        diff = self.diff("tool.sh", DiffSource.UNTRACKED)
        self.assertEqual(diff.new_mode, "100755")
        some = self.pick(diff, (LineKind.ADDED, "#!/bin/sh"), (LineKind.ADDED, "echo b"))
        patch = copy_patch(diff, some)
        self.assertIn("new file mode 100755\n--- /dev/null\n+++ b/tool.sh\n", patch)
        (self.path / "tool.sh").unlink()
        self.git_apply(patch)
        self.assertEqual((self.path / "tool.sh").read_text(), "#!/bin/sh\necho b\n")
        self.assertTrue((self.path / "tool.sh").stat().st_mode & 0o100)
        with self.assertRaises(subprocess.CalledProcessError):  # it's there now
            self.git_apply(patch)

    def test_a_deleted_file_is_deleted_only_when_every_line_goes(self):
        self.commit("gone.txt", lines_of(5))
        (self.path / "gone.txt").unlink()
        diff = self.diff("gone.txt")
        self.assertEqual(diff.deleted_mode, "100644")
        patch = copy_patch(diff, all_lines(diff))
        self.assertIn("deleted file mode 100644\n--- a/gone.txt\n+++ /dev/null\n", patch)
        part = copy_patch(diff, self.pick(diff, (LineKind.REMOVED, "line 2")))
        self.assertNotIn("deleted file", part)
        git(self.path, "checkout", "--", "gone.txt")
        self.git_apply(part)
        self.assertEqual((self.path / "gone.txt").read_text(), lines_of(5).replace("line 2\n", ""))
        git(self.path, "checkout", "--", "gone.txt")
        self.git_apply(patch)
        self.assertFalse((self.path / "gone.txt").exists())

    def test_a_commits_diff_applies_to_its_parent(self):
        self.commit("f.txt", lines_of(30))
        self.write("f.txt", lines_of(30).replace("line 12\n", "twelve\n"))
        git(self.path, "commit", "-q", "-am", "change")
        self.commit("added.txt", "new\n")
        sha = git(self.path, "rev-parse", "HEAD~").strip()
        diff = self.repo.diff("f.txt", DiffSource.COMMIT, commit=sha)
        added = self.repo.diff("added.txt", DiffSource.COMMIT,
                               commit=git(self.path, "rev-parse", "HEAD").strip())
        self.assertEqual(added.new_mode, "100644")
        self.assertIsNone(diff.new_mode)
        self.assertIsNone(diff.deleted_mode)
        git(self.path, "checkout", "-q", "HEAD~2", "--", "f.txt")
        self.git_apply(copy_patch(diff, all_lines(diff)))
        self.assertEqual((self.path / "f.txt").read_text(), lines_of(30).replace("line 12\n", "twelve\n"))

    def test_a_staged_new_file_keeps_its_mode(self):
        self.write("n.txt", "x\n")
        git(self.path, "add", "n.txt")
        self.assertEqual(self.diff("n.txt", DiffSource.INDEX).new_mode, "100644")
        self.assertIsNone(self.diff("n.txt", DiffSource.WORKTREE).new_mode)


if __name__ == "__main__":
    unittest.main()
