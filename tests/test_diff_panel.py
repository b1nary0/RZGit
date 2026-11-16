import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QThread

from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.ui.panels import diff_panel
from rzgitclient.ui.panels.diff_panel import DiffPanel
from rzgitclient.ui.panels.diff_rows import RowKind
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from tests.support import commit_file, make_panel_context, make_repo, qt_app, wait_idle

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


if __name__ == "__main__":
    unittest.main()
