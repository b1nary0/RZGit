import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QToolBar, QToolButton

from rzgitclient.core.app_settings import AppSettings
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
        self.assertEqual(set(again.layout_host.panels), {"controls", "files", "history"})
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
                         ["&Layout", "&Panels", "", "&Reset Layout"])

    def test_reset_layout_shows_hidden_panels(self):
        window = self.start()
        window.layout_host.hide_panel("files")
        window.ctx.actions.reset_layout.trigger()
        self.assertEqual(set(window.layout_host.panels), {"controls", "branches", "files", "history"})
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
