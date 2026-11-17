import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.ui.panels import diff_panel, find_bar
from rzgitclient.ui.panels.diff_panel import DiffPanel
from rzgitclient.ui.panels.diff_rows import TextPos
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from tests.support import commit_file, make_panel_context, make_repo, qt_app, wait_idle

A = FileSelection("a.txt", FileSource.WORKTREE)
B = FileSelection("b.txt", FileSource.WORKTREE)


def near(a: QColor, b: QColor, tolerance: int) -> bool:
    return all(abs(x - y) <= tolerance for x, y in
               ((a.red(), b.red()), (a.green(), b.green()), (a.blue(), b.blue())))


class DiffFindTests(unittest.TestCase):
    """Find in the Diff panel (RZG-74, spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        patcher = mock.patch.object(find_bar, "_options", find_bar._Options())
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        base = "".join(f"line {n}\n" for n in range(1, 101))
        commit_file(self.repo, "a.txt", base, "first")
        commit_file(self.repo, "b.txt", "needle b\n", "second")
        # Two changes, far apart: lines 30 and 70.
        (self.repo / "a.txt").write_text(
            base.replace("line 30\n", "Needle thirty\n")
                .replace("line 70\n", "needle SEVENTY needle\n"))
        (self.repo / "b.txt").write_text("needle B\n")
        self.ctx = make_panel_context()
        self.actions = self.ctx.actions
        self.session = self.ctx.session
        self.session.open(self.repo)
        wait_idle(self.session)
        self.panel = DiffPanel(self.ctx)
        self.addCleanup(self.panel.deleteLater)
        self.panel.resize(700, 300)
        self.panel.show()
        self.bar = self.panel.find_bar
        self.select(A)

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def settle(self):
        wait_idle(self.session)
        QCoreApplication.processEvents()

    def select(self, file):
        self.ctx.selection.file = file
        self.settle()

    def find(self, text, **options):
        for name, on in options.items():
            getattr(self.bar, name).setChecked(on)
        if self.bar.isHidden():
            self.panel.open_find()
        self.bar.field.setText(text)
        self.bar.flush()

    def view(self):
        return self.panel._unified

    def selected(self, view=None):
        """The selected text of ``view`` (the unified view), as (row text, selected part)."""
        view = view or self.view()
        span = view.text_range()
        if span is None:
            return None
        start, end = span
        self.assertEqual(start.row, end.row)
        return view.rows[start.row].text[start.col:end.col]

    def test_typing_finds_and_selects_the_first_match(self):
        self.find("needle")
        self.assertEqual(self.bar.count.text(), "1 of 3")
        self.assertEqual(self.selected(), "Needle")
        rows = self.view().rows
        self.assertEqual({rows[r].text for r in self.view().find_spans},
                         {"Needle thirty", "needle SEVENTY needle"})

    def test_stepping_wraps_both_ways(self):
        self.find("needle")
        texts = []
        for _ in range(4):
            self.panel.find_step(1)
            start, _end = self.view().text_range()
            texts.append((self.view().rows[start.row].text, start.col, self.bar.count.text()))
        self.assertEqual(texts, [("needle SEVENTY needle", 0, "2 of 3"),
                                 ("needle SEVENTY needle", 15, "3 of 3"),
                                 ("Needle thirty", 0, "1 of 3"),
                                 ("needle SEVENTY needle", 0, "2 of 3")])
        self.panel.find_step(-1)
        self.panel.find_step(-1)
        self.assertEqual(self.bar.count.text(), "3 of 3")

    def test_the_first_match_is_at_or_after_the_caret(self):
        view = self.view()
        row = next(n for n, r in enumerate(view.rows) if r.text == "line 69")
        view.select_text(TextPos(row, 0), TextPos(row, 0))
        self.find("needle")
        self.assertEqual(self.bar.count.text(), "2 of 3")
        # Typing on keeps the current match while it still matches.
        self.find("needle S")
        self.assertEqual(self.bar.count.text(), "1 of 1")
        self.assertEqual(self.selected(), "needle S")

    def test_options(self):
        self.find("needle", case=True)
        self.assertEqual(self.bar.count.text(), "1 of 2")
        self.bar.case.setChecked(False)
        self.bar.word.setChecked(True)
        self.find("needl")
        self.assertEqual(self.bar.count.text(), find_bar.NO_RESULTS)
        self.bar.word.setChecked(False)
        self.find(r"needle\s+\w+", regex=True)
        # From the last selection (needle SEVENTY), still there with no results.
        self.assertEqual(self.bar.count.text(), "2 of 2")
        self.assertEqual(self.selected(), "needle SEVENTY")
        self.find("(")
        self.assertEqual(self.bar.count.text(), find_bar.INVALID)
        self.assertEqual(self.view().find_spans, {})

    def test_hunk_headers_are_not_searched(self):
        self.find("@@")
        self.assertEqual(self.bar.count.text(), find_bar.NO_RESULTS)

    def test_side_by_side_orders_left_before_right_on_a_row(self):
        self.actions.side_by_side_diff.setChecked(True)
        self.settle()
        left, right = self.panel._left, self.panel._right
        self.find("line 31|thirty", regex=True)
        found = []
        for _ in range(4):
            side = left if left.text_range() is not None else right
            other = right if side is left else left
            self.assertIsNone(other.text_range())  # one side at a time
            found.append(("left" if side is left else "right", self.selected(side)))
            self.panel.find_step(1)
        self.assertEqual(found, [("right", "thirty"), ("left", "line 31"),
                                 ("right", "line 31"), ("right", "thirty")])
        # On one row, left before right even where the right match starts sooner.
        right.clear_text_selection()
        left.select_text(TextPos(0, 0), TextPos(0, 0))
        self.find("0$|^Needle")
        self.assertEqual((self.selected(left), right.text_range()), ("0", None))
        self.panel.find_step(1)
        self.assertEqual((left.text_range(), self.selected(right)), (None, "Needle"))

    def test_a_match_in_a_fold_unfolds_it(self):
        self.actions.whole_file_diff.setChecked(True)
        self.settle()
        self.panel.fold_all()
        self.assertTrue(any(r.text.startswith("⋯") and "line 50" in [h.text for h in r.hidden]
                            for r in self.view().rows))
        self.find("line 50")
        self.assertEqual(self.bar.count.text(), "1 of 1")
        self.assertEqual(self.selected(), "line 50")
        row = self.view().text_range()[0].row
        self.assertEqual(self.view().rows[row].new_no, 50)
        self.assertFalse(any("line 50" in [h.text for h in r.hidden] for r in self.view().rows))

    def test_a_refresh_keeps_the_current_match(self):
        self.find("needle")
        self.panel.find_step(1)
        self.session.refresh()
        self.settle()
        self.assertEqual(self.bar.count.text(), "2 of 3")

    def test_another_file_closes_the_search(self):
        self.find("needle")
        self.select(B)
        self.assertTrue(self.bar.isHidden())
        self.assertEqual(self.view().find_spans, {})
        self.assertIsNone(self.view().text_range())

    def test_with_keep_the_search_applies_to_the_new_file(self):
        self.find("needle", keep=True)
        self.select(B)
        self.assertTrue(self.bar.isVisible())
        self.assertEqual(self.bar.count.text(), "1 of 2")  # needle b, needle B
        self.assertEqual(self.selected(), "needle")
        self.panel.find_step(1)
        self.panel.find_step(1)
        self.assertEqual(self.bar.count.text(), "1 of 2")  # it never spills back into a.txt
        self.select(A)
        self.assertEqual(self.bar.count.text(), "1 of 3")

    def test_ctrl_f_fills_the_field_with_the_selection(self):
        view = self.view()
        row = next(n for n, r in enumerate(view.rows) if r.text.startswith("needle SEVENTY"))
        view.select_text(TextPos(row, 7), TextPos(row, 14))
        self.panel.open_find()
        self.assertEqual(self.bar.field.text(), "SEVENTY")
        self.assertEqual(self.bar.count.text(), "1 of 1")
        self.assertEqual(self.selected(), "SEVENTY")
        # The current match selected: Ctrl+F keeps the query.
        self.bar.field.setText("seven")
        self.bar.flush()
        self.panel.open_find()
        self.assertEqual(self.bar.field.text(), "seven")

    def test_the_shortcuts(self):
        self.panel.activateWindow()
        self.view().setFocus()
        QApplication.processEvents()
        QTest.keyClick(self.view(), Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(self.bar.isVisible())
        self.bar.field.setText("needle")
        self.bar.flush()
        QTest.keyClick(self.view(), Qt.Key.Key_F3)
        self.assertEqual(self.bar.count.text(), "2 of 3")
        QTest.keyClick(self.view(), Qt.Key.Key_F3, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.bar.count.text(), "1 of 3")

    def test_esc_closes_the_bar_and_clears_the_highlights(self):
        self.find("needle")
        QTest.keyClick(self.bar.field, Qt.Key.Key_Escape)
        self.assertTrue(self.bar.isHidden())
        self.assertEqual(self.view().find_spans, {})
        self.assertEqual(self.selected(), "Needle")  # the last match stays selected

    def test_selecting_other_text_drops_the_current_match(self):
        self.find("needle")
        self.view().select_text(TextPos(1, 0), TextPos(1, 2))
        self.assertEqual(self.bar.count.text(), "3 matches")
        self.panel.find_step(1)  # from the selection
        self.assertEqual(self.bar.count.text(), "1 of 3")

    def test_new_rows_drop_the_views_highlights(self):
        self.find("needle")
        view = self.view()
        self.assertTrue(view.find_spans)
        view.set_rows(view.rows)  # the panel finds them again in new rows
        self.assertEqual(view.find_spans, {})

    def test_the_current_match_is_outlined_and_only_it(self):
        self.find("needle")
        view = self.view()
        start, end = view.text_range()
        self.assertEqual(view.find_current, (start.row, start.col, end.col))
        self.panel.find_step(1)
        start, end = view.text_range()
        self.assertEqual(view.find_current, (start.row, start.col, end.col))
        view.select_text(TextPos(1, 0), TextPos(1, 2))  # other text: no current match
        self.assertIsNone(view.find_current)
        self.panel.find_step(1)
        self.assertIsNotNone(view.find_current)
        self.bar.close_bar()
        self.assertIsNone(view.find_current)

    def test_the_outline_goes_round_the_whole_word(self):
        self.find("venty nee")
        view = self.view()
        row = view.text_range()[0].row
        self.assertEqual(self.selected(), "VENTY nee")  # the match itself is selected
        text = view.rows[row].text
        self.assertEqual(text[view.find_current[1]:view.find_current[2]], "SEVENTY needle")
        self.assertEqual(view.find_spans[row], ((9, 18),))  # and filled

    def test_side_by_side_outlines_only_the_current_side(self):
        self.actions.side_by_side_diff.setChecked(True)
        self.settle()
        left, right = self.panel._left, self.panel._right
        self.find("line 31|thirty", regex=True)
        self.assertIsNone(left.find_current)
        self.assertIsNotNone(right.find_current)
        self.panel.find_step(1)
        self.assertIsNotNone(left.find_current)
        self.assertIsNone(right.find_current)

    def test_the_outline_is_painted(self):
        self.find("needle")
        view = self.view()
        row, start, end = view.find_current
        view.verticalScrollBar().setValue(max(0, row - 2))
        QApplication.processEvents()
        top, height = next((t, h) for n, _r, t, h in view.visible_rows(view.viewport().rect())
                           if n == row)
        [rect] = view.span_rects(row, ((start, end),), top, height)
        image = view.viewport().grab().toImage()
        colour = QColor(LIGHT_THEME.colors.diff_find_current)
        middle = int(rect.center().x())
        # 1 px, on the edge pixels of the match's word.
        self.assertTrue(near(image.pixelColor(middle, int(rect.top())), colour, 10))
        self.assertTrue(near(image.pixelColor(middle, int(rect.bottom()) - 1), colour, 10))
        self.assertTrue(near(image.pixelColor(int(rect.left()), int(rect.center().y())),
                             colour, 10))
        self.assertFalse(near(image.pixelColor(middle, int(rect.top()) + 1), colour, 10))  # thin
        self.assertFalse(near(image.pixelColor(middle, int(rect.top()) - 3), colour, 10))

    def test_find_stops_at_the_limit(self):
        with mock.patch.object(diff_panel, "MAX_MATCHES", 2):
            self.find("needle")
        self.assertEqual(self.bar.count.text(), "1 of 2+")
        self.assertEqual(len(self.panel._matches), 2)

    def test_the_matches_are_painted(self):
        self.find("needle")
        self.panel.find_step(1)  # the first one is no longer the selection
        view = self.view()
        row = next(n for n, r in enumerate(view.rows) if r.text == "Needle thirty")
        view.verticalScrollBar().setValue(max(0, row - 2))
        QApplication.processEvents()
        top, height = next((t, h) for n, _r, t, h in view.visible_rows(view.viewport().rect())
                           if n == row)
        [rect] = view.span_rects(row, view.find_spans[row], top, height)
        image = view.viewport().grab().toImage()
        colour = QColor(LIGHT_THEME.colors.diff_find_match)
        self.assertTrue(near(image.pixelColor(int(rect.left()) + 1, int(top + 1)), colour, 10))
        self.assertFalse(near(image.pixelColor(int(rect.right()) + 20, int(top + 1)), colour, 10))


if __name__ == "__main__":
    unittest.main()
