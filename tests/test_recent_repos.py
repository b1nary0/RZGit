import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.core.recent_repos import RecentRepos
from tests.support import SignalRecorder, qt_app


def repo(root, main=None):
    """What touch() reads of a Repo: its worktree root and main root."""
    return SimpleNamespace(root=Path(root), main_root=Path(main or root))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        self.now += 1
        return self.now


class RecentReposTests(unittest.TestCase):
    """The record of what was opened when (spec §6.3, RZG-87)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.path = self.tmp / "settings.ini"
        self.clock = Clock()
        self.recent = RecentRepos(AppSettings(self.path), clock=self.clock)

    def reopen(self):
        return RecentRepos(AppSettings(self.path))

    def test_newest_first_and_touching_moves_to_the_top(self):
        a, b, c = self.tmp / "a", self.tmp / "b", self.tmp / "c"
        for path in (a, b, c):
            self.recent.touch(repo(path))
        self.assertEqual(self.recent.repositories(), (c, b, a))
        self.recent.touch(repo(a))
        self.assertEqual(self.recent.repositories(), (a, c, b))
        # One saved entry per worktree: touching again replaces it.
        saved = AppSettings(self.path).recent_repos
        self.assertEqual([e["root"] for e in saved], [str(a), str(c), str(b)])

    def test_a_worktree_moves_its_main_repository_to_the_top(self):
        main, other = self.tmp / "main", self.tmp / "other"
        linked = self.tmp / "main-feature"
        self.recent.touch(repo(main))
        self.recent.touch(repo(other))
        self.recent.touch(repo(linked, main))
        self.assertEqual(self.recent.repositories(), (main, other))
        self.assertEqual(self.recent.last_opened(linked), self.clock.now)
        self.assertLess(self.recent.last_opened(main), self.recent.last_opened(linked))
        self.assertIsNone(self.recent.last_opened(self.tmp / "never"))

    def test_at_most_ten_repositories_with_their_worktrees(self):
        paths = [self.tmp / f"r{i}" for i in range(12)]
        self.recent.touch(repo(self.tmp / "r0-wt", paths[0]))
        for path in paths:
            self.recent.touch(repo(path))
        self.assertEqual(self.recent.repositories(), tuple(reversed(paths))[:10])
        self.assertEqual(RecentRepos.LIMIT, 10)
        # The oldest repositories are dropped, with their worktrees.
        self.assertIsNone(self.recent.last_opened(paths[0]))
        self.assertIsNone(self.recent.last_opened(self.tmp / "r0-wt"))
        self.assertEqual(self.reopen().repositories(), tuple(reversed(paths))[:10])

    def test_the_list_survives_a_restart(self):
        main, linked = self.tmp / "main", self.tmp / "linked"
        self.recent.touch(repo(main))
        self.recent.touch(repo(linked, main))
        again = self.reopen()
        self.assertEqual(again.repositories(), (main,))
        self.assertEqual(again.last_opened(linked), self.recent.last_opened(linked))
        self.assertEqual(again.last_opened(main), self.recent.last_opened(main))

    def test_remove_takes_the_repository_and_its_worktrees(self):
        main, other, linked = self.tmp / "main", self.tmp / "other", self.tmp / "linked"
        self.recent.touch(repo(main))
        self.recent.touch(repo(linked, main))
        self.recent.touch(repo(other))
        signals = SignalRecorder(self.recent, "changed")
        self.recent.remove(main)
        self.assertEqual(self.recent.repositories(), (other,))
        self.assertIsNone(self.recent.last_opened(linked))
        self.assertEqual(signals.names(), ["changed"])
        self.recent.remove(main)  # not listed: nothing changes
        self.assertEqual(signals.names(), ["changed"])
        self.assertEqual(self.reopen().repositories(), (other,))

    def test_clear(self):
        self.recent.touch(repo(self.tmp / "a"))
        signals = SignalRecorder(self.recent, "changed")
        self.recent.clear()
        self.assertEqual(self.recent.repositories(), ())
        self.assertEqual(self.reopen().repositories(), ())
        self.recent.clear()
        self.assertEqual(signals.names(), ["changed"])

    def test_touch_emits_changed(self):
        signals = SignalRecorder(self.recent, "changed")
        self.recent.touch(repo(self.tmp / "a"))
        self.assertEqual(signals.names(), ["changed"])

    def test_malformed_entries_are_dropped(self):
        good = {"root": str(self.tmp / "a"), "main": str(self.tmp / "a"), "opened": 5}
        settings = AppSettings(self.path)
        settings.recent_repos = [good, {"root": "x"}, "nope",
                                 {"root": "/b", "main": "/b", "opened": True}, good]
        with self.assertLogs("rzgitclient.core.recent_repos", "WARNING") as logs:
            recent = RecentRepos(settings)
        self.assertEqual(len(logs.records), 3)
        self.assertEqual(recent.repositories(), (self.tmp / "a",))
        self.assertEqual(recent.last_opened(self.tmp / "a"), 5.0)

    def test_without_settings_it_is_in_memory(self):
        recent = RecentRepos()
        recent.touch(repo(self.tmp / "a"))
        self.assertEqual(recent.repositories(), (self.tmp / "a",))
        self.assertEqual(self.reopen().repositories(), ())


if __name__ == "__main__":
    unittest.main()
