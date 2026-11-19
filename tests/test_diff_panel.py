import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from rzgit.core.diff_patch import hunk_lines
from rzgit.core.selection import FileSelection, FileSource
from rzgit.ui.app_actions import OPEN_IN_VSCODE_TEXT as VSCODE, HunkHeaders
from rzgit.ui.panels import diff_panel
from rzgit.ui.panels.diff_panel import DiffPanel
from rzgit.ui.panels.diff_rows import RowKind, TextPos, unified_rows
from rzgit.ui.theme.app_theme import LIGHT_THEME
from tests.support import commit_file, git, make_panel_context, make_repo, qt_app, wait_idle

A = FileSelection("a.txt", FileSource.WORKTREE)
B = FileSelection("b.txt", FileSource.WORKTREE)


class DiffPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.repo, "a.txt", "".join(f"line {n}\n" for n in range(300)), "first")
        commit_file(self.repo, "b.txt", "b\n", "second")
        (self.repo / "a.txt").write_text("".join(f"LINE {n}\n" for n in range(300)))  # 600 rows
        (self.repo / "b.txt").write_text("B\n")
        self.ctx = make_panel_context()
        self.session = self.ctx.session
        self.session.open(self.repo)
        wait_idle(self.session)
        self.panel = self.make_panel()
        self.panel.resize(600, 300)

    def make_panel(self):
        panel = DiffPanel(self.ctx)
        # Live widgets make every later stylesheet change slower: delete them.
        self.addCleanup(panel.deleteLater)
        return panel

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def select(self, file):
        self.ctx.selection.file = file
        wait_idle(self.session)

    def page(self, panel=None):
        panel = panel or self.panel
        return {panel._message_page: "message", panel._unified: "unified",
                panel._split: "split"}[panel._stack.currentWidget()]

    def test_views_are_built_when_first_needed(self):
        self.assertEqual(self.panel._stack.count(), 1)  # just the message
        self.select(A)
        self.assertEqual(self.panel._stack.count(), 2)
        self.assertIsNone(self.panel._split)

    def test_empty_state(self):
        self.assertEqual(self.page(), "message")
        self.assertEqual(self.panel._message.text(), diff_panel.NOTHING_SELECTED)

    def test_selecting_a_file_shows_its_diff(self):
        self.select(A)
        self.assertEqual(self.page(), "unified")
        rows = self.panel._unified.rows
        self.assertEqual(rows[0].kind, RowKind.HUNK)
        self.assertIn(RowKind.REMOVED, {r.kind for r in rows})
        self.assertIn("a.txt", self.panel._path.text())
        self.assertEqual(self.panel._unified.toPlainText().count("\n") + 1, len(rows))
        self.select(None)
        self.assertEqual(self.panel._message.text(), diff_panel.NOTHING_SELECTED)

    def test_side_by_side_follows_the_action(self):
        self.select(A)
        self.ctx.actions.side_by_side_diff.setChecked(True)
        self.assertEqual(self.page(), "split")
        self.assertEqual(len(self.panel._left.rows), len(self.panel._right.rows))
        self.panel._right.verticalScrollBar().setValue(5)
        self.assertEqual(self.panel._left.verticalScrollBar().value(), 5)  # they scroll together
        self.ctx.actions.side_by_side_diff.setChecked(False)
        self.assertEqual(self.page(), "unified")

    def hint(self):
        return "" if self.panel._hint.isHidden() else self.panel._hint.text()

    def test_a_whole_added_or_deleted_file_shows_unified_with_a_hint(self):
        """RZG-61 (spec §5.4): side by side, one half would be empty."""
        (self.repo / "new.txt").write_text("one\ntwo\n")
        (self.repo / "b.txt").unlink()
        commit_file(self.repo, "c.txt", "c\n", "third")
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        self.session.refresh()  # so new.txt is known as untracked
        wait_idle(self.session)
        side_by_side = self.ctx.actions.side_by_side_diff
        side_by_side.setChecked(True)
        for file, why in ((FileSelection("new.txt", FileSource.WORKTREE), "added"),
                          (B, "deleted"),
                          (FileSelection("c.txt", FileSource.COMMIT, sha), "added")):
            with self.subTest(file=file):
                self.select(file)
                self.assertEqual(self.page(), "unified")
                self.assertEqual(self.hint(), f"Unified: whole file {why}")
                self.assertTrue(side_by_side.isChecked())
        self.select(A)  # changes on both sides: side by side again
        self.assertEqual(self.page(), "split")
        self.assertEqual(self.hint(), "")
        self.select(B)
        side_by_side.setChecked(False)  # chosen unified: no hint needed
        self.assertEqual(self.page(), "unified")
        self.assertEqual(self.hint(), "")
        side_by_side.setChecked(True)
        self.assertEqual(self.hint(), "Unified: whole file deleted")
        self.select(None)
        self.assertEqual(self.hint(), "")

    def test_a_staged_new_file_shows_unified(self):
        (self.repo / "new.txt").write_text("one\n")
        git(self.repo, "add", "new.txt")
        self.ctx.actions.side_by_side_diff.setChecked(True)
        self.select(FileSelection("new.txt", FileSource.INDEX))
        self.assertEqual(self.page(), "unified")
        self.assertEqual(self.hint(), "Unified: whole file added")

    def test_both_views_highlight_changed_words(self):
        self.select(A)
        view = self.panel._unified
        first_removed = next(n for n, r in enumerate(view.rows) if r.kind is RowKind.REMOVED)
        first_added = next(n for n, r in enumerate(view.rows) if r.kind is RowKind.ADDED)
        self.assertEqual(view.word_spans(first_removed), ((0, 4),))  # "line" → "LINE"
        self.assertEqual(view.word_spans(first_added), ((0, 4),))
        self.ctx.actions.side_by_side_diff.setChecked(True)
        n = next(n for n, r in enumerate(self.panel._left.rows) if r.kind is RowKind.REMOVED)
        self.assertEqual(self.panel._left.word_spans(n), ((0, 4),))
        self.assertEqual(self.panel._right.word_spans(n), ((0, 4),))

    def test_a_panel_built_late_starts_in_the_saved_view_and_shows_the_diff(self):
        self.select(A)
        self.ctx.actions.side_by_side_diff.setChecked(True)
        late = self.make_panel()
        self.assertEqual(self.page(late), "split")  # from the session's latest diff

    def test_an_update_keeps_the_scroll_position(self):
        self.select(A)
        self.panel.show()
        bar = self.panel._unified.verticalScrollBar()
        bar.setValue(40)
        (self.repo / "a.txt").write_text("".join(f"Line {n}\n" for n in range(300)))
        self.session.refresh()
        wait_idle(self.session)
        self.assertEqual(bar.value(), 40)
        self.select(B)  # another file starts at the top
        self.assertEqual(bar.value(), 0)

    def test_another_path_asks_to_be_activated_a_new_source_does_not(self):
        asked = []
        self.panel.activate_requested.connect(lambda: asked.append(1))
        self.select(A)
        self.select(FileSelection("a.txt", FileSource.INDEX))
        self.assertEqual(len(asked), 1)
        self.select(B)
        self.assertEqual(len(asked), 2)

    def test_binary_and_no_changes(self):
        commit_file(self.repo, "c.bin", "x", "binary")
        (self.repo / "c.bin").write_bytes(b"\x00\x01")
        self.session.refresh()
        wait_idle(self.session)
        self.select(FileSelection("c.bin", FileSource.WORKTREE))
        self.assertEqual(self.panel._message.text(), diff_panel.BINARY)
        self.select(FileSelection("b.txt", FileSource.INDEX))  # nothing staged
        self.assertEqual(self.panel._message.text(), diff_panel.NO_CHANGES)

    def test_large_diff_can_be_shown_anyway(self):
        self.ctx.prefs.set(diff_panel.LARGE_DIFF, 50)  # the setting (spec §8.2)
        self.select(A)
        self.assertEqual(self.page(), "message")
        self.assertIn("Large diff", self.panel._message.text())
        self.assertFalse(self.panel._show_anyway.isHidden())
        self.panel._show_anyway.click()
        wait_idle(self.session)
        self.assertEqual(self.page(), "unified")

    def test_loading_shows_after_a_delay(self):
        with mock.patch.object(diff_panel, "LOADING_DELAY_MS", 0):
            panel = self.make_panel()
        self.ctx.selection.file = B
        while self.session.busy:  # the timer fires before the worker is done… or not
            QCoreApplication.processEvents()
            QThread.msleep(1)
        QCoreApplication.processEvents()
        self.assertEqual(self.page(panel), "unified")  # the result replaces it
        panel._loading.timeout.emit()  # as if the worker were slow
        self.assertEqual(panel._message.text(), diff_panel.LOADING)

    def test_theme_colours_rows(self):
        self.select(A)
        view = self.panel._unified
        self.assertEqual(view._backgrounds[RowKind.ADDED].name(), LIGHT_THEME.colors.diff_added_bg)
        self.assertEqual(view.font().family(), LIGHT_THEME.mono_font().family())
        self.assertGreater(view.gutter_width(), 0)
        self.panel.show()
        self.panel.grab()  # paints rows and gutter without errors



class DiffPanelHunkTests(unittest.TestCase):
    """Stage, unstage and discard hunks and lines (RZG-23, spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        base = "".join(f"line {n}\n" for n in range(200))
        commit_file(self.repo, "a.txt", base, "first")
        # Two hunks: lines 5 to 40 changed (a long one), and line 190.
        changed = base.replace("line 5\n", "FIVE\n").replace("line 190\n", "NINETY\n")
        for n in range(6, 41):
            changed = changed.replace(f"line {n}\n", f"LINE {n}\n")
        (self.repo / "a.txt").write_text(changed)
        (self.repo / "new.txt").write_text("one\ntwo\n")
        self.ctx = make_panel_context()
        self.session = self.ctx.session
        self.session.open(self.repo)
        wait_idle(self.session)
        self.panel = DiffPanel(self.ctx)
        self.addCleanup(self.panel.deleteLater)
        self.panel.resize(700, 300)
        self.panel.show()

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    def select(self, path, source=FileSource.WORKTREE):
        self.ctx.selection.file = FileSelection(path, source)
        wait_idle(self.session)
        QCoreApplication.processEvents()

    def index(self, path):
        return git(self.repo, "show", f":{path}")

    def menu(self, view, row_index):
        """The menu for a right-click on ``view.rows[row_index]``, scrolled into view."""
        cursor = view.textCursor()
        cursor.setPosition(view.document().findBlockByNumber(row_index).position())
        view.setTextCursor(cursor)
        view.ensureCursorVisible()
        QCoreApplication.processEvents()
        block = view.document().findBlockByNumber(row_index)
        geometry = view.blockBoundingGeometry(block).translated(view.contentOffset())
        menu = self.panel.menu_for(view, QPoint(30, int(geometry.center().y())))
        self.addCleanup(menu.deleteLater)
        return menu

    def entries(self, menu):
        return [(a.text(), a.isEnabled()) for a in menu.actions() if not a.isSeparator()]

    def trigger(self, menu, text):
        next(a for a in menu.actions() if a.text() == text).trigger()
        wait_idle(self.session)
        QCoreApplication.processEvents()

    def test_the_hunk_link_stages_one_hunk_and_keeps_the_scroll(self):
        self.select("a.txt")
        view = self.panel._unified
        self.assertEqual(view._link, "Stage Hunk")
        bar = view.verticalScrollBar()
        self.assertGreater(bar.maximum(), 20)
        bar.setValue(20)  # scrolled into the first hunk, which doesn't change
        QCoreApplication.processEvents()
        view.hunk_clicked.emit(1)  # the second hunk: line 190
        wait_idle(self.session)
        QCoreApplication.processEvents()
        self.assertIn("NINETY", self.index("a.txt"))
        self.assertNotIn("FIVE", self.index("a.txt"))
        self.assertEqual(len(self.session.diff.hunks), 1)  # the other one is left
        self.assertEqual(view.rows, unified_rows(self.session.diff))  # shown
        self.assertEqual(bar.value(), 20)

    def test_the_menu_of_a_tracked_files_unstaged_diff(self):
        self.select("a.txt")
        menu = self.menu(self.panel._unified, 4)  # a changed line of the first hunk
        self.assertEqual(self.entries(menu), [
            ("Stage Hunk", True), ("Stage Selected Lines", True),
            ("Discard Hunk…", True), ("Discard Selected Lines…", True), ("Copy", False),
            (VSCODE, False)])

    def test_stage_the_clicked_line_only(self):
        self.select("a.txt")
        view = self.panel._unified
        added = next(n for n, r in enumerate(view.rows) if r.kind is RowKind.ADDED)
        self.trigger(self.menu(view, added), "Stage Selected Lines")
        # Only "+ FIVE": the unselected "- line 5" stays, and FIVE follows it.
        self.assertIn("line 4\nline 5\nFIVE\nline 6\n", self.index("a.txt"))

    def test_unstage_from_a_staged_diff(self):
        git(self.repo, "add", "a.txt")
        self.session.refresh()
        self.select("a.txt", FileSource.INDEX)
        view = self.panel._unified
        self.assertEqual(view._link, "Unstage Hunk")
        menu = self.menu(view, 4)
        self.assertEqual([t for t, _ in self.entries(menu)],
                         ["Unstage Hunk", "Unstage Selected Lines", "Copy", VSCODE])
        self.trigger(menu, "Unstage Hunk")
        self.assertNotIn("FIVE", self.index("a.txt"))
        self.assertIn("NINETY", self.index("a.txt"))

    def test_an_untracked_file_can_be_staged_in_part_but_not_discarded(self):
        self.select("new.txt")
        view = self.panel._unified
        menu = self.menu(view, 1)
        self.assertEqual([t for t, _ in self.entries(menu)],
                         ["Stage Hunk", "Stage Selected Lines", "Copy", VSCODE])
        self.trigger(menu, "Stage Selected Lines")
        self.assertEqual(self.index("new.txt"), "one\n")

    def test_discard_goes_through_the_window(self):
        self.select("a.txt")
        asked = []
        self.ctx.actions.discard_lines_requested.connect(lambda d, l: asked.append((d, l)))
        self.trigger(self.menu(self.panel._unified, 4), "Discard Hunk…")
        self.assertEqual(len(asked), 1)
        diff, lines = asked[0]
        self.assertEqual(diff.path, "a.txt")
        self.assertEqual(lines, hunk_lines(diff, 0))
        self.assertIn("FIVE", (self.repo / "a.txt").read_text())  # not discarded yet

    def test_a_staged_rename_has_no_links_and_says_why(self):
        git(self.repo, "checkout", "--", "a.txt")
        git(self.repo, "mv", "a.txt", "moved.txt")
        (self.repo / "moved.txt").write_text((self.repo / "moved.txt").read_text() + "end\n")
        git(self.repo, "add", "moved.txt")
        self.session.refresh()
        self.select("moved.txt", FileSource.INDEX)
        view = self.panel._unified
        self.assertEqual(view._link, "")
        self.assertIn("renamed", view._hunk_tooltip)
        entries = self.entries(self.menu(view, 1))
        self.assertEqual([e for e in entries if e[0].startswith("Unstage")],
                         [("Unstage Hunk", False), ("Unstage Selected Lines", False)])
        self.assertTrue(any("renamed" in text for text, _ in entries))

    def test_a_commits_diff_has_no_hunk_commands(self):
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        self.ctx.selection.file = FileSelection("a.txt", FileSource.COMMIT, sha)
        wait_idle(self.session)
        view = self.panel._unified
        self.assertEqual(view._link, "")
        self.assertEqual(self.entries(self.menu(view, 1)), [("Copy", False), (VSCODE, False)])

    def test_open_with_vs_code_opens_the_shown_file_at_the_line_under_the_pointer(self):
        self.select("a.txt")
        self.ctx.actions.set_vscode_check(lambda paths: None)
        asked = []
        self.ctx.actions.open_in_vscode_requested.connect(lambda p, l: asked.append((p, l)))
        view = self.panel._unified
        n = next(n for n, r in enumerate(view.rows) if r.kind is RowKind.ADDED)
        menu = self.menu(view, n)
        self.assertEqual(self.entries(menu)[-1], (VSCODE, True))
        self.assertTrue(menu.actions()[-2].isSeparator())
        self.trigger(menu, VSCODE)
        self.assertEqual(asked, [(("a.txt",), view.rows[n].new_no)])

    def click_button(self, view, row_index):
        """Click the gutter button of ``view.rows[row_index]``, scrolled into view."""
        cursor = view.textCursor()
        cursor.setPosition(view.document().findBlockByNumber(row_index).position())
        view.setTextCursor(cursor)
        view.ensureCursorVisible()
        QCoreApplication.processEvents()
        n, _row, top, height = next(r for r in view.visible_rows(view.gutter.rect())
                                    if r[0] == row_index)
        QTest.mouseClick(view.gutter, Qt.MouseButton.LeftButton,
                         pos=view.gutter.button_rect(top, height).center())
        wait_idle(self.session)
        QCoreApplication.processEvents()

    def test_a_line_button_stages_that_line(self):
        self.select("a.txt")
        view = self.panel._unified
        self.assertEqual(view.gutter.label, "Stage")
        removed = next(n for n, r in enumerate(view.rows) if r.text == "line 190")
        self.click_button(view, removed)
        # Only "- line 190": the added NINETY isn't staged.
        self.assertNotIn("line 190\n", self.index("a.txt"))
        self.assertNotIn("NINETY", self.index("a.txt"))

    def test_a_line_button_in_a_staged_diff_unstages_it(self):
        git(self.repo, "add", "a.txt")
        self.session.refresh()
        self.select("a.txt", FileSource.INDEX)
        view = self.panel._unified
        self.assertEqual(view.gutter.label, "Unstage")
        added = next(n for n, r in enumerate(view.rows) if r.text == "NINETY")
        self.click_button(view, added)
        self.assertNotIn("NINETY", self.index("a.txt"))
        self.assertIn("FIVE", self.index("a.txt"))

    def test_staging_then_unstaging_a_new_files_line_leaves_it_untracked(self):
        self.select("new.txt")
        view = self.panel._unified
        self.click_button(view, next(n for n, r in enumerate(view.rows) if r.text == "one"))
        codes = {f.path: f.index + f.worktree for f in self.session.status}
        self.assertEqual(codes["new.txt"], "AM")
        self.select("new.txt", FileSource.INDEX)
        view = self.panel._unified
        self.click_button(view, next(n for n, r in enumerate(view.rows) if r.text == "one"))
        codes = {f.path: f.index + f.worktree for f in self.session.status}
        self.assertEqual(codes["new.txt"], "??")  # not left staged as an empty file

    def test_a_selected_lines_button_stages_the_whole_selection(self):
        self.select("a.txt")
        view = self.panel._unified
        chosen = [n for n, r in enumerate(view.rows) if r.text in ("FIVE", "NINETY")]
        view.selection.set_rows(chosen)
        self.click_button(view, chosen[1])
        index = self.index("a.txt")
        self.assertIn("FIVE", index)
        self.assertIn("NINETY", index)
        self.assertIn("line 5\n", index)  # its unselected "-" line stays
        self.assertEqual(view.selection.rows, frozenset())  # a new diff: cleared

    def test_the_menu_uses_the_line_selection_and_copies_it(self):
        self.select("a.txt")
        view = self.panel._unified
        chosen = [n for n, r in enumerate(view.rows) if r.text in ("line 5", "FIVE")]
        view.selection.set_rows(chosen)
        menu = self.menu(view, chosen[0])
        self.assertIn(("Copy", True), self.entries(menu))
        next(a for a in menu.actions() if a.text() == "Copy").trigger()
        self.assertEqual(QApplication.clipboard().text(), "line 5\nFIVE")
        self.trigger(menu, "Stage Selected Lines")
        self.assertIn("line 4\nFIVE\nline 6\n", self.index("a.txt"))

    def test_the_menu_uses_a_text_selection_when_no_line_is_selected(self):
        self.select("a.txt")
        view = self.panel._unified
        removed = next(n for n, r in enumerate(view.rows) if r.text == "line 5")
        # From inside "line 4" (context) to inside "- line 5".
        view.select_text(TextPos(removed - 1, 2), TextPos(removed, 2))
        menu = self.panel.menu_for(view, QPoint(30, 5))  # a right-click elsewhere
        self.addCleanup(menu.deleteLater)
        self.assertIn(("Copy", True), self.entries(menu))
        next(a for a in menu.actions() if a.text() == "Copy").trigger()
        self.assertEqual(QApplication.clipboard().text(), "ne 4\nli")
        self.trigger(menu, "Stage Selected Lines")
        self.assertIn("line 4\nline 6\n", self.index("a.txt"))

    def test_no_line_buttons_where_there_is_no_link(self):
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        self.ctx.selection.file = FileSelection("a.txt", FileSource.COMMIT, sha)
        wait_idle(self.session)
        self.assertFalse(self.panel._unified.gutter.buttons)

    def test_side_by_side_links_both_sides(self):
        self.ctx.actions.side_by_side_diff.setChecked(True)
        self.select("a.txt")
        left, right = self.panel._left, self.panel._right
        self.assertEqual((left._link, right._link), ("Stage Hunk", "Stage Hunk"))
        self.assertTrue(left.gutter.buttons and right.gutter.buttons)
        right.hunk_clicked.emit(0)
        wait_idle(self.session)
        self.assertIn("FIVE", self.index("a.txt"))


if __name__ == "__main__":
    unittest.main()


class DiffPanelWholeFileTests(unittest.TestCase):
    """Show Whole File: hunk headers, folding, keeping the place (RZG-60, spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        base = "".join(f"line {n}\n" for n in range(1, 101))
        commit_file(self.repo, "a.txt", base, "first")
        # Two changes, far apart: lines 30 and 70.
        changed = base.replace("line 30\n", "THIRTY\n").replace("line 70\n", "SEVENTY\n")
        (self.repo / "a.txt").write_text(changed)
        self.ctx = make_panel_context()
        self.actions = self.ctx.actions
        self.session = self.ctx.session
        self.session.open(self.repo)
        wait_idle(self.session)
        self.panel = DiffPanel(self.ctx)
        self.addCleanup(self.panel.deleteLater)
        self.panel.resize(700, 300)
        self.panel.show()
        self.select(A)

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    def settle(self):
        wait_idle(self.session)
        QCoreApplication.processEvents()

    def select(self, file):
        self.ctx.selection.file = file
        self.settle()

    def whole(self, on=True):
        self.actions.whole_file_diff.setChecked(on)
        self.settle()

    def view(self):
        return self.panel._unified

    def kinds(self, view=None):
        return [r.kind for r in (view or self.view()).rows]

    def test_it_shows_every_line_and_off_shows_the_hunks_again(self):
        hunks_rows = self.view().rows
        self.assertTrue(self.panel._headers.isHidden())
        self.whole()
        self.assertFalse(self.panel._headers.isHidden())
        self.assertTrue(self.session.diff_whole)
        rows = self.view().rows
        self.assertEqual([r.new_no for r in rows if r.new_no], list(range(1, 101)))
        # Slim headers by default: one empty header row above each change.
        headers = [n for n, r in enumerate(rows) if r.kind is RowKind.HUNK]
        self.assertEqual([rows[n].text for n in headers], ["", ""])
        self.assertEqual([rows[n + 1].text for n in headers], ["line 30", "line 70"])
        self.assertEqual(self.view()._link, "Stage Hunk")
        self.whole(False)
        self.assertTrue(self.panel._headers.isHidden())
        self.assertEqual(self.view().rows, hunks_rows)

    def test_hunk_headers_full_slim_and_off(self):
        self.whole()
        header_texts = {}
        for mode in HunkHeaders:
            self.actions.hunk_headers[mode].setChecked(True)
            header_texts[mode] = [r.text for r in self.view().rows if r.kind is RowKind.HUNK]
        self.assertEqual(header_texts[HunkHeaders.FULL],
                         [h.header for h in self.session.diff.hunks])
        self.assertEqual(header_texts[HunkHeaders.SLIM], ["", ""])
        self.assertEqual(header_texts[HunkHeaders.OFF], [])
        self.assertEqual([a.text() for a in self.panel._headers.menu().actions()],
                         ["&Full", "&Slim", "&Off"])

    def test_stage_hunk_stages_one_change_of_the_whole_file(self):
        self.whole()
        self.view().hunk_clicked.emit(1)
        self.settle()
        index = git(self.repo, "show", ":a.txt")
        self.assertIn("SEVENTY", index)
        self.assertNotIn("THIRTY", index)
        self.assertTrue(self.session.diff_whole)  # still whole after the reload
        self.assertEqual([r.new_no for r in self.view().rows if r.new_no], list(range(1, 101)))

    def visible(self, view, n):
        first = view.firstVisibleBlock().blockNumber()
        return first <= n < first + view.verticalScrollBar().pageStep()

    def test_toggling_keeps_the_selected_line_selected_and_in_view(self):
        view = self.view()
        added = next(n for n, r in enumerate(view.rows) if r.text == "SEVENTY")
        view.selection.set_rows([added])
        self.whole()
        rows = view.rows
        (n,) = view.selection.rows
        self.assertEqual((rows[n].kind, rows[n].text, rows[n].new_no),
                         (RowKind.ADDED, "SEVENTY", 70))
        self.assertGreater(n, view.verticalScrollBar().pageStep())  # it had to scroll
        self.assertTrue(self.visible(view, n))
        self.whole(False)
        (n,) = view.selection.rows
        self.assertEqual(view.rows[n].text, "SEVENTY")
        self.assertTrue(self.visible(view, n))

    def top(self, view):
        return view.rows[view.firstVisibleBlock().blockNumber()]

    def test_with_nothing_selected_the_top_line_stays(self):
        view = self.view()
        self.whole()
        view.verticalScrollBar().setValue(next(n for n, r in enumerate(view.rows)
                                               if r.new_no == 50))
        self.actions.hunk_headers[HunkHeaders.FULL].setChecked(True)
        self.assertEqual(self.top(view).new_no, 50)
        view.fold_clicked.emit((1, 1))  # above it
        self.assertEqual(self.top(view).new_no, 50)
        view.fold_clicked.emit((34, 34))  # around it: the fold row is at the top
        self.assertEqual(self.top(view).fold, (34, 34))
        view.fold_clicked.emit((34, 34))
        view.verticalScrollBar().setValue(next(n for n, r in enumerate(view.rows)
                                               if r.new_no == 68))
        self.whole(False)  # the hunks view is short: line 68 can't be at the top
        self.assertTrue(self.visible(view, next(n for n, r in enumerate(view.rows)
                                                if r.new_no == 68)))

    def fold_rows(self, view=None):
        return [r for r in (view or self.view()).rows if r.kind is RowKind.FOLD]

    def test_folding_a_stretch_and_unfolding_it(self):
        self.whole()
        view = self.view()
        self.assertEqual(sorted(view.fold_marks.values()), [(1, 1), (34, 34), (74, 74)])
        view.fold_clicked.emit((34, 34))
        self.assertEqual([r.text for r in self.fold_rows()], ["⋯ 33 unchanged lines"])
        self.assertNotIn((34, 34), view.fold_marks.values())
        # Folding is for viewing: Stage Hunk still stages the whole change block.
        view.fold_clicked.emit((34, 34))
        self.assertEqual(self.fold_rows(), [])

    def test_fold_all_and_unfold_all_from_the_menu(self):
        self.whole()
        view = self.view()
        menu = self.panel.menu_for(view, QPoint(30, 5))
        self.addCleanup(menu.deleteLater)
        entries = {a.text(): a for a in menu.actions()}
        self.assertTrue(entries["Fold All Unchanged"].isEnabled())
        self.assertFalse(entries["Unfold All"].isEnabled())
        entries["Fold All Unchanged"].trigger()
        self.assertEqual(len(self.fold_rows()), 3)
        self.assertEqual(len(view.rows), 100 + 2 + 2 - 26 - 33 - 27 + 3)
        menu = self.panel.menu_for(view, QPoint(30, 5))
        self.addCleanup(menu.deleteLater)
        entries = {a.text(): a for a in menu.actions()}
        self.assertFalse(entries["Fold All Unchanged"].isEnabled())
        entries["Unfold All"].trigger()
        self.assertEqual(self.fold_rows(), [])

    def test_the_hunks_view_has_no_fold_entries_or_marks(self):
        menu = self.panel.menu_for(self.view(), QPoint(30, 5))
        self.addCleanup(menu.deleteLater)
        self.assertNotIn("Fold All Unchanged", [a.text() for a in menu.actions()])
        self.assertEqual(self.view().fold_marks, {})

    def test_folds_survive_header_and_view_changes_and_a_new_diff_resets_them(self):
        self.whole()
        self.panel.fold_all()
        self.actions.hunk_headers[HunkHeaders.FULL].setChecked(True)
        self.assertEqual(len(self.fold_rows()), 3)
        self.actions.side_by_side_diff.setChecked(True)
        left, right = self.panel._left, self.panel._right
        self.assertEqual(len(self.fold_rows(left)), 3)
        self.assertEqual([n for n, r in enumerate(left.rows) if r.kind is RowKind.FOLD],
                         [n for n, r in enumerate(right.rows) if r.kind is RowKind.FOLD])
        self.actions.side_by_side_diff.setChecked(False)
        (self.repo / "a.txt").write_text((self.repo / "a.txt").read_text() + "more\n")
        self.session.refresh()
        self.settle()
        self.assertEqual(self.fold_rows(), [])

    def test_a_click_on_a_fold_mark_or_a_fold_row_folds_and_unfolds(self):
        self.whole()
        view = self.view()
        gutter = view.gutter
        view.verticalScrollBar().setValue(0)
        QCoreApplication.processEvents()
        (_n, _row, top, height), *_ = view.visible_rows(view.viewport().rect())
        mark = gutter.mark_rect(top, height).center()
        QTest.mouseClick(gutter, Qt.MouseButton.LeftButton, pos=mark)
        self.assertIs(view.rows[0].kind, RowKind.FOLD)
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(40, 2))
        self.assertEqual(self.fold_rows(), [])
        self.assertEqual(view.rows[0].new_no, 1)

    def test_the_fold_settings_apply_at_once(self):
        """Settings (spec §8.2): the folds start again when they change."""
        self.whole()
        view = self.view()
        self.panel.fold_all()
        self.ctx.prefs.set(diff_panel.FOLD_MIN, 40)  # no stretch is longer
        self.assertEqual((self.fold_rows(), view.fold_marks), ([], {}))
        self.ctx.prefs.set(diff_panel.FOLD_MIN, 20)
        self.ctx.prefs.set(diff_panel.FOLD_KEPT, 5)
        self.assertEqual(sorted(view.fold_marks.values()), [(1, 1), (36, 36), (76, 76)])
        self.ctx.prefs.set(diff_panel.FOLD_ON_OPEN, True)
        self.assertEqual(len(self.fold_rows()), 3)

    def test_a_whole_file_opens_folded_with_the_setting(self):
        self.ctx.prefs.set(diff_panel.FOLD_ON_OPEN, True)
        self.whole()
        self.assertEqual(len(self.fold_rows()), 3)
        self.panel.unfold_all()
        self.actions.side_by_side_diff.setChecked(True)  # not a new diff: stays unfolded
        self.assertEqual(self.fold_rows(self.panel._left), [])
        self.actions.side_by_side_diff.setChecked(False)
        self.whole(False)
        self.whole()  # a new diff
        self.assertEqual(len(self.fold_rows()), 3)

    def test_the_tab_width_setting(self):
        view = self.view()
        space = view.fontMetrics().horizontalAdvance(" ")
        self.assertEqual(view.tabStopDistance(), space * 4)
        self.ctx.prefs.set(diff_panel.TAB_SPACES, 8)
        self.assertEqual(view.tabStopDistance(), space * 8)
        self.actions.side_by_side_diff.setChecked(True)  # a view made later has it too
        self.assertEqual(self.panel._left.tabStopDistance(), space * 8)

    def test_a_new_large_diff_limit_reads_the_diff_again(self):
        self.whole()  # 100 lines
        self.assertIs(self.panel._stack.currentWidget(), self.view())
        self.ctx.prefs.set(diff_panel.LARGE_DIFF, 50)
        self.settle()
        self.assertIs(self.panel._stack.currentWidget(), self.panel._message_page)
        self.assertIn("Large diff", self.panel._message.text())
        self.ctx.prefs.set(diff_panel.LARGE_DIFF, 1000)
        self.settle()
        self.assertIs(self.panel._stack.currentWidget(), self.view())

    def test_the_context_lines_setting_reads_the_hunks_again_in_place(self):
        """RZG-97 (spec §8.2): more context at once, the selected line kept."""
        view = self.view()
        shown = lambda: [r.new_no for r in view.rows if r.new_no]
        self.assertEqual(shown(), [*range(27, 34), *range(67, 74)])
        thirty = next(n for n, r in enumerate(view.rows) if r.text == "THIRTY")
        view.selection.set_rows([thirty])
        self.ctx.prefs.set(diff_panel.CONTEXT_LINES, 10)
        self.settle()
        self.assertEqual(self.session.diff_context, 10)
        self.assertEqual(shown(), [*range(20, 41), *range(60, 81)])
        self.assertEqual([view.rows[n].text for n in view.selection.rows], ["THIRTY"])
        self.ctx.prefs.set(diff_panel.CONTEXT_LINES, 30)  # lines 31 to 69 split, 20 and 19
        self.settle()
        self.assertEqual(shown(), list(range(1, 101)))
        self.assertEqual(sum(r.kind is RowKind.HUNK for r in view.rows), 2)

    def test_the_whole_file_ignores_the_context_lines(self):
        self.whole()
        with mock.patch.object(self.session, "request_diff") as request:
            self.ctx.prefs.set(diff_panel.CONTEXT_LINES, 10)
        request.assert_not_called()
        self.assertIsNone(self.session.diff_context)
        self.whole(False)  # read at the new count
        self.assertEqual(self.session.diff_context, 10)

    def test_a_diff_read_at_another_count_isn_t_shown_as_current(self):
        self.assertTrue(self.panel._cached(A))
        self.ctx.prefs.set(diff_panel.CONTEXT_LINES, 10)  # asked for, not yet in
        self.assertFalse(self.panel._cached(A))
        self.settle()
        self.assertTrue(self.panel._cached(A))

    def test_a_whole_file_too_large_offers_the_changes_only(self):
        self.ctx.prefs.set(diff_panel.LARGE_DIFF, 50)  # the setting (spec §8.2)
        self.whole()
        self.assertEqual(self.panel._stack.currentWidget(), self.panel._message_page)
        self.assertFalse(self.panel._changes_only.isHidden())
        self.assertFalse(self.panel._show_anyway.isHidden())
        self.panel._changes_only.click()
        self.settle()
        self.assertFalse(self.actions.whole_file_diff.isChecked())
        self.assertIs(self.panel._stack.currentWidget(), self.view())

    def test_a_whole_added_file_still_shows_unified_side_by_side(self):
        """RZG-61's rule holds in the whole-file view."""
        (self.repo / "new.txt").write_text("".join(f"n {i}\n" for i in range(40)))
        self.session.refresh()
        self.settle()
        self.actions.side_by_side_diff.setChecked(True)
        self.whole()
        self.select(FileSelection("new.txt", FileSource.WORKTREE))
        self.assertIs(self.panel._stack.currentWidget(), self.view())
        self.assertEqual(self.panel._hint.text(), "Unified: whole file added")
        self.assertEqual(len([r for r in self.view().rows if r.kind is RowKind.ADDED]), 40)
