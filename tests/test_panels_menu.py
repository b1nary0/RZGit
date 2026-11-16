import unittest

from PySide6.QtCore import QCoreApplication, QEvent

from rzgitclient.ui.layout.layout_host import LayoutHost
from rzgitclient.ui.layout.layout_model import PanelNode, SplitNode, Orientation
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT
from rzgitclient.ui.layout.panels_menu import PanelsMenu
from rzgitclient.ui.panels.panel_registry import PANEL_TYPES
from tests.support import make_panel_context, qt_app


class PanelsMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.host = LayoutHost(make_panel_context())
        self.host.apply(DEFAULT_LAYOUT)
        self.menu = PanelsMenu(self.host)
        self.actions = {t.panel_id: a for t, a in zip(PANEL_TYPES, self.menu.actions())}

    def tearDown(self):
        self.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def checked(self):
        return {pid for pid, a in self.actions.items() if a.isChecked()}

    def test_an_entry_per_panel_checked_when_shown(self):
        self.assertEqual([a.text() for a in self.menu.actions()], [t.title for t in PANEL_TYPES])
        self.assertTrue(all(a.isCheckable() for a in self.menu.actions()))
        self.assertEqual(self.checked(), {"controls", "branches", "files", "history"})

    def test_controls_is_checked_and_can_not_be_unchecked(self):
        self.assertTrue(self.actions["controls"].isChecked())
        self.assertFalse(self.actions["controls"].isEnabled())

    def test_unchecking_hides_and_checking_shows(self):
        self.actions["branches"].trigger()
        self.assertNotIn("branches", self.host.panels)
        self.assertEqual(self.checked(), {"controls", "files", "history"})
        self.actions["branches"].trigger()
        self.assertIn("branches", self.host.panels)
        self.assertEqual(self.checked(), {"controls", "branches", "files", "history"})

    def test_follows_layout_changes_made_elsewhere(self):
        self.host.apply(SplitNode(Orientation.HORIZONTAL, (PanelNode("files"), PanelNode("history"))))
        self.assertEqual(self.checked(), {"controls", "files", "history"})

    def test_the_last_visible_panel_can_not_be_unchecked(self):
        self.actions["branches"].trigger()
        self.actions["files"].trigger()
        self.assertFalse(self.actions["history"].isEnabled())
        self.assertTrue(self.actions["files"].isEnabled())  # hidden ones can be shown
        self.actions["files"].trigger()
        self.assertTrue(self.actions["history"].isEnabled())


if __name__ == "__main__":
    unittest.main()
