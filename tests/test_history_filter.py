"""History's filter: Ctrl+F shows only the commits that match (spec §5.4, RZG-88)."""

import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QThread, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from rzgit.core.history_search import HistoryMatches
from rzgit.ui.panels.find_bar import INVALID, NO_RESULTS
from rzgit.ui.panels.history_model import AUTHOR_COLUMN, GRAPH_COLUMN, SUBJECT_COLUMN
from rzgit.ui.panels.find_query import FindQuery
from rzgit.ui.panels.history_panel import (SEARCHING, HistoryPanel, filter_terms,
                                           highlight_pattern, matches_message, sha_prefix)
from rzgit.ui.theme.app_theme import LIGHT_THEME
from tests.support import commit_file, git, make_panel_context, make_repo, qt_app, wait_idle

UNCOMMITTED = "Uncommitted changes (0)"


class HistoryFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        for n in range(1, 7):
            commit_file(self.repo, "a.txt", str(n), f"{'odd' if n % 2 else 'even'} {n}")
        self.ctx = make_panel_context()
        self.addCleanup(self.ctx.session.shutdown)
        self.panel = HistoryPanel(self.ctx)
        self.addCleanup(self.panel.deleteLater)
        self.view, self.model, self.bar = self.panel._view, self.panel._model, self.panel.find_bar
        self.panel.resize(500, 300)
        self.panel.show()
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)

    def subjects(self):
        return [self.model.text(row, SUBJECT_COLUMN) for row in range(self.model.rowCount())]

    def current_subject(self):
        index = self.view.currentIndex()
        return self.model.text(index.row(), SUBJECT_COLUMN) if index.isValid() else None

    def settle(self, timeout=10.0):
        """Wait for the search to finish (its count is no longer "Searching…")."""
        deadline = time.monotonic() + timeout
        self.bar.flush()
        while self.bar.count.text() == SEARCHING or self.panel._search_id is None:
            if time.monotonic() > deadline:
                raise AssertionError("still searching")
            QCoreApplication.processEvents()
            QThread.msleep(1)
        wait_idle(self.ctx.session)
        self.app.processEvents()

    def find(self, text):
        QTest.keyClick(self.view, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        self.bar.field.setText(text)
        self.settle()

    def test_ctrl_f_opens_the_bar_without_keep(self):
        QTest.keyClick(self.view, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(self.bar.isVisible())
        self.assertTrue(self.bar.field.hasFocus())
        self.assertFalse(self.bar.keep.isVisible())
        self.assertEqual(self.subjects()[0], UNCOMMITTED)  # nothing typed: not filtered

    def test_filters_to_matches_and_hides_uncommitted_and_graph(self):
        self.find("odd")
        self.assertEqual(self.subjects(), ["odd 5", "odd 3", "odd 1"])
        self.assertEqual(self.bar.count.text(), "3 commits")
        self.assertFalse(self.model.has_uncommitted)
        self.assertIsNone(self.model.graph_row(0))
        self.assertFalse(self.view.pinned.isVisible())

    def test_words_match_on_their_own_in_any_order(self):
        """RZG-88: "5 odd" isn't in any subject, but both words are in "odd 5"."""
        self.find("5 odd")
        self.assertEqual(self.subjects(), ["odd 5"])
        self.assertEqual([m.group() for m in self.model.highlight.finditer("odd 5")],
                         ["odd", "5"])  # both words are filled
        self.find("  odd   even ")  # no commit has both
        self.assertEqual(self.subjects(), [])
        self.assertEqual(self.bar.count.text(), NO_RESULTS)
        self.find("   ")  # spaces only: nothing to find
        self.assertEqual(self.subjects()[0], UNCOMMITTED)

    def test_first_match_is_selected_and_written(self):
        self.find("odd")
        self.assertEqual(self.current_subject(), "odd 5")
        self.assertEqual(self.ctx.selection.commit.subject, "odd 5")

    def test_a_matching_selected_commit_stays_selected(self):
        self.ctx.selection.commit = next(c for c in self.ctx.session.history if c.subject == "odd 3")
        self.app.processEvents()
        self.find("odd")
        self.assertEqual(self.current_subject(), "odd 3")

    def test_stepping_wraps(self):
        self.find("odd")
        self.bar.field.setFocus()
        QTest.keyClick(self.bar.field, Qt.Key.Key_Return)
        self.assertEqual(self.current_subject(), "odd 3")
        QTest.keyClick(self.view, Qt.Key.Key_F3)
        QTest.keyClick(self.view, Qt.Key.Key_F3)
        self.assertEqual(self.current_subject(), "odd 5")  # wrapped
        QTest.keyClick(self.view, Qt.Key.Key_F3, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.current_subject(), "odd 1")
        self.bar.previous.click()
        self.assertEqual(self.current_subject(), "odd 3")
        self.assertEqual(self.ctx.selection.commit.subject, "odd 3")

    def test_sha_prefix(self):
        target = self.ctx.session.history[3]
        self.find(target.sha[:7].upper())
        self.assertIn(target.subject, self.subjects())
        self.find(f" {target.sha[:7]} ")  # spaces around it
        self.assertEqual(self.subjects(), [target.subject])

    def test_no_results_and_invalid_pattern(self):
        self.find("nowhere")
        self.assertEqual(self.subjects(), [])
        self.assertEqual(self.bar.count.text(), NO_RESULTS)
        self.assertFalse(self.bar.next.isEnabled())
        self.bar.regex.setChecked(True)
        self.bar.field.setText("(")
        self.bar.flush()
        self.assertEqual(self.bar.count.text(), INVALID)
        self.assertEqual(self.subjects(), [])
        self.bar.regex.setChecked(False)

    def test_empty_field_shows_everything_with_the_bar_open(self):
        self.find("odd")
        self.bar.field.clear()
        self.bar.flush()
        self.assertTrue(self.bar.isVisible())
        self.assertEqual(self.subjects()[0], UNCOMMITTED)
        self.assertEqual(len(self.subjects()), 7)
        self.assertEqual(self.bar.count.text(), "")

    def test_closing_restores_history_with_the_chosen_commit(self):
        self.find("odd")
        self.panel.find_step(1)  # odd 3
        QTest.keyClick(self.bar.field, Qt.Key.Key_Escape)
        self.assertFalse(self.bar.isVisible())
        self.assertEqual(self.subjects()[0], UNCOMMITTED)
        self.assertEqual(self.current_subject(), "odd 3")
        self.assertEqual(self.ctx.selection.commit.subject, "odd 3")
        self.assertTrue(self.view.hasFocus())
        self.assertIsNone(self.model.highlight)

    def test_closing_with_uncommitted_selected_keeps_it(self):
        self.find("nowhere")
        self.bar.close_button.click()
        self.assertEqual(self.current_subject(), UNCOMMITTED)
        self.assertIsNone(self.ctx.selection.commit)

    def test_a_new_commit_searches_again(self):
        self.find("odd")
        commit_file(self.repo, "a.txt", "7", "odd 7")
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        self.settle()
        self.assertEqual(self.subjects(), ["odd 7", "odd 5", "odd 3", "odd 1"])
        self.bar.close_bar()
        self.assertEqual(self.subjects()[:2], [UNCOMMITTED, "odd 7"])  # the history went on meanwhile

    def test_another_repo_closes_the_bar(self):
        self.find("odd")
        other = make_repo(Path(self._tmp.name) / "other")
        commit_file(other, "b.txt", "x", "elsewhere")
        self.ctx.session.open(other)
        wait_idle(self.ctx.session)
        self.assertFalse(self.bar.isVisible())
        self.assertEqual(self.subjects(), [UNCOMMITTED, "elsewhere"])

    def test_stale_matches_are_dropped(self):
        self.find("odd")
        old = HistoryMatches(self.panel._search_id - 1, (), True)
        self.panel._on_matches(old)
        self.assertEqual(len(self.subjects()), 3)

    def test_highlight_pattern_and_fill(self):
        self.find("odd")
        self.assertIsNotNone(self.model.highlight)
        self.assertEqual(self.model.highlight.pattern, "odd")
        image = QImage(self.view.viewport().size(), QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        self.view.viewport().render(image)
        fill = QColor(LIGHT_THEME.colors.diff_find_match).rgb()
        rect = self.view.visualRect(self.model.index(1, SUBJECT_COLUMN))  # odd 3: not selected
        found = any(image.pixel(x, y) == fill for x in range(rect.left(), rect.right())
                    for y in range(rect.top(), rect.bottom()))
        self.assertTrue(found)
        rect = self.view.visualRect(self.model.index(1, AUTHOR_COLUMN))  # no match there
        self.assertFalse(any(image.pixel(x, y) == fill for x in range(rect.left(), rect.right())
                             for y in range(rect.top(), rect.bottom())))

    def test_ref_labels_still_drawn_while_filtered(self):
        git(self.repo, "tag", "v-odd", "HEAD~1")  # on odd 5
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        self.find("odd")
        self.assertTrue(self.model.ref_labels(0))
        width = self.view.columnWidth(GRAPH_COLUMN)
        image = QImage(self.view.viewport().size(), QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        self.view.viewport().render(image)
        rect = self.view.visualRect(self.model.index(0, GRAPH_COLUMN))
        colours = {image.pixel(x, rect.center().y()) for x in range(rect.left(), rect.right())}
        self.assertGreater(len(colours), 2)  # something is drawn: the label
        self.assertEqual(self.view.columnWidth(GRAPH_COLUMN), width)

    def test_paging_and_back_to_top_off(self):
        self.find("odd")
        with mock.patch.object(self.ctx.session, "load_more_history") as more:
            self.panel._load_more_near_the_end()
        more.assert_not_called()
        self.assertFalse(self.view.back_to_top_wanted())


class HistoryFilterRestoreTests(unittest.TestCase):
    """Ten commits in pages of three, in a view two rows tall."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = make_repo(Path(tmp.name) / "repo")
        for n in range(1, 11):
            commit_file(repo, "a.txt", str(n), f"c{n}")
        page = mock.patch("rzgit.core.repo_session.HISTORY_PAGE", 3)
        page.start()
        self.addCleanup(page.stop)
        self.ctx = ctx = make_panel_context()
        self.addCleanup(ctx.session.shutdown)
        self.panel = panel = HistoryPanel(ctx)
        self.addCleanup(panel.deleteLater)
        panel.resize(400, 66)
        panel.show()
        panel.activateWindow()
        QTest.qWaitForWindowActive(panel)
        ctx.session.open(repo)
        wait_idle(ctx.session)
        self.assertEqual(len(ctx.session.history), 3)

    def find(self, text):
        bar = self.panel.find_bar
        self.panel.open_find()
        bar.field.setText(text)
        bar.flush()
        deadline = time.monotonic() + 10
        while bar.count.text() == SEARCHING:
            self.assertLess(time.monotonic(), deadline)
            QCoreApplication.processEvents()
        self.app.processEvents()

    def test_loads_pages_until_the_commit_is_there(self):
        ctx, panel = self.ctx, self.panel
        self.find("c1")
        panel.find_step(1)  # c10, c1: to c1, the oldest
        self.assertEqual(ctx.selection.commit.subject, "c1")
        panel.find_bar.close_bar()
        self.assertTrue(panel._view.hasFocus())
        self.assertEqual(ctx.selection.commit.subject, "c1")  # not Uncommitted changes meanwhile
        for _ in range(10):
            wait_idle(ctx.session)
            self.app.processEvents()
        model = panel._model
        self.assertEqual(len(ctx.session.history), 10)
        index = panel._view.currentIndex()
        self.assertEqual(model.text(index.row(), SUBJECT_COLUMN), "c1")
        self.assertEqual(ctx.selection.commit.subject, "c1")

    def test_closing_with_focus_elsewhere_keeps_an_unloaded_commit(self):
        """× takes no focus: with focus outside History, the view gets it on closing,
        and a focused view with no current row would make Uncommitted changes current."""
        ctx, panel = self.ctx, self.panel
        other = QLineEdit(panel)
        other.show()
        self.find("c1")
        panel.find_step(1)  # c1, not loaded
        other.setFocus()
        self.app.processEvents()
        self.assertTrue(other.hasFocus())
        panel.find_bar.close_button.click()
        self.assertTrue(panel._view.hasFocus())
        self.assertEqual(ctx.selection.commit.subject, "c1")

    def test_back_to_top_stays_hidden_while_filtered_and_scrolled(self):
        self.find("c")
        self.assertEqual(self.panel._model.rowCount(), 10)
        bar = self.panel._view.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.app.processEvents()
        self.assertGreater(bar.value(), bar.pageStep())
        self.assertFalse(self.panel._view.back_to_top_wanted())


class HelperTests(unittest.TestCase):
    def test_sha_prefix(self):
        self.assertEqual(sha_prefix("ABCD"), "abcd")
        self.assertIsNone(sha_prefix("abc"))  # too short
        self.assertIsNone(sha_prefix("abcg"))
        self.assertIsNone(sha_prefix("a" * 41))

    def test_filter_terms(self):
        self.assertEqual(filter_terms(FindQuery("")), ())
        terms = filter_terms(FindQuery(" fix  Parser fix", word=True))
        self.assertEqual([t.pattern for t in terms], [r"(?<!\w)(?:fix)(?!\w)",
                                                      r"(?<!\w)(?:Parser)(?!\w)"])
        self.assertTrue(all(t.flags & re.IGNORECASE for t in terms))
        self.assertFalse(filter_terms(FindQuery("a b", case=True))[0].flags & re.IGNORECASE)
        (regex,) = filter_terms(FindQuery("a b+", regex=True))  # one regex, spaces and all
        self.assertEqual(regex.pattern, "a b+")
        with self.assertRaises(ValueError):
            filter_terms(FindQuery("(", regex=True))

    def test_highlight_pattern(self):
        self.assertIsNone(highlight_pattern(()))
        (one,) = filter_terms(FindQuery("odd"))
        self.assertIs(highlight_pattern((one,)), one)
        both = highlight_pattern(filter_terms(FindQuery("pars parser")))
        self.assertEqual([m.group() for m in both.finditer("A PARSER, pars")], ["PARSER", "pars"])

    def test_messages(self):
        self.assertEqual(matches_message(HistoryMatches(1, (), True)), NO_RESULTS)
        self.assertEqual(matches_message(HistoryMatches(1, (object(),), True)), "1 commit")
        many = tuple(object() for _ in range(1200))
        self.assertEqual(matches_message(HistoryMatches(1, many, True)), "1,200 commits")
        self.assertEqual(matches_message(HistoryMatches(1, many, True, capped=True)),
                         "1,200+ commits")


if __name__ == "__main__":
    unittest.main()
