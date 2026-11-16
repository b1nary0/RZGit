import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QSplitter, QTabWidget

from rzgitclient.ui.layout.layout_host import LayoutHost
from rzgitclient.ui.layout.layout_model import Orientation, PanelNode, SplitNode, TabsNode
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

    # ---- building -------------------------------------------------------

    def test_builds_the_default_layout(self):
        self.host.apply(DEFAULT_LAYOUT)
        self.assertEqual(set(self.host.panels), {"branches", "files", "history"})

        root = self.root()
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
        tabs = self.root()
        self.assertIsInstance(tabs, QTabWidget)
        self.assertEqual([tabs.tabText(i) for i in range(2)], ["History", "Files"])
        self.assertEqual(tabs.currentIndex(), 1)
        self.host.panels["files"].set_header("Files (3)")
        self.assertEqual(tabs.tabText(1), "Files (3)")

    def test_unknown_panels_are_dropped_and_empty_tree_shows_nothing(self):
        self.host.apply(SplitNode(H, (PanelNode("history"), PanelNode("nope"))))
        self.assertEqual(set(self.host.panels), {"history"})
        self.assertIsInstance(self.root(), PanelFrame)  # single-child split collapsed

        self.host.apply(PanelNode("nope"))
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

    # ---- reading back ---------------------------------------------------

    def test_current_layout_reads_sizes_and_current_tab(self):
        tree = SplitNode(H, (PanelNode("branches"), TABS), sizes=(200, 599))
        self.host.apply(tree)
        self.show()
        splitter, tabs = self.root(), self.root().widget(1)
        splitter.setSizes([300, 499])
        tabs.setCurrentIndex(0)

        read = self.host.current_layout()
        self.assertEqual(read, SplitNode(
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
        self.assertEqual(sum(read.sizes) + 1, 800)  # the 1px divider takes the rest


if __name__ == "__main__":
    unittest.main()
