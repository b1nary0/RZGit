import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QSplitter, QTabWidget

from rzgitclient.ui.layout.layout_host import LayoutHost
from rzgitclient.ui.layout.layout_model import (
    LayoutState,
    Orientation,
    PanelNode,
    SplitNode,
    TabsNode,
    remove_panel,
)
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT
from rzgitclient.ui.layout.panel_frame import PanelFrame
from tests.support import commit_file, make_panel_context, make_repo, qt_app, wait_idle

H, V = Orientation.HORIZONTAL, Orientation.VERTICAL
TABS = TabsNode((PanelNode("history"), PanelNode("files")), current=1)


def flush_deletes():
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class LayoutHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.ctx = make_panel_context()
        self.host = LayoutHost(self.ctx)
        self.host.resize(800, 600)

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        flush_deletes()

    def show(self):
        self.host.show()
        self.app.processEvents()

    def root(self):
        return self.host.layout().itemAt(0).widget()

    def body(self):
        """What's below the Controls bar, which every layout gets (§6.6)."""
        root = self.root()
        self.assertIs(root.widget(0).panel, self.host.panels["controls"])
        return root.widget(1)

    # ---- building -------------------------------------------------------

    def test_builds_the_default_layout(self):
        self.host.apply(DEFAULT_LAYOUT)
        self.assertEqual(set(self.host.panels), {"controls", "branches", "files", "history"})

        top = self.root()
        self.assertEqual(top.orientation(), Qt.Orientation.Vertical)
        root = self.body()
        self.assertIsInstance(root, QSplitter)
        self.assertEqual(root.orientation(), Qt.Orientation.Horizontal)
        self.assertFalse(root.childrenCollapsible())
        sidebar, history = root.widget(0), root.widget(1)
        self.assertEqual(sidebar.orientation(), Qt.Orientation.Vertical)
        frames = [sidebar.widget(0), sidebar.widget(1), history]
        self.assertTrue(all(isinstance(f, PanelFrame) for f in frames))
        self.assertEqual(
            [f.panel for f in frames],
            [self.host.panels["branches"], self.host.panels["files"], self.host.panels["history"]],
        )

    def test_tabs_use_the_panel_header_and_follow_it(self):
        self.host.apply(TABS)
        tabs = self.body()
        self.assertIsInstance(tabs, QTabWidget)
        self.assertEqual([tabs.tabText(i) for i in range(2)], ["History", "Files"])
        self.assertEqual(tabs.currentIndex(), 1)
        self.host.panels["files"].set_header("Files (3)")
        self.assertEqual(tabs.tabText(1), "Files (3)")

    def test_unknown_panels_are_dropped_and_empty_tree_shows_nothing(self):
        self.host.apply(SplitNode(H, (PanelNode("history"), PanelNode("nope"))))
        self.assertEqual(set(self.host.panels), {"controls", "history"})
        self.assertIsInstance(self.body(), PanelFrame)  # single-child split collapsed

        self.host.apply(PanelNode("nope"))  # nothing left: not even the Controls bar
        self.assertEqual(dict(self.host.panels), {})
        self.assertIsNone(self.host.current_layout())
        self.assertEqual(self.host.layout().count(), 0)

    # ---- re-applying ----------------------------------------------------

    def test_reapplying_reuses_panels_and_deletes_dropped_ones(self):
        self.host.apply(DEFAULT_LAYOUT)
        history, files = self.host.panels["history"], self.host.panels["files"]
        deleted = []
        self.host.panels["branches"].destroyed.connect(lambda: deleted.append("branches"))

        self.host.apply(TABS)
        flush_deletes()
        self.assertIs(self.host.panels["history"], history)
        self.assertIs(self.host.panels["files"], files)
        self.assertNotIn("branches", self.host.panels)
        self.assertEqual(deleted, ["branches"])

    def test_panel_state_survives_and_is_visible_again(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = make_repo(Path(tmp.name) / "repo")
        commit_file(repo, "a.txt", "1", "first")
        commit_file(repo, "a.txt", "2", "second")
        self.ctx.session.open(repo)
        wait_idle(self.ctx.session)

        self.host.apply(DEFAULT_LAYOUT)
        self.show()
        history = self.host.panels["history"]
        history._view.setCurrentIndex(history._model.index(1, 0))

        self.host.apply(TabsNode((PanelNode("files"), PanelNode("history")), current=1))
        flush_deletes()
        self.app.processEvents()
        self.assertEqual(history._view.currentIndex().data(), "first")
        self.assertEqual(self.ctx.selection.commit.subject, "first")
        self.assertTrue(history.isVisible())
        self.assertFalse(self.host.panels["files"].isVisible())  # the other tab

        self.host.apply(DEFAULT_LAYOUT)  # back from tabs to frames
        flush_deletes()
        self.app.processEvents()
        self.assertTrue(all(p.isVisible() for p in self.host.panels.values()))

    def test_header_changes_after_moving_out_of_tabs(self):
        self.host.apply(TABS)
        self.host.apply(DEFAULT_LAYOUT)
        flush_deletes()  # the old tabs are gone; their connection must be too
        files = self.host.panels["files"]
        files.set_header("Files (2)")
        frame = files.parentWidget()
        self.assertIsInstance(frame, PanelFrame)
        self.assertEqual(frame.header.text(), "FILES (2)")

    # ---- hiding and showing (§6.6) ---------------------------------------

    def test_hide_and_show_a_panel_where_it_was(self):
        changes = []
        self.host.layout_changed.connect(lambda: changes.append(1))
        self.host.apply(DEFAULT_LAYOUT)
        self.show()
        self.host.hide_panel("files")
        flush_deletes()
        self.assertEqual(set(self.host.panels), {"controls", "branches", "history"})
        self.assertEqual(set(self.host.hidden), {"files"})
        self.assertEqual(self.host.state().hidden, self.host.hidden)

        self.host.show_panel("files")
        self.assertEqual(self.host.hidden, {})
        sidebar = self.body().widget(0)
        self.assertEqual([sidebar.widget(i).panel.panel_id for i in range(2)], ["branches", "files"])
        self.assertEqual(len(changes), 3)  # apply, hide, show

    def test_the_last_panel_can_not_be_hidden(self):
        self.host.apply(PanelNode("history"))
        self.assertFalse(self.host.can_hide("history"))  # Controls doesn't count
        self.host.hide_panel("history")
        self.assertEqual(set(self.host.panels), {"controls", "history"})
        self.host.hide_panel("nope")  # not shown: nothing happens
        self.assertEqual(self.host.hidden, {})

    def test_an_always_shown_panel_can_not_be_hidden_or_left_out(self):
        self.host.apply(DEFAULT_LAYOUT)
        self.assertFalse(self.host.can_hide("controls"))
        self.host.hide_panel("controls")
        self.assertIn("controls", self.host.panels)
        _, anchor = remove_panel(DEFAULT_LAYOUT, "controls")
        self.host.apply_state(LayoutState(DEFAULT_LAYOUT.children[1], {"controls": anchor}))
        self.assertIn("controls", self.host.panels)  # put back, at its default place
        self.assertEqual(self.host.hidden, {})

    def test_controls_bar_has_no_header_and_a_fixed_height(self):
        self.host.apply(DEFAULT_LAYOUT)
        self.show()
        top = self.root()
        bar = top.widget(0)
        self.assertFalse(bar.header.isVisible())
        self.assertEqual(bar.minimumHeight(), bar.maximumHeight())
        self.assertFalse(top.handle(1).isEnabled())  # its divider can't be dragged
        top.setSizes([300, 299])
        self.assertEqual(top.sizes()[0], bar.maximumHeight())
        self.assertTrue(self.body().widget(0).widget(0).header.isVisible())  # others keep theirs

    def test_a_panel_hidden_without_an_anchor_goes_to_its_default_place(self):
        self.host.apply(SplitNode(H, (PanelNode("branches"), PanelNode("history"))))
        self.host.show_panel("files")  # never hidden, e.g. new in this version
        self.assertEqual(self.body().widget(0).orientation(), Qt.Orientation.Vertical)
        self.assertEqual(set(self.host.panels), {"controls", "branches", "files", "history"})

    def test_apply_keeps_hidden_only_for_panels_not_shown(self):
        _, anchor = remove_panel(DEFAULT_LAYOUT, "files")
        self.host.apply(DEFAULT_LAYOUT, {"files": anchor, "nope": anchor})
        self.assertEqual(self.host.hidden, {})  # files is shown; nope isn't a panel
        self.host.apply_state(LayoutState(PanelNode("history"), {"files": anchor}))
        self.assertEqual(set(self.host.hidden), {"files"})
        self.host.apply(PanelNode("history"))  # a preset or reset forgets hidden panels
        self.assertEqual(self.host.hidden, {})

    # ---- reading back ---------------------------------------------------

    def test_current_layout_reads_sizes_and_current_tab(self):
        tree = SplitNode(H, (PanelNode("branches"), TABS), sizes=(200, 599))
        self.host.apply(tree)
        self.show()
        splitter = self.body()
        tabs = splitter.widget(1)
        splitter.setSizes([300, 499])
        tabs.setCurrentIndex(0)

        read = self.host.current_layout()
        self.assertEqual(read.children[0], PanelNode("controls"))
        self.assertEqual(read.children[1], SplitNode(
            H, (PanelNode("branches"), TabsNode(TABS.children, current=0)), sizes=(300, 499)
        ))

        # Applying what was read back restores it.
        self.host.apply(read)
        self.app.processEvents()
        self.assertEqual(self.host.current_layout(), read)

    def test_current_layout_of_the_default_keeps_its_shape(self):
        self.host.apply(DEFAULT_LAYOUT)
        self.show()
        read = self.host.current_layout()
        strip = lambda n: (  # noqa: E731
            SplitNode(n.orientation, tuple(strip(c) for c in n.children))
            if isinstance(n, SplitNode) else n
        )
        self.assertEqual(strip(read), strip(DEFAULT_LAYOUT))
        self.assertEqual(sum(read.children[1].sizes) + 1, 800)  # the 1px divider takes the rest
        self.assertEqual(sum(read.sizes) + 1, 600)


if __name__ == "__main__":
    unittest.main()
