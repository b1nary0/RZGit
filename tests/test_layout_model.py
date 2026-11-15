import json
import subprocess
import sys
import unittest

from rzgitclient.ui.layout.layout_model import (
    LayoutError,
    Orientation,
    PanelNode,
    SplitNode,
    TabsNode,
    from_dict,
    normalize,
    to_dict,
)
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT

H, V = Orientation.HORIZONTAL, Orientation.VERTICAL
A, B, C = PanelNode("a"), PanelNode("b"), PanelNode("c")
KNOWN = {"a", "b", "c"}


def root(node: dict) -> dict:
    return {"version": 1, "root": node}


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
                data = json.loads(json.dumps(to_dict(tree)))
                self.assertEqual(from_dict(data), tree)

    def test_shape(self):
        data = to_dict(SplitNode(H, (A, TabsNode((B,))), sizes=(1, 2)))
        self.assertEqual(data, root({
            "type": "split", "orientation": "horizontal", "sizes": [1, 2],
            "children": [panel("a"), {"type": "tabs", "current": 0, "children": [panel("b")]}],
        }))

    def test_structural_errors_raise_layout_error(self):
        bad = {
            "not an object": [],
            "missing version": {"root": panel("a")},
            "wrong version": {"version": 2, "root": panel("a")},
            "missing root": {"version": 1},
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
                self.assertEqual(from_dict(data), SplitNode(V, (A, B)))

    def test_current_defaults_to_zero(self):
        self.assertEqual(from_dict(root({"type": "tabs", "children": [panel("a")]})),
                         TabsNode((A,)))


class NormalizeTests(unittest.TestCase):
    def test_valid_tree_is_unchanged(self):
        known = {"branches", "files", "history"}
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


class LayeringTests(unittest.TestCase):
    def test_layout_modules_import_no_qt(self):
        code = (
            "import sys, rzgitclient.ui.layout.layout_model, "
            "rzgitclient.ui.layout.layout_presets; "
            "assert 'PySide6' not in sys.modules, sorted(m for m in sys.modules if 'PySide6' in m)"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
