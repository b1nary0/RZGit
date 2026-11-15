import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.ui.layout.layout_model import SplitNode
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT
from rzgitclient.ui.main_window import MainWindow
from tests.support import commit_file, make_panel_context, make_repo, qt_app


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
        self.tmp = Path(self._tmp.name)
        self.settings_path = self.tmp / "settings.ini"

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self._tmp.cleanup()

    def start(self):
        window = MainWindow(make_panel_context(), AppSettings(self.settings_path))
        window.show()
        self.app.processEvents()
        self.addCleanup(window.deleteLater)
        return window

    def quit(self, window):
        window.close()
        window.settings.sync()

    def sizes(self, window):
        root = window.layout_host.current_layout()
        return root.sizes, root.children[0].sizes

    def test_first_start_uses_the_default_layout(self):
        window = self.start()
        self.assertEqual(strip_sizes(window.layout_host.current_layout()), strip_sizes(DEFAULT_LAYOUT))

    def test_layout_and_window_size_come_back_after_a_restart(self):
        window = self.start()
        window.resize(700, 500)  # fits the 800x800 offscreen screen; larger is clamped
        self.app.processEvents()
        root = window.layout_host.layout().itemAt(0).widget()
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
        root = window.layout_host.layout().itemAt(0).widget()
        root.setSizes([500, 599])
        window.ctx.actions.reset_layout.trigger()
        self.app.processEvents()
        self.assertIs(window.layout_host.panels["history"], history)
        read = window.layout_host.current_layout()
        self.assertEqual(strip_sizes(read), strip_sizes(DEFAULT_LAYOUT))
        self.assertNotEqual(read.sizes[0], 500)

    def test_opened_repo_is_remembered(self):
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "1", "first")
        window = self.start()
        window.session.open(repo)
        self.quit(window)
        self.assertEqual(
            Path(AppSettings(self.settings_path).last_repo).resolve(), repo.resolve()
        )


if __name__ == "__main__":
    unittest.main()
