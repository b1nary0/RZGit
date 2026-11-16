import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent

from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.ui.panels.commit_files_panel import CommitFilesPanel
from tests.support import commit_file, git, make_panel_context, make_repo, qt_app, wait_idle


class CommitFilesPanelTests(unittest.TestCase):
    """The files of the commit selected in History (spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.repo, "a.txt", "1", "first")
        commit_file(self.repo, "old.txt", "keep\n" * 20, "second")
        git(self.repo, "mv", "old.txt", "new.txt")
        (self.repo / "a.txt").write_text("2")
        git(self.repo, "commit", "-qam", "third")
        self.ctx = make_panel_context()
        self.session, self.selection = self.ctx.session, self.ctx.selection
        self.panel = CommitFilesPanel(self.ctx)
        self.addCleanup(self.panel.deleteLater)
        self.session.open(self.repo)
        wait_idle(self.session)
        self.third = self.session.history[0]

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def choose(self, commit):
        self.selection.commit = commit
        wait_idle(self.session)

    def rows(self):
        tree = self.panel._list._tree
        return [(tree.topLevelItem(i).text(0), tree.topLevelItem(i).text(1))
                for i in range(tree.topLevelItemCount())]

    def test_collapsed_with_no_list_on_uncommitted_changes(self):
        self.assertTrue(self.panel.collapsed)
        self.assertIsNone(self.panel._list)
        self.assertEqual(self.panel.header, "Commit Files")

    def test_a_selected_commit_opens_it_with_its_files(self):
        self.choose(self.third)
        self.assertFalse(self.panel.collapsed)
        self.assertEqual(self.panel.header, f"Files in {self.third.short_sha} (2)")
        self.assertEqual(sorted(self.rows()),
                         [("a.txt", "Modified"), ("old.txt → new.txt", "Renamed")])

    def test_back_to_uncommitted_changes_collapses_it(self):
        self.choose(self.third)
        self.choose(None)
        self.assertTrue(self.panel.collapsed)
        self.assertEqual(self.panel.header, "Commit Files")
        self.assertIsNone(self.session.commit_files_sha)

    def test_selecting_a_file_selects_it_in_that_commit(self):
        self.choose(self.third)
        tree = self.panel._list._tree
        item = next(tree.topLevelItem(i) for i in range(2) if tree.topLevelItem(i).text(0) == "a.txt")
        tree.setCurrentItem(item)
        a = FileSelection("a.txt", FileSource.COMMIT, self.third.sha)
        self.assertEqual((self.selection.files, self.selection.file), ((a,), a))

    def test_no_staging_from_a_commit(self):
        self.choose(self.third)
        tree = self.panel._list._tree
        with mock.patch.object(self.ctx.actions, "move") as move:
            tree.itemDoubleClicked.emit(tree.topLevelItem(0), 0)
            move.assert_not_called()
        with mock.patch("rzgitclient.ui.panels.file_list.QMenu") as menu:
            self.panel._list._show_menu(tree.rect().center())
            menu.assert_not_called()

    def test_the_header_counts_only_once_the_files_arrive(self):
        self.selection.commit = self.third
        self.assertEqual(self.panel.header, f"Files in {self.third.short_sha}")
        wait_idle(self.session)
        self.assertEqual(self.panel.header, f"Files in {self.third.short_sha} (2)")


if __name__ == "__main__":
    unittest.main()
