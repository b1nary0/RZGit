import tempfile
import unittest
from pathlib import Path

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.core.column_prefs import ColumnPrefs
from tests.support import qt_app


class ColumnPrefsTests(unittest.TestCase):
    """Each panel's column choices (spec §6.3, RZG-41)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "settings.ini"
        self.settings = AppSettings(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def reopen(self) -> ColumnPrefs:
        self.settings.sync()
        return ColumnPrefs(AppSettings(self.path))

    def test_nothing_hidden_by_default(self):
        self.assertEqual(ColumnPrefs().hidden("stash"), frozenset())

    def test_hidden_columns_are_kept_per_panel(self):
        prefs = ColumnPrefs()
        prefs.set_hidden("stash", "Branch", True)
        prefs.set_hidden("history", "Author", True)
        self.assertEqual(prefs.hidden("stash"), {"Branch"})
        self.assertEqual(prefs.hidden("history"), {"Author"})
        prefs.set_hidden("stash", "Branch", False)
        self.assertEqual(prefs.hidden("stash"), frozenset())

    def test_choices_survive_a_restart(self):
        prefs = ColumnPrefs(self.settings)
        prefs.set_hidden("stash", "Age", True)
        prefs.set_hidden("files", "Status", True)
        self.assertEqual(self.reopen().hidden("stash"), {"Age"})
        self.assertEqual(self.reopen().hidden("files"), {"Status"})
        prefs.set_hidden("stash", "Age", False)
        prefs.set_hidden("files", "Status", False)
        self.assertEqual(self.settings.column_prefs, {})

    def test_malformed_entries_are_dropped(self):
        self.settings.column_prefs = {"stash": {"hidden": ["Age"]}, "files": {"hidden": "Status"},
                                      "history": [1]}
        with self.assertLogs("rzgitclient.core.column_prefs", "WARNING"):
            prefs = ColumnPrefs(self.settings)
        self.assertEqual(prefs.hidden("stash"), {"Age"})
        self.assertEqual(prefs.hidden("files"), frozenset())
        self.assertEqual(prefs.hidden("history"), frozenset())


if __name__ == "__main__":
    unittest.main()
