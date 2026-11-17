import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from rzgitclient.core.git_commands import CommandKind, GitCommand
from rzgitclient.core.repo_session import RepoSession
from rzgitclient.core.selection import Selection
from rzgitclient.git.git_models import Branch, Worktree, WorktreeState
from rzgitclient.ui.app_actions import AppActions, WorktreeOp
from rzgitclient.ui.worktree_dialogs import (
    CREATE,
    NOT_ABSOLUTE,
    NOT_EMPTY,
    PRUNE,
    REMOVE,
    REMOVE_ANYWAY,
    TERMINALS,
    NewWorktree,
    NewWorktreeDialog,
    WorktreeDialogs,
    default_folder,
    find_terminal,
    folder_problem,
)
from tests.support import qt_app


class TerminalTests(unittest.TestCase):
    def which(self, *found):
        return lambda name: f"/usr/bin/{name}" if name in found else None

    def test_terminal_from_the_environment_split_like_a_shell(self):
        self.assertEqual(find_terminal({"TERMINAL": "kitty --single-instance 'a b'"},
                                       self.which("kitty", "konsole")),
                         ["kitty", "--single-instance", "a b"])

    def test_else_the_first_known_terminal_on_the_path(self):
        self.assertEqual(TERMINALS, ("konsole", "gnome-terminal", "xfce4-terminal",
                                     "x-terminal-emulator"))
        self.assertEqual(find_terminal({}, self.which("xfce4-terminal", "x-terminal-emulator")),
                         ["/usr/bin/xfce4-terminal"])
        # $TERMINAL that isn't there, or can't be split, falls back to the list.
        self.assertEqual(find_terminal({"TERMINAL": "nope"}, self.which("konsole")),
                         ["/usr/bin/konsole"])
        self.assertEqual(find_terminal({"TERMINAL": "'kitty"}, self.which("kitty", "konsole")),
                         ["/usr/bin/konsole"])
        self.assertIsNone(find_terminal({}, self.which()))


    def test_the_setting_comes_first(self):
        """The terminal command setting (spec §8.2), else as before."""
        env = {"TERMINAL": "kitty"}
        which = self.which("kitty", "foot", "konsole")
        self.assertEqual(find_terminal(env, which, command="foot -e 'a b'"), ["foot", "-e", "a b"])
        self.assertEqual(find_terminal(env, which, command="nope"), ["kitty"])
        self.assertEqual(find_terminal(env, which, command=""), ["kitty"])
        self.assertEqual(find_terminal({}, which, command="'foot"), ["/usr/bin/konsole"])


class FolderTests(unittest.TestCase):
    def test_default_folder_is_beside_the_main_repository(self):
        self.assertEqual(default_folder("/dev/RZGitClient", "rzg-40"), "/dev/RZGitClient-rzg-40")
        self.assertEqual(default_folder("/dev/app", "feature/x/y"), "/dev/app-feature-x-y")
        self.assertEqual(default_folder("/dev/app", ""), "")

    def test_folder_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "empty").mkdir()
            (tmp / "full").mkdir()
            (tmp / "full" / "x").write_text("x")
            (tmp / "file").write_text("x")
            self.assertIsNone(folder_problem(str(tmp / "new")))
            self.assertIsNone(folder_problem(str(tmp / "empty")))
            self.assertEqual(folder_problem(str(tmp / "full")), NOT_EMPTY)
            self.assertEqual(folder_problem(str(tmp / "file")), NOT_EMPTY)
            self.assertEqual(folder_problem("relative/x"), NOT_ABSOLUTE)
            self.assertEqual(folder_problem("  "), "")


class NewWorktreeDialogTests(unittest.TestCase):
    """The branch, the folder and Open it (spec §8)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve() / "app"
        self.root.mkdir()
        (self.root / "a.txt").write_text("a")
        self.branches = (Branch("main", True), Branch("feature/x", False), Branch("busy", False))
        self.used = {"main": "app", "busy": "app-busy"}

    def dialog(self, start="", head="main", used=None):
        dialog = NewWorktreeDialog(None, self.branches, self.used if used is None else used,
                                   self.root, head, start)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def items(self, dialog):
        combo, model = dialog.existing_combo, dialog.existing_combo.model()
        return [(combo.itemText(i), model.item(i).isEnabled()) for i in range(combo.count())]

    def test_branches_in_a_worktree_are_named_and_cant_be_picked(self):
        dialog = self.dialog()
        self.assertEqual(self.items(dialog), [("main (in worktree app)", False),
                                              ("feature/x", True),
                                              ("busy (in worktree app-busy)", False)])
        # The first free branch, and its folder.
        self.assertTrue(dialog.existing_radio.isChecked())
        self.assertEqual(dialog.branch(), "feature/x")
        self.assertEqual(dialog.folder_edit.text(), str(self.root.parent / "app-feature-x"))
        self.assertTrue(dialog.open_check.isChecked())
        self.assertEqual(dialog.create_button.text(), CREATE)
        self.assertEqual(dialog.result_value(),
                         NewWorktree(str(self.root.parent / "app-feature-x"), "feature/x",
                                     None, True))

    def test_from_a_free_branch_starts_on_it(self):
        dialog = self.dialog(start="feature/x")
        self.assertTrue(dialog.existing_radio.isChecked())
        self.assertEqual(dialog.branch(), "feature/x")

    def test_from_a_branch_in_use_starts_a_new_branch_from_it(self):
        dialog = self.dialog(start="busy")
        self.assertTrue(dialog.new_radio.isChecked())
        self.assertEqual(dialog.existing_combo.currentData(), "feature/x")  # a free one
        self.assertEqual(dialog.start_combo.currentText(), "busy")
        self.assertFalse(dialog.create_button.isEnabled())  # no name yet
        dialog.name_edit.setText("busy-2")
        self.assertEqual(dialog.folder_edit.text(), str(self.root.parent / "app-busy-2"))
        self.assertTrue(dialog.create_button.isEnabled())
        dialog.open_check.setChecked(False)
        self.assertEqual(dialog.result_value(),
                         NewWorktree(str(self.root.parent / "app-busy-2"), "busy-2", "busy",
                                     False))

    def test_with_no_free_branch_only_a_new_one(self):
        dialog = self.dialog(used={"main": "app", "feature/x": "a", "busy": "b"}, head=None)
        self.assertFalse(dialog.existing_radio.isEnabled())
        self.assertTrue(dialog.new_radio.isChecked())
        self.assertEqual(dialog.start_combo.currentText(), "HEAD")  # detached

    def test_the_folder_follows_the_branch_until_edited(self):
        dialog = self.dialog()
        dialog.new_radio.setChecked(True)
        dialog.name_edit.setText("a")
        self.assertEqual(dialog.folder_edit.text(), str(self.root.parent / "app-a"))
        dialog.folder_edit.textEdited.emit("mine")  # as if typed
        dialog.folder_edit.setText(str(self.root.parent / "mine"))
        dialog.name_edit.setText("b")
        self.assertEqual(dialog.folder_edit.text(), str(self.root.parent / "mine"))
        self.assertEqual(dialog.result_value().path, str(self.root.parent / "mine"))

    def test_create_needs_a_usable_folder(self):
        dialog = self.dialog()
        dialog.folder_edit.setText(str(self.root))  # the repository: not empty
        self.assertFalse(dialog.create_button.isEnabled())
        self.assertEqual(dialog.problem_label.text(), NOT_EMPTY)
        self.assertFalse(dialog.problem_label.isHidden())
        dialog.folder_edit.setText("")
        self.assertFalse(dialog.create_button.isEnabled())
        self.assertTrue(dialog.problem_label.isHidden())
        dialog.folder_edit.setText(str(self.root.parent / "free"))
        self.assertTrue(dialog.create_button.isEnabled())

    def test_a_new_branch_needs_a_start_point(self):
        dialog = self.dialog()
        dialog.new_radio.setChecked(True)
        dialog.name_edit.setText("n")
        dialog.start_combo.setCurrentText(" ")
        self.assertFalse(dialog.create_button.isEnabled())


class _Signals(QObject):
    command_finished = Signal(object, bool)


class _Reader:
    """The few parts of a WorktreeReader the dialogs use."""

    def __init__(self, worktrees, states=None):
        self.worktrees = tuple(worktrees)
        self.states = states or {}
        self.refresh = mock.Mock()

    def state(self, path):
        return self.states.get(path)


class _Repo:
    root = "/w/app-own"
    main_root = Path("/w/app")


class WorktreeDialogsTests(unittest.TestCase):
    """What each worktree operation asks, and what it then runs (spec §8)."""

    MAIN = Worktree("/w/app", "a" * 40, "main", main=True)
    OWN = Worktree("/w/app-own", "a" * 40, "own")
    SIDE = Worktree("/w/app-side", "a" * 40, "side")
    LOCKED = Worktree("/w/app-locked", "a" * 40, None, locked=True)
    GONE = Worktree("/w/app-gone", "a" * 40, "gone", missing=True)
    GONE_LOCKED = Worktree("/w/usb", "a" * 40, None, locked=True, missing=True)

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.real_session = RepoSession()
        self.addCleanup(self.real_session.shutdown)
        self.actions = AppActions(self.real_session, Selection(self.real_session))
        self.signals = _Signals()
        self.session = mock.Mock(spec=RepoSession)
        self.session.command_finished = self.signals.command_finished
        self.session.repo = _Repo()
        self.session.head = "own"
        self.session.branches = (Branch("main", False), Branch("own", True), Branch("free", False))
        self.reader = _Reader([self.MAIN, self.OWN, self.SIDE, self.LOCKED, self.GONE,
                               self.GONE_LOCKED])
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.other_window = mock.Mock()
        self.windows = {"/w/app-side": self.other_window, "/w/app-own": self.window}
        self.opened = []
        self.calls = []  # the order of closing and removing
        self.other_window.close.side_effect = lambda: self.calls.append("close")
        self.session.remove_worktree.side_effect = lambda *a, **k: self.calls.append("remove")
        self.dialogs = WorktreeDialogs(self.window, self.session, self.actions, self.reader,
                                       open_in_window=self.opened.append,
                                       window_for=self.windows.get)
        self.asked = []  # (title, text, info, labels, default, warn)
        self.answers = []

    def fake_ask(self, title, text, info, buttons, *, default=None, warn=False):
        self.asked.append((title, text, info, [b for b, _ in buttons], default, warn))
        return self.answers.pop(0) if self.answers else None

    def request(self, op, target="", *answers):
        self.answers = list(answers)
        with mock.patch.object(self.dialogs, "ask", side_effect=self.fake_ask):
            self.actions.request_worktree(op, target)

    # ---- New --------------------------------------------------------------

    def test_new_asks_with_the_branches_in_use_and_adds_it(self):
        command = GitCommand(CommandKind.WORKTREE_ADD, path="/w/app-free", branch="free")
        self.session.add_worktree.return_value = command
        answer = NewWorktree("/w/app-free", "free", None, True)
        with mock.patch.object(self.dialogs, "ask_new", return_value=answer) as ask:
            self.actions.request_worktree(WorktreeOp.NEW, "free")
        branches, used, main_root, head, start = ask.call_args.args
        self.assertEqual(used, {"main": "app", "own": "app-own", "side": "app-side",
                                "gone": "app-gone"})
        self.assertEqual((branches, main_root, head, start),
                         (self.session.branches, Path("/w/app"), "own", "free"))
        self.session.add_worktree.assert_called_once_with("/w/app-free", "free", None)
        self.assertEqual(self.opened, [])
        self.signals.command_finished.emit(command, True)
        self.assertEqual(self.opened, ["/w/app-free"])  # Open it
        self.reader.refresh.assert_called_once_with()
        self.signals.command_finished.emit(command, True)  # once only
        self.assertEqual(self.opened, ["/w/app-free"])

    def test_new_that_fails_or_without_open_it_opens_nothing(self):
        failing = GitCommand(CommandKind.WORKTREE_ADD)
        quiet = GitCommand(CommandKind.WORKTREE_ADD)
        self.session.add_worktree.side_effect = [failing, quiet]
        with mock.patch.object(self.dialogs, "ask_new", side_effect=[
                NewWorktree("/w/a", "a", "main", True), NewWorktree("/w/b", "b", "main", False)]):
            self.actions.request_worktree(WorktreeOp.NEW)
            self.actions.request_worktree(WorktreeOp.NEW)
        self.signals.command_finished.emit(failing, False)
        self.signals.command_finished.emit(quiet, True)
        self.assertEqual(self.opened, [])
        self.assertEqual(self.reader.refresh.call_count, 2)  # the list follows either way

    def test_new_cancelled_adds_nothing(self):
        with mock.patch.object(self.dialogs, "ask_new", return_value=None):
            self.actions.request_worktree(WorktreeOp.NEW)
        self.session.add_worktree.assert_not_called()

    def test_other_commands_refresh_nothing(self):
        self.signals.command_finished.emit(GitCommand(CommandKind.COMMIT), True)
        self.reader.refresh.assert_not_called()
        self.signals.command_finished.emit(GitCommand(CommandKind.WORKTREE_PRUNE), True)
        self.reader.refresh.assert_called_once_with()

    # ---- Remove ----------------------------------------------------------

    def test_remove_a_clean_worktree_asks_once_and_closes_its_window_first(self):
        self.request(WorktreeOp.REMOVE, self.SIDE.path, None)
        self.session.remove_worktree.assert_not_called()
        self.request(WorktreeOp.REMOVE, self.SIDE.path, REMOVE)
        self.session.remove_worktree.assert_called_once_with(self.SIDE.path, force=False)
        self.assertEqual(self.calls, ["close", "remove"])
        title, text, info, labels, default, warn = self.asked[0]
        self.assertEqual((text, labels, default, warn),
                         ("Remove worktree app-side?", [REMOVE], None, True))
        self.assertIn("deleted, with any ignored files", info)
        self.assertIn("The branch side is kept.", info)
        self.assertEqual(len(self.asked), 2)  # no second box

    def test_remove_with_changes_needs_remove_anyway(self):
        self.reader.states[self.SIDE.path] = WorktreeState(3)
        self.request(WorktreeOp.REMOVE, self.SIDE.path, REMOVE, None)
        self.session.remove_worktree.assert_not_called()
        self.other_window.close.assert_not_called()
        self.assertEqual(self.asked[1][1], "app-side has 3 changed files.")
        self.assertEqual(self.asked[1][3], [REMOVE_ANYWAY])
        self.request(WorktreeOp.REMOVE, self.SIDE.path, REMOVE, REMOVE_ANYWAY)
        self.session.remove_worktree.assert_called_once_with(self.SIDE.path, force=True)

    def test_remove_a_missing_worktree_removes_only_its_record(self):
        self.reader.states[self.GONE.path] = WorktreeState(3)  # stale: not asked again
        self.request(WorktreeOp.REMOVE, self.GONE.path, REMOVE)
        self.assertIn("already gone: only git's record", self.asked[0][2])
        self.assertEqual(len(self.asked), 1)
        self.session.remove_worktree.assert_called_once_with(self.GONE.path, force=False)

    def test_remove_refuses_the_main_a_locked_and_this_window_s_worktree(self):
        for worktree in (self.MAIN, self.LOCKED, self.OWN):
            self.request(WorktreeOp.REMOVE, worktree.path, REMOVE, REMOVE_ANYWAY)
        self.request(WorktreeOp.REMOVE, "/w/unknown", REMOVE)
        self.assertEqual(self.asked, [])
        self.session.remove_worktree.assert_not_called()

    # ---- Prune -----------------------------------------------------------

    def test_prune_lists_the_missing_and_names_the_locked_as_kept(self):
        self.request(WorktreeOp.PRUNE, "", None)
        self.session.prune_worktrees.assert_not_called()
        self.request(WorktreeOp.PRUNE, "", PRUNE)
        self.session.prune_worktrees.assert_called_once_with()
        _title, text, info, labels, _default, warn = self.asked[0]
        self.assertEqual((text, labels, warn), ("Prune 1 missing worktree?", [PRUNE], True))
        listed, kept = info.split("Locked, so kept:")
        self.assertIn("app-gone  (/w/app-gone)", listed)
        self.assertNotIn("usb", listed)
        self.assertIn("usb  (/w/usb)", kept)

    def test_prune_with_nothing_to_prune_asks_nothing(self):
        self.reader.worktrees = (self.MAIN, self.GONE_LOCKED)
        self.request(WorktreeOp.PRUNE, "", PRUNE)
        self.assertEqual(self.asked, [])
        self.session.prune_worktrees.assert_not_called()

    # ---- Folder and terminal -------------------------------------------

    def test_open_folder(self):
        with mock.patch.object(self.dialogs, "open_url", return_value=True) as open_url, \
                mock.patch.object(self.dialogs, "warn") as warn:
            self.actions.request_worktree(WorktreeOp.OPEN_FOLDER, "/w/app-side")
        self.assertEqual(open_url.call_args.args[0].toLocalFile(), "/w/app-side")
        warn.assert_not_called()
        with mock.patch.object(self.dialogs, "open_url", return_value=False), \
                mock.patch.object(self.dialogs, "warn") as warn:
            self.actions.request_worktree(WorktreeOp.OPEN_FOLDER, "/w/app-side")
        warn.assert_called_once()

    def test_open_terminal_starts_it_in_the_folder(self):
        with mock.patch.object(self.dialogs, "terminal", return_value=["kitty", "-1"]), \
                mock.patch.object(self.dialogs, "start_detached", return_value=True) as start, \
                mock.patch.object(self.dialogs, "warn") as warn:
            self.actions.request_worktree(WorktreeOp.OPEN_TERMINAL, "/w/app-side")
        start.assert_called_once_with(["kitty", "-1"], "/w/app-side")
        warn.assert_not_called()

    def test_open_terminal_says_when_there_is_none_or_it_fails(self):
        with mock.patch.object(self.dialogs, "terminal", return_value=None), \
                mock.patch.object(self.dialogs, "start_detached") as start, \
                mock.patch.object(self.dialogs, "warn") as warn:
            self.actions.request_worktree(WorktreeOp.OPEN_TERMINAL, "/w/app-side")
        start.assert_not_called()
        self.assertIn("$TERMINAL", warn.call_args.args[2])
        with mock.patch.object(self.dialogs, "terminal", return_value=["konsole"]), \
                mock.patch.object(self.dialogs, "start_detached", return_value=False), \
                mock.patch.object(self.dialogs, "warn") as warn:
            self.actions.request_worktree(WorktreeOp.OPEN_TERMINAL, "/w/app-side")
        self.assertEqual(warn.call_args.args[1], "Couldn't start konsole in /w/app-side.")

    def test_start_detached_really_starts_a_program_in_the_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"  # absolute: never written where the tests run
            self.assertTrue(self.dialogs.start_detached(["sh", "-c", 'pwd > "$0"', str(out)],
                                                        tmp))
            for _ in range(200):
                if out.exists() and out.read_text():
                    break
                time.sleep(0.01)
            self.assertEqual(out.read_text().strip(), str(Path(tmp).resolve()))
            self.assertFalse(self.dialogs.start_detached(["/no/such/terminal"], tmp))


if __name__ == "__main__":
    unittest.main()
