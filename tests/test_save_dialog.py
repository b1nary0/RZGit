import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtWidgets import QWidget

from rzgit.ui.save_dialog import SaveDialog
from tests.support import qt_app


class SaveDialogTests(unittest.TestCase):
    """Asking where to save and saying if it failed (RZG-76, spec §8.3)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.dialog = SaveDialog()
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.asked: list[tuple[str, Path, str]] = []
        self.answer = ""
        self.dialog.ask_path = self.ask_path

    def ask_path(self, parent, title, suggested, filters):
        self.assertIs(parent, self.parent)
        self.asked.append((title, suggested, filters))
        return str(self.answer)

    def save(self, **kwargs):
        return self.dialog.ask_and_save(self.parent, "data\n", kind="patch",
                                        suggested_name="a.patch", title="Save Patch",
                                        filters="Patches (*.patch)", **kwargs)

    def test_it_starts_in_the_kinds_last_folder_else_start_else_home(self):
        self.save()
        self.assertEqual(self.asked[-1], ("Save Patch", Path.home() / "a.patch", "Patches (*.patch)"))
        self.save(start=self.dir)
        self.assertEqual(self.asked[-1][1], self.dir / "a.patch")
        folder = self.dir / "patches"
        folder.mkdir()
        self.answer = folder / "chosen.patch"
        self.assertEqual(self.save(start=self.dir), self.answer.resolve())
        self.answer = ""
        self.save(start=self.dir)
        self.assertEqual(self.asked[-1][1], folder.resolve() / "a.patch")

    def test_it_saves_with_the_parent_as_origin(self):
        got = []
        self.dialog.saved.connect(lambda path, origin: got.append((path, origin)))
        self.answer = self.dir / "out.patch"
        path = self.save()
        self.assertEqual(path.read_text(), "data\n")
        self.assertEqual(got, [(path, self.parent)])

    def test_cancel_saves_nothing(self):
        got = []
        self.dialog.saved.connect(lambda *args: got.append(args))
        self.assertIsNone(self.save(start=self.dir))
        self.assertEqual(got, [])
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_a_failed_write_is_shown_and_gives_none(self):
        self.answer = self.dir / "missing" / "out.patch"
        with mock.patch("rzgit.ui.save_dialog.QMessageBox.warning") as warning:
            self.assertIsNone(self.save())
        parent, title, text = warning.call_args.args
        self.assertIs(parent, self.parent)
        self.assertEqual(title, "Couldn't save")
        self.assertIn(str(self.answer), text)
        self.assertIn("No such file or directory", text)


if __name__ == "__main__":
    unittest.main()
