import unittest

from rzgitclient.ui.layout.layout_model import (
    Orientation,
    PanelNode,
    SplitNode,
    TabsNode,
    panel_ids,
    to_dict,
)
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT, restore_layout
from rzgitclient.ui.panels.panel_registry import PANEL_TYPES

H, V = Orientation.HORIZONTAL, Orientation.VERTICAL
# A user's layout: the same panels as the default, arranged differently.
CUSTOM = SplitNode(
    V,
    (PanelNode("history"), TabsNode((PanelNode("branches"), PanelNode("files")), current=1)),
    sizes=(500, 200),
)


class DefaultLayoutTests(unittest.TestCase):
    def test_default_shows_every_registered_panel_once(self):
        self.assertEqual(sorted(panel_ids(DEFAULT_LAYOUT)), sorted(t.panel_id for t in PANEL_TYPES))


class RestoreLayoutTests(unittest.TestCase):
    def test_nothing_saved_gives_the_default(self):
        self.assertIs(restore_layout(None), DEFAULT_LAYOUT)

    def test_saved_layout_is_used(self):
        self.assertEqual(restore_layout(to_dict(CUSTOM)), CUSTOM)

    def test_malformed_layout_gives_the_default_with_a_warning(self):
        for saved in ({"version": 99, "root": {}}, {"version": 1, "root": {"type": "?"}}, {}):
            with self.subTest(saved=saved):
                with self.assertLogs("rzgitclient.ui.layout.layout_presets", "WARNING"):
                    self.assertIs(restore_layout(saved), DEFAULT_LAYOUT)

    def test_missing_panel_gives_the_default(self):
        # Saved by a version without the Files panel.
        older = SplitNode(H, (PanelNode("branches"), PanelNode("history")), sizes=(100, 500))
        self.assertIs(restore_layout(to_dict(older)), DEFAULT_LAYOUT)

    def test_panel_this_version_no_longer_has_is_dropped(self):
        newer = SplitNode(V, (*CUSTOM.children, PanelNode("retired")), sizes=(400, 200, 100))
        restored = restore_layout(to_dict(newer))
        self.assertEqual(sorted(panel_ids(restored)), ["branches", "files", "history"])
        self.assertIsNone(restored.sizes)  # children changed, so sizes no longer fit

    def test_nothing_known_left_gives_the_default(self):
        saved = to_dict(PanelNode("retired"))
        self.assertIs(restore_layout(saved), DEFAULT_LAYOUT)


if __name__ == "__main__":
    unittest.main()
