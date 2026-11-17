import unittest
from datetime import datetime, timezone
from unittest import mock

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QMessageBox, QWidget

from rzgitclient.core.repo_session import RepoSession
from rzgitclient.core.selection import Selection
from rzgitclient.git.git_models import Stash
from rzgitclient.ui.app_actions import AppActions, StashOp
from rzgitclient.ui.stash_dialogs import DROP, StashDialogs
from tests.support import qt_app

STASH = Stash("a" * 40, "Test User", datetime(2026, 11, 16, tzinfo=timezone.utc), "wip",
              1, "main", "wip", True)


class StashDialogsTests(unittest.TestCase):
    """What each stash operation asks, and what it then runs (RZG-36, spec §8)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.real_session = RepoSession()
        self.addCleanup(self.real_session.shutdown)
        self.actions = AppActions(self.real_session, Selection(self.real_session))
        self.session = mock.Mock(spec=RepoSession)
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.dialogs = StashDialogs(self.window, self.session, self.actions)

    def test_apply_and_pop_run_at_once_by_sha(self):
        self.dialogs.confirm_drop = mock.Mock()
        self.actions.request_stash(StashOp.APPLY, STASH)
        self.session.apply_stash.assert_called_once_with(STASH.sha, pop=False)
        self.session.reset_mock()
        self.actions.request_stash(StashOp.POP, STASH)
        self.session.apply_stash.assert_called_once_with(STASH.sha, pop=True)
        self.dialogs.confirm_drop.assert_not_called()

    def test_drop_asks_first(self):
        self.dialogs.confirm_drop = mock.Mock(return_value=False)
        self.actions.request_stash(StashOp.DROP, STASH)
        self.dialogs.confirm_drop.assert_called_once_with(STASH)
        self.session.drop_stash.assert_not_called()
        self.dialogs.confirm_drop.return_value = True
        self.actions.request_stash(StashOp.DROP, STASH)
        self.session.drop_stash.assert_called_once_with(STASH.sha)

    def test_stash_changes_asks_for_the_message(self):
        self.dialogs.ask_stash = mock.Mock(return_value=None)
        self.actions.stash_changes.setEnabled(True)
        self.actions.stash_changes.trigger()
        self.session.stash_changes.assert_not_called()
        self.dialogs.ask_stash.return_value = ("half done", False)
        self.actions.stash_changes.trigger()
        self.session.stash_changes.assert_called_once_with("half done", False)

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

    def test_the_drop_box_says_it_can_t_be_undone_and_defaults_to_cancel(self):
        seen = {}

        def look(box):
            seen["text"] = box.text() + " " + box.informativeText()
            seen["cancel"] = box.defaultButton() is box.button(QMessageBox.StandardButton.Cancel)
            next(b for b in box.buttons() if b.text() == DROP).click()
        self.answer_later(look)
        self.assertTrue(self.dialogs.confirm_drop(STASH))
        self.assertIn("“wip”", seen["text"])
        self.assertIn("stash@{1}, made on main", seen["text"])
        self.assertIn("can't be undone", seen["text"])
        self.assertTrue(seen["cancel"])
        self.answer_later(lambda box: box.reject())
        self.assertFalse(self.dialogs.confirm_drop(STASH))

    def test_the_stash_box_strips_the_message_and_includes_untracked_files_by_default(self):
        seen = {}

        def fill(dialog):
            seen["checked"] = dialog.findChild(QCheckBox).isChecked()
            dialog.findChild(QLineEdit).setText("  wip  ")
            dialog.accept()
        self.answer_later(fill)
        self.assertEqual(self.dialogs.ask_stash(), ("wip", True))
        self.assertTrue(seen["checked"])
        self.answer_later(lambda dialog: dialog.reject())
        self.assertIsNone(self.dialogs.ask_stash())


if __name__ == "__main__":
    unittest.main()
