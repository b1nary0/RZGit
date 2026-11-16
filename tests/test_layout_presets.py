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
from rzgitclient.ui.layout.layout_presets import (
    DEFAULT_LAYOUT,
    PRESETS,
    layout_name_problem,
    matching_preset,
    restore_layout,
)
from rzgitclient.ui.panels.panel_registry import PANEL_TYPES

H, V = Orientation.HORIZONTAL, Orientation.VERTICAL
# A user's layout: the same panels as the default, arranged differently.
CUSTOM = SplitNode(
    V,
    (PanelNode("controls"),
     SplitNode(H, (PanelNode("history"), PanelNode("diff"), PanelNode("staged"),
                   PanelNode("commit"), PanelNode("commit_details"),
                   PanelNode("commit_files"))),
     TabsNode((PanelNode("branches"), PanelNode("files")), current=1)),
    sizes=(30, 500, 200),
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


def strip_sizes(node):
    if isinstance(node, SplitNode):
        return SplitNode(node.orientation, tuple(strip_sizes(c) for c in node.children))
    return node


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
        # Saved by a version without the Files, Controls, Diff, Staged, Commit
        # Commit Details and Commit Files panels: the user's arrangement is kept, Files
        # joins Branches with Staged, Commit, Commit Details and Commit Files below it (at the default
        # sizes: that split is the default's now), Controls goes across the top,
        # and Diff becomes History's second tab.
        older = SplitNode(H, (PanelNode("branches"), PanelNode("history")), sizes=(100, 500))
        restored = restore_layout(saved(older))
        self.assertEqual(restored.root, SplitNode(V, (
            PanelNode("controls"),
            SplitNode(H, (
                SplitNode(V, (PanelNode("branches"), PanelNode("files"), PanelNode("staged"),
                              PanelNode("commit"), PanelNode("commit_details"),
                              PanelNode("commit_files")),
                          sizes=(200, 220, 120, 160, 0, 0)),
                TabsNode((PanelNode("history"), PanelNode("diff"))),
            ), sizes=(100, 500)),
        )))
        self.assertEqual(restored.hidden, {})

    def test_the_default_layout_before_the_diff_panel_migrates_to_today_s(self):
        before_rzg_18 = SplitNode(V, (PanelNode("controls"), SplitNode(H, (
            SplitNode(V, (PanelNode("branches"), PanelNode("files")), sizes=(300, 400)),
            PanelNode("history"),
        ), sizes=(220, 880))))
        self.assertEqual(restore_layout(saved(before_rzg_18)), LayoutState(DEFAULT_LAYOUT))

    def test_staged_and_commit_go_below_files_in_a_layout_saved_before_them(self):
        for preset in PRESETS:
            with self.subTest(preset.title):
                before_rzg_20, _anchor = remove_panel(preset.root, "commit")
                before_rzg_20, _anchor = remove_panel(before_rzg_20, "staged")
                self.assertEqual(restore_layout(saved(before_rzg_20)).root, preset.root)

    def test_a_split_unlike_any_preset_keeps_its_sizes_and_shares_out_the_new_panes(self):
        mine = SplitNode(V, (PanelNode("controls"), SplitNode(H, (
            PanelNode("history"), SplitNode(V, (PanelNode("files"), PanelNode("branches")),
                                            sizes=(500, 100)),
        ), sizes=(700, 300))))
        sidebar = restore_layout(saved(mine)).root.children[1].children[1]
        self.assertEqual(panel_ids(sidebar),
                         ["files", "staged", "commit", "commit_details", "commit_files", "branches"])
        self.assertEqual(sidebar.sizes[-1], 100)  # Branches keeps its own

    def test_a_new_tab_leaves_the_current_tab_current(self):
        older = SplitNode(V, (PanelNode("controls"), SplitNode(H, (
            PanelNode("branches"),
            TabsNode((PanelNode("files"), PanelNode("history")), current=1)))))
        # Staged and Commit go below the tabs that hold Files.
        tabs = restore_layout(saved(older)).root.children[1].children[1].children[0]
        self.assertEqual(tabs, TabsNode(
            (PanelNode("files"), PanelNode("history"), PanelNode("diff")), current=1))

    def test_version_1_files_load(self):
        self.assertEqual(restore_layout({"version": 1, "root": saved(CUSTOM)["root"]}),
                         LayoutState(CUSTOM))

    def test_panel_this_version_no_longer_has_is_dropped(self):
        newer = SplitNode(V, (*CUSTOM.children, PanelNode("retired")), sizes=(30, 400, 200, 100))
        retired_anchor = Anchor(("history",), V, True)
        restored = restore_layout(saved(newer, {"gone": retired_anchor}))
        self.assertEqual(sorted(panel_ids(restored.root)),
                         ["branches", "commit", "commit_details", "commit_files", "controls", "diff", "files",
                          "history", "staged"])
        self.assertIsNone(restored.root.sizes)  # children changed, so sizes no longer fit
        self.assertEqual(restored.hidden, {})

    def test_nothing_known_left_gives_the_default(self):
        self.assertEqual(restore_layout(saved(PanelNode("retired"))), LayoutState(DEFAULT_LAYOUT))


class BuiltInLayoutTests(unittest.TestCase):
    """Built-in layouts follow the code; custom ones stay (spec §6.3)."""

    # Standard as an older version had it: Diff below History, no Commit Files.
    OLD_STANDARD = SplitNode(V, (PanelNode("controls"), SplitNode(H, (
        SplitNode(V, (PanelNode("branches"), PanelNode("files"), PanelNode("staged"),
                      PanelNode("commit")), sizes=(200, 220, 120, 160)),
        SplitNode(V, (PanelNode("history"), PanelNode("diff")), sizes=(400, 300)),
    ), sizes=(250, 850))))

    def resized(self, node):
        """``node`` with every split's sizes changed, as a user's drags would."""
        if isinstance(node, SplitNode):
            sizes = tuple(s + 7 * i for i, s in enumerate(node.sizes)) if node.sizes else None
            return SplitNode(node.orientation, tuple(self.resized(c) for c in node.children), sizes)
        return node

    def test_each_preset_matches_itself_resized_or_with_another_tab_current(self):
        for preset in PRESETS:
            with self.subTest(preset.title):
                self.assertEqual(matching_preset(LayoutState(self.resized(preset.root))),
                                 preset.title)
        body = DEFAULT_LAYOUT.children[1]
        diff_current = TabsNode(body.children[1].children, current=1)
        other_tab = SplitNode(V, (PanelNode("controls"), SplitNode(
            H, (body.children[0], diff_current), body.sizes)))
        self.assertEqual(matching_preset(LayoutState(other_tab)), "Standard")

    def test_a_preset_with_panels_hidden_still_matches(self):
        root, anchor = remove_panel(DEFAULT_LAYOUT, "branches")
        self.assertEqual(matching_preset(LayoutState(root, {"branches": anchor})), "Standard")

    def test_a_rearranged_layout_is_custom(self):
        self.assertIsNone(matching_preset(LayoutState(CUSTOM)))
        moved = remove_panel(DEFAULT_LAYOUT, "branches")[0]  # removed, but not hidden
        self.assertIsNone(matching_preset(LayoutState(moved)))

    def test_an_unchanged_preset_keeps_the_user_s_sizes(self):
        mine = self.resized(DEFAULT_LAYOUT)
        self.assertEqual(restore_layout(saved(mine), "Standard"), LayoutState(mine))

    def test_a_preset_changed_in_code_is_used_instead(self):
        self.assertEqual(restore_layout(saved(self.OLD_STANDARD), "Standard"),
                         LayoutState(DEFAULT_LAYOUT))

    def test_the_new_preset_keeps_the_hidden_panels_hidden(self):
        old, anchor = remove_panel(self.OLD_STANDARD, "branches")
        restored = restore_layout(saved(old, {"branches": anchor}), "Standard")
        new_root, new_anchor = remove_panel(DEFAULT_LAYOUT, "branches")
        self.assertEqual(restored, LayoutState(new_root, {"branches": new_anchor}))

    def test_a_custom_layout_stays_as_it_is(self):
        restored = restore_layout(saved(self.OLD_STANDARD), None)
        self.assertEqual(panel_ids(restored.root)[:8],
                         ["controls", "branches", "files", "staged", "commit",
                          "commit_details", "commit_files", "history"])
        self.assertIn(SplitNode(V, (PanelNode("history"), PanelNode("diff")), sizes=(400, 300)),
                      restored.root.children[1].children)  # Diff still below History

    def test_an_unknown_preset_name_is_a_custom_layout(self):
        self.assertEqual(restore_layout(saved(CUSTOM), "Gone"), LayoutState(CUSTOM))


class LayoutNameTests(unittest.TestCase):
    def test_a_new_name_is_fine(self):
        self.assertIsNone(layout_name_problem("Wide Diff", ["Review"]))

    def test_empty_builtin_and_taken_names_are_refused_ignoring_case(self):
        self.assertIn("Enter a name", layout_name_problem(""))
        self.assertIn("built-in", layout_name_problem("standard"))
        self.assertIn("built-in", layout_name_problem(PRESETS[1].title.upper()))
        self.assertIn("already called", layout_name_problem("REVIEW", ["Review"]))


if __name__ == "__main__":
    unittest.main()
