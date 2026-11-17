import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication, QEvent

from rzgitclient.core.recent_repos import RecentRepos
from rzgitclient.ui.recent_repos_menu import RecentReposMenu, entry_labels, home_relative
from tests.support import qt_app


def texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def entry(menu, text):
    return next(a.menu() for a in menu.actions() if a.text() == text)


def item(submenu, text):
    return next(a for a in submenu.actions() if a.text() == text)


class RecentReposMenuTests(unittest.TestCase):
    """File → Recent Repositories (spec §8, RZG-87)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.recent = RecentRepos()
        self.here, self.new = [], []
        self.menu = RecentReposMenu(self.recent, self.here.append, self.new.append)
        self.addCleanup(self.menu.deleteLater)

    def add(self, *names):
        paths = []
        for name in names:
            path = self.tmp / name
            path.mkdir(parents=True, exist_ok=True)
            self.recent.touch(SimpleNamespace(root=path, main_root=path))
            paths.append(path)
        self.menu.rebuild()
        return paths

    def test_empty(self):
        self.assertEqual(texts(self.menu), ["No Recent Repositories"])
        self.assertFalse(self.menu.actions()[0].isEnabled())
        self.assertEqual(self.menu.title(), "Re&cent Repositories")

    def test_entries_newest_first_then_clear(self):
        self.add("one", "two")
        self.assertEqual(texts(self.menu), ["two", "one", "&Clear Recent Repositories"])
        self.assertTrue(self.menu.toolTipsVisible())
        self.assertEqual(self.menu.actions()[0].toolTip(), str(self.tmp / "two"))
        self.menu.actions()[-1].trigger()
        self.assertEqual(self.recent.repositories(), ())

    def test_rebuilt_as_it_opens(self):
        self.add("one")
        self.recent.touch(SimpleNamespace(root=self.tmp / "two", main_root=self.tmp / "two"))
        self.menu.aboutToShow.emit()
        self.assertEqual(texts(self.menu)[:2], ["two (missing)", "one"])

    def test_old_submenus_are_deleted(self):
        self.add("one")
        old = entry(self.menu, "one")
        destroyed = []
        old.destroyed.connect(lambda: destroyed.append(True))
        self.menu.rebuild()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(destroyed, [True])

    def test_the_submenu_opens_here_or_in_a_new_window(self):
        (one,) = self.add("one")
        sub = entry(self.menu, "one")
        self.assertEqual([a.text() for a in sub.actions() if not a.isSeparator()],
                         ["Open in &Current Window", "Open in &New Window",
                          "&Remove from List"])
        item(sub, "Open in &Current Window").trigger()
        self.assertEqual((self.here, self.new), ([one], []))
        item(sub, "Open in &New Window").trigger()
        self.assertEqual((self.here, self.new), ([one], [one]))

    def test_remove_from_list(self):
        self.add("one", "two")
        item(entry(self.menu, "one"), "&Remove from List").trigger()
        self.assertEqual(self.recent.repositories(), (self.tmp / "two",))

    def test_a_missing_folder_is_shown_and_cannot_be_opened(self):
        (one,) = self.add("one")
        one.rmdir()
        self.menu.rebuild()
        sub = entry(self.menu, "one (missing)")
        self.assertFalse(item(sub, "Open in &Current Window").isEnabled())
        self.assertFalse(item(sub, "Open in &New Window").isEnabled())
        self.assertTrue(item(sub, "&Remove from List").isEnabled())
        self.assertTrue(self.menu.actions()[0].isEnabled())  # to reach Remove

    def test_ampersands_are_shown(self):
        self.add("R&D")
        self.assertEqual(texts(self.menu)[0], "R&&D")

    def test_same_names_add_their_parent_folder(self):
        a, b, c = self.tmp / "x" / "RZE", self.tmp / "y" / "RZE", self.tmp / "z"
        self.assertEqual(entry_labels([a, b, c]),
                         [f"RZE — {self.tmp / 'x'}", f"RZE — {self.tmp / 'y'}", "z"])

    def test_home_is_shown_as_tilde(self):
        home = Path.home().resolve()
        self.assertEqual(home_relative(home), "~")
        self.assertEqual(home_relative(home / "dev"), "~/dev")
        self.assertEqual(home_relative(Path("/srv/git")), "/srv/git")


if __name__ == "__main__":
    unittest.main()
