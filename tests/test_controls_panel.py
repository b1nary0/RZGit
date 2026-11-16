import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QToolButton

from rzgitclient.ui.panels.controls_panel import ControlsPanel
from rzgitclient.ui.theme.style_roles import SURFACE_PROPERTY
from tests.support import commit_file, git, make_panel_context, make_repo, qt_app, wait_idle


class ControlsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.repo, "a.txt", "1", "first")
        self.ctx = make_panel_context()
        self.addCleanup(self.ctx.session.shutdown)
        self.panel = ControlsPanel(self.ctx)

    def open(self):
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)

    def test_a_bar_without_header_fixed_and_always_shown(self):
        self.assertEqual(self.panel.property(SURFACE_PROPERTY), "chrome")
        self.assertFalse(ControlsPanel.show_header)
        self.assertTrue(ControlsPanel.fixed_height)
        self.assertTrue(ControlsPanel.always_shown)

    def test_buttons_are_the_app_actions(self):
        buttons = self.panel.findChildren(QToolButton)
        actions = self.ctx.actions
        self.assertEqual([b.defaultAction() for b in buttons], [actions.open_repo, actions.refresh])
        self.assertEqual([b.text() for b in buttons], ["Open Repository", "Refresh"])  # no & or …
        self.assertFalse(buttons[1].isEnabled())  # Refresh, with no repo open
        self.open()
        self.assertTrue(buttons[1].isEnabled())

    def test_branch_label(self):
        self.assertEqual(self.panel._branch.text(), "")  # no repo
        self.open()
        self.assertEqual(self.panel._branch.text(), "main")
        git(self.repo, "checkout", "-q", "--detach")
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        self.assertEqual(self.panel._branch.text(), "detached HEAD")
        self.ctx.session.close()
        self.assertEqual(self.panel._branch.text(), "")


if __name__ == "__main__":
    unittest.main()
