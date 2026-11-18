"""migrate_old_name: the settings and drafts of RZGitClient, copied once (spec §6.3)."""

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QSettings, QStandardPaths

from rzgit import app as app_module
from rzgit.core import old_name_migration
from rzgit.core.old_name_migration import data_folder, migrate_old_name, settings_file
from tests.support import qt_app


class OldNameMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.config = base / "config"
        self.data = base / "data"
        self.use_names("RZGit")
        self.old_conf = self.config / "RZGitClient" / "RZGitClient.conf"
        self.new_conf = self.config / "RZGit" / "RZGit.conf"
        self.old_drafts = self.data / "RZGitClient" / "RZGitClient" / "commit-drafts"
        self.new_drafts = self.data / "RZGit" / "RZGit" / "commit-drafts"

    def use_names(self, name):
        org, app = QCoreApplication.organizationName(), QCoreApplication.applicationName()
        self.addCleanup(QCoreApplication.setOrganizationName, org)
        self.addCleanup(QCoreApplication.setApplicationName, app)
        QCoreApplication.setOrganizationName(name)
        QCoreApplication.setApplicationName(name)

    def write_old(self):
        self.old_conf.parent.mkdir(parents=True)
        self.old_conf.write_text("[General]\nlast_repo=/r\n")
        self.old_drafts.mkdir(parents=True)
        (self.old_drafts / "a.json").write_text('{"root": "/r", "text": "wip"}')

    def migrate(self):
        return migrate_old_name(self.config, self.data)

    def test_copies_the_settings_and_drafts(self):
        self.write_old()
        self.assertEqual(self.migrate(), [self.new_conf, self.new_drafts])
        self.assertEqual(self.new_conf.read_text(), "[General]\nlast_repo=/r\n")
        self.assertEqual((self.new_drafts / "a.json").read_text(), '{"root": "/r", "text": "wip"}')
        # The old files stay as they were.
        self.assertEqual(self.old_conf.read_text(), "[General]\nlast_repo=/r\n")
        self.assertTrue((self.old_drafts / "a.json").exists())
        self.assertEqual(sorted(p.name for p in self.new_conf.parent.iterdir()), ["RZGit.conf"])

    def test_copies_once(self):
        self.write_old()
        self.migrate()
        self.new_conf.write_text("[General]\nlast_repo=/new\n")  # used since
        (self.new_drafts / "a.json").unlink()  # a draft committed since
        self.assertEqual(self.migrate(), [])
        self.assertEqual(self.new_conf.read_text(), "[General]\nlast_repo=/new\n")
        self.assertEqual(list(self.new_drafts.iterdir()), [])

    def test_existing_new_files_are_kept(self):
        self.write_old()
        self.new_conf.parent.mkdir(parents=True)
        self.new_conf.write_text("new")
        self.assertEqual(self.migrate(), [self.new_drafts])  # only what was missing
        self.assertEqual(self.new_conf.read_text(), "new")

    def test_nothing_to_copy(self):
        self.assertEqual(self.migrate(), [])
        self.assertFalse(self.config.exists())
        self.assertFalse(self.data.exists())

    def test_still_the_old_name_copies_nothing(self):
        self.write_old()
        self.use_names("RZGitClient")
        self.assertEqual(self.migrate(), [])

    def test_a_failed_copy_leaves_nothing_behind(self):
        self.write_old()
        def half_copy(old, new, **kwargs):  # fails partway through
            Path(new).mkdir()
            (Path(new) / "a.json").write_text("{")
            raise OSError("disk full")

        with mock.patch.object(old_name_migration.shutil, "copytree", side_effect=half_copy), \
                self.assertLogs(old_name_migration.log, "WARNING"):
            self.assertEqual(self.migrate(), [self.new_conf])
        self.assertEqual(list(self.new_drafts.parent.iterdir()), [])  # no partial copy
        self.assertEqual(self.migrate(), [self.new_drafts])  # tried again next start

    def test_paths_are_where_qt_keeps_them(self):
        config = Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.GenericConfigLocation))
        data = Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.GenericDataLocation))
        self.assertEqual(settings_file(config, "RZGit", "RZGit"),
                         Path(QSettings("RZGit", "RZGit").fileName()))
        self.assertEqual(data_folder(data, "RZGit", "RZGit"), Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation)))

    def test_the_app_is_named_rzgit_and_migrates_before_reading_settings(self):
        source = inspect.getsource(app_module.main)
        for line in ('setApplicationName("RZGit")', 'setOrganizationName("RZGit")',
                     'setDesktopFileName("rzgit")'):
            self.assertIn(line, source)
        self.assertLess(source.index('setOrganizationName("RZGit")'),
                        source.index("migrate_old_name()"))
        self.assertLess(source.index("migrate_old_name()"), source.index("AppSettings()"))


if __name__ == "__main__":
    unittest.main()
