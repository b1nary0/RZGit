import tempfile
import unittest
from pathlib import Path

from rzgit.core.vscode_target import VSCodeTarget


class VSCodeTargetTests(unittest.TestCase):
    """Which VS Code window a worktree's files open in (spec §8, RZG-96)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "repo-RZG-1"
        (self.root / "tools").mkdir(parents=True)
        (self.root / "a.txt").write_text("a")
        (self.root / "tools" / "x.code-workspace").write_text("{}")

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_one_tracked_workspace_file_picks_the_window(self):
        target = VSCodeTarget(self.root, ("tools/x.code-workspace",))
        self.assertEqual(target.target, self.root / "tools" / "x.code-workspace")

    def test_else_the_worktree_s_folder(self):
        for files in ((), ("tools/x.code-workspace", "b.code-workspace"),
                      ("gone.code-workspace",)):  # none, two, or not in this worktree
            with self.subTest(files):
                self.assertEqual(VSCodeTarget(self.root, files).target, self.root)

    def test_present_keeps_the_files_in_the_working_tree(self):
        target = VSCodeTarget(self.root)
        self.assertEqual(target.present(["gone.txt", "a.txt", "tools"]), ("a.txt",))

    def test_the_command_line_names_the_target_then_absolute_files(self):
        target = VSCodeTarget(self.root, ("tools/x.code-workspace",))
        workspace = str(self.root / "tools" / "x.code-workspace")
        self.assertEqual(target.argv(["flatpak", "run", "code"], ["a.txt", "-b.txt"]),
                         ["flatpak", "run", "code", workspace,
                          str(self.root / "a.txt"), str(self.root / "-b.txt")])

    def test_with_a_line_the_first_file_opens_there(self):
        target = VSCodeTarget(self.root)
        self.assertEqual(target.argv(["code"], ["a.txt"], line=12),
                         ["code", str(self.root), "--goto", f"{self.root / 'a.txt'}:12"])
        self.assertEqual(target.argv(["code"], [], line=12), ["code", str(self.root)])


if __name__ == "__main__":
    unittest.main()
