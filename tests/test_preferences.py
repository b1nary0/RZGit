import tempfile
import unittest
from pathlib import Path

from rzgit.core import preferences
from rzgit.core.app_settings import AppSettings
from rzgit.core.preferences import Preferences, Setting, declare, declared
from tests.support import qt_app

COUNT = Setting("test/count", 20, "Count", minimum=4, maximum=100)
RATIO = Setting("test/ratio", 0.0, "Ratio", minimum=0.0, maximum=10.0)
ON = Setting("test/on", False, "On")
NAME = Setting("test/name", "", "Name")
MODE = Setting("test/mode", "a", "Mode", choices=(("a", "A"), ("b", "B")))


class SettingTests(unittest.TestCase):
    """Checking a stored value (spec §8.2)."""

    def test_values_of_its_type_are_kept_and_clamped(self):
        self.assertEqual(COUNT.check(30), 30)
        self.assertEqual(COUNT.check("30"), 30)  # QSettings gives strings back
        self.assertEqual(COUNT.check(1), 4)
        self.assertEqual(COUNT.check(1000), 100)
        self.assertEqual(RATIO.check("2.5"), 2.5)
        self.assertEqual(RATIO.check(3), 3.0)
        self.assertIs(ON.check("true"), True)
        self.assertIs(ON.check(False), False)
        self.assertEqual(NAME.check("kitty"), "kitty")
        self.assertEqual(MODE.check("b"), "b")

    def test_other_values_arent_valid(self):
        for setting, value in ((COUNT, "x"), (COUNT, 2.5), (COUNT, True), (COUNT, "2.5"),
                               (RATIO, "x"), (RATIO, True), (ON, "yes"), (ON, 1),
                               (NAME, 3), (MODE, "c"), (COUNT, None)):
            with self.subTest(setting=setting.key, value=value):
                self.assertIsNone(setting.check(value))


class DeclareTests(unittest.TestCase):
    def setUp(self):
        saved = dict(preferences._DECLARED)
        self.addCleanup(lambda: (preferences._DECLARED.clear(),
                                 preferences._DECLARED.update(saved)))

    def test_declared_in_order_and_once(self):
        first = declare(Setting("test/first", 1, "First"))
        second = declare(Setting("test/second", 2, "Second"))
        self.assertEqual(declared()[-2:], (first, second))
        with self.assertRaises(ValueError):
            declare(Setting("test/first", 3, "Again"))

    def test_the_default_must_be_valid(self):
        with self.assertRaises(ValueError):
            declare(Setting("test/bad", 1, "Bad", minimum=4))

    def test_the_apps_settings_are_declared_on_import(self):
        import rzgit.ui.panels.diff_panel  # noqa: F401
        import rzgit.ui.panels.history_model  # noqa: F401
        import rzgit.ui.theme.theme_manager  # noqa: F401
        import rzgit.ui.worktree_dialogs  # noqa: F401
        pages = {s.key: s.page for s in declared()}
        self.assertEqual(pages["diff/fold_min_lines"], "Diff")
        self.assertEqual(pages["history/date_format"], "History")
        self.assertEqual(pages["appearance/mono_family"], "Appearance")
        self.assertEqual(pages["tools/terminal"], "Tools")


class PreferencesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "settings.ini"
        self.settings = AppSettings(self.path)
        self.prefs = Preferences(self.settings)
        self.changes = []
        self.prefs.changed.connect(lambda key, value: self.changes.append((key, value)))

    def test_defaults_until_set(self):
        self.assertEqual(self.prefs.value(COUNT), 20)
        self.assertEqual(self.prefs.value(MODE), "a")

    def test_set_saves_at_once_and_emits_once(self):
        self.prefs.set(COUNT, 30)
        self.prefs.set(COUNT, 30)
        self.assertEqual(self.changes, [("test/count", 30)])
        self.settings.sync()
        self.assertEqual(Preferences(AppSettings(self.path)).value(COUNT), 30)

    def test_set_checks_the_value(self):
        self.prefs.set(COUNT, 500)
        self.assertEqual(self.prefs.value(COUNT), 100)
        with self.assertLogs("rzgit.core.preferences", "WARNING"):
            self.prefs.set(MODE, "zzz")
        self.assertEqual(self.prefs.value(MODE), "a")
        self.assertEqual(self.changes, [("test/count", 100)])

    def test_the_default_is_stored_as_no_value(self):
        self.prefs.set(ON, True)
        self.assertIn(self.settings.pref("test/on"), (True, "true"))
        self.prefs.set(ON, False)
        self.assertIsNone(self.settings.pref("test/on"))

    def test_a_bad_stored_value_reads_as_the_default(self):
        self.settings.set_pref("test/count", "lots")
        self.settings.set_pref("test/mode", "c")
        prefs = Preferences(self.settings)
        with self.assertLogs("rzgit.core.preferences", "WARNING"):
            self.assertEqual(prefs.value(COUNT), 20)
            self.assertEqual(prefs.value(MODE), "a")

    def test_stored_apart_from_the_layouts(self):
        self.prefs.set(NAME, "kitty")
        self.settings.sync()
        self.assertIn("[prefs]", self.path.read_text())
        self.assertIsNone(self.settings.layout)

    def test_reset(self):
        self.prefs.set(COUNT, 30)
        self.prefs.set(ON, True)
        self.changes.clear()
        self.prefs.reset([COUNT, ON, NAME])
        self.assertEqual((self.prefs.value(COUNT), self.prefs.value(ON)), (20, False))
        self.assertEqual(self.changes, [("test/count", 20), ("test/on", False)])

    def test_in_memory_without_settings(self):
        prefs = Preferences()
        prefs.set(COUNT, 50)
        self.assertEqual(prefs.value(COUNT), 50)


if __name__ == "__main__":
    unittest.main()
