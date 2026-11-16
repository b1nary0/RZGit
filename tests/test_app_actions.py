import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtGui import QKeySequence

from rzgitclient.core.repo_session import RepoSession
from rzgitclient.core.selection import FileSelection, FileSource, Selection
from rzgitclient.ui.app_actions import AppActions, BranchOp
from rzgitclient.ui.theme.theme_manager import ThemeMode
from tests.support import commit_file, git, make_repo, qt_app, wait_idle

W, I = FileSource.WORKTREE, FileSource.INDEX


class AppActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        self.session = RepoSession()
        self.selection = Selection(self.session)
        self.actions = AppActions(self.session, self.selection)

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    def enabled(self):
        a = self.actions
        return {name for name in ("stage", "unstage", "discard", "stage_all", "unstage_all")
                if getattr(a, name).isEnabled()}

    def test_theme_modes_are_one_of_three(self):
        modes = self.actions.theme_modes
        self.assertEqual([a.text() for a in modes.values()], ["&Light", "&Dark", "&System"])
        self.assertTrue(all(a.isCheckable() for a in modes.values()))
        modes[ThemeMode.DARK].trigger()
        modes[ThemeMode.SYSTEM].trigger()
        self.assertEqual([m for m, a in modes.items() if a.isChecked()], [ThemeMode.SYSTEM])

    def test_colour_blind_is_checkable_and_off(self):
        action = self.actions.colour_blind
        self.assertEqual(action.text(), "Colour-blind &Friendly Colours")
        self.assertTrue(action.isCheckable())
        self.assertFalse(action.isChecked())

    def test_refresh_enabled_only_with_a_repo(self):
        self.assertFalse(self.actions.refresh.isEnabled())
        self.session.open(self.repo)
        wait_idle(self.session)
        self.assertTrue(self.actions.refresh.isEnabled())
        self.session.close()
        self.assertFalse(self.actions.refresh.isEnabled())

    def test_enabled_state_matches_a_session_opened_earlier(self):
        self.session.open(self.repo)
        wait_idle(self.session)
        actions = AppActions(self.session, self.selection)
        self.assertTrue(actions.refresh.isEnabled())

    def test_refresh_refreshes_everything(self):
        self.session.open(self.repo)
        wait_idle(self.session)
        with mock.patch.object(self.session, "refresh") as refresh:
            self.actions.refresh.trigger()
        refresh.assert_called_once_with()

    def test_open_and_quit_are_always_enabled(self):
        self.assertTrue(self.actions.open_repo.isEnabled())
        self.assertTrue(self.actions.quit.isEnabled())

    def test_shortcuts_are_the_platform_standard_keys(self):
        # The platform decides the keys (offscreen Qt has no Quit key; KDE uses Ctrl+Q).
        expected = {
            self.actions.open_repo: QKeySequence.StandardKey.Open,
            self.actions.refresh: QKeySequence.StandardKey.Refresh,
            self.actions.quit: QKeySequence.StandardKey.Quit,
        }
        for action, key in expected.items():
            with self.subTest(action.text()):
                self.assertEqual(action.shortcuts(), QKeySequence.keyBindings(key))

    # ---- staging ---------------------------------------------------------

    def test_staging_actions_follow_the_selection_and_the_status(self):
        self.assertEqual(self.enabled(), set())
        commit_file(self.repo, "a.txt", "1", "first")
        (self.repo / "a.txt").write_text("2")
        (self.repo / "b.txt").write_text("new")
        git(self.repo, "add", "b.txt")
        self.session.open(self.repo)
        wait_idle(self.session)
        self.assertEqual(self.enabled(), {"stage_all", "unstage_all"})
        self.selection.files = (FileSelection("a.txt", W),)
        self.assertEqual(self.enabled(), {"stage", "discard", "stage_all", "unstage_all"})
        self.selection.files = (FileSelection("b.txt", I),)
        self.assertEqual(self.enabled(), {"unstage", "stage_all", "unstage_all"})

    def test_stage_and_unstage_act_on_the_selected_rows_of_their_kind(self):
        self.selection.files = (FileSelection("a", W), FileSelection("b", I), FileSelection("c", W))
        with mock.patch.object(self.session, "stage") as stage, \
                mock.patch.object(self.session, "unstage") as unstage:
            self.actions.stage.trigger()
            self.actions.unstage.trigger()
        stage.assert_called_once_with(("a", "c"))
        unstage.assert_called_once_with(("b",))

    def test_discard_is_left_to_the_window(self):
        self.selection.files = (FileSelection("a", W),)
        with mock.patch.object(self.session, "discard") as discard:
            self.actions.discard.trigger()
        discard.assert_not_called()

    def test_staging_shortcuts(self):
        keys = {"stage": "Ctrl+S", "unstage": "Ctrl+U", "discard": "Ctrl+Backspace",
                "stage_all": "Ctrl+Shift+S", "unstage_all": "Ctrl+Shift+U"}
        for name, key in keys.items():
            with self.subTest(name):
                self.assertEqual(getattr(self.actions, name).shortcut(), QKeySequence(key))


    def test_commit_needs_a_repo_something_staged_and_a_ready_message(self):
        commit = self.actions.commit
        self.actions.set_commit_ready(True)
        self.assertFalse(commit.isEnabled())  # no repo
        commit_file(self.repo, "a.txt", "1", "first")
        self.session.open(self.repo)
        wait_idle(self.session)
        self.assertFalse(commit.isEnabled())  # nothing staged
        (self.repo / "a.txt").write_text("2")
        git(self.repo, "add", "a.txt")
        self.session.refresh()
        wait_idle(self.session)
        self.assertTrue(commit.isEnabled())
        self.actions.set_commit_ready(False)
        self.assertFalse(commit.isEnabled())

    def test_commit_shortcut_is_ctrl_enter(self):
        self.assertEqual(self.actions.commit.shortcuts(),
                         [QKeySequence("Ctrl+Return"), QKeySequence("Ctrl+Enter")])

    def test_request_branch_sends_branch_requested(self):
        seen = []
        self.actions.branch_requested.connect(lambda op, name: seen.append((op, name)))
        self.actions.request_branch(BranchOp.MERGE, "feature")
        self.assertEqual(seen, [(BranchOp.MERGE, "feature")])


    # ---- Amend Last Commit (spec §8) --------------------------------------

    def open_with_commit(self):
        commit_file(self.repo, "a.txt", "1", "first")
        self.session.open(self.repo)
        wait_idle(self.session)

    def test_amend_is_enabled_only_with_commits(self):
        self.assertFalse(self.actions.amend.isEnabled())
        self.session.open(self.repo)
        wait_idle(self.session)
        self.assertFalse(self.actions.amend.isEnabled())  # no commits yet
        commit_file(self.repo, "a.txt", "1", "first")
        self.session.refresh()
        wait_idle(self.session)
        self.assertTrue(self.actions.amend.isEnabled())

    def test_amend_lets_commit_run_with_nothing_staged(self):
        self.open_with_commit()
        self.actions.set_commit_ready(True)
        self.assertFalse(self.actions.commit.isEnabled())
        self.actions.amend.setChecked(True)
        self.assertTrue(self.actions.commit.isEnabled())
        self.actions.amend.setChecked(False)
        self.assertFalse(self.actions.commit.isEnabled())

    def test_amend_turns_off_when_head_moves_but_not_on_a_refresh(self):
        self.open_with_commit()
        self.actions.amend.setChecked(True)
        self.session.refresh()
        wait_idle(self.session)
        self.assertTrue(self.actions.amend.isChecked())
        commit_file(self.repo, "b.txt", "1", "second")
        self.session.refresh()
        wait_idle(self.session)
        self.assertFalse(self.actions.amend.isChecked())

    def test_amend_turns_off_when_the_repository_changes(self):
        self.open_with_commit()
        self.actions.amend.setChecked(True)
        self.session.open(self.repo)
        self.assertFalse(self.actions.amend.isChecked())
        wait_idle(self.session)
        self.actions.amend.setChecked(True)
        self.session.close()
        self.assertFalse(self.actions.amend.isChecked())
        self.assertFalse(self.actions.amend.isEnabled())

    def test_confirm_amend_asks_the_confirmer(self):
        from rzgitclient.git.git_models import LastCommit
        last = LastCommit("abc", "m", True)
        self.assertTrue(self.actions.confirm_amend(last))  # none set
        seen = []
        self.actions.set_amend_confirmer(lambda l: seen.append(l) or False)
        self.assertFalse(self.actions.confirm_amend(last))
        self.assertEqual(seen, [last])


if __name__ == "__main__":
    unittest.main()
