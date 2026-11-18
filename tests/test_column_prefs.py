import tempfile
import unittest
from pathlib import Path

from rzgit.core.app_settings import AppSettings
from rzgit.core.column_prefs import ColumnPrefs
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

    def test_columns_hidden_at_first_until_the_user_chooses(self):
        """RZG-48: Worktrees' Branch and Path start hidden (spec §5.2)."""
        default = frozenset({"Branch", "Path"})
        prefs = ColumnPrefs(self.settings)
        self.assertEqual(prefs.hidden("worktrees", default), default)
        prefs.set_hidden("worktrees", "Branch", False, default)
        self.assertEqual(prefs.hidden("worktrees", default), {"Path"})
        prefs.set_hidden("worktrees", "Path", False, default)
        self.assertEqual(prefs.hidden("worktrees", default), frozenset())  # none, kept
        self.assertEqual(self.reopen().hidden("worktrees", default), frozenset())
        self.assertEqual(self.settings.column_prefs, {"worktrees": {"hidden": []}})

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

    def test_widths_are_kept_per_panel_and_survive_a_restart(self):
        prefs = ColumnPrefs(self.settings)
        prefs.set_width("stash", "Age", 60)
        prefs.set_width("stash", "Message", 400)
        prefs.set_hidden("stash", "Branch", True)
        self.assertEqual(self.reopen().widths("stash"), {"Age": 60, "Message": 400})
        self.assertEqual(self.reopen().hidden("stash"), {"Branch"})
        prefs.set_width("stash", "Age", None)
        self.assertEqual(self.reopen().widths("stash"), {"Message": 400})
        prefs.reset_widths("stash")
        self.assertEqual(self.reopen().widths("stash"), {})
        self.assertEqual(self.reopen().hidden("stash"), {"Branch"})
        self.assertEqual(prefs.widths("history"), {})

    def test_malformed_widths_are_dropped(self):
        self.settings.column_prefs = {"stash": {"widths": {"Age": "wide"}},
                                      "files": {"widths": {"Status": 40}}}
        with self.assertLogs("rzgit.core.column_prefs", "WARNING"):
            prefs = ColumnPrefs(self.settings)
        self.assertEqual(prefs.widths("stash"), {})
        self.assertEqual(prefs.widths("files"), {"Status": 40})

    def test_malformed_entries_are_dropped(self):
        self.settings.column_prefs = {"stash": {"hidden": ["Age"]}, "files": {"hidden": "Status"},
                                      "history": [1]}
        with self.assertLogs("rzgit.core.column_prefs", "WARNING"):
            prefs = ColumnPrefs(self.settings)
        self.assertEqual(prefs.hidden("stash"), {"Age"})
        self.assertEqual(prefs.hidden("files"), frozenset())
        self.assertEqual(prefs.hidden("history"), frozenset())


if __name__ == "__main__":
    unittest.main()
