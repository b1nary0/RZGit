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

    def test_last_repo_is_saved_as_its_canonical_path(self):
        real = Path(self._tmp.name).resolve() / "repo"
        real.mkdir()
        (real.parent / "link").symlink_to(real)
        self.settings.last_repo = real.parent / "link"
        self.assertEqual(self.reopen().last_repo, str(real))
        self.settings.last_repo = None
        self.assertIsNone(self.reopen().last_repo)

    def test_side_by_side_diff_defaults_to_off_and_survives_a_reopen(self):
        self.assertFalse(self.settings.side_by_side_diff)
        self.settings.side_by_side_diff = True
        self.assertTrue(self.reopen().side_by_side_diff)

    def test_theme_mode_defaults_to_system_and_survives_a_reopen(self):
        self.assertEqual(self.settings.theme_mode, "system")
        self.settings.theme_mode = "dark"
        self.assertEqual(self.reopen().theme_mode, "dark")

    def test_colour_blind_defaults_to_off_and_survives_a_reopen(self):
        self.assertFalse(self.settings.colour_blind)
        self.settings.colour_blind = True
        self.assertTrue(self.reopen().colour_blind)

    def test_an_unknown_theme_mode_reads_as_system(self):
        self.settings.theme_mode = "purple"
        self.assertEqual(self.reopen().theme_mode, "system")

    def test_topo_order_defaults_to_off_and_survives_a_reopen(self):
        self.assertFalse(self.settings.topo_order)
        self.settings.topo_order = True
        self.assertTrue(self.reopen().topo_order)

    def test_file_tree_defaults_to_on_and_survives_a_reopen(self):
        self.assertTrue(self.settings.file_tree)
        self.settings.file_tree = False
        self.assertFalse(self.reopen().file_tree)

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


    def test_old_commit_drafts_are_read_until_dropped(self):
        self.assertEqual(self.settings.old_commit_drafts, {})
        self.settings._settings.setValue("commit/drafts", '{"/repos/one": "Fix\\n\\nBody"}')
        self.assertEqual(self.reopen().old_commit_drafts, {"/repos/one": "Fix\n\nBody"})
        self.settings.drop_old_commit_drafts()
        self.assertEqual(self.reopen().old_commit_drafts, {})
        self.assertFalse(self.reopen()._settings.contains("commit/drafts"))

    def test_old_drafts_that_are_not_valid_are_ignored(self):
        self.settings._settings.setValue("commit/drafts", "{not json")
        with self.assertLogs("rzgitclient.core.app_settings", "WARNING"):
            self.assertEqual(self.settings.old_commit_drafts, {})
        self.settings._settings.setValue("commit/drafts", "[1, 2]")
        self.assertEqual(self.settings.old_commit_drafts, {})
        self.settings._settings.setValue("commit/drafts", '{"/a": 3, "/b": "ok"}')
        self.assertEqual(self.settings.old_commit_drafts, {"/b": "ok"})

    def test_drafts_are_not_settings(self):
        """Only the move from older versions reads drafts here (spec §6.3)."""
        self.assertFalse(hasattr(AppSettings, "commit_drafts"))

    def test_column_prefs_survive_a_reopen(self):
        self.assertEqual(self.settings.column_prefs, {})
        self.settings.column_prefs = {"stash": {"hidden": ["Age"]}}
        self.assertEqual(self.reopen().column_prefs, {"stash": {"hidden": ["Age"]}})
        self.settings.column_prefs = {}
        self.assertEqual(self.reopen().column_prefs, {})

    def test_column_prefs_that_are_not_valid_are_ignored(self):
        self.settings._settings.setValue("columns/prefs", "{not json")
        with self.assertLogs("rzgitclient.core.app_settings", "WARNING"):
            self.assertEqual(self.settings.column_prefs, {})
        self.settings._settings.setValue("columns/prefs", "[1, 2]")
        self.assertEqual(self.settings.column_prefs, {})

    def test_layout_preset_survives_a_reopen(self):
        self.assertIsNone(self.settings.layout_preset)
        self.settings.layout_preset = "Standard"
        self.assertEqual(self.reopen().layout_preset, "Standard")
        self.settings.layout_preset = None
        self.assertIsNone(self.reopen().layout_preset)

    def test_saved_layouts_survive_a_reopen_in_order(self):
        self.assertEqual(self.settings.saved_layouts, [])
        layouts = [("Wide Diff", {"version": 2, "root": {}}), ("Review", {"version": 2})]
        self.settings.saved_layouts = layouts
        self.assertEqual(self.reopen().saved_layouts, layouts)
        self.settings.saved_layouts = []
        self.assertEqual(self.reopen().saved_layouts, [])

    def test_saved_layouts_that_are_not_valid_are_dropped_with_a_warning(self):
        self.settings._settings.setValue("layouts/saved", "[not json")
        with self.assertLogs("rzgitclient.core.app_settings", "WARNING"):
            self.assertEqual(self.settings.saved_layouts, [])
        self.settings._settings.setValue("layouts/saved", '{"name": "x"}')
        with self.assertLogs("rzgitclient.core.app_settings", "WARNING"):
            self.assertEqual(self.settings.saved_layouts, [])
        self.settings._settings.setValue(
            "layouts/saved",
            '[{"name": "ok", "layout": {}}, {"name": "", "layout": {}}, '
            '{"name": "x", "layout": [1]}, 3, {"layout": {}}]')
        with self.assertLogs("rzgitclient.core.app_settings", "WARNING") as logs:
            self.assertEqual(self.settings.saved_layouts, [("ok", {})])
        self.assertEqual(len(logs.records), 4)


if __name__ == "__main__":
    unittest.main()
