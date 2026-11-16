import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.ui.layout.layout_host import LayoutHost
from rzgitclient.ui.layout.layout_menu import LayoutMenu
from rzgitclient.ui.layout.layout_model import (
    LayoutState,
    PanelNode,
    SplitNode,
    remove_panel,
    to_dict,
)
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT, PRESETS
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
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings = AppSettings(Path(tmp.name) / "settings.ini")
        self.host = LayoutHost(make_panel_context())
        self.host.apply(DEFAULT_LAYOUT)
        self.menu = LayoutMenu(self.host, self.settings)

    def texts(self):
        return [a.text() for a in self.menu.actions()]

    def action(self, text):
        return next(a for a in self.menu.actions() if a.text() == text)

    def tearDown(self):
        self.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_an_entry_per_preset_then_save_and_manage(self):
        self.assertEqual(self.texts(), [p.title for p in PRESETS]
                         + ["", "&Save Layout As…", "&Manage Layouts…"])
        self.assertFalse(self.action("&Manage Layouts…").isEnabled())  # nothing saved yet

    def test_saved_layouts_are_listed_below_the_presets(self):
        self.settings.saved_layouts = [("Review", {}), ("Wide Diff", {})]
        self.menu.rebuild()
        self.assertEqual(self.texts(), [p.title for p in PRESETS]
                         + ["", "Review", "Wide Diff", "", "&Save Layout As…", "&Manage Layouts…"])
        self.assertTrue(self.action("&Manage Layouts…").isEnabled())

    def test_save_as_saves_the_current_layout_with_hidden_panels_and_lists_it(self):
        self.host.hide_panel("branches")
        with mock.patch.object(self.menu, "ask_name", return_value="  Review "):
            self.action("&Save Layout As…").trigger()
        self.assertEqual(self.settings.saved_layouts, [("Review", to_dict(self.host.state()))])
        self.assertIn("branches", self.settings.saved_layouts[0][1]["hidden"])
        self.assertIn("Review", self.texts())

    def test_cancelling_the_name_prompt_saves_nothing(self):
        with mock.patch.object(self.menu, "ask_name", return_value=None):
            self.menu.save_as()
        self.assertEqual(self.settings.saved_layouts, [])

    def test_opening_a_saved_layout_restores_it_and_its_hidden_panels(self):
        self.host.hide_panel("branches")
        self.menu.save("Review")
        self.menu.apply(PRESETS[2])
        self.assertEqual(self.host.hidden, {})
        self.action("Review").trigger()
        self.assertEqual(set(self.host.hidden), {"branches"})
        self.assertEqual(shape(self.host.current_layout()),
                         shape(remove_panel(DEFAULT_LAYOUT, "branches")[0]))

    def test_a_saved_layout_with_an_unknown_panel_still_opens(self):
        data = to_dict(LayoutState(SplitNode(DEFAULT_LAYOUT.orientation,
                                             (*DEFAULT_LAYOUT.children, PanelNode("gone")))))
        self.settings.saved_layouts = [("Old", data)]
        self.menu.rebuild()
        self.action("Old").trigger()
        self.assertEqual(shape(self.host.current_layout()), shape(DEFAULT_LAYOUT))

    def test_saving_over_a_saved_name_asks_and_keeps_its_place(self):
        self.settings.saved_layouts = [("Review", {"old": 1}), ("Other", {})]
        with mock.patch.object(self.menu, "confirm_replace", return_value=False) as confirm:
            self.assertFalse(self.menu.save("review"))
            confirm.assert_called_once_with("Review")
        self.assertEqual(self.settings.saved_layouts[0], ("Review", {"old": 1}))
        with mock.patch.object(self.menu, "confirm_replace", return_value=True):
            self.assertTrue(self.menu.save("review"))
        self.assertEqual([n for n, _ in self.settings.saved_layouts], ["review", "Other"])
        self.assertEqual(self.settings.saved_layouts[0][1], to_dict(self.host.state()))

    def test_builtin_and_empty_names_are_refused(self):
        for name in ("Standard", "sidebar tabs", "   "):
            with self.subTest(name), mock.patch.object(self.menu, "show_problem") as problem:
                self.assertFalse(self.menu.save(name))
                problem.assert_called_once()
        self.assertEqual(self.settings.saved_layouts, [])

    def test_the_menu_is_rebuilt_after_managing(self):
        self.settings.saved_layouts = [("Review", {})]
        self.menu.rebuild()
        with mock.patch("rzgitclient.ui.layout.layout_menu.ManageLayoutsDialog") as dialog:
            dialog.return_value.exec.side_effect = lambda: setattr(
                self.settings, "saved_layouts", [("Renamed", {})])
            self.action("&Manage Layouts…").trigger()
        self.assertIn("Renamed", self.texts())
        self.assertNotIn("Review", self.texts())

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
