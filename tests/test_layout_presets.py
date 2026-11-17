import dataclasses
import unittest

from rzgitclient.ui.layout.layout_model import (
    Anchor,
    Area,
    Arrangement,
    DropZone,
    LayoutState,
    Orientation,
    PanelNode,
    SplitNode,
    TabsNode,
    flatten,
    panel_ids,
    remove_panel,
    to_dict,
)
from rzgitclient.ui.layout.layout_presets import (
    DEFAULT_LAYOUT,
    PRESETS,
    default_anchor,
    default_area,
    layout_name_problem,
    matching_preset,
    restore_layout,
)
from rzgitclient.ui.panels.panel_registry import PANEL_TYPES

H, V = Orientation.HORIZONTAL, Orientation.VERTICAL
P = PanelNode
CONTROLS, HISTORY, DIFF, DETAILS = P("controls"), P("history"), P("diff"), P("commit_details")
MAIN = SplitNode(V, (TabsNode((HISTORY, DIFF)), DETAILS), sizes=(500, 220))
# A user's layout: the same panels as the default, arranged differently.
CUSTOM = LayoutState(
    {Area.CONTROLS: CONTROLS,
     Area.SIDEBAR: SplitNode(V, (
         TabsNode((P("branches"), P("worktrees"), P("files")), current=2),
         SplitNode(H, (P("staged"), P("commit"), P("commit_files"), P("stash"))))),
     Area.MAIN: SplitNode(H, (HISTORY, DIFF, DETAILS), sizes=(300, 200, 100))},
    Arrangement.SIDEBAR_RIGHT, (200, 500))

# The presets as one tree, as they were before areas (RZG-69).
OLD_PRESETS = {
    "Standard": SplitNode(V, (CONTROLS, SplitNode(H, (
        SplitNode(V, tuple(P(p) for p in ("branches", "worktrees", "files", "staged", "commit",
                                          "commit_files", "stash")),
                  sizes=(180, 140, 220, 120, 160, 0, 170)),
        MAIN), sizes=(220, 880)))),
    "Sidebar Tabs": SplitNode(V, (CONTROLS, SplitNode(H, (
        SplitNode(V, (TabsNode((P("branches"), P("worktrees"), P("files"), P("stash"))),
                      P("staged"), P("commit"), P("commit_files")), sizes=(330, 120, 160, 0)),
        MAIN), sizes=(220, 880)))),
    "Stacked": SplitNode(V, (CONTROLS, SplitNode(V, (
        MAIN,
        SplitNode(H, (SplitNode(V, (P("branches"), P("worktrees"), P("stash")),
                                sizes=(240, 130, 130)),
                      SplitNode(V, (P("files"), P("staged"), P("commit"), P("commit_files")),
                                sizes=(120, 90, 160, 0))),
                  sizes=(400, 700))),
        sizes=(380, 370)))),
}


class PresetTests(unittest.TestCase):
    def test_default_shows_every_registered_panel_once(self):
        self.assertEqual(sorted(DEFAULT_LAYOUT.panel_ids()), sorted(t.panel_id for t in PANEL_TYPES))

    def test_every_preset_shows_every_panel_once_and_is_already_normal(self):
        known = [t.panel_id for t in PANEL_TYPES]
        self.assertIs(PRESETS[0].state, DEFAULT_LAYOUT)
        self.assertEqual(len({p.title for p in PRESETS}), len(PRESETS))
        for preset in PRESETS:
            with self.subTest(preset.title):
                self.assertEqual(sorted(preset.state.panel_ids()), sorted(known))
                self.assertEqual(preset.state.normalized(known), preset.state)
                self.assertEqual(preset.state.hidden, {})

    def test_the_presets_look_as_they_did_before_areas(self):
        """RZG-69: as one tree, each preset is the tree it was."""
        self.assertEqual({p.title: flatten(p.state) for p in PRESETS}, OLD_PRESETS)

    def test_every_preset_has_controls_alone_and_main_in_the_main_pane(self):
        arrangements = {"Standard": Arrangement.SIDEBAR_LEFT,
                        "Sidebar Tabs": Arrangement.SIDEBAR_LEFT, "Stacked": Arrangement.MAIN_TOP}
        for preset in PRESETS:
            with self.subTest(preset.title):
                self.assertEqual(preset.state.tree(Area.CONTROLS), CONTROLS)
                self.assertEqual(preset.state.tree(Area.MAIN), MAIN)
                self.assertEqual(preset.state.arrangement, arrangements[preset.title])

    def test_default_areas(self):
        """The Default area column of spec §5.4."""
        main = {"history", "diff", "commit_details"}
        for panel_type in PANEL_TYPES:
            pid = panel_type.panel_id
            with self.subTest(pid):
                expected = (Area.CONTROLS if pid == "controls"
                            else Area.MAIN if pid in main else Area.SIDEBAR)
                self.assertEqual(default_area(pid), expected)
        self.assertIsNone(default_area("gone"))

    def test_default_anchor_is_in_the_default_area(self):
        self.assertEqual(default_anchor("diff"), Anchor(("history",), None, False, area=Area.MAIN))
        # Alone in its area: no neighbours, it fills the area.
        self.assertEqual(default_anchor("controls"), Anchor((), None, False, area=Area.CONTROLS))
        self.assertEqual(default_anchor("history", shown=["controls"]),
                         Anchor((), None, False, area=Area.MAIN))
        self.assertIsNone(default_anchor("gone"))


def saved(tree, hidden=None):
    """A layout as versions before areas saved it (version 2): one tree."""
    data = to_dict(LayoutState({Area.MAIN: tree}, hidden=hidden or {}))
    return {"version": 2, "root": data["areas"]["main"], "hidden": data["hidden"]}


def resized(node):
    """``node`` with every split's sizes changed, as a user's drags would."""
    if isinstance(node, LayoutState):
        areas = {a: resized(t) for a, t in node.areas.items()}
        return dataclasses.replace(node, areas=areas, sizes=(301, 799))
    if isinstance(node, SplitNode):
        sizes = tuple(s + 7 * i for i, s in enumerate(node.sizes)) if node.sizes else None
        return SplitNode(node.orientation, tuple(resized(c) for c in node.children), sizes)
    return node


class RestoreLayoutTests(unittest.TestCase):
    def test_nothing_saved_gives_the_default(self):
        self.assertIs(restore_layout(None), DEFAULT_LAYOUT)

    def test_saved_layout_is_used(self):
        self.assertEqual(restore_layout(to_dict(CUSTOM)), CUSTOM)

    def test_malformed_layout_gives_the_default_with_a_warning(self):
        for data in ({"version": 99, "root": {}}, {"version": 1, "root": {"type": "?"}}, {},
                     {**to_dict(CUSTOM), "arrangement": "?"}):
            with self.subTest(saved=data):
                with self.assertLogs("rzgitclient.ui.layout.layout_presets", "WARNING"):
                    self.assertEqual(restore_layout(data), DEFAULT_LAYOUT)

    def test_a_hidden_panel_stays_hidden(self):
        hidden = CUSTOM.hide("files")
        self.assertEqual(restore_layout(to_dict(hidden)), hidden)

    def test_a_panel_new_in_this_version_goes_to_its_default_place(self):
        # Saved by a version without the Worktrees, Files, Controls, Diff, Staged, Commit,
        # Commit Details, Commit Files and Stashes panels: the user's arrangement is kept,
        # Worktrees and Files join Branches with Staged, Commit, Commit Files and Stashes
        # below it (at the default sizes: that split is the default's now), Controls goes
        # across the top, Diff becomes History's second tab and Commit Details goes below
        # the two (MAIN's sizes: that split is MAIN now).
        older = SplitNode(H, (P("branches"), HISTORY), sizes=(100, 500))
        restored = restore_layout(saved(older))
        self.assertEqual(flatten(restored), SplitNode(V, (
            CONTROLS,
            SplitNode(H, (
                SplitNode(V, (P("branches"), P("worktrees"), P("files"), P("staged"), P("commit"),
                              P("commit_files"), P("stash")),
                          sizes=(180, 140, 220, 120, 160, 0, 170)),
                MAIN,
            ), sizes=(100, 500)),
        )))
        self.assertEqual(restored.hidden, {})

    def test_a_panel_new_in_this_version_goes_to_its_default_area(self):
        # Every Main pane panel moved to the Sidebar: Diff, new, still goes to the Main pane.
        mine = DEFAULT_LAYOUT.move("history", "branches", DropZone.CENTER)
        mine = mine.move("commit_details", "files", DropZone.CENTER).hide("diff")
        mine = dataclasses.replace(mine, hidden={})  # Diff: as if it were new
        self.assertIsNone(mine.tree(Area.MAIN))
        restored = restore_layout(to_dict(mine))
        self.assertEqual(restored.tree(Area.MAIN), DIFF)
        self.assertEqual(restored.area_of("history"), Area.SIDEBAR)

    def test_the_default_layout_before_the_diff_panel_migrates_to_today_s(self):
        before_rzg_18 = SplitNode(V, (CONTROLS, SplitNode(H, (
            SplitNode(V, (P("branches"), P("files")), sizes=(300, 400)),
            HISTORY,
        ), sizes=(220, 880))))
        self.assertEqual(restore_layout(saved(before_rzg_18)), DEFAULT_LAYOUT)

    def test_stashes_a_tab_only_in_sidebar_tabs(self):
        """RZG-36: at the bottom of Standard's sidebar, the last tab in Sidebar Tabs, below
        Branches and Worktrees in Stacked."""
        standard, tabs, stacked = (p.state.tree(Area.SIDEBAR) for p in PRESETS)
        self.assertEqual(standard.children[-1], P("stash"))
        self.assertEqual(tabs.children[0], TabsNode(
            (P("branches"), P("worktrees"), P("files"), P("stash"))))
        self.assertEqual(stacked.children[0].children,
                         (P("branches"), P("worktrees"), P("stash")))

    def test_commit_details_is_below_history_and_diff_in_every_preset(self):
        """RZG-65: a vertical split of their own, the tabs first; Commit Files alone
        directly below Commit, at size 0 (the only stand-in for the working tree)."""
        def splits(node):
            if isinstance(node, SplitNode):
                yield node
                for child in node.children:
                    yield from splits(child)
        for preset in PRESETS:
            with self.subTest(preset.title):
                self.assertEqual(preset.state.tree(Area.MAIN), MAIN)
                [sidebar] = [s for s in splits(preset.state.tree(Area.SIDEBAR))
                             if P("commit_files") in s.children]
                at = sidebar.children.index(P("commit_files"))
                self.assertEqual(sidebar.children[at - 1], P("commit"))
                self.assertEqual(sidebar.sizes[at], 0)
                self.assertNotIn(0, sidebar.sizes[:at] + sidebar.sizes[at + 1:])

    def test_worktrees_is_below_branches_in_every_preset(self):
        """RZG-48: directly below Branches in a split, the tab after it in tabs."""
        for preset in PRESETS:
            with self.subTest(preset.title):
                ids = preset.state.panel_ids()
                self.assertEqual(ids[ids.index("branches") + 1], "worktrees")

    def test_staged_and_commit_go_below_files_in_a_layout_saved_before_them(self):
        for preset in PRESETS:
            with self.subTest(preset.title):
                before_rzg_20, _anchor = remove_panel(flatten(preset.state), "commit")
                before_rzg_20, _anchor = remove_panel(before_rzg_20, "staged")
                self.assertEqual(restore_layout(saved(before_rzg_20)), preset.state)

    def test_a_split_unlike_any_preset_keeps_its_sizes_and_shares_out_the_new_panes(self):
        mine = SplitNode(V, (CONTROLS, SplitNode(H, (
            HISTORY, SplitNode(V, (P("files"), P("branches")), sizes=(500, 100)),
        ), sizes=(700, 300))))
        restored = restore_layout(saved(mine))
        self.assertEqual(restored.arrangement, Arrangement.SIDEBAR_RIGHT)
        self.assertEqual(restored.sizes, (300, 700))
        sidebar = restored.tree(Area.SIDEBAR)
        self.assertEqual(panel_ids(sidebar),
                         ["worktrees", "files", "staged", "commit", "commit_files", "stash",
                          "branches"])
        self.assertEqual(sidebar.sizes[-1], 100)  # Branches keeps its own

    def test_a_new_tab_leaves_the_current_tab_current(self):
        mine = DEFAULT_LAYOUT.with_tree(Area.MAIN, TabsNode((HISTORY, DETAILS), current=1))
        # Diff goes after History, as in the default; Commit Details stays current.
        self.assertEqual(restore_layout(to_dict(mine)).tree(Area.MAIN),
                         TabsNode((HISTORY, DIFF, DETAILS), current=2))

    def test_version_1_files_load(self):
        old = saved(flatten(DEFAULT_LAYOUT))
        self.assertEqual(restore_layout({"version": 1, "root": old["root"]}), DEFAULT_LAYOUT)

    def test_panel_this_version_no_longer_has_is_dropped(self):
        main = SplitNode(H, (*CUSTOM.tree(Area.MAIN).children, P("retired")),
                         sizes=(300, 200, 100, 50))
        newer = dataclasses.replace(CUSTOM.with_tree(Area.MAIN, main),
                                    hidden={"gone": Anchor(("history",), V, True)})
        restored = restore_layout(to_dict(newer))
        self.assertEqual(sorted(restored.panel_ids()),
                         ["branches", "commit", "commit_details", "commit_files", "controls", "diff", "files",
                          "history", "staged", "stash", "worktrees"])
        self.assertIsNone(restored.tree(Area.MAIN).sizes)  # children changed: sizes don't fit
        self.assertEqual(restored.hidden, {})

    def test_nothing_known_left_gives_the_default(self):
        self.assertEqual(restore_layout(saved(P("retired"))), DEFAULT_LAYOUT)
        retired = LayoutState({Area.MAIN: P("retired")})
        self.assertEqual(restore_layout(to_dict(retired)), DEFAULT_LAYOUT)


class MigrationTests(unittest.TestCase):
    """Layouts saved before areas (versions 1 and 2) load into areas (spec §6.3, RZG-69)."""

    def test_each_preset_saved_before_areas_loads_as_itself_with_the_user_s_sizes(self):
        for preset in PRESETS:
            with self.subTest(preset.title):
                mine = resized(OLD_PRESETS[preset.title])
                restored = restore_layout(saved(mine), preset.title)
                self.assertEqual(flatten(restored), mine)
                self.assertEqual(matching_preset(restored), preset.title)

    def test_a_preset_with_panels_hidden_keeps_them_hidden_in_their_areas(self):
        for preset in PRESETS:
            for pid in ("branches", "commit_details"):
                with self.subTest((preset.title, pid)):
                    tree, anchor = remove_panel(OLD_PRESETS[preset.title], pid)
                    restored = restore_layout(saved(tree, {pid: anchor}), preset.title)
                    self.assertEqual(restored, preset.state.hide(pid))
                    back = restored.show(pid, default_anchor(pid))
                    self.assertEqual(matching_preset(back), preset.title)
                    self.assertEqual(back.area_of(pid), default_area(pid))

    def test_a_saved_layout_from_before_areas_loads_into_areas(self):
        """A named saved layout has no preset name: it is migrated the same way."""
        tree = OLD_PRESETS["Sidebar Tabs"]
        self.assertEqual(restore_layout(saved(tree)), PRESETS[1].state)

    def test_an_anchor_beside_another_area_is_dropped(self):
        # Diff hidden while it sat beside Branches: its neighbour isn't on the Main pane.
        tree = SplitNode(V, (CONTROLS, SplitNode(H, (P("branches"), HISTORY))))
        restored = restore_layout(saved(tree, {"diff": Anchor(("branches",), V, False)}))
        self.assertNotIn("diff", restored.hidden)
        self.assertEqual(restored.area_of("diff"), Area.MAIN)  # so it's new: default place


class BuiltInLayoutTests(unittest.TestCase):
    """Built-in layouts follow the code; custom ones stay (spec §6.3)."""

    # Standard as an older version had it: Diff below History, no Commit Files.
    OLD_STANDARD = SplitNode(V, (CONTROLS, SplitNode(H, (
        SplitNode(V, (P("branches"), P("files"), P("staged"), P("commit")),
                  sizes=(200, 220, 120, 160)),
        SplitNode(V, (HISTORY, DIFF), sizes=(400, 300)),
    ), sizes=(250, 850))))

    def test_each_preset_matches_itself_resized_or_with_another_tab_current(self):
        for preset in PRESETS:
            with self.subTest(preset.title):
                self.assertEqual(matching_preset(resized(preset.state)), preset.title)
        diff_current = SplitNode(V, (TabsNode((HISTORY, DIFF), current=1), DETAILS), MAIN.sizes)
        self.assertEqual(matching_preset(DEFAULT_LAYOUT.with_tree(Area.MAIN, diff_current)),
                         "Standard")

    def test_a_preset_with_panels_hidden_still_matches(self):
        self.assertEqual(matching_preset(DEFAULT_LAYOUT.hide("branches")), "Standard")
        self.assertEqual(matching_preset(DEFAULT_LAYOUT.hide("history")), "Standard")

    def test_a_rearranged_layout_is_custom(self):
        self.assertIsNone(matching_preset(CUSTOM))
        sidebar = remove_panel(DEFAULT_LAYOUT.tree(Area.SIDEBAR), "branches")[0]
        moved = DEFAULT_LAYOUT.with_tree(Area.SIDEBAR, sidebar)  # removed, but not hidden
        self.assertIsNone(matching_preset(moved))
        other_side = dataclasses.replace(DEFAULT_LAYOUT, arrangement=Arrangement.SIDEBAR_RIGHT)
        self.assertIsNone(matching_preset(other_side))

    def test_an_unchanged_preset_keeps_the_user_s_sizes(self):
        mine = resized(DEFAULT_LAYOUT)
        self.assertEqual(restore_layout(to_dict(mine), "Standard"), mine)

    def test_a_preset_changed_in_code_is_used_instead(self):
        self.assertEqual(restore_layout(saved(self.OLD_STANDARD), "Standard"), DEFAULT_LAYOUT)
        changed = DEFAULT_LAYOUT.with_tree(Area.MAIN, TabsNode((HISTORY, DIFF, DETAILS)))
        self.assertEqual(restore_layout(to_dict(changed), "Standard"), DEFAULT_LAYOUT)

    def test_the_new_preset_keeps_the_hidden_panels_hidden(self):
        old, anchor = remove_panel(self.OLD_STANDARD, "branches")
        restored = restore_layout(saved(old, {"branches": anchor}), "Standard")
        self.assertEqual(restored, DEFAULT_LAYOUT.hide("branches"))

    def test_a_custom_layout_stays_as_it_is(self):
        restored = restore_layout(saved(self.OLD_STANDARD), None)
        self.assertEqual(restored.panel_ids(),
                         ["controls", "branches", "worktrees", "files", "staged", "commit",
                          "commit_files", "stash", "history", "diff", "commit_details"])
        main = restored.tree(Area.MAIN)
        self.assertEqual(main.children[0],  # Diff still below History, Commit Details below
                         SplitNode(V, (HISTORY, DIFF), sizes=(400, 300)))
        self.assertEqual(main.children[1], DETAILS)

    # Standard before RZG-65: Commit Details in the sidebar, above Commit Files.
    SIDEBAR_DETAILS = SplitNode(V, (CONTROLS, SplitNode(H, (
        SplitNode(V, tuple(P(p) for p in (
            "branches", "worktrees", "files", "staged", "commit", "commit_details",
            "commit_files", "stash")), sizes=(180, 140, 220, 120, 160, 0, 0, 170)),
        TabsNode((HISTORY, DIFF)),
    ), sizes=(220, 880))))

    def test_standard_saved_with_commit_details_in_the_sidebar_follows_the_preset(self):
        self.assertEqual(restore_layout(saved(self.SIDEBAR_DETAILS), "Standard"), DEFAULT_LAYOUT)

    def test_a_custom_layout_from_before_areas_moves_commit_details_to_the_main_pane(self):
        """RZG-69: each panel goes to its default area; the rest keeps its place."""
        restored = restore_layout(saved(self.SIDEBAR_DETAILS), None)
        self.assertEqual(restored.area_of("commit_details"), Area.MAIN)
        self.assertEqual(restored.tree(Area.SIDEBAR),
                         remove_panel(self.SIDEBAR_DETAILS.children[1].children[0],
                                      "commit_details")[0])
        self.assertIsNone(matching_preset(restored))

    def test_an_unknown_preset_name_is_a_custom_layout(self):
        self.assertEqual(restore_layout(to_dict(CUSTOM), "Gone"), CUSTOM)


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
