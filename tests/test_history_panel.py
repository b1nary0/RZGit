import dataclasses
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QHoverEvent
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

from rzgitclient.ui.panels.history_panel import HistoryPanel
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from rzgitclient.ui.widgets.column_header import ColumnHeader
from tests.support import SignalRecorder, commit_file, make_panel_context, make_repo, qt_app, wait_idle

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
        return [self.cell(row, 0) for row in range(self.model.rowCount())]

    def cell(self, row, col, role=ROLE.DisplayRole):
        return self.model.data(self.model.index(row, col), role)

    def select(self, row):
        self.view.setCurrentIndex(self.model.index(row, 0))

    def current_subject(self):
        return self.cell(self.view.currentIndex().row(), 0)

    def test_empty_state(self):
        self.assertEqual(self.subjects(), [])
        self.assertIsNone(self.ctx.selection.commit)

    def test_rows_hash_and_muted_columns(self):
        self.open()
        self.assertEqual(self.subjects(), [UNCOMMITTED, "second", "first"])
        commit = self.ctx.session.history[0]
        self.assertEqual(self.model.headerData(0, Qt.Orientation.Horizontal), "Subject")
        self.assertEqual(self.cell(1, 1), "Test User")
        self.assertEqual(self.cell(1, 3), commit.short_sha)
        self.assertEqual(self.cell(1, 3, ROLE.ToolTipRole), commit.sha)
        self.assertEqual(self.cell(1, 3, ROLE.FontRole).family(), LIGHT_THEME.mono_font().family())
        self.assertIsNone(self.cell(1, 0, ROLE.ForegroundRole))  # subject: normal text
        for col in (1, 2, 3):
            self.assertEqual(self.cell(1, col, ROLE.ForegroundRole).name(), LIGHT_THEME.colors.text_muted)

    def test_columns_are_labelled_by_a_column_header(self):
        """RZG-41: History's labels get the shared look."""
        header = self.panel._view.horizontalHeader()
        self.assertIsInstance(header, ColumnHeader)
        self.assertEqual([header.label(c) for c in range(4)], ["Subject", "Author", "Date", "Commit"])

    def test_a_hidden_column_stays_hidden_through_a_reload(self):
        self.open()
        self.panel._view.horizontalHeader().set_column_shown("Author", False)
        commit_file(self.repo, "b.txt", "2", "third")
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        self.assertEqual(self.subjects()[1], "third")
        self.assertTrue(self.panel._view.isColumnHidden(1))

    def test_uncommitted_changes_is_row_0_selected_by_default(self):
        self.open()
        self.assertEqual(self.view.currentIndex().row(), 0)
        self.assertIsNone(self.ctx.selection.commit)
        self.assertTrue(self.cell(0, 0, ROLE.FontRole).italic())
        self.assertEqual([self.cell(0, c) for c in (1, 2, 3)], ["", "", ""])
        self.assertIsNone(self.cell(0, 3, ROLE.ToolTipRole))
        self.assertIsNone(self.model.commit_at(0))
        self.assertEqual(self.model.row_of(self.ctx.session.history[0].sha), 1)

    def test_columns_fit_the_commits_though_uncommitted_changes_came_first(self):
        self.open()  # the row arrives with the repo, before the first page
        header = self.view.horizontalHeader()
        for col in (1, 2, 3):
            self.assertGreaterEqual(header.sectionSize(col),
                                    self.view.width_hint(self.model.index(1, col)))

    def test_uncommitted_changes_counts_the_changed_files(self):
        self.open()
        (self.repo / "a.txt").write_text("changed")
        (self.repo / "new.txt").write_text("new")
        self.refresh()
        self.assertEqual(self.cell(0, 0), "Uncommitted changes (2)")
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

        self.assertEqual([hovered(1, c) for c in range(4)], [True] * 4)  # not just the cell
        self.assertEqual([hovered(0, c) for c in range(4)], [False] * 4)
        self.view.viewportEvent(QHoverEvent(QEvent.Type.HoverLeave, QPointF(-1, -1), QPointF(-1, -1), QPointF(5, y)))
        self.assertFalse(hovered(1, 0))

    def test_apply_theme_recolours_rows(self):
        self.open()
        changed = []
        self.model.dataChanged.connect(lambda *args: changed.append(args))
        colors = dataclasses.replace(LIGHT_THEME.colors, text_muted="#123456")
        self.ctx.theme.set_theme(dataclasses.replace(LIGHT_THEME, colors=colors))
        self.assertEqual(self.cell(1, 1, ROLE.ForegroundRole).name(), "#123456")
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
        top = self.model.commit_at(self.view.indexAt(QPoint(0, 0)).row())
        self.assertEqual(top.subject, "c6")

        commit_file(self.repo, "a.txt", "11", "c11")
        self.ctx.session.refresh()
        self.settle()
        self.assertEqual(self.model.commits[0].subject, "c11")
        self.assertEqual(self.model.commit_at(self.view.indexAt(QPoint(0, 0)).row()), top)
        self.assertEqual(self.ctx.selection.commit, selected)

    def test_refresh_at_the_top_shows_new_commits(self):
        commit_file(self.repo, "a.txt", "11", "c11")
        self.ctx.session.refresh()
        self.settle()
        self.assertEqual(self.view.verticalScrollBar().value(), 0)
        self.assertEqual(self.view.indexAt(QPoint(0, 0)).row(), 0)


if __name__ == "__main__":
    unittest.main()
