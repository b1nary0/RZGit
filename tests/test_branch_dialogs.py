import unittest
from unittest import mock

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QMessageBox, QWidget

from rzgit.core.repo_session import RepoSession
from rzgit.core.selection import Selection
from rzgit.git.git_models import Branch, FileStatus
from rzgit.ui.app_actions import AppActions, BranchOp
from rzgit.ui.branch_dialogs import BRING, STASH, BranchDialogs
from tests.support import qt_app


class BranchDialogsTests(unittest.TestCase):
    """What each branch operation asks, and what it then runs (spec §8)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.real_session = RepoSession()
        self.addCleanup(self.real_session.shutdown)
        self.actions = AppActions(self.real_session, Selection(self.real_session))
        self.session = mock.Mock(spec=RepoSession)
        self.session.head = "main"
        self.session.status = ()
        self.session.branches = (Branch("main", True, True), Branch("done", False, True),
                                 Branch("wip", False, False))
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.dialogs = BranchDialogs(self.window, self.session, self.actions)
        self.asked = []  # (title, text, info, labels, default, warn)
        self.answers = []  # clicked labels, in order; None = Cancel

    def fake_ask(self, title, text, info, buttons, *, default=None, warn=False):
        self.asked.append((title, text, info, [b for b, _ in buttons], default, warn))
        return self.answers.pop(0) if self.answers else None

    def request(self, op, name, *answers):
        self.answers = list(answers)
        with mock.patch.object(self.dialogs, "ask", side_effect=self.fake_ask):
            self.actions.request_branch(op, name)

    def test_switch_asks_then_switches(self):
        self.request(BranchOp.SWITCH, "done", None)
        self.session.switch_branch.assert_not_called()
        self.request(BranchOp.SWITCH, "done", "Switch")
        self.session.switch_branch.assert_called_once_with("done")
        self.assertEqual(self.asked[0][1], "Switch to done?")
        self.assertEqual(self.asked[0][3:5], (["Switch"], "Switch"))

    def test_switching_to_the_current_branch_does_nothing(self):
        self.request(BranchOp.SWITCH, "main", "Switch")
        self.assertEqual(self.asked, [])
        self.session.switch_branch.assert_not_called()

    def test_switch_with_changes_offers_to_stash_them(self):
        self.session.status = (FileStatus("?", "?", "new.txt"), FileStatus(" ", "M", "a.txt"))
        self.request(BranchOp.SWITCH, "done", STASH)
        self.session.switch_branch.assert_called_once_with("done", stash=True)
        _title, _text, info, labels, default, _warn = self.asked[0]
        self.assertEqual((labels, default), ([STASH, BRING], STASH))
        self.assertIn("2 changed files", info)
        self.assertIn("RZGit: before switching to done", info)
        self.assertIn("Stashes panel", info)

        self.session.reset_mock()
        self.request(BranchOp.SWITCH, "done", BRING)
        self.session.switch_branch.assert_called_once_with("done", stash=False)
        self.session.reset_mock()
        self.request(BranchOp.SWITCH, "done", None)
        self.session.switch_branch.assert_not_called()

    def test_new_branch(self):
        with mock.patch.object(self.dialogs, "ask_name", return_value=("topic", True)) as ask:
            self.actions.request_branch(BranchOp.NEW, "done")
        self.assertEqual(ask.call_args.kwargs, {"check": "Switch to it"})
        self.assertEqual(ask.call_args.args[1], "New branch from done:")
        self.session.create_branch.assert_called_once_with("topic", "done", switch=True)
        with mock.patch.object(self.dialogs, "ask_name", return_value=None):
            self.actions.request_branch(BranchOp.NEW, "done")
        self.session.create_branch.assert_called_once()

    def test_rename(self):
        with mock.patch.object(self.dialogs, "ask_name", return_value=("main", False)) as ask:
            self.actions.request_branch(BranchOp.RENAME, "main")
        self.assertEqual(ask.call_args.args[2], "main")  # filled with the old name
        self.session.rename_branch.assert_not_called()  # unchanged
        with mock.patch.object(self.dialogs, "ask_name", return_value=("trunk", False)):
            self.actions.request_branch(BranchOp.RENAME, "main")
        self.session.rename_branch.assert_called_once_with("main", "trunk")

    def test_deleting_a_merged_branch_asks_once(self):
        self.request(BranchOp.DELETE, "done", "Delete")
        self.session.delete_branch.assert_called_once_with("done")
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.asked[0][4:], (None, True))  # Cancel is the default

    def test_deleting_an_unmerged_branch_asks_again(self):
        self.request(BranchOp.DELETE, "wip", "Delete", None)
        self.session.delete_branch.assert_not_called()
        self.assertEqual(self.asked[1][1], "wip isn't merged into main.")
        self.assertIn("lost", self.asked[1][2])
        self.request(BranchOp.DELETE, "wip", "Delete", "Delete Anyway")
        self.session.delete_branch.assert_called_once_with("wip", force=True)

    def test_cancelling_delete_deletes_nothing(self):
        self.request(BranchOp.DELETE, "done", None)
        self.assertEqual(len(self.asked), 1)
        self.session.delete_branch.assert_not_called()

    def test_merge_and_rebase_ask_and_say_a_conflict_aborts(self):
        self.request(BranchOp.MERGE, "wip", "Merge")
        self.session.merge.assert_called_once_with("wip")
        self.request(BranchOp.REBASE, "wip", "Rebase")
        self.session.rebase.assert_called_once_with("wip")
        merge, rebase = self.asked
        self.assertEqual(merge[1], "Merge wip into main?")
        self.assertEqual(rebase[1], "Rebase main onto wip?")
        self.assertIn("rewrites the commits of main", rebase[2])
        self.assertEqual(rebase[4:], (None, True))  # Cancel is the default
        for box in (merge, rebase):
            self.assertIn("aborted and nothing changes", box[2])

    def test_a_detached_head_is_named_head(self):
        self.session.head = None
        self.request(BranchOp.MERGE, "wip", None)
        self.assertEqual(self.asked[0][1], "Merge wip into HEAD?")

    # ---- the real boxes ---------------------------------------------------

    def answer_later(self, act):
        def run():
            box = QApplication.activeModalWidget()
            try:
                act(box)
            finally:
                if box is not None and box.isVisible():
                    box.reject()  # never leave the test waiting on a dialog
        QTimer.singleShot(0, run)

    def test_ask_returns_the_clicked_label_and_defaults_to_cancel(self):
        seen = {}

        def click(box):
            seen["default_is_cancel"] = (
                box.defaultButton() is box.button(QMessageBox.StandardButton.Cancel))
            next(b for b in box.buttons() if b.text() == "Delete").click()
        self.answer_later(click)
        role = QMessageBox.ButtonRole.DestructiveRole
        self.assertEqual(self.dialogs.ask("T", "Text", "", [("Delete", role)]), "Delete")
        self.assertTrue(seen["default_is_cancel"])
        self.answer_later(lambda box: box.reject())
        self.assertIsNone(self.dialogs.ask("T", "Text", "", [("Delete", role)]))

    def test_ask_name_strips_and_reports_the_check(self):
        def fill(dialog):
            dialog.findChild(QLineEdit).setText("  topic  ")
            dialog.findChild(QCheckBox).setChecked(False)
            dialog.accept()
        self.answer_later(fill)
        self.assertEqual(self.dialogs.ask_name("New", "Name:", "", check="Switch to it"),
                         ("topic", False))

    def test_ask_name_cannot_accept_an_empty_name(self):
        seen = {}

        def look(dialog):
            from PySide6.QtWidgets import QDialogButtonBox
            ok = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
            seen["empty"] = ok.isEnabled()
            dialog.findChild(QLineEdit).setText("x")
            seen["filled"] = ok.isEnabled()
            dialog.reject()
        self.answer_later(look)
        self.assertIsNone(self.dialogs.ask_name("New", "Name:", ""))
        self.assertEqual(seen, {"empty": False, "filled": True})


if __name__ == "__main__":
    unittest.main()
