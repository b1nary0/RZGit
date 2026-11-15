import dataclasses
import tempfile
import unittest
from pathlib import Path

from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.ui.panels.files_panel import FilesPanel
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from tests.support import (
    wait_idle,
    SignalRecorder,
    commit_file,
    git,
    make_panel_context,
    make_repo,
    qt_app,
)


class FilesPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.repo, "a.txt", "1", "first")
        commit_file(self.repo, "old.txt", "rename me", "add old")
        self.ctx = make_panel_context()
        self.panel = FilesPanel(self.ctx)
        self.tree = self.panel._tree

    def tearDown(self):
        self._tmp.cleanup()

    def rows(self):
        return {
            item.text(0): (item.text(1), item.text(2))
            for item in (self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount()))
        }

    def select(self, path):
        for i in range(self.tree.topLevelItemCount()):
            if self.tree.topLevelItem(i).text(0) == path:
                self.tree.setCurrentItem(self.tree.topLevelItem(i))
                return
        self.fail(f"no row {path!r}")

    def test_empty_state(self):
        self.assertEqual(self.rows(), {})
        self.assertEqual(self.panel.header, "Files")

    def test_rows_labels_colours_and_header(self):
        (self.repo / "a.txt").write_text("changed")
        (self.repo / "new.txt").write_text("hi")
        git(self.repo, "mv", "old.txt", "renamed.txt")
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.assertEqual(self.rows(), {
            "a.txt": ("", "Modified"),
            "new.txt": ("", "Untracked"),
            "old.txt → renamed.txt": ("Renamed", ""),
        })
        self.assertEqual(self.panel.header, "Files (3)")
        status = LIGHT_THEME.status
        colour = {
            self.tree.topLevelItem(i).text(0): self.tree.topLevelItem(i)
            for i in range(self.tree.topLevelItemCount())
        }
        self.assertEqual(colour["a.txt"].foreground(2).color().name(), status.modified)
        self.assertEqual(colour["new.txt"].foreground(2).color().name(), status.untracked)
        self.assertEqual(colour["old.txt → renamed.txt"].foreground(1).color().name(), status.renamed)

    def test_selection_source(self):
        (self.repo / "a.txt").write_text("changed")
        (self.repo / "staged.txt").write_text("x")
        git(self.repo, "add", "staged.txt")
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.select("a.txt")
        self.assertEqual(self.ctx.selection.file, FileSelection("a.txt", FileSource.WORKTREE))
        self.select("staged.txt")
        self.assertEqual(self.ctx.selection.file, FileSelection("staged.txt", FileSource.INDEX))

    def test_refresh_keeps_path_and_updates_source(self):
        (self.repo / "a.txt").write_text("changed")
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.select("a.txt")
        rec = SignalRecorder(self.ctx.selection, "file_changed")
        git(self.repo, "add", "a.txt")
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        self.assertEqual(self.tree.currentItem().text(0), "a.txt")
        self.assertEqual(rec.events, [("file_changed", FileSelection("a.txt", FileSource.INDEX))])

    def test_selection_cleared_when_file_is_gone(self):
        (self.repo / "a.txt").write_text("changed")
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.select("a.txt")
        git(self.repo, "commit", "-qam", "commit it")
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        self.assertEqual(self.rows(), {})
        self.assertIsNone(self.ctx.selection.file)
        self.assertEqual(self.panel.header, "Files")

    def test_apply_theme_recolours_rows(self):
        (self.repo / "a.txt").write_text("changed")
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        status = dataclasses.replace(LIGHT_THEME.status, modified="#123456")
        self.ctx.theme.set_theme(dataclasses.replace(LIGHT_THEME, status=status))
        self.assertEqual(self.tree.topLevelItem(0).foreground(2).color().name(), "#123456")


if __name__ == "__main__":
    unittest.main()
