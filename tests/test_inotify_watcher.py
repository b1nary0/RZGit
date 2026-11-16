import os
import tempfile
import unittest
from pathlib import Path

from rzgitclient.core.inotify_watcher import (
    IN_CREATE,
    IN_DELETE,
    IN_ISDIR,
    IN_MODIFY,
    IN_MOVED_FROM,
    IN_MOVED_TO,
    InotifyWatcher,
)
from tests.support import pump, qt_app


class InotifyWatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        (self.path / "f").write_text("a")
        self.watcher = InotifyWatcher()
        if not self.watcher.available:
            self.skipTest("inotify unavailable")
        self.events = []
        self.watcher.events_read.connect(self.events.extend)
        self.assertTrue(self.watcher.add(str(self.path)))

    def tearDown(self):
        self.watcher.close()
        self._tmp.cleanup()

    def named(self, name):
        return [e for e in self.events if e.name == name]

    def test_a_file_changed_in_place_is_named(self):
        with open(self.path / "f", "a") as f:
            f.write("more")
        pump(50)
        events = self.named("f")
        self.assertTrue(events)
        self.assertEqual(events[0].folder, str(self.path))
        self.assertTrue(any(e.mask & IN_MODIFY for e in events))

    def test_create_rename_delete(self):
        (self.path / "g").write_text("b")
        os.replace(self.path / "g", self.path / "h")
        (self.path / "h").unlink()
        pump(50)
        self.assertTrue(self.named("g")[0].mask & IN_CREATE)
        self.assertTrue(any(e.mask & IN_MOVED_FROM for e in self.named("g")))
        self.assertTrue(self.named("h")[0].mask & IN_MOVED_TO)
        self.assertTrue(self.named("h")[-1].mask & IN_DELETE)
        self.assertTrue(self.named("h")[0].appeared)
        self.assertTrue(self.named("h")[-1].vanished)

    def test_folders_are_marked(self):
        (self.path / "d").mkdir()
        pump(50)
        self.assertTrue(self.named("d")[0].mask & IN_ISDIR)
        self.assertTrue(self.named("d")[0].is_dir)

    def test_read_now_emits_at_once(self):
        (self.path / "f").write_text("b")
        self.watcher.read_now()
        self.assertTrue(self.named("f"))

    def test_a_deleted_folder_stops_being_watched(self):
        sub = self.path / "sub"
        sub.mkdir()
        self.assertTrue(self.watcher.add(str(sub)))
        sub.rmdir()
        pump(50)
        self.assertEqual(self.watcher.folders, {str(self.path)})

    def test_removed_folders_report_nothing(self):
        self.watcher.remove(str(self.path))
        (self.path / "f").write_text("b")
        pump(50)
        self.assertEqual(self.events, [])
        self.assertEqual(self.watcher.folders, set())

    def test_a_missing_folder_cant_be_added(self):
        self.assertFalse(self.watcher.add(str(self.path / "missing")))
