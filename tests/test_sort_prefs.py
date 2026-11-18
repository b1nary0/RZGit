import tempfile
import unittest
from pathlib import Path

from rzgit.core.app_settings import AppSettings
from rzgit.core.sort_options import SortOrder
from rzgit.core.sort_prefs import SortPrefs
from tests.support import qt_app


class SortPrefsTests(unittest.TestCase):
    """Each panel's sort order, saved at once (spec §6.3, RZG-43)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "settings.ini"
        self.settings = AppSettings(self.path)

    def reopen(self) -> SortPrefs:
        self.settings.sync()
        return SortPrefs(AppSettings(self.path))

    def test_none_until_chosen(self):
        self.assertIsNone(SortPrefs().order("stash"))
        self.assertIsNone(SortPrefs(self.settings).order("stash"))

    def test_an_order_is_saved_at_once_and_read_back(self):
        prefs = SortPrefs(self.settings)
        prefs.set_order("stash", SortOrder("branch", True))
        prefs.set_order("files", SortOrder("status"))
        self.assertEqual(self.settings.sort_prefs,
                         {"files": {"by": "status", "reversed": False},
                          "stash": {"by": "branch", "reversed": True}})
        again = self.reopen()
        self.assertEqual(again.order("stash"), SortOrder("branch", True))
        self.assertEqual(again.order("files"), SortOrder("status"))

    def test_none_forgets_it(self):
        prefs = SortPrefs(self.settings)
        prefs.set_order("stash", SortOrder("branch"))
        prefs.set_order("stash", None)
        prefs.set_order("branches", None)  # never set: nothing to forget
        self.assertIsNone(self.reopen().order("stash"))
        self.assertEqual(self.settings.sort_prefs, {})

    def test_malformed_entries_are_dropped(self):
        self.settings.sort_prefs = {"stash": {"by": "age", "reversed": "yes"}, "files": [1],
                                    "branches": {"by": ""}, "worktrees": {"by": "name"}}
        with self.assertLogs("rzgit.core.sort_prefs", "WARNING") as logs:
            prefs = SortPrefs(self.settings)
        self.assertEqual(len(logs.records), 3)
        self.assertIsNone(prefs.order("stash"))
        self.assertIsNone(prefs.order("files"))
        self.assertEqual(prefs.order("worktrees"), SortOrder("name"))  # reversed defaults


    def test_the_grouping_is_saved_in_the_same_entry(self):
        """RZG-53: "group" is left out when there is none."""
        prefs = SortPrefs(self.settings)
        prefs.set_order("stash", SortOrder("age", group="branch"))
        prefs.set_order("files", SortOrder("status"))
        self.assertEqual(self.settings.sort_prefs,
                         {"files": {"by": "status", "reversed": False},
                          "stash": {"by": "age", "reversed": False, "group": "branch"}})
        again = self.reopen()
        self.assertEqual(again.order("stash"), SortOrder("age", group="branch"))
        self.assertIsNone(again.order("files").group)

    def test_a_malformed_grouping_drops_the_entry(self):
        self.settings.sort_prefs = {"stash": {"by": "age", "group": 3},
                                    "files": {"by": "name", "group": ""},
                                    "branches": {"by": "name", "group": None}}
        with self.assertLogs("rzgit.core.sort_prefs", "WARNING") as logs:
            prefs = SortPrefs(self.settings)
        self.assertEqual(len(logs.records), 2)
        self.assertIsNone(prefs.order("stash"))
        self.assertIsNone(prefs.order("files"))
        self.assertEqual(prefs.order("branches"), SortOrder("name"))

if __name__ == "__main__":
    unittest.main()
