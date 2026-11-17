import tempfile
import unittest
from pathlib import Path
from unittest import mock

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialogButtonBox,
                               QDoubleSpinBox, QLineEdit, QSpinBox)

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.core.column_prefs import ColumnPrefs
from rzgitclient.core.commit_drafts import CommitDrafts
from rzgitclient.core.preferences import Preferences, Setting
from rzgitclient.core.sort_prefs import SortPrefs
from rzgitclient.ui.app_windows import AppWindows
from rzgitclient.ui.main_window import MainWindow
from rzgitclient.ui.panels.diff_panel import FOLD_MIN, TAB_SPACES
from rzgitclient.ui import settings_window
from rzgitclient.ui.settings_window import APPEARANCE, Restore, SettingsWindow
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from rzgitclient.ui.theme.theme_manager import ThemeManager, ThemeMode
from tests.support import make_panel_context, qt_app

COUNT = Setting("test/count", 20, "Count", page="Diff", minimum=4, maximum=100)
SIZE = Setting("test/size", 0.0, "Size", page=APPEARANCE, minimum=0.0, maximum=40.0,
               step=0.5, editor="font_size")
ON = Setting("test/on", False, "On", page="Diff")
NAME = Setting("test/name", "", "Name", page="Tools", special="Automatic", help="Its help")
MODE = Setting("test/mode", "a", "Mode", page="History", choices=(("a", "A"), ("b", "B")))
FONT = Setting("test/font", "", "Font", page=APPEARANCE, editor="font")
OTHER = Setting("test/other", 1, "Other", page="Zebra")
ALL = (OTHER, COUNT, SIZE, ON, NAME, MODE, FONT)


class SettingsWindowTests(unittest.TestCase):
    """The Settings window (spec §8.2, RZG-68)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.prefs = Preferences()
        self.theme = ThemeManager(self.app, LIGHT_THEME, mode=ThemeMode.LIGHT)
        self.addCleanup(lambda: shiboken6.delete(self.theme))
        self.window = SettingsWindow(self.prefs, self.theme, ALL)
        self.addCleanup(self.window.deleteLater)

    def test_pages_in_order_with_a_form_of_their_settings(self):
        self.assertEqual(self.window.page_names,
                         ["Appearance", "Diff", "History", "Tools", "Zebra"])
        kinds = {key: next(c for c in (QSpinBox, QDoubleSpinBox, QCheckBox, QLineEdit, QComboBox)
                           if isinstance(editor, c))
                 for key, editor in self.window.editors.items()}
        self.assertEqual(kinds, {"test/other": QSpinBox, "test/count": QSpinBox,
                                 "test/size": QDoubleSpinBox, "test/on": QCheckBox,
                                 "test/name": QLineEdit, "test/mode": QComboBox,
                                 "test/font": QComboBox})
        count = self.window.editors["test/count"]
        self.assertEqual((count.minimum(), count.maximum()), (4, 100))
        self.assertEqual(count.text(), "20")  # only the number
        self.assertEqual(self.window.editors["test/name"].placeholderText(), "Automatic")
        self.assertEqual(self.window.editors["test/name"].toolTip(), "Its help")
        self.window.show_page("Tools")
        self.assertEqual(self.window.page_list.currentRow(), 3)

    def test_without_a_theme_appearance_has_only_its_settings(self):
        window = SettingsWindow(self.prefs, None, (COUNT,))
        self.addCleanup(window.deleteLater)
        self.assertEqual(window.page_names, ["Diff"])
        self.assertIsNone(window.theme_mode_box)

    def test_a_change_applies_at_once(self):
        editors = self.window.editors
        editors["test/count"].setValue(40)
        editors["test/on"].setChecked(True)
        editors["test/name"].textEdited.emit("kitty")
        editors["test/mode"].setCurrentIndex(1)
        self.assertEqual([self.prefs.value(s) for s in (COUNT, ON, NAME, MODE)],
                         [40, True, "kitty", "b"])

    def test_the_editors_show_a_change_made_elsewhere(self):
        self.prefs.set(COUNT, 50)
        self.prefs.set(NAME, "foot")
        self.prefs.set(MODE, "b")
        self.prefs.set(FONT, "No Such Mono")  # not installed: still shown
        editors = self.window.editors
        self.assertEqual(editors["test/count"].value(), 50)
        self.assertEqual(editors["test/name"].text(), "foot")
        self.assertEqual(editors["test/mode"].currentData(), "b")
        self.assertEqual(editors["test/font"].currentText(), "No Such Mono")

    def test_the_font_settings_show_the_themes_font_until_set(self):
        from PySide6.QtGui import QFontInfo
        box, size = self.window.editors["test/font"], self.window.editors["test/size"]
        family = QFontInfo(self.theme.current.mono_font()).family()
        self.assertEqual(box.currentText(), family)  # what "monospace" is here
        self.assertNotIn("Theme's", [box.itemText(n) for n in range(box.count())])
        self.assertEqual((size.value(), size.text()), (10.0, "10"))
        self.assertEqual(size.minimum(), settings_window.FONT_SIZE_MIN)
        size.setValue(11.5)
        self.assertEqual((self.prefs.value(SIZE), size.text()), (11.5, "11.5"))
        self.prefs.reset([SIZE])
        self.assertEqual(size.value(), 10.0)
        self.assertEqual(self.prefs.value(FONT), "")  # showing it doesn't set it

    def test_restore_defaults_of_the_page_or_of_all(self):
        self.prefs.set(COUNT, 50)
        self.prefs.set(ON, True)
        self.prefs.set(NAME, "kitty")
        self.window.show_page("Diff")
        with mock.patch.object(self.window, "ask_restore", return_value=None) as ask:
            self.window.restore_button.click()
        ask.assert_called_once_with("Diff")
        self.assertEqual(self.prefs.value(COUNT), 50)
        with mock.patch.object(self.window, "ask_restore", return_value=Restore.PAGE):
            self.window.restore_button.click()
        self.assertEqual((self.prefs.value(COUNT), self.prefs.value(ON)), (20, False))
        self.assertEqual(self.window.editors["test/count"].value(), 20)
        self.assertEqual(self.prefs.value(NAME), "kitty")
        self.theme.set_mode(ThemeMode.DARK)
        with mock.patch.object(self.window, "ask_restore", return_value=Restore.ALL):
            self.window.restore_button.click()
        self.assertEqual(self.prefs.value(NAME), "")
        self.assertIs(self.theme.mode, ThemeMode.SYSTEM)

    def test_the_restore_question_and_its_answers(self):
        self.window.show_page("Tools")
        for label, answer in (("Restore All Settings", Restore.ALL),
                              ("Restore Tools Settings", Restore.PAGE), ("Cancel", None)):
            with self.subTest(label=label):
                seen = []

                def click(label=label, seen=seen):
                    box = QApplication.activeModalWidget()
                    seen.extend(b.text().replace("&", "") for b in box.buttons())
                    next(b for b in box.buttons() if b.text().replace("&", "") == label).click()

                QTimer.singleShot(0, click)
                self.assertIs(self.window.ask_restore("Tools"), answer)
                self.assertCountEqual(seen, ["Restore All Settings", "Restore Tools Settings",
                                             "Cancel"])

    def test_restore_defaults_is_beside_close_and_the_page_is_outlined(self):
        buttons = self.window.restore_button.parent()
        self.assertIsInstance(buttons, QDialogButtonBox)
        self.assertIsNotNone(buttons.button(QDialogButtonBox.StandardButton.Close))
        self.assertEqual(self.window.page_frame.property("role"), "settings-page")
        self.assertIn('QFrame[role="settings-page"]', LIGHT_THEME.stylesheet())

    def test_the_theme_rows_drive_the_theme_and_follow_it(self):
        box, check = self.window.theme_mode_box, self.window.colour_blind_box
        self.assertEqual(box.currentText(), "Light")
        box.setCurrentIndex(box.findData(ThemeMode.DARK.value))
        box.activated.emit(box.currentIndex())
        self.assertIs(self.theme.mode, ThemeMode.DARK)
        check.setChecked(True)
        self.assertTrue(self.theme.colour_blind)
        self.theme.set_mode(ThemeMode.LIGHT)  # from the View menu
        self.assertEqual(box.currentText(), "Light")
        self.prefs.set(SIZE, 14.0)
        self.prefs.set(COUNT, 50)
        self.window.show_page(APPEARANCE)
        with mock.patch.object(self.window, "ask_restore", return_value=Restore.PAGE):
            self.window.restore_defaults()
        self.assertEqual(self.prefs.value(COUNT), 50)  # another page's
        self.assertEqual((self.theme.mode, self.theme.colour_blind), (ThemeMode.SYSTEM, False))
        self.assertEqual((box.currentText(), check.isChecked()), ("System", False))
        self.assertEqual(self.prefs.value(SIZE), 0.0)

    def test_it_doesnt_keep_the_app_running(self):
        self.assertFalse(self.window.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose))
        self.assertTrue(self.window.isWindow())

    def test_the_apps_settings_are_shown(self):
        window = SettingsWindow(self.prefs, self.theme)
        self.addCleanup(window.deleteLater)
        self.assertEqual(window.page_names[:4], ["Appearance", "Diff", "History", "Tools"])
        self.assertIn(FOLD_MIN.key, window.editors)


class OpeningTests(unittest.TestCase):
    """File → Settings… (Ctrl+,), one window for the app (spec §8.2)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = AppSettings(Path(self._tmp.name) / "settings.ini")
        self.theme = ThemeManager(self.app, LIGHT_THEME)
        self.prefs = Preferences(self.settings)
        self.windows = AppWindows(self.app, self.settings, self.theme,
                                  CommitDrafts(Path(self._tmp.name) / "drafts"), ColumnPrefs(),
                                  SortPrefs(), prefs=self.prefs)
        self.addCleanup(self.close_all)

    def close_all(self):
        for window in self.windows.windows():
            window.close()
        self.windows.shutdown()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_the_file_menu_entry_and_shortcut(self):
        window = self.windows.new_window()
        action = window.ctx.actions.settings
        self.assertEqual(action.shortcut().toString(), "Ctrl+,")
        file_menu = window.menuBar().actions()[0].menu()
        self.assertIn(action, file_menu.actions())

    def test_one_window_for_the_app_closed_with_the_last_main_window(self):
        one, two = self.windows.new_window(), self.windows.new_window()
        with mock.patch("rzgitclient.ui.app_windows.show_window") as show:
            one.ctx.actions.settings.trigger()
            first = self.windows.settings_window
            two.ctx.actions.settings.trigger()
        self.assertIs(self.windows.settings_window, first)
        self.assertEqual(show.call_count, 2)
        first.show()
        one.close()
        self.assertTrue(first.isVisible())
        two.close()
        self.assertFalse(first.isVisible())

    def test_a_change_reaches_every_window(self):
        one, two = self.windows.new_window(), self.windows.new_window()
        self.assertIs(one.ctx.prefs, two.ctx.prefs)
        self.windows.show_settings()
        self.windows.settings_window.editors[TAB_SPACES.key].setValue(8)
        for window in (one, two):
            self.assertEqual(window.ctx.prefs.value(TAB_SPACES), 8)
        self.windows.settings_window.close()

    def test_a_main_window_without_app_windows_has_its_own(self):
        ctx = make_panel_context()
        window = MainWindow(ctx, self.settings)
        with mock.patch("rzgitclient.ui.main_window.show_window"):
            window.show_settings()
        self.assertIsNotNone(window._settings_window)
        self.assertIs(window._settings_window._prefs, ctx.prefs)
        window._settings_window.show()
        window.close()
        self.assertFalse(window._settings_window.isVisible())
        window._settings_window.deleteLater()
        window.deleteLater()
        ctx.session.shutdown()


if __name__ == "__main__":
    unittest.main()
