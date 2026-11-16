import random
import subprocess
import tempfile
import unittest
from pathlib import Path

from rzgitclient.core.diff_patch import build_patch, can_patch, hunk_lines
from rzgitclient.git.git_models import DiffSource, LineKind
from rzgitclient.git.repo import GitError, Repo
from tests.support import git, make_repo


def lines_of(n: int, prefix: str = "line") -> str:
    return "".join(f"{prefix} {i}\n" for i in range(1, n + 1))


class DiffPatchTests(unittest.TestCase):
    """Patches of chosen lines, applied by git to temp repos (spec §4.1)."""

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


if __name__ == "__main__":
    unittest.main()
