import unittest

from rzgitclient.ui.layout.layout_model import (
    Anchor,
    LayoutState,
    Orientation,
    PanelNode,
    SplitNode,
    TabsNode,
    normalize,
    panel_ids,
    remove_panel,
    to_dict,
)
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT, PRESETS, restore_layout
from rzgitclient.ui.panels.panel_registry import PANEL_TYPES

H, V = Orientation.HORIZONTAL, Orientation.VERTICAL
# A user's layout: the same panels as the default, arranged differently.
CUSTOM = SplitNode(
    V,
    (PanelNode("history"), TabsNode((PanelNode("branches"), PanelNode("files")), current=1)),
    sizes=(500, 200),
)


class PresetTests(unittest.TestCase):
    def test_default_shows_every_registered_panel_once(self):
        self.assertEqual(sorted(panel_ids(DEFAULT_LAYOUT)), sorted(t.panel_id for t in PANEL_TYPES))

    def test_every_preset_shows_every_panel_once_and_is_already_normal(self):
        known = [t.panel_id for t in PANEL_TYPES]
        self.assertEqual(PRESETS[0].root, DEFAULT_LAYOUT)
        self.assertEqual(len({p.title for p in PRESETS}), len(PRESETS))
        for preset in PRESETS:
            with self.subTest(preset.title):
                self.assertEqual(sorted(panel_ids(preset.root)), sorted(known))
                self.assertEqual(normalize(preset.root, known), preset.root)


def saved(tree, hidden=None):
    return to_dict(LayoutState(tree, hidden or {}))


class RestoreLayoutTests(unittest.TestCase):
    def test_nothing_saved_gives_the_default(self):
        self.assertEqual(restore_layout(None), LayoutState(DEFAULT_LAYOUT))

    def test_saved_layout_is_used(self):
        self.assertEqual(restore_layout(saved(CUSTOM)), LayoutState(CUSTOM))

    def test_malformed_layout_gives_the_default_with_a_warning(self):
        for data in ({"version": 99, "root": {}}, {"version": 1, "root": {"type": "?"}}, {}):
            with self.subTest(saved=data):
                with self.assertLogs("rzgitclient.ui.layout.layout_presets", "WARNING"):
                    self.assertEqual(restore_layout(data), LayoutState(DEFAULT_LAYOUT))

    def test_a_hidden_panel_stays_hidden(self):
        rest, anchor = remove_panel(CUSTOM, "files")
        self.assertEqual(restore_layout(saved(rest, {"files": anchor})),
                         LayoutState(rest, {"files": anchor}))

    def test_a_panel_new_in_this_version_goes_to_its_default_place(self):
        # Saved by a version without the Files panel: the user's arrangement is kept.
        older = SplitNode(H, (PanelNode("branches"), PanelNode("history")), sizes=(100, 500))
        restored = restore_layout(saved(older))
        self.assertEqual(restored.root, SplitNode(H, (
            SplitNode(V, (PanelNode("branches"), PanelNode("files")), sizes=(429, 571)),
            PanelNode("history"),
        ), sizes=(100, 500)))
        self.assertEqual(restored.hidden, {})

    def test_version_1_files_load(self):
        self.assertEqual(restore_layout({"version": 1, "root": saved(CUSTOM)["root"]}),
                         LayoutState(CUSTOM))

    def test_panel_this_version_no_longer_has_is_dropped(self):
        newer = SplitNode(V, (*CUSTOM.children, PanelNode("retired")), sizes=(400, 200, 100))
        retired_anchor = Anchor(("history",), V, True)
        restored = restore_layout(saved(newer, {"gone": retired_anchor}))
        self.assertEqual(sorted(panel_ids(restored.root)), ["branches", "files", "history"])
        self.assertIsNone(restored.root.sizes)  # children changed, so sizes no longer fit
        self.assertEqual(restored.hidden, {})

    def test_nothing_known_left_gives_the_default(self):
        self.assertEqual(restore_layout(saved(PanelNode("retired"))), LayoutState(DEFAULT_LAYOUT))


if __name__ == "__main__":
    unittest.main()
