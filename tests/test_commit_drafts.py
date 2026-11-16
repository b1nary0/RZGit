import tempfile
import unittest
from pathlib import Path

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.core.commit_drafts import CommitDrafts
from tests.support import qt_app


class CommitDraftsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "settings.ini"

    def tearDown(self):
        self._tmp.cleanup()

    def test_one_draft_per_repository(self):
        drafts = CommitDrafts()
        drafts.set("/repos/one", "Fix one")
        drafts.set(Path("/repos/two"), "Fix two")
        self.assertEqual(drafts.get(Path("/repos/one")), "Fix one")
        self.assertEqual(drafts.get("/repos/two"), "Fix two")
        self.assertEqual(drafts.get("/repos/three"), "")

    def test_old_keys_are_migrated_when_loaded(self):
        tmp = Path(self._tmp.name).resolve()
        real, other = tmp / "repo", tmp / "other"
        real.mkdir()
        other.mkdir()
        (tmp / "link").symlink_to(real)
        (tmp / "link2").symlink_to(other)
        settings = AppSettings(self.path)
        settings.commit_drafts = {
            str(tmp / "link"): "Old key",  # meets the canonical key below: that one wins
            str(real): "Canonical key",
            str(tmp / "link2"): "Only under a link",
            str(tmp / "gone"): "Folder gone",  # kept: its drive may be back
        }
        drafts = CommitDrafts(settings)
        self.assertEqual(drafts.get(real), "Canonical key")
        self.assertEqual(drafts.get(other), "Only under a link")
        self.assertEqual(drafts.get(tmp / "gone"), "Folder gone")
        settings.sync()
        self.assertEqual(AppSettings(self.path).commit_drafts,  # saved once, migrated
                         {str(real): "Canonical key", str(other): "Only under a link",
                          str(tmp / "gone"): "Folder gone"})

    def test_every_path_to_a_repository_finds_its_draft(self):
        real = Path(self._tmp.name) / "repo"
        real.mkdir()
        link = Path(self._tmp.name) / "link"
        link.symlink_to(real)
        drafts = CommitDrafts()
        drafts.set(link, "Through the link")
        self.assertEqual(drafts.get(real.resolve()), "Through the link")
        drafts.set(real, "  ")
        self.assertEqual(drafts.get(link), "")

    def test_a_blank_draft_is_removed(self):
        settings = AppSettings(self.path)
        drafts = CommitDrafts(settings)
        drafts.set("/repos/one", "Fix")
        drafts.set("/repos/one", "  \n")
        self.assertEqual(drafts.get("/repos/one"), "")
        self.assertEqual(settings.commit_drafts, {})

    def test_drafts_survive_a_restart(self):
        settings = AppSettings(self.path)
        CommitDrafts(settings).set("/repos/one", "Half written\n\nBody")
        settings.sync()
        self.assertEqual(CommitDrafts(AppSettings(self.path)).get("/repos/one"),
                         "Half written\n\nBody")

    def test_without_settings_drafts_stay_in_memory(self):
        CommitDrafts().set("/repos/one", "Fix")
        self.assertEqual(CommitDrafts().get("/repos/one"), "")


if __name__ == "__main__":
    unittest.main()
