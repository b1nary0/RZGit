import dataclasses
import tempfile
import unittest
from pathlib import Path

from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import (
    QColor,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QMouseEvent,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QSplitter, QTabWidget
from PySide6.QtGui import QAction

from rzgitclient.ui.layout.layout_host import PANEL_MIME, LayoutHost, _drag_source
from rzgitclient.ui.layout.layout_host import EMPTY_AREA_MIN, AreaPane
from rzgitclient.ui.layout.layout_model import (
    Anchor,
    Area,
    Arrangement,
    LayoutState,
    Orientation,
    PanelNode,
    SplitNode,
    TabsNode,
)
from rzgitclient.ui.layout.layout_model import DropZone, panel_ids
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT, matching_preset
from rzgitclient.ui.layout.panel_frame import PanelFrame
from rzgitclient.ui.panels.history_model import SUBJECT_COLUMN
from tests.support import commit_file, make_panel_context, make_repo, qt_app, wait_idle

H, V = Orientation.HORIZONTAL, Orientation.VERTICAL
TABS = TabsNode((PanelNode("history"), PanelNode("files")), current=1)


def apply_to(host, layout, hidden=None):
    """Apply a state, or a tree as the Main pane's (Controls joins it in its area),
    with the empty Sidebar at its narrowest."""
    if not isinstance(layout, LayoutState):
        layout = LayoutState({Area.MAIN: layout}, sizes=(0, 1000), hidden=hidden or {})
    host.apply(layout)


def main_tree(host):
    return host.state().tree(Area.MAIN)


def area_content(host, area):
    """The widget of ``area``'s tree, below the Controls bar or in it."""
    root = host.layout().itemAt(0).widget()
    panes = [root.widget(0), *(root.widget(1).widget(i) for i in range(2))]
    [pane] = [p for p in panes if isinstance(p, AreaPane) and p.area is area]
    return pane.content


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
        """The Main pane's widget (Controls, which every layout gets, is above, §6.6)."""
        self.assertIs(area_content(self.host, Area.CONTROLS).panel, self.host.panels["controls"])
        return area_content(self.host, Area.MAIN)

    def sidebar(self):
        return area_content(self.host, Area.SIDEBAR)

    # ---- building -------------------------------------------------------

    def test_builds_the_default_layout(self):
        apply_to(self.host, DEFAULT_LAYOUT)
        self.assertEqual(set(self.host.panels), {"controls", "branches", "worktrees", "files", "history", "diff", "staged", "commit", "commit_details", "commit_files", "stash"})

        top = self.root()
        self.assertEqual(top.orientation(), Qt.Orientation.Vertical)
        areas = top.widget(1)  # below the Controls area
        self.assertIsInstance(areas, QSplitter)
        self.assertEqual(areas.orientation(), Qt.Orientation.Horizontal)
        self.assertFalse(areas.childrenCollapsible())
        self.assertEqual([areas.widget(i).area for i in range(2)], [Area.SIDEBAR, Area.MAIN])
        sidebar, content = self.sidebar(), self.body()
        self.assertEqual(sidebar.orientation(), Qt.Orientation.Vertical)
        frames = [sidebar.widget(0), sidebar.widget(1), sidebar.widget(2)]
        self.assertTrue(all(isinstance(f, PanelFrame) for f in frames))
        self.assertEqual([f.panel for f in frames],
                         [self.host.panels["branches"], self.host.panels["worktrees"],
                          self.host.panels["files"]])
        self.assertEqual(content.orientation(), Qt.Orientation.Vertical)
        tabs, details = content.widget(0), content.widget(1)
        self.assertIsInstance(tabs, QTabWidget)  # History and Diff
        self.assertEqual([tabs.widget(i) for i in range(2)],
                         [self.host.panels["history"], self.host.panels["diff"]])
        self.assertEqual(tabs.currentIndex(), 0)
        self.assertIs(details.panel, self.host.panels["commit_details"])  # below them
        self.assertEqual([sidebar.widget(i).panel.panel_id for i in range(3, 6)],
                         ["staged", "commit", "commit_files"])

    def test_tabs_use_the_panel_header_and_follow_it(self):
        apply_to(self.host, TABS)
        tabs = self.body()
        self.assertIsInstance(tabs, QTabWidget)
        self.assertEqual([tabs.tabText(i) for i in range(2)], ["History", "Unstaged Files"])
        self.assertEqual(tabs.currentIndex(), 1)
        self.host.panels["files"].set_header("Files (3)")
        self.assertEqual(tabs.tabText(1), "Files (3)")

    def test_unknown_panels_are_dropped_and_an_empty_area_shows_its_surface(self):
        apply_to(self.host, SplitNode(H, (PanelNode("history"), PanelNode("nope"))))
        self.assertEqual(set(self.host.panels), {"controls", "history"})
        self.assertIsInstance(self.body(), PanelFrame)  # single-child split collapsed
        self.assertIsNone(self.sidebar())  # empty, but there
        self.assertEqual(area_content(self.host, Area.MAIN).parentWidget().property("surface"),
                         "content")

    def test_an_empty_area_keeps_its_space_without_saved_sizes(self):
        self.host.apply(LayoutState({Area.MAIN: PanelNode("history")}))  # no sizes
        self.show()
        areas = self.root().widget(1)
        # An even split, as far as History's own minimum width allows: not just a strip.
        self.assertGreater(areas.sizes()[0], sum(areas.sizes()) // 3)
        areas.setSizes([0, sum(areas.sizes())])  # dragged as far as it goes
        self.assertEqual(areas.sizes()[0], EMPTY_AREA_MIN)

    def test_every_area_is_built_even_when_every_panel_is_gone(self):
        self.host.apply(LayoutState({Area.MAIN: PanelNode("nope")}))
        self.assertEqual(dict(self.host.panels), {"controls": self.host.panels["controls"]})
        self.assertIsNone(self.body())
        self.assertIsNone(self.sidebar())
        self.assertEqual(set(self.host.state().areas), set(Area))

    # ---- re-applying ----------------------------------------------------

    def test_reapplying_reuses_panels_and_deletes_dropped_ones(self):
        apply_to(self.host, DEFAULT_LAYOUT)
        history, files = self.host.panels["history"], self.host.panels["files"]
        deleted = []
        self.host.panels["branches"].destroyed.connect(lambda: deleted.append("branches"))

        apply_to(self.host, TABS)
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

        apply_to(self.host, DEFAULT_LAYOUT)
        self.show()
        history = self.host.panels["history"]
        history._view.setCurrentIndex(history._model.index(2, SUBJECT_COLUMN))  # row 0: Uncommitted

        apply_to(self.host, TabsNode((PanelNode("files"), PanelNode("history")), current=1))
        flush_deletes()
        self.app.processEvents()
        self.assertEqual(history._view.currentIndex().data(), "first")
        self.assertEqual(self.ctx.selection.commit.subject, "first")
        self.assertTrue(history.isVisible())
        self.assertFalse(self.host.panels["files"].isVisible())  # the other tab

        apply_to(self.host, DEFAULT_LAYOUT)  # back from tabs to frames
        flush_deletes()
        self.app.processEvents()
        # Only Diff, History's other tab, and the collapsed Unstaged Files, Staged
        # Files and Commit: a commit is selected, so Commit Files is in their place.
        # Worktrees is collapsed too: it has no worktrees to list (no reader here).
        self.assertEqual({pid for pid, p in self.host.panels.items() if not p.isVisible()},
                         {"diff", "files", "staged", "commit", "worktrees"})

    def test_header_changes_after_moving_out_of_tabs(self):
        apply_to(self.host, TABS)
        apply_to(self.host, DEFAULT_LAYOUT)
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
        apply_to(self.host, DEFAULT_LAYOUT)
        self.show()
        self.host.hide_panel("files")
        flush_deletes()
        self.assertEqual(set(self.host.panels), {"controls", "branches", "worktrees", "history", "diff", "staged", "commit", "commit_details", "commit_files", "stash"})
        self.assertEqual(set(self.host.hidden), {"files"})
        self.assertEqual(self.host.state().hidden, self.host.hidden)

        self.host.show_panel("files")
        self.assertEqual(self.host.hidden, {})
        sidebar = self.sidebar()
        self.assertEqual([sidebar.widget(i).panel.panel_id for i in range(3)],
                         ["branches", "worktrees", "files"])
        self.assertEqual(len(changes), 3)  # apply, hide, show

    def test_the_last_panel_can_not_be_hidden(self):
        apply_to(self.host, PanelNode("history"))
        self.assertFalse(self.host.can_hide("history"))  # Controls doesn't count
        self.host.hide_panel("history")
        self.assertEqual(set(self.host.panels), {"controls", "history"})
        self.host.hide_panel("nope")  # not shown: nothing happens
        self.assertEqual(self.host.hidden, {})

    def test_an_always_shown_panel_can_not_be_hidden_or_left_out(self):
        apply_to(self.host, DEFAULT_LAYOUT)
        self.assertFalse(self.host.can_hide("controls"))
        self.host.hide_panel("controls")
        self.assertIn("controls", self.host.panels)
        apply_to(self.host, DEFAULT_LAYOUT.hide("controls"))
        self.assertIn("controls", self.host.panels)  # put back, at its default place
        self.assertEqual(self.host.hidden, {})

    def test_controls_bar_has_no_header_and_a_fixed_height(self):
        apply_to(self.host, DEFAULT_LAYOUT)
        self.show()
        top = self.root()
        bar = top.widget(0)
        self.assertFalse(bar.content.header.isVisible())
        self.assertEqual(bar.minimumHeight(), bar.maximumHeight())
        self.assertFalse(top.handle(1).isEnabled())  # its divider can't be dragged
        top.setSizes([300, 299])
        self.assertEqual(top.sizes()[0], bar.maximumHeight())
        self.assertIsInstance(bar, AreaPane)  # the Controls area holds the bar alone
        self.assertIs(bar.content.panel, self.host.panels["controls"])
        self.assertTrue(self.sidebar().widget(0).header.isVisible())  # others keep theirs

    def test_a_panel_hidden_without_an_anchor_goes_to_its_default_place(self):
        apply_to(self.host, LayoutState({Area.SIDEBAR: PanelNode("branches"),
                                         Area.MAIN: PanelNode("history")}))
        self.host.show_panel("files")  # never hidden, e.g. new in this version
        self.assertEqual(main_tree(self.host), PanelNode("history"))
        sidebar = self.host.state().tree(Area.SIDEBAR)
        self.assertEqual((sidebar.orientation, panel_ids(sidebar)), (V, ["branches", "files"]))
        self.assertEqual(set(self.host.panels), {"controls", "branches", "files", "history"})

    def test_a_panel_comes_back_to_the_area_it_was_hidden_from(self):
        apply_to(self.host, LayoutState({Area.SIDEBAR: PanelNode("files"), Area.MAIN: SplitNode(
            H, (PanelNode("history"), PanelNode("branches")))}))
        self.host.hide_panel("branches")
        self.assertEqual(self.host.hidden["branches"].area, Area.MAIN)
        self.host.show_panel("branches")
        self.assertEqual(self.host.area_of("branches"), Area.MAIN)

    def test_area_of(self):
        apply_to(self.host, DEFAULT_LAYOUT)
        self.assertEqual(self.host.area_of("history"), Area.MAIN)
        self.assertEqual(self.host.area_of("files"), Area.SIDEBAR)
        self.assertEqual(self.host.area_of("controls"), Area.CONTROLS)
        self.host.hide_panel("history")
        self.assertIsNone(self.host.area_of("history"))

    def test_a_panel_asking_to_be_activated_becomes_the_current_tab(self):
        apply_to(self.host, DEFAULT_LAYOUT)
        tabs = self.body().widget(0)
        self.host.panels["diff"].request_activation()
        self.assertEqual(tabs.currentWidget(), self.host.panels["diff"])
        self.host.panels["history"].request_activation()
        self.assertEqual(tabs.currentWidget(), self.host.panels["history"])

    def test_activation_follows_a_panel_into_new_tabs_and_is_harmless_in_a_split(self):
        apply_to(self.host, DEFAULT_LAYOUT)
        apply_to(self.host, SplitNode(H, (PanelNode("history"), TabsNode(
            (PanelNode("files"), PanelNode("diff"), PanelNode("branches"))))))
        flush_deletes()  # the old tabs, and their connections, are gone
        tabs = self.body().widget(1)
        self.host.panels["diff"].request_activation()
        self.assertEqual(tabs.currentIndex(), 1)
        self.host.panels["history"].request_activation()  # in a split: nothing to do
        self.assertEqual(tabs.currentIndex(), 1)

    def test_apply_keeps_hidden_only_for_panels_not_shown(self):
        anchor = Anchor(("branches",), V, False)
        apply_to(self.host, dataclasses.replace(DEFAULT_LAYOUT, hidden={"files": anchor,
                                                                        "nope": anchor}))
        self.assertEqual(self.host.hidden, {})  # files is shown; nope isn't a panel
        apply_to(self.host, PanelNode("history"), {"files": anchor})
        self.assertEqual(set(self.host.hidden), {"files"})
        apply_to(self.host, PanelNode("history"))  # a preset or reset forgets hidden panels
        self.assertEqual(self.host.hidden, {})

    # ---- collapsed panels (§5.1) ------------------------------------------

    def settle(self):
        """Let the host apply collapse changes (a zero-time timer), then lay out."""
        for _ in range(2):
            self.app.processEvents()

    SIDE = SplitNode(H, (PanelNode("history"), PanelNode("commit")), sizes=(500, 300))

    def test_a_collapsed_pane_lends_its_space_and_takes_it_back(self):
        apply_to(self.host, self.SIDE)
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
        apply_to(self.host, self.SIDE)
        self.show()
        commit = self.host.panels["commit"]
        commit.set_collapsed(False)
        self.settle()
        before = main_tree(self.host)
        commit.set_collapsed(True)
        self.settle()
        read = main_tree(self.host)
        self.assertEqual(read.children, before.children)
        for got, want in zip(read.sizes, before.sizes):
            self.assertAlmostEqual(got, want, delta=1)

    def test_a_panel_collapsed_from_the_start_comes_back_at_its_saved_size(self):
        apply_to(self.host, self.SIDE)  # nothing staged: Commit is collapsed
        self.show()
        splitter = self.body()
        self.assertFalse(splitter.widget(1).isVisible())
        self.assertEqual(main_tree(self.host).sizes[1], 300)
        self.host.panels["commit"].set_collapsed(False)
        self.settle()
        self.assertTrue(splitter.widget(1).isVisible())
        self.assertAlmostEqual(splitter.sizes()[1], 300, delta=3)

    def test_a_collapsing_pane_lends_its_space_to_the_pane_above(self):
        tree = SplitNode(V, (PanelNode("branches"), PanelNode("files"), PanelNode("commit")),
                         sizes=(150, 250, 150))
        apply_to(self.host, SplitNode(H, (tree, PanelNode("history"))))  # Commit starts collapsed
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
        read = main_tree(self.host).children[0]
        self.assertEqual(read.sizes[0], branches)
        self.assertEqual(read.sizes[2], 150)

    def test_a_swap_gives_the_freed_space_to_the_panes_shown_and_back(self):
        column = SplitNode(V, (PanelNode("branches"), PanelNode("files"), PanelNode("staged"),
                               PanelNode("commit_files")), sizes=(150, 250, 150, 300))
        apply_to(self.host, SplitNode(H, (column, PanelNode("history"))))
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
        apply_to(self.host, SplitNode(H, (column, PanelNode("history"))))
        self.show()
        panels = self.host.panels
        panels["commit_files"].set_collapsed(True)
        self.settle()
        before = main_tree(self.host)
        panels["files"].set_collapsed(True)  # a commit is selected
        panels["commit_files"].set_collapsed(False)
        self.settle()
        read = main_tree(self.host)
        self.assertEqual(read, before)  # as if swapped back: Commit Files has no size
        apply_to(self.host, read)  # e.g. after hiding another panel
        self.settle()
        splitter = self.body().widget(0)
        self.assertFalse(splitter.widget(1).isVisible())
        self.assertTrue(splitter.widget(2).isVisible())
        self.assertGreater(splitter.sizes()[2], 300)  # in Unstaged Files' place
        self.assertEqual(main_tree(self.host), before)

    def test_a_change_undone_in_the_same_moment_does_nothing(self):
        apply_to(self.host, self.SIDE)
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
        apply_to(self.host, TabsNode((PanelNode("history"), PanelNode("commit"))))
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

    def test_the_state_reads_sizes_and_current_tab(self):
        tree = SplitNode(H, (PanelNode("branches"), TABS), sizes=(200, 599))
        apply_to(self.host, tree)
        self.show()
        splitter = self.body()
        tabs = splitter.widget(1)
        total = sum(splitter.sizes())
        splitter.setSizes([300, total - 300])
        tabs.setCurrentIndex(0)

        read = main_tree(self.host)
        self.assertEqual(read, SplitNode(
            H, (PanelNode("branches"), TabsNode(TABS.children, current=0)),
            sizes=(300, total - 300)
        ))

        # Applying what was read back restores it.
        apply_to(self.host, read)
        self.app.processEvents()
        self.assertEqual(main_tree(self.host), read)

    def test_the_state_of_the_default_keeps_its_shape(self):
        apply_to(self.host, DEFAULT_LAYOUT)
        self.show()
        read = self.host.state()
        self.assertEqual(matching_preset(read), "Standard")
        self.assertEqual(sum(read.sizes) + 1, 800)  # the 1px divider takes the rest
        # Commit Details is collapsed: its divider is hidden too.
        self.assertEqual(sum(read.tree(Area.MAIN).sizes), self.body().height())

    def test_the_state_reads_the_split_between_the_areas_sidebar_first(self):
        for arrangement in Arrangement:
            with self.subTest(arrangement):
                apply_to(self.host, dataclasses.replace(
                    DEFAULT_LAYOUT, arrangement=arrangement, sizes=(200, 599)))
                self.show()
                areas = self.root().widget(1)
                self.assertEqual(areas.orientation(), Qt.Orientation.Vertical
                                 if arrangement is Arrangement.MAIN_TOP
                                 else Qt.Orientation.Horizontal)
                first = Area.SIDEBAR if arrangement is Arrangement.SIDEBAR_LEFT else Area.MAIN
                self.assertIs(areas.widget(0).area, first)
                total = sum(areas.sizes())
                areas.setSizes([200, total - 200])
                now = areas.sizes()
                self.assertEqual(self.host.state().sizes,
                                 tuple(now if first is Area.SIDEBAR else now[::-1]))

    def test_surfaces_follow_the_area(self):
        """RZG-69: a panel takes its area's surface, in a split or in tabs, and a new
        one when it moves (spec §6.1, §7.2)."""
        apply_to(self.host, DEFAULT_LAYOUT)
        self.show()
        panels = self.host.panels
        surfaces = {pid: panels[pid].property("surface") for pid in ("controls", "files", "diff")}
        self.assertEqual(surfaces, {"controls": "chrome", "files": "sidebar", "diff": "content"})
        self.assertEqual(panels["files"].parentWidget().property("surface"), "sidebar")
        apply_to(self.host, DEFAULT_LAYOUT.move("files", "history", DropZone.CENTER))
        files = panels["files"]
        self.assertEqual(files.property("surface"), "content")
        apply_to(self.host, DEFAULT_LAYOUT.move("history", "branches", DropZone.BOTTOM))
        self.app.processEvents()
        self.assertEqual(panels["history"].property("surface"), "sidebar")
        self.assertEqual(panels["history"].parentWidget().property("surface"), "sidebar")
        # The stylesheet follows: a list inside takes the sidebar's colours.
        view = panels["history"]._view
        self.assertEqual(view.palette().color(view.backgroundRole()).name(),
                         self.ctx.theme.current.colors.sidebar_bg)


class HeaderMenuHostTests(unittest.TestCase):
    """The header menu in a split and in tabs (RZG-48, spec §6.2)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.ctx = make_panel_context()
        self.host = LayoutHost(self.ctx)
        self.host.resize(800, 600)
        self.addCleanup(flush_deletes)
        self.addCleanup(self.host.deleteLater)

    def test_right_clicking_a_tab_shows_its_panel_s_actions(self):
        apply_to(self.host, TABS)
        history = self.host.panels["history"]
        action = QAction("Sort by Date", history)
        history.set_header_actions([action])
        tabs = self.host.findChild(QTabWidget)
        menu = tabs.menu_for(tabs.indexOf(history))
        first, separator, *moves = menu.actions()
        self.assertIs(first, action)
        self.assertTrue(separator.isSeparator())
        self.assertEqual([a.text() for a in moves], ["Move &Left", "Move &Right"])
        # A panel without header actions: only the moves (RZG-79).
        self.host.panels["files"].set_header_actions([])
        menu = tabs.menu_for(tabs.indexOf(self.host.panels["files"]))
        self.assertEqual([a.text() for a in menu.actions()], ["Move &Left", "Move &Right"])
        self.assertIsNone(tabs.menu_for(-1))

    def test_a_press_on_the_button_starts_no_drag(self):
        apply_to(self.host, SplitNode(H, (PanelNode("branches"), PanelNode("history"))))
        history = self.host.panels["history"]
        history.set_header_actions([QAction("One", history)])
        frame = history.parentWidget()
        self.assertIsInstance(frame, PanelFrame)
        self.assertEqual(_drag_source(frame.header, QPoint(1, 1)), "history")
        self.assertIsNone(_drag_source(frame.menu_button, QPoint(1, 1)))


class DragDropTests(unittest.TestCase):
    """Moving panels by drag and drop (spec §6.2)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.ctx = make_panel_context()
        self.host = LayoutHost(self.ctx)
        self.host.resize(800, 600)
        apply_to(self.host, SplitNode(H, (PanelNode("history"), PanelNode("files")), sizes=(400, 400)))
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
        return main_tree(self.host)

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
        # History's old row collapses into Files, so the two stack.
        root = main_tree(self.host)
        self.assertEqual((root.orientation, [c.panel_id for c in root.children]),
                         (V, ["files", "history"]))
        self.assertIs(self.host.panels["history"], history)  # the same panel, state kept

    def test_the_centre_and_the_tab_bar_make_tabs(self):
        self.drop("history", self.at("files", 0.5, 0.5))
        self.assertEqual(self.body(), TabsNode((PanelNode("files"), PanelNode("history")), 1))
        apply_to(self.host, SplitNode(H, (TABS, PanelNode("branches"))))
        self.app.processEvents()
        tab_bar = self.pane("history").tabBar()
        self.drop("branches", tab_bar.mapTo(self.host, tab_bar.rect().center()))
        self.assertEqual(self.body(), TabsNode(
            (PanelNode("history"), PanelNode("files"), PanelNode("branches")), 2))

    def test_controls_is_not_a_target_and_can_not_be_dropped(self):
        before = main_tree(self.host)
        controls = self.host.panels["controls"].parentWidget()
        pos = controls.mapTo(self.host, controls.rect().center())

        def during():
            self.assertFalse(self.drag_to("history", pos).isAccepted())
            self.assertFalse(self.host._indicator.isVisible())
            self.send(QDropEvent, "history", pos)

        self.drag("history", during)
        self.assertEqual(main_tree(self.host), before)
        self.assertIsNone(self.host.drop_target("controls", self.at("files", 0.5, 0.9)))
        with mock.patch.object(self.host, "drag_panel") as drag:
            header = self.pane("controls").header
            self.press_and_move(header, QPoint(5, 5))
            drag.assert_not_called()

    def area_pane(self, area):
        root = self.host.layout().itemAt(0).widget()
        panes = [root.widget(0), root.widget(1).widget(0), root.widget(1).widget(1)]
        return next(p for p in panes if p.area is area)

    def test_an_empty_area_is_a_target_as_a_whole_and_the_panel_fills_it(self):
        """RZG-69: the Sidebar is empty here (everything is in the Main pane)."""
        pane = self.area_pane(Area.SIDEBAR)
        self.assertIsNone(pane.content)
        pos = pane.mapTo(self.host, QPoint(pane.width() // 2, pane.height() // 4))

        def during():
            self.assertTrue(self.drag_to("files", pos).isAccepted())
            self.assertEqual(self.host._indicator.geometry(),
                             QRect(pane.mapTo(self.host, QPoint(0, 0)), pane.size()))
            self.send(QDropEvent, "files", pos)

        self.drag("files", during)
        self.assertEqual(self.host.state().tree(Area.SIDEBAR), PanelNode("files"))
        self.assertEqual(self.body(), PanelNode("history"))
        self.assertEqual(self.host.panels["files"].property("surface"), "sidebar")

    def test_the_last_panel_can_leave_the_main_pane_which_stays_empty_and_a_target(self):
        apply_to(self.host, LayoutState({Area.SIDEBAR: PanelNode("files"),
                                         Area.MAIN: PanelNode("history")}))
        self.app.processEvents()
        self.drop("history", self.at("files", 0.5, 0.9))  # into the Sidebar, below Files
        self.assertIsNone(self.body())
        self.assertEqual(panel_ids(self.host.state().tree(Area.SIDEBAR)), ["files", "history"])
        self.app.processEvents()
        pane = self.area_pane(Area.MAIN)
        self.drop("history", pane.mapTo(self.host, pane.rect().center()))  # and back
        self.assertEqual(self.body(), PanelNode("history"))

    def test_an_edge_drop_stays_in_the_target_s_area(self):
        apply_to(self.host, LayoutState({Area.SIDEBAR: PanelNode("files"),
                                         Area.MAIN: SplitNode(V, (PanelNode("history"),
                                                                  PanelNode("diff")))}))
        self.app.processEvents()
        self.drop("files", self.at("history", 0.05, 0.5))  # the left edge, by the Sidebar
        self.assertEqual(self.host.area_of("files"), Area.MAIN)
        self.assertIsNone(self.host.state().tree(Area.SIDEBAR))

    def test_the_controls_area_is_not_a_target(self):
        pane = self.area_pane(Area.CONTROLS)
        pos = pane.mapTo(self.host, pane.rect().center())
        self.assertIsNone(self.host.drop_target("history", pos))

    def test_its_own_pane_is_not_a_target(self):
        def during():
            self.assertFalse(self.drag_to("history", self.at("history", 0.9, 0.5)).isAccepted())
            self.assertFalse(self.host._indicator.isVisible())
        self.drag("history", during)

    def test_drag_events_are_left_alone_when_no_panel_drag_runs(self):
        before = main_tree(self.host)
        self.assertFalse(self.drag_to("history", self.at("files", 0.9, 0.5)).isAccepted())
        self.send(QDropEvent, "history", self.at("files", 0.9, 0.5))
        self.assertEqual(main_tree(self.host), before)

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

    def press_and_move(self, widget, start, by=QPoint(40, 0)):
        for kind, pos in ((QEvent.Type.MouseButtonPress, start),
                          (QEvent.Type.MouseMove, start + by)):
            QApplication.sendEvent(widget, QMouseEvent(
                kind, QPointF(pos), QPointF(widget.mapToGlobal(pos)), Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    def test_a_drag_starts_from_a_header_or_a_tab(self):
        with mock.patch.object(self.host, "drag_panel") as drag:
            header = self.pane("files").header
            self.press_and_move(header, header.rect().center())
            drag.assert_called_once_with("files")
        apply_to(self.host, SplitNode(H, (TABS, PanelNode("branches"))))
        self.app.processEvents()
        tab_bar = self.pane("history").tabBar()
        with mock.patch.object(self.host, "drag_panel") as drag:
            self.press_and_move(tab_bar, tab_bar.tabRect(0).center(), QPoint(0, 80))
            drag.assert_called_once_with("history")

    def test_a_cancelled_drag_leaves_the_layout_as_it_was(self):
        before = main_tree(self.host)
        changed = mock.Mock()
        self.host.layout_changed.connect(changed)

        def cancel():  # Escape: Qt sends a leave, and no drop
            self.drag_to("history", self.at("files", 0.9, 0.5))
            QApplication.sendEvent(self.host, QDragLeaveEvent())

        self.drag("history", cancel, Qt.DropAction.IgnoreAction)
        changed.assert_not_called()
        self.assertEqual(main_tree(self.host), before)
        self.assertFalse(self.host._indicator.isVisible())

class TabOrderTests(unittest.TestCase):
    """Reordering the tabs of a tab group (RZG-79, spec §6.2)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.ctx = make_panel_context()
        self.host = LayoutHost(self.ctx)
        self.host.resize(800, 600)
        apply_to(self.host, TabsNode((PanelNode("history"), PanelNode("files"),
                                      PanelNode("branches"))))
        self.host.show()
        self.app.processEvents()
        self.tabs = area_content(self.host, Area.MAIN)
        self.bar = self.tabs.tabBar()
        self.changed = mock.Mock()
        self.host.layout_changed.connect(self.changed)

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        flush_deletes()

    def order(self):
        tree = main_tree(self.host)
        return [c.panel_id for c in tree.children], tree.children[tree.current].panel_id

    def mouse(self, kind, pos, buttons=Qt.MouseButton.LeftButton):
        QApplication.sendEvent(self.bar, QMouseEvent(
            kind, QPointF(pos), QPointF(self.bar.mapToGlobal(pos)), Qt.MouseButton.LeftButton,
            buttons, Qt.KeyboardModifier.NoModifier))

    def drag_along(self, index, to_x):
        """Press on tab ``index`` and move along the bar to ``to_x``, in steps."""
        start = self.bar.tabRect(index).center()
        self.mouse(QEvent.Type.MouseButtonPress, start)
        for x in range(start.x(), to_x, 4):
            self.mouse(QEvent.Type.MouseMove, QPoint(x, start.y()))
        return QPoint(to_x, start.y())

    def menu_action(self, panel_id, text):
        menu = self.tabs.menu_for(self.tabs.indexOf(self.host.panels[panel_id]))
        [action] = [a for a in menu.actions() if a.text() == text]
        return action

    def test_dragging_a_tab_along_its_bar_reorders_and_the_state_keeps_it(self):
        with mock.patch.object(self.host, "drag_panel") as drag:
            end = self.drag_along(0, self.bar.tabRect(1).right() + 4)
            self.mouse(QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.NoButton)
        drag.assert_not_called()
        self.assertEqual(self.order(), (["files", "history", "branches"], "history"))
        self.changed.assert_called()
        self.assertIsNone(matching_preset(self.host.state()))  # not one of the presets anyway
        # The order is applied again as saved.
        self.host.apply(self.host.state())
        self.assertEqual(self.order(), (["files", "history", "branches"], "history"))

    def test_leaving_the_bar_puts_the_tab_back_and_starts_the_panel_drag(self):
        seen = []
        with mock.patch.object(self.host, "drag_panel",
                               side_effect=lambda pid: seen.append((pid, self.order()))) as drag:
            end = self.drag_along(0, self.bar.tabRect(1).right() + 4)
            self.assertEqual(self.order()[0], ["files", "history", "branches"])  # live
            self.mouse(QEvent.Type.MouseMove, end + QPoint(0, 4))  # still near the bar
            drag.assert_not_called()
            self.mouse(QEvent.Type.MouseMove, end + QPoint(0, 120))
        self.assertEqual(seen, [("history", (["history", "files", "branches"], "history"))])
        # Qt's own move ended: further moves don't move the tab.
        self.mouse(QEvent.Type.MouseMove, QPoint(self.bar.tabRect(2).center()))
        self.assertEqual(self.order()[0], ["history", "files", "branches"])

    def test_move_left_and_right_from_the_menu_keep_the_current_panel(self):
        self.tabs.setCurrentIndex(0)  # history
        self.assertFalse(self.menu_action("history", "Move &Left").isEnabled())
        self.assertFalse(self.menu_action("branches", "Move &Right").isEnabled())
        self.menu_action("files", "Move &Left").trigger()
        self.assertEqual(self.order(), (["files", "history", "branches"], "history"))
        self.changed.assert_called()
        self.menu_action("files", "Move &Right").trigger()
        self.menu_action("files", "Move &Right").trigger()
        self.assertEqual(self.order(), (["history", "branches", "files"], "history"))

    def test_a_move_skips_collapsed_tabs(self):
        apply_to(self.host, TabsNode((PanelNode("history"), PanelNode("commit"),
                                      PanelNode("files"))))
        self.tabs = area_content(self.host, Area.MAIN)
        self.assertFalse(self.tabs.isTabVisible(1))  # Commit, collapsed
        self.assertTrue(self.menu_action("history", "Move &Right").isEnabled())  # past Commit
        self.menu_action("files", "Move &Left").trigger()
        self.assertEqual(self.order()[0], ["files", "history", "commit"])
        self.assertFalse(self.menu_action("history", "Move &Right").isEnabled())  # only Commit

    def test_the_shortcut_moves_the_current_tab_of_the_focused_group(self):
        self.host.activateWindow()
        QTest.qWaitForWindowActive(self.host)
        self.tabs.setCurrentIndex(1)  # files
        self.host.panels["files"].setFocus()
        self.app.processEvents()
        shift_ctrl = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        focused = QApplication.focusWidget()
        self.assertTrue(self.tabs.isAncestorOf(focused))
        QTest.keyClick(focused, Qt.Key.Key_PageUp, shift_ctrl)
        self.assertEqual(self.order(), (["files", "history", "branches"], "files"))
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_PageDown, shift_ctrl)
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_PageDown, shift_ctrl)
        self.assertEqual(self.order(), (["history", "branches", "files"], "files"))

    def test_a_hidden_tab_comes_back_to_its_place_in_the_new_order(self):
        self.menu_action("branches", "Move &Left").trigger()  # history, branches, files
        self.host.hide_panel("branches")
        self.assertEqual(self.order()[0], ["history", "files"])
        self.host.show_panel("branches")
        self.assertEqual(self.order()[0], ["history", "branches", "files"])


if __name__ == "__main__":
    unittest.main()
