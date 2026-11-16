import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QToolBar, QToolButton

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.ui.layout.layout_model import SplitNode
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT
from rzgitclient.ui.main_window import MainWindow, commit_summary
from tests.support import commit_file, make_panel_context, make_repo, qt_app, wait_idle


def strip_sizes(node):
    if isinstance(node, SplitNode):
        return SplitNode(node.orientation, tuple(strip_sizes(c) for c in node.children))
    return node


class MainWindowTests(unittest.TestCase):
    """Startup and shutdown persistence (spec §6.3)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)  # last: after every window is gone
        self.tmp = Path(self._tmp.name)
        self.settings_path = self.tmp / "settings.ini"

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def start(self):
        window = MainWindow(make_panel_context(), AppSettings(self.settings_path))
        window.show()
        self.app.processEvents()
        self.addCleanup(window.deleteLater)
        # Runs first: no late git result may reach a window being deleted.
        self.addCleanup(window.session.shutdown)
        return window

    def quit(self, window):
        window.close()
        window.settings.sync()

    def sizes(self, window):
        body = window.layout_host.current_layout().children[1]  # below the Controls bar
        return body.sizes, body.children[0].sizes

    def body(self, window):
        return window.layout_host.layout().itemAt(0).widget().widget(1)

    def test_first_start_uses_the_default_layout(self):
        window = self.start()
        self.assertEqual(strip_sizes(window.layout_host.current_layout()), strip_sizes(DEFAULT_LAYOUT))

    def test_layout_and_window_size_come_back_after_a_restart(self):
        window = self.start()
        window.resize(700, 500)  # fits the 800x800 offscreen screen; larger is clamped
        self.app.processEvents()
        root = self.body(window)
        root.setSizes([250, 449])
        root.widget(0).setSizes([150, 200])
        before = self.sizes(window)
        self.quit(window)

        again = self.start()
        self.assertEqual((again.width(), again.height()), (700, 500))
        self.assertEqual(self.sizes(again), before)

    def test_corrupt_saved_layout_starts_with_the_default(self):
        self.settings_path.write_text('[General]\nlayout="{broken"\n')
        with self.assertLogs("rzgitclient.core.app_settings", "WARNING"):
            window = self.start()
        self.assertEqual(strip_sizes(window.layout_host.current_layout()), strip_sizes(DEFAULT_LAYOUT))

    def test_reset_layout_restores_the_default_and_keeps_panels(self):
        window = self.start()
        history = window.layout_host.panels["history"]
        self.body(window).setSizes([500, 599])
        window.ctx.actions.reset_layout.trigger()
        self.app.processEvents()
        self.assertIs(window.layout_host.panels["history"], history)
        read = window.layout_host.current_layout()
        self.assertEqual(strip_sizes(read), strip_sizes(DEFAULT_LAYOUT))
        self.assertNotEqual(read.children[1].sizes[0], 500)

    def test_a_hidden_panel_stays_hidden_after_a_restart(self):
        window = self.start()
        window.layout_host.hide_panel("branches")
        self.quit(window)

        again = self.start()
        self.assertEqual(set(again.layout_host.panels), {"controls", "files", "history", "diff", "staged", "commit", "commit_files"})
        again.layout_host.show_panel("branches")  # and comes back where it was
        self.assertEqual(strip_sizes(again.layout_host.current_layout()), strip_sizes(DEFAULT_LAYOUT))

    def test_no_toolbar_the_controls_panel_has_its_buttons(self):
        window = self.start()
        self.assertEqual(window.findChildren(QToolBar), [])
        buttons = window.layout_host.panels["controls"].findChildren(QToolButton)
        actions = window.ctx.actions
        self.assertEqual([b.defaultAction() for b in buttons], [actions.open_repo, actions.refresh])

    def test_view_menu_has_layout_panels_and_reset(self):
        window = self.start()
        view = next(a.menu() for a in window.menuBar().actions() if a.text() == "&View")
        self.assertEqual([a.text() for a in view.actions()],
                         ["&Layout", "&Panels", "", "&Side by Side Diff", "", "&Reset Layout"])

    def test_a_saved_layout_survives_a_restart(self):
        window = self.start()
        window.layout_host.hide_panel("branches")
        layout_menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "&View"
                           ).actions()[0].menu()
        self.assertTrue(layout_menu.save("Review"))
        window.ctx.actions.reset_layout.trigger()
        self.quit(window)

        again = self.start()
        layout_menu = next(a.menu() for a in again.menuBar().actions() if a.text() == "&View"
                           ).actions()[0].menu()
        self.assertEqual(again.layout_host.hidden, {})
        next(a for a in layout_menu.actions() if a.text() == "Review").trigger()
        self.assertEqual(set(again.layout_host.hidden), {"branches"})

    def test_a_commit_s_file_shows_its_diff_from_history_to_diff(self):
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "1\n", "first")
        commit_file(repo, "a.txt", "2\n", "second")
        window = self.start()
        window.session.open(repo)
        wait_idle(window.session)
        panels = window.layout_host.panels
        self.assertTrue(panels["commit_files"].collapsed)  # Uncommitted changes
        sidebar = panels["branches"].parentWidget().parentWidget()
        branches_height = sidebar.sizes()[0]
        history = panels["history"]
        history._view.setCurrentIndex(history._model.index(2, 0))  # "first"
        wait_idle(window.session)
        self.app.processEvents()
        first = window.ctx.selection.commit
        self.assertEqual(first.subject, "first")
        # Commit Files takes the place of the working tree's panels.
        self.assertEqual([pid for pid in ("files", "staged", "commit", "commit_files")
                          if not panels[pid].collapsed], ["commit_files"])
        self.assertTrue(panels["commit_files"].isVisible())
        self.assertFalse(panels["files"].isVisible())
        self.assertEqual(sidebar.sizes()[0], branches_height)
        tree = panels["commit_files"]._list._tree
        tree.setCurrentItem(tree.topLevelItem(0))
        wait_idle(window.session)
        self.assertEqual(window.session.diff_file,
                         FileSelection("a.txt", FileSource.COMMIT, first.sha))
        self.assertEqual([l.text for h in window.session.diff.hunks for l in h.lines], ["1"])
        history._view.setCurrentIndex(history._model.index(0, 0))  # back to Uncommitted
        self.app.processEvents()
        self.assertTrue(panels["commit_files"].collapsed)
        self.assertTrue(panels["files"].isVisible())
        self.assertEqual(sidebar.sizes()[0], branches_height)
        self.assertIsNone(window.ctx.selection.file)

    def test_a_layout_saved_while_a_commit_is_selected_is_the_working_tree_s(self):
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "1\n", "first")
        window = self.start()
        window.session.open(repo)
        wait_idle(window.session)
        sidebar = window.layout_host.panels["branches"].parentWidget().parentWidget()
        working = sidebar.sizes()
        history = window.layout_host.panels["history"]
        history._view.setCurrentIndex(history._model.index(1, 0))
        wait_idle(window.session)
        self.app.processEvents()
        self.assertTrue(window.layout_host.panels["files"].collapsed)
        self.quit(window)

        again = self.start()
        again.session.open(repo)
        wait_idle(again.session)
        self.app.processEvents()
        sidebar = again.layout_host.panels["branches"].parentWidget().parentWidget()
        self.assertEqual(sidebar.sizes(), working)

    def test_side_by_side_diff_is_remembered(self):
        window = self.start()
        self.assertFalse(window.ctx.actions.side_by_side_diff.isChecked())
        window.ctx.actions.side_by_side_diff.setChecked(True)
        self.quit(window)
        again = self.start()
        self.assertTrue(again.ctx.actions.side_by_side_diff.isChecked())
        diff = again.layout_host.panels["diff"]
        self.assertIs(diff._stack.currentWidget(), diff._message_page)  # no file yet

    def test_repository_menu_has_the_staging_and_commit_actions(self):
        window = self.start()
        menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "&Repository")
        actions = window.ctx.actions
        self.assertEqual([a for a in menu.actions() if not a.isSeparator()],
                         [actions.stage, actions.unstage, actions.discard,
                          actions.stage_all, actions.unstage_all, actions.commit])

    def test_discard_asks_first_and_acts_only_on_unstaged_rows(self):
        window = self.start()
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "1", "first")
        (repo / "a.txt").write_text("2")
        window.session.open(repo)
        wait_idle(window.session)
        window.ctx.selection.files = (FileSelection("a.txt", FileSource.WORKTREE),
                                      FileSelection("b.txt", FileSource.INDEX))
        with mock.patch.object(window, "confirm_discard", return_value=False) as confirm, \
                mock.patch.object(window.session, "discard") as discard:
            window.ctx.actions.discard.trigger()
            confirm.assert_called_once_with(("a.txt",))
            discard.assert_not_called()
            confirm.return_value = True
            window.ctx.actions.discard.trigger()
            discard.assert_called_once_with(("a.txt",))

    def test_the_discard_confirmation_names_the_files_and_defaults_to_cancel(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QMessageBox
        window = self.start()
        paths = tuple(f"f{n}.txt" for n in range(12))
        seen = {}

        def answer(discard):
            box = QApplication.activeModalWidget()
            try:
                seen["text"], seen["info"] = box.text(), box.informativeText()
                seen["default_is_cancel"] = (
                    box.defaultButton() is box.button(QMessageBox.StandardButton.Cancel))
                if discard:
                    next(b for b in box.buttons()
                         if box.buttonRole(b) is QMessageBox.ButtonRole.DestructiveRole).click()
            finally:
                if box.isVisible():
                    box.reject()  # never leave the test waiting on a dialog

        QTimer.singleShot(0, lambda: answer(False))
        self.assertFalse(window.confirm_discard(paths))
        self.assertEqual(seen["text"], "Discard the unstaged changes to 12 files?")
        self.assertIn("f9.txt\n… and 2 more", seen["info"])
        self.assertIn("trash", seen["info"])
        self.assertTrue(seen["default_is_cancel"])
        QTimer.singleShot(0, lambda: answer(True))
        self.assertTrue(window.confirm_discard(paths[:1]))

    def test_reset_layout_shows_hidden_panels(self):
        window = self.start()
        window.layout_host.hide_panel("files")
        window.ctx.actions.reset_layout.trigger()
        self.assertEqual(set(window.layout_host.panels), {"controls", "branches", "files", "history", "diff", "staged", "commit", "commit_files"})
        self.assertEqual(window.layout_host.hidden, {})

    def test_status_bar_counts_every_commit_not_just_the_loaded_page(self):
        repo = make_repo(self.tmp / "repo")
        for n in range(1, 6):
            commit_file(repo, "a.txt", str(n), f"c{n}")
        with mock.patch("rzgitclient.core.repo_session.HISTORY_PAGE", 2):
            window = self.start()
            window.session.open(repo)
            wait_idle(window.session)
        self.assertEqual(len(window.session.history), 2)  # one page loaded…
        self.assertIn("  ·  5 commits  ·  ", window.statusBar().currentMessage())  # …all counted

    def test_opened_repo_is_remembered(self):
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "1", "first")
        window = self.start()
        window.session.open(repo)
        wait_idle(window.session)
        self.quit(window)
        self.assertEqual(
            Path(AppSettings(self.settings_path).last_repo).resolve(), repo.resolve()
        )


    def test_loading_label_shows_while_git_is_busy(self):
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "1", "first")
        window = self.start()
        label = window._busy_label
        self.assertFalse(label.isVisible())
        window.session.open(repo)
        self.assertTrue(label.isVisible())
        self.assertEqual(label.text(), "Loading…")
        wait_idle(window.session)
        self.assertFalse(label.isVisible())
        self.assertIn("1 commit ", window.statusBar().currentMessage())  # summary kept



class CommitSummaryTests(unittest.TestCase):
    def test_exact_count_with_separators_and_singular(self):
        self.assertEqual(commit_summary(500, False, 2421), "2,421 commits")
        self.assertEqual(commit_summary(1, True, 1), "1 commit")
        self.assertEqual(commit_summary(0, True, 0), "0 commits")

    def test_until_counted_the_loaded_count_with_a_plus_if_more_exist(self):
        self.assertEqual(commit_summary(500, False, None), "500+ commits")
        self.assertEqual(commit_summary(2421, True, None), "2,421 commits")
        self.assertEqual(commit_summary(1, True, None), "1 commit")


if __name__ == "__main__":
    unittest.main()
