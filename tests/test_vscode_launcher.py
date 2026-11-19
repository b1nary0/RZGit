import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtWidgets import QWidget

from rzgit.core.preferences import Preferences
from rzgit.core.repo_session import RepoSession
from rzgit.core.selection import Selection
from rzgit.ui.app_actions import NO_FILE_TO_OPEN, NO_VSCODE_CHECK, AppActions
from rzgit.ui.vscode_launcher import (
    NO_COMMAND,
    NO_REPO,
    NOT_PRESENT,
    VSCODE_COMMAND,
    VSCodeLauncher,
    find_vscode,
)
from tests.support import commit_file, git, make_repo, qt_app, wait_idle


class FindVSCodeTests(unittest.TestCase):
    def which(self, *found):
        return lambda name: f"/usr/bin/{name}" if name in found else None

    def test_empty_means_code(self):
        self.assertEqual(find_vscode("", self.which("code")), ["code"])
        self.assertIsNone(find_vscode("", self.which()))

    def test_the_setting_split_like_a_shell(self):
        self.assertEqual(find_vscode("flatpak run 'com.visualstudio.code'", self.which("flatpak")),
                         ["flatpak", "run", "com.visualstudio.code"])
        self.assertIsNone(find_vscode("codium", self.which("code")))  # not found: no fallback
        self.assertIsNone(find_vscode("'codium", self.which("codium")))  # unbalanced quote

    def test_the_setting_is_on_the_tools_page(self):
        self.assertEqual((VSCODE_COMMAND.key, VSCODE_COMMAND.default, VSCODE_COMMAND.page,
                          VSCODE_COMMAND.special), ("tools/vscode", "", "Tools", "code"))


class VSCodeLauncherTests(unittest.TestCase):
    """Open with VS Code (spec §8, RZG-96)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = make_repo(Path(self._tmp.name) / "repo")
        (self.path / "repo.code-workspace").write_text("{}")
        git(self.path, "add", "repo.code-workspace")
        commit_file(self.path, "a.txt", "a", "first")
        (self.path / "b.txt").write_text("b")
        self.session = RepoSession()
        self.actions = AppActions(self.session, Selection(self.session))
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.prefs = Preferences()
        self.launcher = VSCodeLauncher(self.window, self.session, self.actions, self.prefs)
        self.started = []
        self.launcher.start_detached = lambda argv, folder: self.started.append((argv, folder)) or True
        self.which = mock.patch("rzgit.ui.vscode_launcher.shutil.which",
                                side_effect=lambda name: f"/usr/bin/{name}" if name == "code" else None)
        self.which.start()
        self.addCleanup(self.which.stop)

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    def open_repo(self):
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)
        self.root = self.session.repo.root

    def entry(self, *paths, line=None):
        action = self.actions.open_in_vscode(paths, self.window, line)
        self.addCleanup(action.deleteLater)
        return action

    def test_the_entry_says_why_it_is_disabled(self):
        self.assertEqual(self.entry("a.txt").toolTip(), NO_REPO)
        self.open_repo()
        self.assertEqual(self.entry().toolTip(), NO_FILE_TO_OPEN)
        self.assertEqual(self.entry("gone.txt").toolTip(), NOT_PRESENT)
        self.prefs.set(VSCODE_COMMAND, "codium")
        self.assertEqual(self.entry("a.txt").toolTip(), NO_COMMAND)
        self.prefs.set(VSCODE_COMMAND, "")
        entry = self.entry("a.txt")
        self.assertEqual((entry.isEnabled(), entry.toolTip()), (True, "Open with VS Code"))  # Qt: the text

    def test_files_open_in_one_call_in_the_worktree_s_window(self):
        self.open_repo()
        self.entry("a.txt", "gone.txt", "b.txt").trigger()
        self.assertEqual(self.started, [(
            ["code", str(self.root / "repo.code-workspace"),
             str(self.root / "a.txt"), str(self.root / "b.txt")], str(self.root))])

    def test_the_diff_s_file_opens_at_its_line(self):
        self.open_repo()
        self.entry("a.txt", line=3).trigger()
        self.assertEqual(self.started[0][0][-2:], ["--goto", f"{self.root / 'a.txt'}:3"])

    def test_nothing_starts_for_missing_files_and_a_failed_start_warns(self):
        self.open_repo()
        self.launcher.open(("gone.txt",))
        self.assertEqual(self.started, [])
        self.launcher.start_detached = lambda argv, folder: False
        with mock.patch.object(self.launcher, "warn") as warn:
            self.launcher.open(("a.txt",))
        warn.assert_called_once()

    def test_without_a_launcher_the_entry_is_disabled(self):
        actions = AppActions(self.session, Selection(self.session))
        action = actions.open_in_vscode(("a.txt",), self.window)
        self.addCleanup(action.deleteLater)
        self.assertEqual((action.isEnabled(), action.toolTip()), (False, NO_VSCODE_CHECK))


if __name__ == "__main__":
    unittest.main()
