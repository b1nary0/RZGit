import dataclasses
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QHoverEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

from rzgitclient.core.commit_graph import CommitGraph
from rzgitclient.ui.panels.graph_painter import PADDING_X
from rzgitclient.ui.panels.history_model import (AUTHOR_COLUMN, DATE_COLUMN, GRAPH_COLUMN,
                                                 HASH_COLUMN, MUTED_COLUMNS, SUBJECT_COLUMN)
from rzgitclient.ui.panels.history_panel import HistoryPanel
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from rzgitclient.ui.widgets.column_header import ColumnHeader
from tests.support import (SignalRecorder, commit_file, drag_column_edge, make_branched_repo,
                           make_panel_context, make_repo, qt_app, wait_idle)

ROLE = Qt.ItemDataRole
UNCOMMITTED = "Uncommitted changes (0)"  # row 0 while a repo is open; commits start at 1


class HistoryPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo = make_repo(self.tmp / "repo")
        commit_file(self.repo, "a.txt", "1", "first")
        commit_file(self.repo, "a.txt", "2", "second")
        self.ctx = make_panel_context()
        self.addCleanup(self.ctx.session.shutdown)
        self.panel = HistoryPanel(self.ctx)
        self.view, self.model = self.panel._view, self.panel._model
        self.rec = SignalRecorder(self.ctx.selection, "commit_changed")

    def open(self, path=None):
        self.ctx.session.open(path or self.repo)
        wait_idle(self.ctx.session)

    def refresh(self):
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)

    def subjects(self):
        return [self.cell(row, SUBJECT_COLUMN) for row in range(self.model.rowCount())]

    def cell(self, row, col, role=ROLE.DisplayRole):
        return self.model.data(self.model.index(row, col), role)

    def select(self, row):
        self.view.setCurrentIndex(self.model.index(row, 0))

    def current_subject(self):
        return self.cell(self.view.currentIndex().row(), SUBJECT_COLUMN)

    def test_empty_state(self):
        self.assertEqual(self.subjects(), [])
        self.assertIsNone(self.ctx.selection.commit)

    def test_rows_hash_and_muted_columns(self):
        self.open()
        self.assertEqual(self.subjects(), [UNCOMMITTED, "second", "first"])
        commit = self.ctx.session.history[0]
        self.assertEqual(self.model.headerData(SUBJECT_COLUMN, Qt.Orientation.Horizontal), "Subject")
        self.assertEqual(self.cell(1, GRAPH_COLUMN), "")  # drawn, not written
        self.assertEqual(self.cell(1, AUTHOR_COLUMN), "Test User")
        self.assertEqual(self.cell(1, HASH_COLUMN), commit.short_sha)
        self.assertEqual(self.cell(1, HASH_COLUMN, ROLE.ToolTipRole), commit.sha)
        self.assertEqual(self.cell(1, HASH_COLUMN, ROLE.FontRole).family(),
                         LIGHT_THEME.mono_font().family())
        self.assertIsNone(self.cell(1, SUBJECT_COLUMN, ROLE.ForegroundRole))  # normal text
        for col in MUTED_COLUMNS:
            self.assertEqual(self.cell(1, col, ROLE.ForegroundRole).name(), LIGHT_THEME.colors.text_muted)

    def test_columns_are_labelled_by_a_column_header(self):
        """RZG-41: History's labels get the shared look."""
        header = self.panel._view.horizontalHeader()
        self.assertIsInstance(header, ColumnHeader)
        self.assertEqual([header.label(c) for c in range(5)],
                         ["Graph", "Subject", "Author", "Date", "Commit"])
        self.assertEqual(header.main_column, SUBJECT_COLUMN)  # the graph comes before it

    def test_a_hidden_column_stays_hidden_through_a_reload(self):
        self.open()
        self.panel._view.horizontalHeader().set_column_shown("Author", False)
        commit_file(self.repo, "b.txt", "2", "third")
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        self.assertEqual(self.subjects()[1], "third")
        self.assertTrue(self.panel._view.isColumnHidden(AUTHOR_COLUMN))

    def show(self):
        self.panel.resize(900, 300)
        self.panel.show()
        QTest.qWaitForWindowExposed(self.panel)

    def test_a_dragged_column_keeps_its_width_through_reloads_and_repositories(self):
        """RZG-40: fitted until the user sizes it; then the user's width stays."""
        self.open()
        self.show()
        header = self.view.horizontalHeader()
        width, date = header.sectionSize(AUTHOR_COLUMN) + 40, header.sectionSize(DATE_COLUMN) - 40
        drag_column_edge(header, AUTHOR_COLUMN, 40)  # Author | Date: only those two move
        self.assertEqual(self.ctx.columns.widths("history"), {"Author": width, "Date": date})
        commit_file(self.repo, "b.txt", "3", "third")
        self.refresh()
        self.assertEqual(self.subjects()[1], "third")
        self.assertEqual(header.sectionSize(AUTHOR_COLUMN), width)
        other = make_repo(self.tmp / "other")
        commit_file(other, "x.txt", "1", "elsewhere")
        self.open(other)
        self.assertEqual(self.subjects()[1], "elsewhere")
        self.assertEqual((header.sectionSize(AUTHOR_COLUMN), header.sectionSize(DATE_COLUMN)),
                         (width, date))
        self.assertEqual(sum(header.sectionSize(c) for c in range(5)), header.width())

    def test_a_new_panel_gives_the_saved_widths_back(self):
        self.ctx.columns.set_width("history", "Date", 222)
        panel = HistoryPanel(self.ctx)
        self.addCleanup(panel.deleteLater)
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.assertEqual(panel._view.horizontalHeader().sectionSize(DATE_COLUMN), 222)

    def test_uncommitted_changes_is_row_0_selected_by_default(self):
        self.open()
        self.assertEqual(self.view.currentIndex().row(), 0)
        self.assertIsNone(self.ctx.selection.commit)
        self.assertTrue(self.cell(0, SUBJECT_COLUMN, ROLE.FontRole).italic())
        self.assertEqual([self.cell(0, c) for c in (GRAPH_COLUMN, *MUTED_COLUMNS)], [""] * 4)
        self.assertIsNone(self.cell(0, HASH_COLUMN, ROLE.ToolTipRole))
        self.assertIsNone(self.model.commit_at(0))
        self.assertEqual(self.model.row_of(self.ctx.session.history[0].sha), 1)

    def test_columns_fit_the_commits_though_uncommitted_changes_came_first(self):
        self.open()  # the row arrives with the repo, before the first page
        header = self.view.horizontalHeader()
        for col in MUTED_COLUMNS:
            self.assertGreaterEqual(header.sectionSize(col),
                                    self.view.width_hint(self.model.index(1, col)))

    def test_uncommitted_changes_counts_the_changed_files(self):
        self.open()
        (self.repo / "a.txt").write_text("changed")
        (self.repo / "new.txt").write_text("new")
        self.refresh()
        self.assertEqual(self.cell(0, SUBJECT_COLUMN), "Uncommitted changes (2)")
        self.ctx.session.close()
        self.assertEqual(self.subjects(), [])  # no repo, no row

    def test_selecting_uncommitted_changes_clears_the_commit(self):
        self.open()
        self.select(2)
        self.assertEqual(self.ctx.selection.commit.subject, "first")
        self.select(0)
        self.assertIsNone(self.ctx.selection.commit)

    def test_selecting_a_row_sets_selection(self):
        self.open()
        self.select(2)
        self.assertEqual(self.ctx.selection.commit.subject, "first")

    def test_follows_a_commit_selected_elsewhere(self):
        self.open()
        first = self.ctx.session.history[-1]
        self.ctx.selection.commit = first  # as a parent link in Commit Details does
        self.assertEqual(self.current_subject(), "first")
        self.ctx.selection.commit = None
        self.assertEqual(self.view.currentIndex().row(), 0)  # Uncommitted changes
        self.assertEqual(self.rec.values("commit_changed"), [first, None])  # each change once

    def test_a_commit_that_isnt_loaded_leaves_it_as_it_is(self):
        self.open()
        self.select(1)
        gone = dataclasses.replace(self.ctx.session.history[0], sha="0" * 40)
        self.ctx.selection.commit = gone
        self.assertEqual(self.current_subject(), "second")

    def test_selection_survives_refresh_silently(self):
        self.open()
        self.select(2)
        self.rec.clear()
        commit_file(self.repo, "a.txt", "3", "third")
        self.refresh()
        self.assertEqual(self.subjects(), [UNCOMMITTED, "third", "second", "first"])
        self.assertEqual(self.current_subject(), "first")
        self.assertEqual(self.rec.events, [])  # no flicker through None

    def test_another_repo_starts_on_uncommitted_changes(self):
        self.open()
        self.select(1)
        other = make_repo(self.tmp / "other")
        commit_file(other, "b.txt", "1", "other")
        self.open(other)
        self.assertEqual(self.view.currentIndex().row(), 0)
        self.assertIsNone(self.ctx.selection.commit)

    def test_an_amended_commit_goes_back_to_uncommitted_changes(self):
        self.open()
        self.select(1)  # "second"
        subprocess.run(["git", "commit", "-q", "--amend", "-m", "second, amended"],
                       cwd=self.repo, check=True, capture_output=True)
        self.refresh()
        self.assertEqual(self.subjects(), [UNCOMMITTED, "second, amended", "first"])
        self.assertEqual(self.view.currentIndex().row(), 0)
        self.assertIsNone(self.ctx.selection.commit)

    def test_hover_marks_the_whole_row(self):
        self.open()
        self.panel.resize(600, 300)
        self.panel.show()
        self.addCleanup(self.panel.hide)
        self.app.processEvents()
        y = self.view.rowViewportPosition(1) + 2
        event = QHoverEvent(QEvent.Type.HoverMove, QPointF(5, y), QPointF(5, y), QPointF(-1, -1))
        self.view.viewportEvent(event)
        delegate = self.view.itemDelegate()

        def hovered(row, col):
            option = QStyleOptionViewItem()
            delegate.initStyleOption(option, self.model.index(row, col))
            return bool(option.state & QStyle.StateFlag.State_MouseOver)

        self.assertEqual([hovered(1, c) for c in range(5)], [True] * 5)  # not just the cell
        self.assertEqual([hovered(0, c) for c in range(5)], [False] * 5)
        self.view.viewportEvent(QHoverEvent(QEvent.Type.HoverLeave, QPointF(-1, -1), QPointF(-1, -1), QPointF(5, y)))
        self.assertFalse(hovered(1, 0))

    def test_apply_theme_recolours_rows(self):
        self.open()
        changed = []
        self.model.dataChanged.connect(lambda *args: changed.append(args))
        colors = dataclasses.replace(LIGHT_THEME.colors, text_muted="#123456")
        self.ctx.theme.set_theme(dataclasses.replace(LIGHT_THEME, colors=colors))
        self.assertEqual(self.cell(1, AUTHOR_COLUMN, ROLE.ForegroundRole).name(), "#123456")
        self.assertEqual(len(changed), 1)  # one signal repaints every row


class HistoryPanelPagingTests(unittest.TestCase):
    """History in pages of three commits, shown in a view a few rows tall."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        for n in range(1, 11):
            commit_file(self.repo, "a.txt", str(n), f"c{n}")
        page = mock.patch("rzgitclient.core.repo_session.HISTORY_PAGE", 3)
        page.start()
        self.addCleanup(page.stop)
        self.ctx = make_panel_context()
        self.addCleanup(self.ctx.session.shutdown)
        self.panel = HistoryPanel(self.ctx)
        self.addCleanup(self.panel.deleteLater)
        self.view, self.model = self.panel._view, self.panel._model
        self.panel.resize(400, 66)  # two rows: a page of three overflows it
        self.panel.show()
        self.ctx.session.open(self.repo)
        self.settle()

    def settle(self):
        """Wait for git, and for the view to lay out (and maybe fetch) what arrived."""
        for _ in range(3):
            wait_idle(self.ctx.session)
            self.app.processEvents()

    def scroll_to_end(self):
        bar = self.view.verticalScrollBar()
        self.assertGreater(bar.maximum(), bar.value())  # laid out, and not at the end yet
        bar.setValue(bar.maximum())
        self.settle()

    def test_first_page_then_more_on_scroll(self):
        self.assertEqual(self.model.rowCount(), 4)  # Uncommitted changes, a page
        inserted = []
        self.model.rowsInserted.connect(lambda _p, first, last: inserted.append((first, last)))
        resets = []
        self.model.modelReset.connect(lambda: resets.append(1))
        self.scroll_to_end()
        self.assertEqual(self.model.rowCount(), 7)
        self.assertEqual(inserted, [(4, 6)])  # the page is appended…
        self.assertEqual(resets, [])  # …and nothing is rebuilt

    def test_scrolling_stops_at_the_first_commit(self):
        while self.model.rowCount() < 11:
            self.scroll_to_end()
        self.assertTrue(self.ctx.session.history_complete)
        bar = self.view.verticalScrollBar()
        bar.setValue(0)
        bar.setValue(bar.maximum())  # at the end again: nothing more to ask for
        self.assertFalse(self.ctx.session.busy)
        self.assertEqual(self.model.rowCount(), 11)

    def test_a_commit_selected_elsewhere_is_scrolled_to(self):
        while self.model.rowCount() < 11:
            self.scroll_to_end()
        self.view.verticalScrollBar().setValue(0)
        self.app.processEvents()
        self.ctx.selection.commit = self.ctx.session.history[-1]  # c1, far below
        self.app.processEvents()
        row = self.view.currentIndex().row()
        self.assertEqual(self.model.commit_at(row).subject, "c1")
        self.assertTrue(self.view.viewport().rect().intersects(
            self.view.visualRect(self.model.index(row, 0))))

    def test_refresh_keeps_the_top_commit_in_place(self):
        self.scroll_to_end()
        self.scroll_to_end()
        self.view.setCurrentIndex(self.model.index(6, 0))
        selected = self.ctx.selection.commit
        self.view.scrollTo(self.model.index(5, 0), self.view.ScrollHint.PositionAtTop)
        top = self.model.commit_at(self.view.top_row())  # the first below the pinned row
        self.assertEqual(top.subject, "c6")

        commit_file(self.repo, "a.txt", "11", "c11")
        self.ctx.session.refresh()
        self.settle()
        self.assertEqual(self.model.commits[0].subject, "c11")
        self.assertEqual(self.model.commit_at(self.view.top_row()), top)
        self.assertEqual(self.ctx.selection.commit, selected)

    def test_refresh_at_the_top_shows_new_commits(self):
        commit_file(self.repo, "a.txt", "11", "c11")
        self.ctx.session.refresh()
        self.settle()
        self.assertEqual(self.view.verticalScrollBar().value(), 0)
        self.assertEqual(self.view.indexAt(QPoint(0, 0)).row(), 0)


if __name__ == "__main__":
    unittest.main()


class HistoryStripesAndPinnedRowTests(unittest.TestCase):
    """Striped commits, and Uncommitted changes pinned at the top (RZG-63, spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        for i in range(1, 13):
            commit_file(self.repo, "a.txt", str(i), f"c{i}")
        (self.repo / "a.txt").write_text("changed")
        self.ctx = make_panel_context()
        self.addCleanup(self.ctx.session.shutdown)
        self.panel = HistoryPanel(self.ctx)
        self.addCleanup(self.panel.deleteLater)
        self.view, self.model = self.panel._view, self.panel._model
        self.pinned = self.view.pinned
        self.panel.resize(500, 140)  # a few rows: the twelve commits overflow it
        self.panel.show()
        self.ctx.session.open(self.repo)
        for _ in range(3):
            wait_idle(self.ctx.session)
            self.app.processEvents()
        self.bar = self.view.verticalScrollBar()
        self.colors = self.ctx.theme.current.colors

    def scroll(self, value):
        self.bar.setValue(value)
        self.app.processEvents()

    def pixel(self, widget, y):
        """The colour at the right of the subject column, where no text is."""
        x = self.view.columnViewportPosition(SUBJECT_COLUMN) + self.view.columnWidth(SUBJECT_COLUMN) - 3
        return widget.grab().toImage().pixelColor(x, y).name()

    def test_every_second_commit_is_striped_from_the_first_one(self):
        background = Qt.ItemDataRole.BackgroundRole
        brushes = [self.model.data(self.model.index(r, 1), background) for r in range(5)]
        self.assertIsNone(brushes[0])  # Uncommitted changes: never
        self.assertIsNone(brushes[1])  # the first commit
        self.assertEqual(brushes[2].color().name(), self.colors.row_alt_bg)
        self.assertIsNone(brushes[3])
        self.assertEqual(brushes[4].color().name(), self.colors.row_alt_bg)
        self.assertEqual([self.model.striped(r) for r in range(5)],
                         [False, False, True, False, True])
        self.view.clearSelection()
        self.view.setCurrentIndex(self.model.index(1, 0))  # nothing selected on row 2 or 3
        self.app.processEvents()
        row = self.view.rowHeight(0)
        self.assertEqual(self.pixel(self.view.viewport(), int(row * 2.5)), self.colors.row_alt_bg)
        self.assertEqual(self.pixel(self.view.viewport(), int(row * 3.5)), self.colors.content_bg)

    def test_at_the_top_uncommitted_changes_is_just_the_first_row(self):
        self.assertEqual(self.bar.value(), 0)
        self.assertFalse(self.pinned.isVisible())
        self.assertEqual(self.view.visualRect(self.model.index(0, 0)).top(), 0)

    def test_scrolled_it_stays_pinned_over_the_rows(self):
        self.scroll(5)
        self.assertTrue(self.pinned.isVisible())
        self.assertEqual(self.pinned.geometry().top(), 0)
        self.assertEqual(self.pinned.height(), self.view.rowHeight(0))
        self.assertEqual(self.view.top_row(), 6)  # row 5 passes under it
        # It's drawn like the row: selected (the default), with a line under it.
        self.assertEqual(self.pixel(self.pinned, self.pinned.height() // 2),
                         self.colors.content_selection)
        self.assertEqual(self.pixel(self.pinned, self.pinned.height() - 1),
                         self.colors.border_light)
        self.scroll(0)
        self.assertFalse(self.pinned.isVisible())

    def test_clicking_it_selects_uncommitted_changes_without_scrolling(self):
        self.scroll(5)
        self.view.setCurrentIndex(self.model.index(7, 0))
        self.assertIsNotNone(self.ctx.selection.commit)
        QTest.mouseClick(self.pinned, Qt.MouseButton.LeftButton, pos=QPoint(10, 5))
        self.assertIsNone(self.ctx.selection.commit)
        self.assertEqual(self.view.currentIndex().row(), 0)
        self.assertEqual(self.bar.value(), 5)

    def test_hovering_it_hovers_row_zero(self):
        self.scroll(5)
        at = QPointF(10, 5)
        QCoreApplication.sendEvent(self.pinned, QEnterEvent(at, at, self.pinned.mapToGlobal(at)))
        self.assertEqual(self.view.hover_row, 0)
        QCoreApplication.sendEvent(self.pinned, QEvent(QEvent.Type.Leave))
        self.assertEqual(self.view.hover_row, -1)
        # The viewport sees the pointer there too: it's the pinned row, not the row beneath.
        y = self.pinned.height() // 2
        self.view.viewportEvent(QHoverEvent(QEvent.Type.HoverMove, QPointF(5, y), QPointF(5, y),
                                            QPointF(-1, -1)))
        self.assertEqual(self.view.hover_row, 0)
        y = self.pinned.height() + self.view.rowHeight(1) // 2
        self.view.viewportEvent(QHoverEvent(QEvent.Type.HoverMove, QPointF(5, y), QPointF(5, y),
                                            QPointF(-1, -1)))
        self.assertEqual(self.view.hover_row, 6)

    def test_the_keyboard_goes_through_it(self):
        self.scroll(5)
        self.view.setFocus()
        self.view.setCurrentIndex(self.model.index(6, 0))  # the first row below it
        QTest.keyClick(self.view, Qt.Key.Key_Up)  # row 5, which was under the pinned row
        self.assertEqual(self.view.currentIndex().row(), 5)
        self.assertEqual(self.view.top_row(), 5)  # scrolled out from under it
        self.view.setCurrentIndex(self.model.index(1, 0))
        QTest.keyClick(self.view, Qt.Key.Key_Up)  # the first commit: up to Uncommitted changes
        self.assertEqual(self.view.currentIndex().row(), 0)
        self.scroll(5)
        QTest.keyClick(self.view, Qt.Key.Key_Down)  # from the pinned row to the first commit
        self.assertEqual(self.view.currentIndex().row(), 1)
        self.assertEqual(self.bar.value(), 0)
        self.scroll(5)
        self.view.setCurrentIndex(self.model.index(8, 0))
        QTest.keyClick(self.view, Qt.Key.Key_Home, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.view.currentIndex().row(), 0)
        self.assertEqual(self.bar.value(), 0)  # Ctrl+Home goes to the top

    def test_selecting_uncommitted_changes_elsewhere_doesnt_scroll(self):
        self.scroll(5)
        self.view.setCurrentIndex(self.model.index(7, 0))
        self.ctx.selection.commit = None
        self.app.processEvents()
        self.assertEqual(self.view.currentIndex().row(), 0)
        self.assertEqual(self.bar.value(), 5)
        self.assertTrue(self.pinned.isVisible())


class HistoryGraphTests(unittest.TestCase):
    """The branch graph column (RZG-58, spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = make_branched_repo(Path(self._tmp.name) / "repo")
        self.ctx = make_panel_context()
        self.addCleanup(self.ctx.session.shutdown)
        self.panel = HistoryPanel(self.ctx)
        self.addCleanup(self.panel.deleteLater)
        self.view, self.model = self.panel._view, self.panel._model
        self.header = self.view.horizontalHeader()
        self.panel.resize(600, 300)
        self.panel.show()
        QTest.qWaitForWindowExposed(self.panel)
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.app.processEvents()
        self.theme = self.ctx.theme.current

    def dot(self, row):
        """The pixel at the middle of ``row``'s dot."""
        rect = self.view.visualRect(self.model.index(row, GRAPH_COLUMN))
        lane = self.model.graph_row(row).lane if self.model.graph_row(row) else 0
        x = int(rect.left() + PADDING_X + (lane + 0.5) * self.theme.metrics.graph_lane_width)
        return self.view.viewport().grab().toImage().pixelColor(x, rect.center().y())

    def test_the_graph_is_the_first_column_and_its_rows_follow_the_commits(self):
        self.assertEqual(self.header.label(GRAPH_COLUMN), "Graph")
        self.assertIsNone(self.model.graph_row(0))  # Uncommitted changes: drawn from HEAD's
        merge = self.model.graph_row(1)
        self.assertEqual(merge.lane, 0)
        self.assertEqual(len(merge.lower), 2)  # to both parents
        self.assertEqual([self.model.graph_row(r).lane for r in range(1, 6)], [0, 1, 0, 1, 0])

    def test_the_column_fits_the_widest_row_in_view(self):
        self.assertEqual(self.header.sectionSize(GRAPH_COLUMN),
                         max(self.panel._graph.width(2), self.header.minimumSectionSize()))

    def test_a_user_sized_graph_column_keeps_its_width(self):
        self.ctx.columns.set_width("history", "Graph", 90)
        self.header.apply_prefs()
        self.panel._fit_graph()
        self.assertEqual(self.header.sectionSize(GRAPH_COLUMN), 90)

    def test_each_commit_has_a_dot_in_its_lanes_colour(self):
        for row in (1, 2, 3):
            graph = self.model.graph_row(row)
            lanes = self.theme.graph.lanes
            self.assertEqual(self.dot(row).name(), lanes[graph.color % len(lanes)], row)

    def test_uncommitted_changes_has_a_hollow_dot(self):
        head = self.model.graph_row(1)
        lanes = self.theme.graph.lanes
        self.assertNotEqual(self.dot(0).name(), lanes[head.color % len(lanes)])

    def test_hover_and_selection_reach_the_graph_cell(self):
        colors = self.theme.colors
        rect = self.view.visualRect(self.model.index(3, GRAPH_COLUMN))

        def background():  # the cell's right edge: no lane there
            image = self.view.viewport().grab().toImage()
            return image.pixelColor(rect.right() - 1, rect.top() + 2).name()

        self.assertEqual(background(), colors.content_bg)
        self.view._set_hover_row(3)
        self.assertEqual(background(), colors.content_hover)
        self.view._set_hover_row(-1)
        self.view.setCurrentIndex(self.model.index(3, SUBJECT_COLUMN))
        self.assertEqual(background(), colors.content_selection)

    def test_a_reset_builds_the_graph_again(self):
        commit_file(self.repo, "n.txt", "1", "on top")  # HEAD moves: the model resets
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        history = self.ctx.session.history
        self.assertEqual(history[0].subject, "on top")
        whole = CommitGraph(history)
        self.assertEqual([self.model.graph_row(r + 1) for r in range(len(history))],
                         [whole.row(r) for r in range(len(history))])

    def test_the_header_menu_has_topological_order(self):
        self.assertIn(self.ctx.actions.topo_order, self.panel.header_actions)

    def test_topological_order_reloads_the_graph(self):
        self.ctx.actions.topo_order.setChecked(True)
        wait_idle(self.ctx.session)
        self.app.processEvents()
        subjects = [self.model.text(r, SUBJECT_COLUMN) for r in range(1, 6)]
        self.assertEqual(abs(subjects.index("s1") - subjects.index("s2")), 1)
        self.assertEqual(self.model.graph_row(1).lane, 0)


class HistoryGraphPagingTests(unittest.TestCase):
    """The graph carries on across pages (RZG-58)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = make_branched_repo(Path(self._tmp.name) / "repo")
        page = mock.patch("rzgitclient.core.repo_session.HISTORY_PAGE", 2)
        page.start()
        self.addCleanup(page.stop)
        self.ctx = make_panel_context()
        self.addCleanup(self.ctx.session.shutdown)
        self.panel = HistoryPanel(self.ctx)
        self.addCleanup(self.panel.deleteLater)
        self.model = self.panel._model

    def test_a_page_continues_the_lanes_and_keeps_the_rows_drawn(self):
        session = self.ctx.session
        session.open(self.repo)
        wait_idle(session)
        first = [self.model.graph_row(r) for r in (1, 2)]
        session.load_more_history()
        wait_idle(session)
        self.assertTrue(all(a is b for a, b in zip(first, (self.model.graph_row(r) for r in (1, 2)))))
        session.load_more_history()
        wait_idle(session)
        self.assertEqual(len(session.history), 5)
        whole = CommitGraph(session.history)
        self.assertEqual([self.model.graph_row(r + 1) for r in range(5)],
                         [whole.row(r) for r in range(5)])
