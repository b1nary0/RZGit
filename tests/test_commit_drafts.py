import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rzgit.core.app_settings import AppSettings
from rzgit.core.commit_drafts import CommitDrafts, drafts_folder
from tests.support import qt_app


class CommitDraftsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.path = self.tmp / "settings.ini"
        self.folder = self.tmp / "drafts"

    def tearDown(self):
        self._tmp.cleanup()

    def files(self):
        return sorted(self.folder.iterdir()) if self.folder.exists() else []

    def test_one_draft_per_repository(self):
        for drafts in (CommitDrafts(), CommitDrafts(self.folder)):
            drafts.set("/repos/one", "Fix one")
            drafts.set(Path("/repos/two"), "Fix two")
            self.assertEqual(drafts.get(Path("/repos/one")), "Fix one")
            self.assertEqual(drafts.get("/repos/two"), "Fix two")
            self.assertEqual(drafts.get("/repos/three"), "")

    def test_each_repository_is_a_file_of_its_own(self):
        drafts = CommitDrafts(self.folder)
        drafts.set("/repos/one", "Fix one")
        drafts.set("/repos/two", "Fix two")
        files = self.files()
        self.assertEqual(len(files), 2)
        self.assertEqual({json.loads(f.read_text())["root"] for f in files},
                         {"/repos/one", "/repos/two"})
        self.assertEqual([f.suffix for f in files], [".json", ".json"])  # no temp file left

    def test_two_instances_never_overwrite_each_others_drafts(self):
        """Two windows, or two app processes, each with its own CommitDrafts."""
        first, second = CommitDrafts(self.folder), CommitDrafts(self.folder)
        first.get("/repos/one"), second.get("/repos/two")
        first.set("/repos/one", "From the first")
        second.set("/repos/two", "From the second")
        first.set("/repos/one", "From the first, again")
        self.assertEqual(second.get("/repos/one"), "From the first, again")  # read, not cached
        self.assertEqual(first.get("/repos/two"), "From the second")
        self.assertEqual(CommitDrafts(self.folder).get("/repos/two"), "From the second")

    def test_drafts_saved_by_older_versions_are_moved_to_files_once(self):
        real, other = self.tmp / "repo", self.tmp / "other"
        real.mkdir()
        other.mkdir()
        (self.tmp / "link").symlink_to(real)
        (self.tmp / "link2").symlink_to(other)
        settings = AppSettings(self.path)
        settings._settings.setValue("commit/drafts", json.dumps({
            str(self.tmp / "link"): "Old key",  # meets the canonical key below: that one wins
            str(real): "Canonical key",
            str(self.tmp / "link2"): "Only under a link",
            str(self.tmp / "gone"): "Folder gone",  # kept: its drive may be back
        }))
        drafts = CommitDrafts(self.folder, legacy=settings)
        self.assertEqual(drafts.get(real), "Canonical key")
        self.assertEqual(drafts.get(other), "Only under a link")
        self.assertEqual(drafts.get(self.tmp / "gone"), "Folder gone")
        self.assertEqual(len(self.files()), 3)
        settings.sync()
        self.assertEqual(AppSettings(self.path).old_commit_drafts, {})  # moved, not copied

    def test_a_draft_file_is_newer_than_the_old_drafts(self):
        CommitDrafts(self.folder).set("/repos/one", "Newer")
        settings = AppSettings(self.path)
        settings._settings.setValue("commit/drafts", json.dumps({"/repos/one": "Older"}))
        self.assertEqual(CommitDrafts(self.folder, legacy=settings).get("/repos/one"), "Newer")

    def test_old_drafts_stay_in_the_settings_if_they_cant_be_written(self):
        settings = AppSettings(self.path)
        settings._settings.setValue("commit/drafts", json.dumps({"/repos/one": "Keep me"}))
        self.folder.write_text("a file, not a folder")
        with self.assertLogs("rzgit.core.commit_drafts", "WARNING"):
            CommitDrafts(self.folder, legacy=settings)
        self.assertEqual(settings.old_commit_drafts, {"/repos/one": "Keep me"})

    def test_every_path_to_a_repository_finds_its_draft(self):
        real = self.tmp / "repo"
        real.mkdir()
        link = self.tmp / "link"
        link.symlink_to(real)
        drafts = CommitDrafts(self.folder)
        drafts.set(link, "Through the link")
        self.assertEqual(drafts.get(real), "Through the link")
        drafts.set(real, "  ")
        self.assertEqual(drafts.get(link), "")

    def test_a_blank_draft_is_removed(self):
        drafts = CommitDrafts(self.folder)
        drafts.set("/repos/one", "Fix")
        drafts.set("/repos/one", "  \n")
        self.assertEqual(drafts.get("/repos/one"), "")
        self.assertEqual(self.files(), [])

    def test_an_unchanged_draft_isnt_written_again(self):
        drafts = CommitDrafts(self.folder)
        drafts.set("/repos/one", "Fix")
        with mock.patch.object(CommitDrafts, "_replace") as replace:
            drafts.set("/repos/one", "Fix")
        replace.assert_not_called()

    def test_drafts_survive_a_restart(self):
        CommitDrafts(self.folder).set("/repos/one", "Half written\n\nBody é 日本")
        self.assertEqual(CommitDrafts(self.folder).get("/repos/one"), "Half written\n\nBody é 日本")

    def test_a_file_that_isnt_valid_reads_as_no_draft(self):
        drafts = CommitDrafts(self.folder)
        drafts.set("/repos/one", "Fix")
        (file,) = self.files()
        for content in ("{not json", "[1]", json.dumps({"root": "/repos/other", "text": "x"}),
                        json.dumps({"root": "/repos/one", "text": 3})):
            file.write_text(content)
            with self.assertLogs("rzgit.core.commit_drafts", "WARNING"):
                self.assertEqual(drafts.get("/repos/one"), "")
        drafts.set("/repos/one", "Fixed")  # replaces it
        self.assertEqual(drafts.get("/repos/one"), "Fixed")

    def test_a_failed_write_is_logged_not_raised(self):
        self.folder.write_text("a file, not a folder")
        with self.assertLogs("rzgit.core.commit_drafts", "WARNING"):
            CommitDrafts(self.folder).set("/repos/one", "Fix")

    def test_without_a_folder_drafts_stay_in_memory(self):
        CommitDrafts().set("/repos/one", "Fix")
        self.assertEqual(CommitDrafts().get("/repos/one"), "")

    def test_the_app_keeps_drafts_in_its_data_folder(self):
        self.assertEqual(drafts_folder().name, "commit-drafts")


if __name__ == "__main__":
    unittest.main()
