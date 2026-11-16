import json
import subprocess
import sys
import unittest

from rzgitclient.ui.layout.layout_model import (
    Anchor,
    DropZone,
    LayoutError,
    LayoutState,
    Orientation,
    PanelNode,
    SplitNode,
    TabsNode,
    anchor_of,
    drop_zone,
    from_dict,
    insert_panel,
    move_panel,
    normalize,
    panel_ids,
    remove_panel,
    to_dict,
)
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT

H, V = Orientation.HORIZONTAL, Orientation.VERTICAL
A, B, C = PanelNode("a"), PanelNode("b"), PanelNode("c")
KNOWN = {"a", "b", "c"}


def root(node: dict, hidden: dict | None = None) -> dict:
    return {"version": 2, "root": node, "hidden": hidden or {}}


def tree_of(data: dict):
    return from_dict(data).root


def panel(pid: str) -> dict:
    return {"type": "panel", "panel_id": pid}


class SerialisationTests(unittest.TestCase):
    def test_round_trip_through_json(self):
        trees = (
            DEFAULT_LAYOUT,
            A,
            SplitNode(V, (A, TabsNode((B, C), current=1))),
            SplitNode(H, (A, SplitNode(V, (B, C), sizes=(1, 2))), sizes=(0, 5)),
        )
        for tree in trees:
            with self.subTest(tree=tree):
                state = LayoutState(tree, {"c": Anchor(("a", "b"), V, True, 0.25),
                                           "d": Anchor(("a",), None, False)})
                data = json.loads(json.dumps(to_dict(state)))
                self.assertEqual(from_dict(data), state)

    def test_version_1_loads_with_nothing_hidden(self):
        self.assertEqual(from_dict({"version": 1, "root": panel("a")}), LayoutState(A))

    def test_anchor_shape(self):
        data = to_dict(LayoutState(A, {"b": Anchor(("a",), None, True),
                                       "c": Anchor(("a", "b"), H, False, 0.5)}))
        self.assertEqual(data["hidden"], {
            "b": {"neighbors": ["a"], "container": "tabs", "before": True},
            "c": {"neighbors": ["a", "b"], "container": "horizontal", "before": False, "share": 0.5},
        })

    def test_bad_share_is_dropped_not_rejected(self):
        for share in (0, 1, 1.5, -0.2, "0.5", True, None):
            with self.subTest(share=share):
                anchor = {"neighbors": ["a"], "container": "vertical", "before": True, "share": share}
                self.assertEqual(from_dict(root(panel("a"), {"b": anchor})).hidden["b"].share, None)

    def test_shape(self):
        data = to_dict(LayoutState(SplitNode(H, (A, TabsNode((B,))), sizes=(1, 2))))
        self.assertEqual(data, root({
            "type": "split", "orientation": "horizontal", "sizes": [1, 2],
            "children": [panel("a"), {"type": "tabs", "current": 0, "children": [panel("b")]}],
        }))

    def test_structural_errors_raise_layout_error(self):
        bad = {
            "not an object": [],
            "missing version": {"root": panel("a")},
            "wrong version": {"version": 3, "root": panel("a"), "hidden": {}},
            "missing root": {"version": 1},
            "missing hidden": {"version": 2, "root": panel("a")},
            "anchor not an object": root(panel("a"), {"b": "a"}),
            "anchor without neighbors": root(panel("a"), {"b": {
                "neighbors": [], "container": "tabs", "before": True}}),
            "unknown anchor container": root(panel("a"), {"b": {
                "neighbors": ["a"], "container": "grid", "before": True}}),
            "mistyped before": root(panel("a"), {"b": {
                "neighbors": ["a"], "container": "tabs", "before": 1}}),
            "unknown node type": root({"type": "grid"}),
            "missing panel id": root({"type": "panel"}),
            "mistyped panel id": root({"type": "panel", "panel_id": 3}),
            "unknown orientation": root({"type": "split", "orientation": "diagonal", "children": []}),
            "children not a list": root({"type": "split", "orientation": "vertical", "children": {}}),
            "child not an object": root({"type": "split", "orientation": "vertical", "children": ["a"]}),
            "split inside tabs": root({"type": "tabs", "children": [
                {"type": "split", "orientation": "vertical", "children": []}]}),
            "mistyped current": root({"type": "tabs", "current": "1", "children": [panel("a")]}),
        }
        for name, data in bad.items():
            with self.subTest(name), self.assertRaises(LayoutError):
                from_dict(data)
        self.assertTrue(issubclass(LayoutError, ValueError))

    def test_bad_sizes_are_dropped_not_rejected(self):
        for sizes in ([1], [1, 2, 3], [1, -2], [1, "2"], [1.5, 2], [True, 2], "12", None):
            with self.subTest(sizes=sizes):
                data = root({"type": "split", "orientation": "vertical",
                             "sizes": sizes, "children": [panel("a"), panel("b")]})
                self.assertEqual(tree_of(data), SplitNode(V, (A, B)))

    def test_current_defaults_to_zero(self):
        self.assertEqual(tree_of(root({"type": "tabs", "children": [panel("a")]})),
                         TabsNode((A,)))


class NormalizeTests(unittest.TestCase):
    def test_valid_tree_is_unchanged(self):
        known = {"controls", "branches", "files", "history", "diff", "staged", "commit", "commit_files"}
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
            "import sys, rzgitclient.ui.layout.layout_model, "
            "rzgitclient.ui.layout.layout_presets; "
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
