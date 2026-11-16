import unittest

from PySide6.QtCore import QCoreApplication, QEvent

from rzgitclient.ui.layout.layout_host import LayoutHost
from rzgitclient.ui.layout.layout_menu import LayoutMenu
from rzgitclient.ui.layout.layout_model import PanelNode, SplitNode
from rzgitclient.ui.layout.layout_presets import PRESETS
from tests.support import make_panel_context, qt_app


def shape(node):
    """The tree without sizes: the host reports the splitters' real pixel sizes."""
    if isinstance(node, SplitNode):
        return SplitNode(node.orientation, tuple(shape(c) for c in node.children))
    return node


class LayoutMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.host = LayoutHost(make_panel_context())
        self.menu = LayoutMenu(self.host)

    def tearDown(self):
        self.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_an_entry_per_preset(self):
        self.assertEqual([a.text() for a in self.menu.actions()], [p.title for p in PRESETS])

    def test_choosing_a_preset_shows_it_with_every_panel(self):
        self.host.apply(PanelNode("history"), {})
        self.host.show_panel("files")
        self.host.hide_panel("files")
        self.assertEqual(set(self.host.hidden), {"files"})
        for action, preset in zip(self.menu.actions(), PRESETS):
            with self.subTest(preset.title):
                action.trigger()
                self.assertEqual(shape(self.host.current_layout()), shape(preset.root))
                self.assertEqual(self.host.hidden, {})


if __name__ == "__main__":
    unittest.main()
