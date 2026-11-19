import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rzgit.core.app_settings import AppSettings
from rzgit.core.file_saver import FileSaver


class FileSaverTests(unittest.TestCase):
    """Atomic writes and the last folder of each kind (RZG-76, spec §8.3)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.saver = FileSaver()

    def leftovers(self, folder: Path) -> list[str]:
        return sorted(p.name for p in folder.iterdir() if p.name.endswith(".tmp"))

    def test_text_is_written_exactly_as_given_and_bytes_as_they_are(self):
        path = self.saver.save(self.dir / "a.patch", "one\r\ntwo\nné\n")
        self.assertEqual(path, (self.dir / "a.patch").resolve())
        self.assertEqual(path.read_bytes(), "one\r\ntwo\nné\n".encode())
        self.saver.save(path, b"\x00\xff")
        self.assertEqual(path.read_bytes(), b"\x00\xff")
        self.assertEqual(self.leftovers(self.dir), [])

    def test_an_existing_file_keeps_its_permissions_a_new_one_gets_the_usual(self):
        old = self.dir / "old.txt"
        old.write_text("x")
        old.chmod(0o640)
        self.saver.save(old, "y")
        self.assertEqual(stat.S_IMODE(old.stat().st_mode), 0o640)
        umask = os.umask(0)
        os.umask(umask)
        new = self.saver.save(self.dir / "new.txt", "z")
        self.assertEqual(stat.S_IMODE(new.stat().st_mode), 0o666 & ~umask)

    def test_a_failed_write_leaves_the_file_as_it_was(self):
        path = self.dir / "keep.txt"
        path.write_text("before")
        with mock.patch("rzgit.core.file_saver.os.replace", side_effect=OSError(28, "No space")):
            with self.assertRaises(OSError):
                self.saver.save(path, "after", kind="patch")
        self.assertEqual(path.read_text(), "before")
        self.assertEqual(self.leftovers(self.dir), [])
        self.assertIsNone(self.saver.folder("patch"))  # nothing saved, nothing remembered
        with self.assertRaises(OSError):
            self.saver.save(self.dir / "missing" / "x.txt", "x")

    def test_a_symlink_is_followed(self):
        real = self.dir / "real.txt"
        real.write_text("old")
        link = self.dir / "link.txt"
        link.symlink_to(real)
        self.assertEqual(self.saver.save(link, "new"), real.resolve())
        self.assertTrue(link.is_symlink())
        self.assertEqual(real.read_text(), "new")

    def test_saved_is_sent_with_the_origin(self):
        got = []
        self.saver.saved.connect(lambda path, origin: got.append((path, origin)))
        origin = object()
        path = self.saver.save(self.dir / "s.txt", "x", origin=origin)
        self.assertEqual(got, [(path, origin)])

    def test_each_kind_remembers_its_folder_across_restarts(self):
        settings_file = self.dir / "settings.ini"
        a, b = self.dir / "a", self.dir / "b"
        a.mkdir()
        b.mkdir()
        saver = FileSaver(AppSettings(settings_file))
        saver.save(a / "x.patch", "x", kind="patch")
        saver.save(b / "y.txt", "y", kind="file")
        saver.save(b / "z.txt", "z")  # no kind: nothing remembered
        again = FileSaver(AppSettings(settings_file))
        self.assertEqual(again.folder("patch"), a.resolve())
        self.assertEqual(again.folder("file"), b.resolve())
        self.assertIsNone(again.folder("stash"))
        (a / "x.patch").unlink()
        a.rmdir()
        self.assertIsNone(again.folder("patch"))  # gone: forgotten

    def test_settings_that_arent_a_json_object_are_ignored(self):
        settings = AppSettings(self.dir / "settings.ini")
        for raw in ("not json", "[1, 2]", '{"patch": 3, "file": "/tmp"}'):
            settings._settings.setValue("save/folders", raw)
            self.assertEqual(settings.save_folders, {"file": "/tmp"} if raw.startswith("{") else {})


if __name__ == "__main__":
    unittest.main()
