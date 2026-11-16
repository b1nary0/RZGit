import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from rzgitclient.core.diff_patch import hunk_lines
from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.ui.panels import diff_panel
from rzgitclient.ui.panels.diff_panel import DiffPanel
from rzgitclient.ui.panels.diff_rows import RowKind, unified_rows
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
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
        with mock.patch("rzgitclient.core.diff_request.DIFF_MAX_LINES", 50):
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
            ("Discard Hunk…", True), ("Discard Selected Lines…", True), ("Copy", False)])

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
                         ["Unstage Hunk", "Unstage Selected Lines", "Copy"])
        self.trigger(menu, "Unstage Hunk")
        self.assertNotIn("FIVE", self.index("a.txt"))
        self.assertIn("NINETY", self.index("a.txt"))

    def test_an_untracked_file_can_be_staged_in_part_but_not_discarded(self):
        self.select("new.txt")
        view = self.panel._unified
        menu = self.menu(view, 1)
        self.assertEqual([t for t, _ in self.entries(menu)],
                         ["Stage Hunk", "Stage Selected Lines", "Copy"])
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
        self.assertEqual(self.entries(self.menu(view, 1)), [("Copy", False)])

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
