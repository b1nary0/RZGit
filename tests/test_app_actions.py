import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtGui import QKeySequence

from rzgitclient.core.repo_session import RepoSession
from rzgitclient.ui.app_actions import AppActions
from tests.support import make_repo, qt_app


class AppActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        self.session = RepoSession()
        self.actions = AppActions(self.session)

    def tearDown(self):
        self._tmp.cleanup()

    def test_refresh_enabled_only_with_a_repo(self):
        self.assertFalse(self.actions.refresh.isEnabled())
        self.session.open(self.repo)
        self.assertTrue(self.actions.refresh.isEnabled())
        self.session.close()
        self.assertFalse(self.actions.refresh.isEnabled())

    def test_enabled_state_matches_a_session_opened_earlier(self):
        self.session.open(self.repo)
        actions = AppActions(self.session)
        self.assertTrue(actions.refresh.isEnabled())

    def test_refresh_refreshes_everything(self):
        self.session.open(self.repo)
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


if __name__ == "__main__":
    unittest.main()
