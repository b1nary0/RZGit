import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.ui.layout.manage_layouts_dialog import ManageLayoutsDialog
from tests.support import qt_app


class ManageLayoutsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings = AppSettings(Path(tmp.name) / "settings.ini")
        self.settings.saved_layouts = [("Review", {"a": 1}), ("Wide Diff", {"b": 2})]
        self.dialog = ManageLayoutsDialog(self.settings)
        self.addCleanup(self.dialog.deleteLater)

    def test_lists_the_saved_layouts_with_the_first_selected(self):
        self.assertEqual(self.dialog.names(), ["Review", "Wide Diff"])
        self.assertEqual(self.dialog.list.currentRow(), 0)
        self.assertTrue(self.dialog.rename_button.isEnabled())

    def test_rename_keeps_the_layout_and_its_place(self):
        self.dialog.list.setCurrentRow(1)
        with mock.patch.object(self.dialog, "ask_name", return_value=" Wide ") as ask:
            self.dialog.rename_button.click()
            ask.assert_called_once_with("Wide Diff")
        self.assertEqual(self.settings.saved_layouts, [("Review", {"a": 1}), ("Wide", {"b": 2})])
        self.assertEqual(self.dialog.names(), ["Review", "Wide"])
        self.assertEqual(self.dialog.list.currentRow(), 1)

    def test_changing_only_the_case_of_a_name_is_allowed(self):
        self.assertTrue(self.dialog.rename(0, "review"))
        self.assertEqual(self.settings.saved_layouts[0][0], "review")

    def test_rename_refuses_builtin_empty_and_taken_names(self):
        for name in ("Stacked", "", "wide diff"):
            with self.subTest(name), mock.patch.object(self.dialog, "show_problem") as problem:
                self.assertFalse(self.dialog.rename(0, name))
                problem.assert_called_once()
        self.assertEqual(self.dialog.names(), ["Review", "Wide Diff"])

    def test_delete_asks_first(self):
        with mock.patch.object(self.dialog, "confirm_delete", return_value=False) as confirm:
            self.dialog.delete_button.click()
            confirm.assert_called_once_with("Review")
        self.assertEqual(len(self.settings.saved_layouts), 2)
        with mock.patch.object(self.dialog, "confirm_delete", return_value=True):
            self.dialog.delete_button.click()
        self.assertEqual(self.settings.saved_layouts, [("Wide Diff", {"b": 2})])
        self.assertEqual(self.dialog.list.currentRow(), 0)

    def test_deleting_the_last_one_disables_the_buttons(self):
        self.dialog.delete(1)
        self.dialog.delete(0)
        self.assertEqual(self.dialog.names(), [])
        self.assertFalse(self.dialog.rename_button.isEnabled())
        self.assertFalse(self.dialog.delete_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
