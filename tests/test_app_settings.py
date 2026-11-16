import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QByteArray

from rzgitclient.core.app_settings import AppSettings
from tests.support import qt_app


class AppSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "settings.ini"
        self.settings = AppSettings(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def reopen(self):
        self.settings.sync()
        return AppSettings(self.path)

    def test_empty_by_default(self):
        self.assertIsNone(self.settings.last_repo)
        self.assertIsNone(self.settings.layout)
        self.assertIsNone(self.settings.window_geometry)

    def test_values_survive_a_reopen(self):
        layout = {"version": 1, "root": {"type": "panel", "panel_id": "history"}}
        self.settings.last_repo = "/repos/one"
        self.settings.layout = layout
        self.settings.window_geometry = QByteArray(b"\x01\x02geometry")
        again = self.reopen()
        self.assertEqual(again.last_repo, "/repos/one")
        self.assertEqual(again.layout, layout)
        self.assertEqual(bytes(again.window_geometry), b"\x01\x02geometry")
        self.assertTrue(self.path.exists())  # really the given file

    def test_side_by_side_diff_defaults_to_off_and_survives_a_reopen(self):
        self.assertFalse(self.settings.side_by_side_diff)
        self.settings.side_by_side_diff = True
        self.assertTrue(self.reopen().side_by_side_diff)

    def test_none_removes_a_value(self):
        self.settings.last_repo = "/repos/one"
        self.settings.layout = {"version": 1}
        self.settings.last_repo = None
        self.settings.layout = None
        again = self.reopen()
        self.assertIsNone(again.last_repo)
        self.assertIsNone(again.layout)

    def test_corrupt_layout_is_ignored_with_a_warning(self):
        self.path.write_text('[General]\nlayout="{not json"\n')
        with self.assertLogs("rzgitclient.core.app_settings", "WARNING"):
            self.assertIsNone(AppSettings(self.path).layout)

    def test_layout_that_is_not_an_object_is_ignored(self):
        self.settings._settings.setValue("layout", "[1, 2]")
        self.assertIsNone(self.settings.layout)


if __name__ == "__main__":
    unittest.main()
