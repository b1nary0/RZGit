import tempfile
import unittest
from pathlib import Path

from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import (
    QColor,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QMouseEvent,
)
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QSplitter, QTabWidget

from rzgitclient.ui.layout.layout_host import PANEL_MIME, LayoutHost
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
        self.assertEqual(set(self.host.panels), {"controls", "branches", "files", "history", "diff", "staged", "commit", "commit_details", "commit_files", "stash"})

        top = self.root()
        self.assertEqual(top.orientation(), Qt.Orientation.Vertical)
        root = self.body()
        self.assertIsInstance(root, QSplitter)
        self.assertEqual(root.orientation(), Qt.Orientation.Horizontal)
        self.assertFalse(root.childrenCollapsible())
        sidebar, content = root.widget(0), root.widget(1)
        self.assertEqual(sidebar.orientation(), Qt.Orientation.Vertical)
        frames = [sidebar.widget(0), sidebar.widget(1)]
        self.assertTrue(all(isinstance(f, PanelFrame) for f in frames))
        self.assertEqual([f.panel for f in frames],
                         [self.host.panels["branches"], self.host.panels["files"]])
        self.assertIsInstance(content, QTabWidget)  # History and Diff
        self.assertEqual([content.widget(i) for i in range(2)],
                         [self.host.panels["history"], self.host.panels["diff"]])
        self.assertEqual(content.currentIndex(), 0)
        self.assertEqual([sidebar.widget(i).panel.panel_id for i in range(2, 6)],
                         ["staged", "commit", "commit_details", "commit_files"])

    def test_tabs_use_the_panel_header_and_follow_it(self):
        self.host.apply(TABS)
        tabs = self.body()
        self.assertIsInstance(tabs, QTabWidget)
        self.assertEqual([tabs.tabText(i) for i in range(2)], ["History", "Unstaged Files"])
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
        history._view.setCurrentIndex(history._model.index(2, 0))  # row 0: Uncommitted

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
        # Only Diff, History's other tab, and the collapsed Unstaged Files, Staged
        # Files and Commit: a commit is selected, so Commit Files is in their place.
        self.assertEqual({pid for pid, p in self.host.panels.items() if not p.isVisible()},
                         {"diff", "files", "staged", "commit"})

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
        self.assertEqual(set(self.host.panels), {"controls", "branches", "history", "diff", "staged", "commit", "commit_details", "commit_files", "stash"})
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

    def test_a_panel_asking_to_be_activated_becomes_the_current_tab(self):
        self.host.apply(DEFAULT_LAYOUT)
        tabs = self.body().widget(1)
        self.host.panels["diff"].request_activation()
        self.assertEqual(tabs.currentWidget(), self.host.panels["diff"])
        self.host.panels["history"].request_activation()
        self.assertEqual(tabs.currentWidget(), self.host.panels["history"])

    def test_activation_follows_a_panel_into_new_tabs_and_is_harmless_in_a_split(self):
        self.host.apply(DEFAULT_LAYOUT)
        self.host.apply(SplitNode(H, (PanelNode("history"), TabsNode(
            (PanelNode("files"), PanelNode("diff"), PanelNode("branches"))))))
        flush_deletes()  # the old tabs, and their connections, are gone
        tabs = self.body().widget(1)
        self.host.panels["diff"].request_activation()
        self.assertEqual(tabs.currentIndex(), 1)
        self.host.panels["history"].request_activation()  # in a split: nothing to do
        self.assertEqual(tabs.currentIndex(), 1)

    def test_apply_keeps_hidden_only_for_panels_not_shown(self):
        _, anchor = remove_panel(DEFAULT_LAYOUT, "files")
        self.host.apply(DEFAULT_LAYOUT, {"files": anchor, "nope": anchor})
        self.assertEqual(self.host.hidden, {})  # files is shown; nope isn't a panel
        self.host.apply_state(LayoutState(PanelNode("history"), {"files": anchor}))
        self.assertEqual(set(self.host.hidden), {"files"})
        self.host.apply(PanelNode("history"))  # a preset or reset forgets hidden panels
        self.assertEqual(self.host.hidden, {})

    # ---- collapsed panels (§5.1) ------------------------------------------

    def settle(self):
        """Let the host apply collapse changes (a zero-time timer), then lay out."""
        for _ in range(2):
            self.app.processEvents()

    SIDE = SplitNode(H, (PanelNode("history"), PanelNode("commit")), sizes=(500, 300))

    def test_a_collapsed_pane_lends_its_space_and_takes_it_back(self):
        self.host.apply(self.SIDE)
        self.show()
        commit = self.host.panels["commit"]
        commit.set_collapsed(False)
        self.settle()
        splitter = self.body()
        before = splitter.sizes()
        commit.set_collapsed(True)
        self.settle()
        self.assertFalse(splitter.widget(1).isVisible())
        self.assertEqual(splitter.sizes()[1], 0)
        self.assertEqual(splitter.sizes()[0], sum(before) + splitter.handleWidth())
        commit.set_collapsed(False)
        self.settle()
        self.assertEqual(splitter.sizes(), before)

    def test_a_layout_read_while_collapsed_keeps_the_pane_s_size(self):
        self.host.apply(self.SIDE)
        self.show()
        commit = self.host.panels["commit"]
        commit.set_collapsed(False)
        self.settle()
        before = self.host.current_layout()
        commit.set_collapsed(True)
        self.settle()
        read = self.host.current_layout()
        self.assertEqual(read.children[1].children, before.children[1].children)
        for got, want in zip(read.children[1].sizes, before.children[1].sizes):
            self.assertAlmostEqual(got, want, delta=1)

    def test_a_panel_collapsed_from_the_start_comes_back_at_its_saved_size(self):
        self.host.apply(self.SIDE)  # nothing staged: Commit is collapsed
        self.show()
        splitter = self.body()
        self.assertFalse(splitter.widget(1).isVisible())
        self.assertEqual(self.host.current_layout().children[1].sizes[1], 300)
        self.host.panels["commit"].set_collapsed(False)
        self.settle()
        self.assertTrue(splitter.widget(1).isVisible())
        self.assertAlmostEqual(splitter.sizes()[1] / sum(splitter.sizes()), 300 / 800, delta=0.01)

    def test_a_collapsing_pane_lends_its_space_to_the_pane_above(self):
        tree = SplitNode(V, (PanelNode("branches"), PanelNode("files"), PanelNode("commit")),
                         sizes=(150, 250, 150))
        self.host.apply(SplitNode(H, (tree, PanelNode("history"))))  # Commit starts collapsed
        self.show()
        column = self.body().widget(0)
        branches, files, commit = column.sizes()
        self.assertEqual(commit, 0)
        self.host.panels["commit"].set_collapsed(False)
        self.settle()
        self.assertEqual(column.sizes(), [branches, files - 150 - column.handleWidth(), 150])
        self.host.panels["commit"].set_collapsed(True)
        self.settle()
        self.assertEqual(column.sizes(), [branches, files, 0])  # Files took it back
        read = self.host.current_layout().children[1].children[0]
        self.assertEqual(read.sizes[0], branches)
        self.assertEqual(read.sizes[2], 150)

    def test_a_swap_gives_the_freed_space_to_the_panes_shown_and_back(self):
        column = SplitNode(V, (PanelNode("branches"), PanelNode("files"), PanelNode("staged"),
                               PanelNode("commit_files")), sizes=(150, 250, 150, 300))
        self.host.apply(SplitNode(H, (column, PanelNode("history"))))
        self.show()
        panels = self.host.panels
        panels["staged"].set_collapsed(False)
        panels["commit_files"].set_collapsed(True)
        self.settle()
        splitter = self.body().widget(0)
        branches, files, staged, _ = splitter.sizes()
        # Both change in one moment, as when a commit is selected.
        panels["files"].set_collapsed(True)
        panels["staged"].set_collapsed(True)
        panels["commit_files"].set_collapsed(False)
        self.settle()
        self.assertEqual(splitter.sizes()[0], branches)  # untouched
        self.assertEqual(splitter.sizes()[3], files + staged + splitter.handleWidth())
        panels["commit_files"].set_collapsed(True)  # and back
        panels["files"].set_collapsed(False)
        panels["staged"].set_collapsed(False)
        self.settle()
        got = splitter.sizes()
        self.assertEqual(got[0], branches)
        self.assertEqual(got[3], 0)
        self.assertAlmostEqual(got[1] / got[2], files / staged, delta=0.05)

    def test_a_swap_reads_as_swapped_back_and_survives_a_rebuild(self):
        column = SplitNode(V, (PanelNode("branches"), PanelNode("files"),
                               PanelNode("commit_files")), sizes=(150, 400, 0))
        self.host.apply(SplitNode(H, (column, PanelNode("history"))))
        self.show()
        panels = self.host.panels
        panels["commit_files"].set_collapsed(True)
        self.settle()
        before = self.host.current_layout()
        panels["files"].set_collapsed(True)  # a commit is selected
        panels["commit_files"].set_collapsed(False)
        self.settle()
        read = self.host.current_layout()
        self.assertEqual(read, before)  # as if swapped back: Commit Files has no size
        self.host.apply(read)  # e.g. after hiding another panel
        self.settle()
        splitter = self.body().widget(0)
        self.assertFalse(splitter.widget(1).isVisible())
        self.assertTrue(splitter.widget(2).isVisible())
        self.assertGreater(splitter.sizes()[2], 300)  # in Unstaged Files' place
        self.assertEqual(self.host.current_layout(), before)

    def test_a_change_undone_in_the_same_moment_does_nothing(self):
        self.host.apply(self.SIDE)
        self.show()
        commit = self.host.panels["commit"]
        commit.set_collapsed(False)
        self.settle()
        before = self.body().sizes()
        commit.set_collapsed(True)
        commit.set_collapsed(False)
        self.settle()
        self.assertEqual(self.body().sizes(), before)

    def test_a_collapsed_tab_is_hidden_and_the_panel_stays_in_the_layout(self):
        self.host.apply(TabsNode((PanelNode("history"), PanelNode("commit"))))
        self.show()
        tabs = self.body()
        self.assertFalse(tabs.isTabVisible(1))
        self.host.panels["commit"].set_collapsed(False)
        self.assertTrue(tabs.isTabVisible(1))
        self.host.panels["commit"].set_collapsed(True)
        self.assertFalse(tabs.isTabVisible(1))
        self.assertIn("commit", self.host.panels)  # still shown, as far as View → Panels goes
        self.assertTrue(self.host.can_hide("commit"))

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


class DragDropTests(unittest.TestCase):
    """Moving panels by drag and drop (spec §6.2)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.ctx = make_panel_context()
        self.host = LayoutHost(self.ctx)
        self.host.resize(800, 600)
        self.host.apply(SplitNode(H, (PanelNode("history"), PanelNode("files")), sizes=(400, 400)))
        self.host.show()
        self.app.processEvents()

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        flush_deletes()

    def pane(self, panel_id):
        """The pane a panel sits in: its frame, or its tab group."""
        widget = self.host.panels[panel_id].parentWidget()
        return widget if isinstance(widget, PanelFrame) else widget.parentWidget()

    def at(self, panel_id, fx, fy):
        """A point in ``panel_id``'s pane, as fractions of its size, in host coordinates."""
        pane = self.pane(panel_id)
        return pane.mapTo(self.host, QPoint(int(pane.width() * fx), int(pane.height() * fy)))

    def mime(self, panel_id):
        data = QMimeData()
        data.setData(PANEL_MIME, panel_id.encode())
        return data

    def send(self, cls, panel_id, pos):
        """Deliver a drag event to the widget under ``pos``, as Qt does, so it
        reaches the host by propagation."""
        child = self.host.childAt(pos) or self.host
        local = child.mapFrom(self.host, pos)
        mime = self.mime(panel_id)
        args = (Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier)
        event = cls(QPointF(local), *args) if cls is QDropEvent else cls(local, *args)
        QApplication.sendEvent(child, event)
        return event

    def drag_to(self, panel_id, pos):
        self.send(QDragEnterEvent, panel_id, pos)
        return self.send(QDragMoveEvent, panel_id, pos)

    def drag(self, panel_id, during, result=Qt.DropAction.MoveAction):
        """Run ``host.drag_panel`` with ``during`` in place of the user's drag."""
        def run(_action):
            during()
            return result
        with mock.patch.object(QDrag, "exec", side_effect=run):
            self.host.drag_panel(panel_id)

    def drop(self, panel_id, pos):
        self.drag(panel_id, lambda: (self.drag_to(panel_id, pos),
                                     self.send(QDropEvent, panel_id, pos)))

    def body(self):
        return self.host.current_layout().children[1]  # below the Controls bar

    def test_an_edge_shows_the_indicator_and_the_drop_splits_beside_it(self):
        history = self.host.panels["history"]
        pos = self.at("files", 0.5, 0.9)
        pane = self.pane("files")

        def during():
            self.assertTrue(self.drag_to("history", pos).isAccepted())
            indicator = self.host._indicator
            self.assertTrue(indicator.isVisible())
            self.assertEqual(indicator.geometry().top(), pane.mapTo(self.host, QPoint(0, 0)).y()
                             + pane.height() - pane.height() // 2)  # the bottom half
            self.send(QDropEvent, "history", pos)
            self.assertFalse(indicator.isVisible())
            self.assertEqual(self.body().children[0], PanelNode("history"))  # not yet

        self.drag("history", during)
        # History's old row collapses into Files, so the two stack below Controls.
        root = self.host.current_layout()
        self.assertEqual((root.orientation, [c.panel_id for c in root.children]),
                         (V, ["controls", "files", "history"]))
        self.assertIs(self.host.panels["history"], history)  # the same panel, state kept

    def test_the_centre_and_the_tab_bar_make_tabs(self):
        self.drop("history", self.at("files", 0.5, 0.5))
        self.assertEqual(self.body(), TabsNode((PanelNode("files"), PanelNode("history")), 1))
        self.host.apply(SplitNode(H, (TABS, PanelNode("branches"))))
        self.app.processEvents()
        tab_bar = self.pane("history").tabBar()
        self.drop("branches", tab_bar.mapTo(self.host, tab_bar.rect().center()))
        self.assertEqual(self.body(), TabsNode(
            (PanelNode("history"), PanelNode("files"), PanelNode("branches")), 2))

    def test_controls_is_not_a_target_and_can_not_be_dropped(self):
        before = self.host.current_layout()
        controls = self.host.panels["controls"].parentWidget()
        pos = controls.mapTo(self.host, controls.rect().center())

        def during():
            self.assertFalse(self.drag_to("history", pos).isAccepted())
            self.assertFalse(self.host._indicator.isVisible())
            self.send(QDropEvent, "history", pos)

        self.drag("history", during)
        self.assertEqual(self.host.current_layout(), before)
        self.assertIsNone(self.host.drop_target("controls", self.at("files", 0.5, 0.9)))
        with mock.patch.object(self.host, "drag_panel") as drag:
            header = self.pane("controls").header
            self.press_and_move(header, QPoint(5, 5))
            drag.assert_not_called()

    def test_its_own_pane_is_not_a_target(self):
        def during():
            self.assertFalse(self.drag_to("history", self.at("history", 0.9, 0.5)).isAccepted())
            self.assertFalse(self.host._indicator.isVisible())
        self.drag("history", during)

    def test_drag_events_are_left_alone_when_no_panel_drag_runs(self):
        before = self.host.current_layout()
        self.assertFalse(self.drag_to("history", self.at("files", 0.9, 0.5)).isAccepted())
        self.send(QDropEvent, "history", self.at("files", 0.9, 0.5))
        self.assertEqual(self.host.current_layout(), before)

    def test_the_indicator_is_painted_in_the_theme_s_accent(self):
        seen = {}

        def during():
            self.drag_to("history", self.at("files", 0.9, 0.5))
            image = self.host._indicator.grab().toImage()
            seen["border"] = image.pixelColor(0, image.height() // 2)
            seen["fill"] = image.pixelColor(image.width() // 2, image.height() // 2)

        self.drag("history", during, Qt.DropAction.IgnoreAction)
        accent = QColor(self.ctx.theme.current.colors.accent)
        self.assertEqual(seen["border"].name(), accent.name())
        self.assertNotEqual(seen["fill"].name(), accent.name())  # the accent, see-through
        self.assertGreater(seen["fill"].blue(), seen["fill"].red())

    def press_and_move(self, widget, start):
        for kind, pos in ((QEvent.Type.MouseButtonPress, start),
                          (QEvent.Type.MouseMove, start + QPoint(40, 0))):
            QApplication.sendEvent(widget, QMouseEvent(
                kind, QPointF(pos), QPointF(widget.mapToGlobal(pos)), Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    def test_a_drag_starts_from_a_header_or_a_tab(self):
        with mock.patch.object(self.host, "drag_panel") as drag:
            header = self.pane("files").header
            self.press_and_move(header, header.rect().center())
            drag.assert_called_once_with("files")
        self.host.apply(SplitNode(H, (TABS, PanelNode("branches"))))
        self.app.processEvents()
        tab_bar = self.pane("history").tabBar()
        with mock.patch.object(self.host, "drag_panel") as drag:
            self.press_and_move(tab_bar, tab_bar.tabRect(0).center())
            drag.assert_called_once_with("history")

    def test_a_cancelled_drag_leaves_the_layout_as_it_was(self):
        before = self.host.current_layout()
        changed = mock.Mock()
        self.host.layout_changed.connect(changed)

        def cancel():  # Escape: Qt sends a leave, and no drop
            self.drag_to("history", self.at("files", 0.9, 0.5))
            QApplication.sendEvent(self.host, QDragLeaveEvent())

        self.drag("history", cancel, Qt.DropAction.IgnoreAction)
        changed.assert_not_called()
        self.assertEqual(self.host.current_layout(), before)
        self.assertFalse(self.host._indicator.isVisible())

if __name__ == "__main__":
    unittest.main()
