import dataclasses
import json
import subprocess
import sys
import unittest

from rzgit.ui.layout.layout_model import (
    Anchor,
    Area,
    Arrangement,
    DropZone,
    LayoutError,
    LayoutState,
    Orientation,
    PanelNode,
    SplitNode,
    TabsNode,
    anchor_of,
    drop_zone,
    flatten,
    from_dict,
    insert_panel,
    move_panel,
    normalize,
    panel_ids,
    remove_panel,
    to_dict,
)
from rzgit.ui.layout.layout_presets import DEFAULT_LAYOUT as DEFAULT_STATE

DEFAULT_LAYOUT = flatten(DEFAULT_STATE)  # the tree functions work on one tree

H, V = Orientation.HORIZONTAL, Orientation.VERTICAL
A, B, C = PanelNode("a"), PanelNode("b"), PanelNode("c")
KNOWN = {"a", "b", "c"}


def root(node: dict, hidden: dict | None = None) -> dict:
    """A version 2 layout: one tree."""
    return {"version": 2, "root": node, "hidden": hidden or {}}


def areas(sidebar: dict | None = None, main: dict | None = None, hidden: dict | None = None,
          controls: dict | None = None, **extra) -> dict:
    """A version 3 layout."""
    return {"version": 3, "arrangement": "sidebar_left",
            "areas": {"controls": controls, "sidebar": sidebar, "main": main},
            "hidden": hidden or {}, **extra}


def tree_of(data: dict):
    return from_dict(data).tree(Area.SIDEBAR)


def panel(pid: str) -> dict:
    return {"type": "panel", "panel_id": pid}


def state(sidebar=None, main=None, controls=None, **kwargs) -> LayoutState:
    return LayoutState({Area.CONTROLS: controls, Area.SIDEBAR: sidebar, Area.MAIN: main},
                       **kwargs)


# For migration: a, b in the Sidebar, c in the Main pane, x in Controls.
AREA_OF = {"a": Area.SIDEBAR, "b": Area.SIDEBAR, "c": Area.MAIN, "x": Area.CONTROLS}.get
X = PanelNode("x")


class SerialisationTests(unittest.TestCase):
    def test_round_trip_through_json(self):
        hidden = {"c": Anchor(("a", "b"), V, True, 0.25, Area.MAIN),
                  "d": Anchor(("a",), None, False), "e": Anchor((), None, False, None, Area.MAIN)}
        states = (
            DEFAULT_STATE,
            state(A),
            state(SplitNode(V, (A, TabsNode((B, C), current=1))), arrangement=Arrangement.MAIN_TOP),
            state(None, SplitNode(H, (A, SplitNode(V, (B, C), sizes=(1, 2))), sizes=(0, 5)),
                  sizes=(10, 20), arrangement=Arrangement.SIDEBAR_RIGHT),
            state(),
        )
        for s in states:
            with self.subTest(state=s):
                s = dataclasses.replace(s, hidden=hidden)
                data = json.loads(json.dumps(to_dict(s)))
                self.assertEqual(from_dict(data), s)

    def test_anchor_shape(self):
        data = to_dict(state(A, hidden={"b": Anchor(("a",), None, True),
                                        "c": Anchor(("a", "b"), H, False, 0.5, Area.MAIN)}))
        self.assertEqual(data["hidden"], {
            "b": {"neighbors": ["a"], "container": "tabs", "before": True},
            "c": {"neighbors": ["a", "b"], "container": "horizontal", "before": False,
                  "share": 0.5, "area": "main"},
        })

    def test_bad_share_is_dropped_not_rejected(self):
        for share in (0, 1, 1.5, -0.2, "0.5", True, None):
            with self.subTest(share=share):
                anchor = {"neighbors": ["a"], "container": "vertical", "before": True, "share": share}
                self.assertEqual(from_dict(areas(panel("a"), hidden={"b": anchor})).hidden["b"].share, None)

    def test_an_unknown_anchor_area_is_dropped_not_rejected(self):
        for area in ("toolbar", 3, None):
            with self.subTest(area=area):
                anchor = {"neighbors": ["a"], "container": "tabs", "before": True, "area": area}
                self.assertIsNone(from_dict(areas(panel("a"), hidden={"b": anchor})).hidden["b"].area)

    def test_shape(self):
        data = to_dict(state(SplitNode(H, (A, TabsNode((B,))), sizes=(1, 2)), sizes=(3, 4)))
        self.assertEqual(data, areas({
            "type": "split", "orientation": "horizontal", "sizes": [1, 2],
            "children": [panel("a"), {"type": "tabs", "current": 0, "children": [panel("b")]}],
        }, sizes=[3, 4]))

    def test_every_area_is_written_an_empty_one_as_null(self):
        self.assertEqual(to_dict(state())["areas"],
                         {"controls": None, "sidebar": None, "main": None})

    def test_a_missing_area_loads_empty(self):
        data = areas(panel("a"))
        del data["areas"]["main"]
        self.assertIsNone(from_dict(data).tree(Area.MAIN))

    def test_structural_errors_raise_layout_error(self):
        bad = {
            "not an object": [],
            "missing version": {"areas": {}},
            "wrong version": {**areas(panel("a")), "version": 4},
            "missing areas": {"version": 3, "arrangement": "sidebar_left", "hidden": {}},
            "missing arrangement": {"version": 3, "areas": {}, "hidden": {}},
            "unknown arrangement": {**areas(panel("a")), "arrangement": "diagonal"},
            "unknown area": {**areas(panel("a")), "areas": {"toolbar": panel("a")}},
            "area not a node": {**areas(panel("a")), "areas": {"main": "a"}},
            "missing hidden": {"version": 3, "arrangement": "sidebar_left", "areas": {}},
            "anchor not an object": areas(panel("a"), hidden={"b": "a"}),
            "anchor neighbors not ids": areas(panel("a"), hidden={"b": {
                "neighbors": [1], "container": "tabs", "before": True}}),
            "unknown anchor container": areas(panel("a"), hidden={"b": {
                "neighbors": ["a"], "container": "grid", "before": True}}),
            "mistyped before": areas(panel("a"), hidden={"b": {
                "neighbors": ["a"], "container": "tabs", "before": 1}}),
            "unknown node type": areas({"type": "grid"}),
            "missing panel id": areas({"type": "panel"}),
            "mistyped panel id": areas({"type": "panel", "panel_id": 3}),
            "unknown orientation": areas({"type": "split", "orientation": "diagonal", "children": []}),
            "children not a list": areas({"type": "split", "orientation": "vertical", "children": {}}),
            "child not an object": areas({"type": "split", "orientation": "vertical", "children": ["a"]}),
            "split inside tabs": areas({"type": "tabs", "children": [
                {"type": "split", "orientation": "vertical", "children": []}]}),
            "mistyped current": areas({"type": "tabs", "current": "1", "children": [panel("a")]}),
            "version 2 without default areas": root(panel("a")),
            "version 2 missing root": {"version": 2, "hidden": {}},
        }
        for name, data in bad.items():
            with self.subTest(name), self.assertRaises(LayoutError):
                from_dict(data, AREA_OF if name == "version 2 missing root" else None)
        self.assertTrue(issubclass(LayoutError, ValueError))

    def test_bad_sizes_are_dropped_not_rejected(self):
        for sizes in ([1], [1, 2, 3], [1, -2], [1, "2"], [1.5, 2], [True, 2], "12", None):
            with self.subTest(sizes=sizes):
                data = areas({"type": "split", "orientation": "vertical",
                              "sizes": sizes, "children": [panel("a"), panel("b")]}, sizes=sizes)
                self.assertEqual(tree_of(data), SplitNode(V, (A, B)))
                if sizes != [1, 2]:
                    self.assertIsNone(from_dict(data).sizes)

    def test_current_defaults_to_zero(self):
        self.assertEqual(tree_of(areas({"type": "tabs", "children": [panel("a")]})),
                         TabsNode((A,)))


class MigrationTests(unittest.TestCase):
    """Versions 1 and 2 (one tree) load into areas (spec §6.3, RZG-69)."""

    def migrate(self, tree, hidden=None, version=2):
        data = {"version": version, "root": json.loads(json.dumps(_node(tree)))}
        if version == 2:
            data["hidden"] = {k: _anchor(a) for k, a in (hidden or {}).items()}
        return from_dict(data, AREA_OF)

    def test_each_panel_goes_to_its_default_area_keeping_structure_and_sizes(self):
        tree = SplitNode(V, (X, SplitNode(H, (SplitNode(V, (A, B), sizes=(1, 2)), C),
                                          sizes=(30, 70))))
        self.assertEqual(self.migrate(tree), state(
            SplitNode(V, (A, B), sizes=(1, 2)), C, X, sizes=(30, 70)))

    def test_version_1_loads_with_nothing_hidden(self):
        self.assertEqual(self.migrate(SplitNode(H, (A, C)), version=1), state(A, C))

    def test_the_arrangement_comes_from_where_the_main_pane_sat(self):
        cases = {
            SplitNode(H, (A, C)): Arrangement.SIDEBAR_LEFT,
            SplitNode(H, (C, A)): Arrangement.SIDEBAR_RIGHT,
            SplitNode(V, (C, A)): Arrangement.MAIN_TOP,
            SplitNode(V, (A, C)): Arrangement.MAIN_TOP,
            TabsNode((A, C)): Arrangement.SIDEBAR_LEFT,  # mixed in one tab group
            SplitNode(H, (A, B)): Arrangement.SIDEBAR_LEFT,  # no Main pane panels
        }
        for tree, arrangement in cases.items():
            with self.subTest(tree=tree):
                self.assertEqual(self.migrate(tree).arrangement, arrangement)

    def test_split_sizes_kept_only_with_one_child_per_area(self):
        self.assertEqual(self.migrate(SplitNode(H, (C, A), sizes=(70, 30))).sizes, (30, 70))
        self.assertIsNone(self.migrate(SplitNode(H, (A, C, B), sizes=(1, 2, 3))).sizes)
        mixed = SplitNode(H, (A, SplitNode(V, (C, B))), sizes=(1, 2))
        self.assertIsNone(self.migrate(mixed).sizes)

    def test_panels_scattered_through_the_tree_land_in_their_areas(self):
        tree = SplitNode(H, (TabsNode((C, A), current=1), B))
        migrated = self.migrate(tree)
        self.assertEqual(migrated.tree(Area.MAIN), TabsNode((C,)))
        self.assertEqual(migrated.tree(Area.SIDEBAR), SplitNode(H, (TabsNode((A,)), B)))

    def test_an_area_with_no_panels_is_empty(self):
        self.assertEqual(self.migrate(SplitNode(H, (A, B))), state(SplitNode(H, (A, B))))

    def test_panels_with_no_default_area_are_dropped(self):
        self.assertEqual(self.migrate(SplitNode(H, (A, PanelNode("gone"), C))), state(A, C))

    def test_a_hidden_anchor_is_kept_in_its_area_when_a_neighbour_is_there(self):
        hidden = {"b": Anchor(("a",), V, False, 0.5), "d": Anchor(("c",), H, True),
                  "c": Anchor(("a", "b"), H, True)}
        migrated = self.migrate(SplitNode(H, (A, PanelNode("d"))), hidden)
        self.assertEqual(migrated.hidden, {"b": Anchor(("a",), V, False, 0.5, Area.SIDEBAR)})


def _node(node):
    return to_dict(state(node))["areas"]["sidebar"]


def _anchor(anchor):
    return to_dict(state(hidden={"p": anchor}))["hidden"]["p"]


class AreaStateTests(unittest.TestCase):
    """LayoutState's areas and its edits (spec §6.1, §6.6)."""

    def test_every_area_is_there_missing_ones_empty(self):
        s = LayoutState({Area.MAIN: A})
        self.assertEqual(dict(s.areas), {Area.CONTROLS: None, Area.SIDEBAR: None, Area.MAIN: A})
        with self.assertRaises(TypeError):
            LayoutState({"main": A})

    def test_area_of(self):
        s = state(SplitNode(V, (A, B)), C, X)
        self.assertEqual([s.area_of(p) for p in "abcx"],
                         [Area.SIDEBAR, Area.SIDEBAR, Area.MAIN, Area.CONTROLS])
        self.assertIsNone(s.area_of("gone"))
        self.assertEqual(s.panel_ids(), ["x", "a", "b", "c"])

    def test_normalized_keeps_a_panel_in_two_areas_at_its_first_place(self):
        s = state(SplitNode(V, (A, B)), SplitNode(H, (B, C)), X,
                  hidden={"c": Anchor(("a",), V, True), "gone": Anchor(("a",), V, True),
                          "d": Anchor(("a",), V, True)})
        n = s.normalized({"a", "b", "c", "d", "x"})
        self.assertEqual(n.tree(Area.MAIN), C)
        self.assertEqual(n.tree(Area.SIDEBAR), SplitNode(V, (A, B)))
        self.assertEqual(set(n.hidden), {"d"})  # shown or unknown ones dropped

    def test_hiding_the_last_panel_of_an_area_leaves_it_empty_and_showing_brings_it_back(self):
        s = state(A, C)
        hidden = s.hide("c")
        self.assertIsNone(hidden.tree(Area.MAIN))
        self.assertEqual(set(hidden.areas), set(Area))
        self.assertEqual(hidden.hidden["c"], Anchor((), None, False, None, Area.MAIN))
        self.assertEqual(hidden.show("c"), s)

    def test_a_panel_comes_back_to_the_area_it_was_hidden_from(self):
        # b moved to the Main pane, then hidden: it comes back there, not to the Sidebar.
        s = state(A, SplitNode(V, (C, B), sizes=(60, 40)))
        hidden = s.hide("b")
        self.assertEqual(hidden.hidden["b"].area, Area.MAIN)
        fallback = Anchor(("a",), V, False, None, Area.SIDEBAR)  # its default place
        back = hidden.show("b", fallback)
        self.assertEqual(back.tree(Area.MAIN), SplitNode(V, (C, B), sizes=(600, 400)))
        self.assertEqual(back.tree(Area.SIDEBAR), A)

    def test_show_uses_the_fallback_and_its_area_when_the_neighbours_are_gone(self):
        s = state(A, None, hidden={"b": Anchor(("gone",), V, False, None, Area.MAIN)})
        shown = s.show("b", Anchor(("a",), V, False, None, Area.SIDEBAR))
        self.assertEqual(shown, state(SplitNode(V, (A, B))))
        # No neighbours anywhere: alone in the anchor's (empty) area.
        self.assertEqual(s.show("b", None).tree(Area.MAIN), B)

    def test_insert_leaves_a_shown_panel_alone(self):
        s = state(A, C)
        self.assertIs(s.insert("c", Anchor(("a",), V, False, None, Area.SIDEBAR)), s)

    def test_move_within_an_area(self):
        s = state(SplitNode(V, (A, B)), C)
        self.assertEqual(s.move("b", "a", DropZone.CENTER),
                         state(TabsNode((A, B), current=1), C))

    def test_move_between_areas_leaves_the_old_area_empty_when_it_was_the_last(self):
        s = state(A, C)
        moved = s.move("a", "c", DropZone.RIGHT)
        self.assertEqual(moved, state(None, SplitNode(H, (C, A), sizes=(500, 500))))
        self.assertEqual(moved.area_of("a"), Area.MAIN)

    def test_moves_that_change_nothing_return_the_same_state(self):
        s = state(TabsNode((A, B)), C)
        for pid, target, zone in (("a", "a", DropZone.LEFT), ("a", "b", DropZone.CENTER),
                                  ("gone", "c", DropZone.LEFT), ("a", "gone", DropZone.LEFT)):
            with self.subTest((pid, target, zone)):
                self.assertIs(s.move(pid, target, zone), s)

    def test_move_to_an_empty_area_fills_it(self):
        s = state(SplitNode(V, (A, B)), None)
        self.assertEqual(s.move_to_area("b", Area.MAIN), state(A, B))
        self.assertIs(s.move_to_area("b", Area.SIDEBAR), s)  # its own area
        full = state(A, C)
        self.assertIs(full.move_to_area("a", Area.MAIN), full)  # not empty
        self.assertIs(full.move_to_area("gone", Area.CONTROLS), full)

    def test_with_current_tab_brings_the_panel_to_the_front_of_its_group(self):
        s = state(TabsNode((A, B), current=1),
                  SplitNode(V, (TabsNode((C, X), current=1), PanelNode("d"))))
        front = s.with_current_tab("c")
        self.assertEqual(front.tree(Area.MAIN),
                         SplitNode(V, (TabsNode((C, X), current=0), PanelNode("d"))))
        self.assertEqual(front.tree(Area.SIDEBAR), TabsNode((A, B), current=1))  # its own
        self.assertEqual(s.with_current_tab("a").tree(Area.SIDEBAR), TabsNode((A, B)))

    def test_with_current_tab_leaves_a_panel_in_no_group_or_in_front_alone(self):
        s = state(TabsNode((A, B), current=1), C, hidden={"x": Anchor(("c",), V, False)})
        for pid in ("b", "c", "x", "gone"):  # current, in no group, hidden, unknown
            with self.subTest(pid):
                self.assertIs(s.with_current_tab(pid), s)

    def test_flatten_is_the_old_single_tree(self):
        s = state(A, C, X, sizes=(30, 70))
        self.assertEqual(flatten(s), SplitNode(V, (X, SplitNode(H, (A, C), sizes=(30, 70)))))
        right = dataclasses.replace(s, arrangement=Arrangement.SIDEBAR_RIGHT)
        self.assertEqual(flatten(right), SplitNode(V, (X, SplitNode(H, (C, A), sizes=(70, 30)))))
        top = dataclasses.replace(s, arrangement=Arrangement.MAIN_TOP)
        self.assertEqual(flatten(top), SplitNode(V, (X, SplitNode(V, (C, A), sizes=(70, 30)))))
        self.assertEqual(flatten(state(None, C)), C)
        self.assertIsNone(flatten(state()))


class NormalizeTests(unittest.TestCase):
    def test_valid_tree_is_unchanged(self):
        known = {"controls", "branches", "worktrees", "files", "history", "diff", "staged", "commit", "commit_details", "commit_files", "stash"}
        self.assertEqual(normalize(DEFAULT_LAYOUT, known), DEFAULT_LAYOUT)

    def test_drops_unknown_panels(self):
        tree = SplitNode(H, (A, PanelNode("gone"), B), sizes=(1, 2, 3))
        self.assertEqual(normalize(tree, KNOWN), SplitNode(H, (A, B)))  # sizes dropped

    def test_drops_duplicates_first_wins(self):
        tree = SplitNode(H, (A, SplitNode(V, (B, A)), TabsNode((C, B))))
        self.assertEqual(normalize(tree, KNOWN), SplitNode(H, (A, B, TabsNode((C,)))))

    def test_collapses_single_child_split(self):
        self.assertEqual(normalize(SplitNode(H, (SplitNode(V, (A,)),)), KNOWN), A)

    def test_keeps_single_tab_group(self):
        self.assertEqual(normalize(TabsNode((A, PanelNode("x"))), KNOWN), TabsNode((A,)))

    def test_removes_empty_containers(self):
        tree = SplitNode(H, (A, SplitNode(V, ()), TabsNode(()), B))
        self.assertEqual(normalize(tree, KNOWN), SplitNode(H, (A, B)))

    def test_nothing_left_returns_none(self):
        for tree in (PanelNode("x"), SplitNode(H, ()), TabsNode((PanelNode("x"),))):
            with self.subTest(tree=tree):
                self.assertIsNone(normalize(tree, KNOWN))

    def test_keeps_sizes_when_child_count_is_unchanged(self):
        # The nested split collapses into one panel, but there are still two
        # children, so the sizes still line up.
        tree = SplitNode(H, (A, SplitNode(V, (B, PanelNode("x")))), sizes=(1, 2))
        self.assertEqual(normalize(tree, KNOWN), SplitNode(H, (A, B), sizes=(1, 2)))

    def test_drops_sizes_that_do_not_fit(self):
        for sizes in ((1,), (1, -1)):
            with self.subTest(sizes=sizes):
                self.assertEqual(normalize(SplitNode(H, (A, B), sizes=sizes), KNOWN),
                                 SplitNode(H, (A, B)))

    def test_current_follows_its_panel(self):
        tree = TabsNode((PanelNode("x"), A, B), current=2)
        self.assertEqual(normalize(tree, KNOWN), TabsNode((A, B), current=1))

    def test_current_is_clamped(self):
        cases = {
            TabsNode((A, B), current=7): 1,
            TabsNode((A, B), current=-3): 0,
            TabsNode((A, PanelNode("x")), current=1): 0,  # current panel dropped
        }
        for tree, expected in cases.items():
            with self.subTest(tree=tree):
                self.assertEqual(normalize(tree, KNOWN).current, expected)


D = PanelNode("d")


class EditTests(unittest.TestCase):
    """remove_panel / insert_panel (spec §6.6)."""

    def round_trip(self, tree, pid):
        rest, anchor = remove_panel(tree, pid)
        return rest, anchor, insert_panel(rest, pid, anchor)

    def test_anchor_is_the_next_node_or_the_previous_one(self):
        tree = SplitNode(H, (A, SplitNode(V, (B, C)), D), sizes=(100, 300, 100))
        self.assertEqual(anchor_of(tree, "a"), Anchor(("b", "c"), H, True, 0.25))
        self.assertEqual(anchor_of(tree, "d"), Anchor(("b", "c"), H, False, 0.25))
        self.assertEqual(anchor_of(tree, "b"), Anchor(("c",), V, True))  # no sizes, no share
        self.assertIsNone(anchor_of(A, "a"))  # the root has nowhere to come back to
        self.assertIsNone(anchor_of(TabsNode((A,)), "a"))
        # The only tab of a group sits where its group does.
        self.assertEqual(anchor_of(SplitNode(H, (TabsNode((A,)), B)), "a"),
                         Anchor(("b",), H, True))
        self.assertIsNone(anchor_of(tree, "nope"))

    def test_every_panel_of_the_default_comes_back_where_it_was(self):
        for pid in ("branches", "files", "history"):
            with self.subTest(pid):
                rest, _, back = self.round_trip(DEFAULT_LAYOUT, pid)
                self.assertNotIn(pid, panel_ids(rest))
                strip = lambda n: (SplitNode(n.orientation, tuple(strip(c) for c in n.children))  # noqa: E731
                                   if isinstance(n, SplitNode) else n)
                self.assertEqual(strip(back), strip(DEFAULT_LAYOUT))

    def test_removing_gives_its_space_to_the_rest_and_collapses(self):
        tree = SplitNode(H, (A, SplitNode(V, (B, C), sizes=(1, 3))), sizes=(100, 300))
        self.assertEqual(remove_panel(tree, "b")[0], SplitNode(H, (A, C), sizes=(100, 300)))
        three = SplitNode(H, (A, B, C), sizes=(1, 2, 3))
        self.assertEqual(remove_panel(three, "b")[0], SplitNode(H, (A, C), sizes=(1, 3)))
        self.assertEqual(remove_panel(A, "a"), (None, None))

    def test_coming_back_takes_its_share(self):
        three = SplitNode(H, (A, B, C), sizes=(100, 300, 600))
        rest, anchor, back = self.round_trip(three, "b")
        self.assertEqual(anchor, Anchor(("c",), H, True, 0.3333))
        self.assertEqual(back, SplitNode(H, (A, B, C), sizes=(100, 200, 400)))  # joins the split

    def test_tabs(self):
        tabs = SplitNode(H, (TabsNode((A, B, C), current=2), D))
        rest, anchor, back = self.round_trip(tabs, "b")
        self.assertEqual(anchor, Anchor(("c",), None, True))
        self.assertEqual(rest, SplitNode(H, (TabsNode((A, C), current=1), D)))
        self.assertEqual(back, SplitNode(H, (TabsNode((A, B, C), current=1), D)))  # shown: current
        # Its tab neighbour is now alone outside tabs: they become tabs again.
        self.assertEqual(insert_panel(SplitNode(H, (C, D)), "b", anchor),
                         SplitNode(H, (TabsNode((B, C), current=0), D)))

    def test_make_current_false_keeps_the_current_tab(self):
        tabs = SplitNode(H, (TabsNode((A, C), current=1), D))
        before_c = Anchor(("c",), None, True)
        self.assertEqual(insert_panel(tabs, "b", before_c, make_current=False),
                         SplitNode(H, (TabsNode((A, B, C), current=2), D)))  # still C
        group = SplitNode(H, (TabsNode((A, C), current=1), D))  # the group itself is the node
        self.assertEqual(insert_panel(group, "b", Anchor(("a", "c"), None, True),
                                      make_current=False),
                         SplitNode(H, (TabsNode((B, A, C), current=2), D)))
        self.assertEqual(insert_panel(SplitNode(H, (C, D)), "b", Anchor(("c",), None, False),
                                      make_current=False),
                         SplitNode(H, (TabsNode((C, B), current=0), D)))

    def test_a_tab_can_not_be_split_so_it_goes_beside_the_group(self):
        tree = SplitNode(V, (TabsNode((A, B)), C))
        self.assertEqual(insert_panel(tree, "d", Anchor(("a",), H, False)),
                         SplitNode(V, (SplitNode(H, (TabsNode((A, B)), D)), C)))

    def test_neighbours_partly_gone_use_the_ones_left(self):
        tree = SplitNode(V, (SplitNode(H, (A, B)), C))
        self.assertEqual(insert_panel(tree, "d", Anchor(("b", "gone"), V, True)),
                         SplitNode(V, (SplitNode(H, (A, SplitNode(V, (D, B)))), C)))

    def test_fallback_then_the_right_edge(self):
        tree = SplitNode(V, (A, B), sizes=(1, 1))
        gone = Anchor(("x",), V, True)
        self.assertEqual(insert_panel(tree, "d", gone, Anchor(("a",), V, True)),
                         SplitNode(V, (D, A, B), sizes=(0, 1, 1)))  # half of a's 1 rounds to 0
        self.assertEqual(insert_panel(tree, "d", gone, None), SplitNode(H, (tree, D)))
        self.assertEqual(insert_panel(None, "d", gone), D)

    def test_a_shown_panel_is_left_alone(self):
        tree = SplitNode(H, (A, B))
        self.assertIs(insert_panel(tree, "a", Anchor(("b",), V, False)), tree)


class LayeringTests(unittest.TestCase):
    def test_layout_modules_import_no_qt(self):
        code = (
            "import sys, rzgit.ui.layout.layout_model, "
            "rzgit.ui.layout.layout_presets; "
            "assert 'PySide6' not in sys.modules, sorted(m for m in sys.modules if 'PySide6' in m)"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class MoveTests(unittest.TestCase):
    """Drag and drop as an edit (spec §6.1)."""

    ROW = SplitNode(H, (A, B, C), sizes=(100, 200, 300))

    def test_drop_zone_is_the_nearest_edge_within_a_quarter_else_the_centre(self):
        cases = {(0.1, 0.5): DropZone.LEFT, (0.9, 0.5): DropZone.RIGHT,
                 (0.5, 0.1): DropZone.TOP, (0.5, 0.95): DropZone.BOTTOM,
                 (0.5, 0.5): DropZone.CENTER, (0.3, 0.6): DropZone.CENTER,
                 (0.05, 0.1): DropZone.LEFT}
        for (x, y), zone in cases.items():
            with self.subTest(x=x, y=y):
                self.assertIs(drop_zone(x, y), zone)

    def test_an_edge_in_the_same_direction_splits_the_targets_space_evenly(self):
        self.assertEqual(move_panel(self.ROW, "a", "c", DropZone.RIGHT),
                         SplitNode(H, (B, C, A), sizes=(200, 150, 150)))
        self.assertEqual(move_panel(self.ROW, "a", "b", DropZone.LEFT),
                         SplitNode(H, (A, B, C), sizes=(100, 100, 300)))

    def test_an_edge_across_makes_a_new_even_split(self):
        self.assertEqual(move_panel(self.ROW, "a", "c", DropZone.BOTTOM),
                         SplitNode(H, (B, SplitNode(V, (C, A), sizes=(500, 500))),
                                   sizes=(200, 300)))
        self.assertEqual(move_panel(self.ROW, "c", "a", DropZone.TOP),
                         SplitNode(H, (SplitNode(V, (C, A), sizes=(500, 500)), B),
                                   sizes=(100, 200)))

    def test_the_centre_makes_tabs_with_the_moved_panel_current(self):
        self.assertEqual(move_panel(self.ROW, "a", "b", DropZone.CENTER),
                         SplitNode(H, (TabsNode((B, A), current=1), C), sizes=(200, 300)))

    def test_the_old_place_collapses(self):
        tree = SplitNode(H, (A, SplitNode(V, (B, C))))
        self.assertEqual(move_panel(tree, "b", "a", DropZone.TOP),
                         SplitNode(H, (SplitNode(V, (B, A), sizes=(500, 500)), C)))

    def test_an_edge_of_a_tab_group_goes_beside_the_whole_group(self):
        tree = SplitNode(H, (TabsNode((A, B)), C))
        self.assertEqual(move_panel(tree, "c", "a", DropZone.LEFT),
                         SplitNode(H, (C, TabsNode((A, B))), sizes=(500, 500)))
        self.assertEqual(move_panel(tree, "a", "b", DropZone.RIGHT),
                         SplitNode(H, (TabsNode((B,)), A, C)))

    def test_moves_that_change_nothing_return_the_same_tree(self):
        tabs = SplitNode(H, (TabsNode((A, B)), C))
        for tree, args in ((self.ROW, ("a", "a", DropZone.LEFT)),
                           (self.ROW, ("a", "gone", DropZone.LEFT)),
                           (self.ROW, ("gone", "a", DropZone.LEFT)),
                           (tabs, ("a", "b", DropZone.CENTER)),
                           (A, ("a", "a", DropZone.CENTER))):
            with self.subTest(args):
                self.assertIs(move_panel(tree, *args), tree)


if __name__ == "__main__":
    unittest.main()
