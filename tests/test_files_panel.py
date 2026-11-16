import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QItemSelectionModel, Qt

from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.ui.panels.files_panel import FilesPanel
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from tests.support import (
    SignalRecorder,
    commit_file,
    git,
    make_panel_context,
    make_repo,
    qt_app,
    wait_idle,
)

W, I = FileSource.WORKTREE, FileSource.INDEX


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
        self.session = self.ctx.session
        self.panel = FilesPanel(self.ctx)
        self.addCleanup(self.panel.deleteLater)
        self.tree = self.panel._tree

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def open(self):
        self.session.open(self.repo)
        wait_idle(self.session)

    def refresh(self):
        self.session.refresh()
        wait_idle(self.session)

    def groups(self):
        """{group title: [(path, status), ...]}"""
        result = {}
        for g in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g)
            result[group.text(0)] = [(group.child(i).text(0), group.child(i).text(1))
                                     for i in range(group.childCount())]
        return result

    def item(self, path, source):
        for g in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g)
            for i in range(group.childCount()):
                child = group.child(i)
                if child.data(0, Qt.ItemDataRole.UserRole)[0] == FileSelection(path, source):
                    return child
        self.fail(f"no row {path!r} in {source}")

    def select(self, *keys):
        """Select rows like the user: the first becomes current, the rest are added."""
        self.tree.setCurrentItem(self.item(*keys[0]))
        model = self.tree.selectionModel()
        for key in keys[1:]:
            model.select(self.tree.indexFromItem(self.item(*key)),
                         QItemSelectionModel.SelectionFlag.Select)

    # ---- rows --------------------------------------------------------------

    def test_empty_state(self):
        self.assertEqual(self.groups(), {})
        self.assertEqual(self.panel.header, "Files")

    def test_unstaged_and_staged_groups(self):
        (self.repo / "a.txt").write_text("staged")
        git(self.repo, "add", "a.txt")
        (self.repo / "a.txt").write_text("staged, then changed")  # in both groups
        (self.repo / "new.txt").write_text("hi")
        git(self.repo, "mv", "old.txt", "renamed.txt")
        self.open()
        self.assertEqual(self.groups(), {
            "Unstaged (2)": [("a.txt", "Modified"), ("new.txt", "Untracked")],
            "Staged (2)": [("a.txt", "Modified"), ("old.txt → renamed.txt", "Renamed")],
        })
        self.assertEqual(self.panel.header, "Files (3)")
        status = LIGHT_THEME.status
        self.assertEqual(self.item("new.txt", W).foreground(1).color().name(), status.untracked)
        self.assertEqual(self.item("renamed.txt", I).foreground(1).color().name(), status.renamed)
        group = self.tree.topLevelItem(0)
        self.assertFalse(group.flags() & Qt.ItemFlag.ItemIsSelectable)
        self.assertTrue(group.font(0).bold())

    def test_apply_theme_recolours_rows(self):
        (self.repo / "a.txt").write_text("changed")
        self.open()
        status = dataclasses.replace(LIGHT_THEME.status, modified="#123456")
        self.ctx.theme.set_theme(dataclasses.replace(LIGHT_THEME, status=status))
        self.assertEqual(self.item("a.txt", W).foreground(1).color().name(), "#123456")

    # ---- selection ---------------------------------------------------------

    def test_rows_select_their_side_and_several_can_be_selected(self):
        (self.repo / "a.txt").write_text("changed")
        (self.repo / "staged.txt").write_text("x")
        git(self.repo, "add", "staged.txt")
        self.open()
        self.select(("staged.txt", I), ("a.txt", W))
        selection = self.ctx.selection
        self.assertEqual(selection.file, FileSelection("staged.txt", I))
        self.assertEqual(selection.files, (FileSelection("a.txt", W), FileSelection("staged.txt", I)))

    def test_refresh_keeps_the_row_or_takes_the_same_path_in_the_other_group(self):
        (self.repo / "a.txt").write_text("changed")
        self.open()
        self.select(("a.txt", W))
        rec = SignalRecorder(self.ctx.selection, "file_changed")
        self.refresh()
        self.assertEqual(rec.events, [])  # nothing changed
        git(self.repo, "add", "a.txt")  # outside the app: the row moves
        self.refresh()
        self.assertEqual(rec.events, [("file_changed", FileSelection("a.txt", I))])

    def test_selection_cleared_when_file_is_gone(self):
        (self.repo / "a.txt").write_text("changed")
        self.open()
        self.select(("a.txt", W))
        git(self.repo, "commit", "-qam", "commit it")
        self.refresh()
        self.assertEqual(self.groups(), {})
        self.assertIsNone(self.ctx.selection.file)
        self.assertEqual(self.ctx.selection.files, ())

    # ---- staging -----------------------------------------------------------

    def test_double_click_toggles_and_the_selection_follows(self):
        (self.repo / "a.txt").write_text("1\n2\n")
        self.open()
        self.select(("a.txt", W))
        self.tree.itemDoubleClicked.emit(self.item("a.txt", W), 0)
        wait_idle(self.session)
        self.assertEqual(self.groups(), {"Staged (1)": [("a.txt", "Modified")]})
        self.assertEqual(self.ctx.selection.file, FileSelection("a.txt", I))
        self.tree.itemDoubleClicked.emit(self.item("a.txt", I), 0)
        wait_idle(self.session)
        self.assertEqual(self.ctx.selection.file, FileSelection("a.txt", W))

    def test_stage_follows_to_the_staged_row_though_unstaged_changes_remain(self):
        (self.repo / "a.txt").write_text("staged")
        git(self.repo, "add", "a.txt")
        (self.repo / "a.txt").write_text("more")
        (self.repo / "b.txt").write_text("new")
        self.open()
        self.select(("b.txt", W))
        self.ctx.actions.stage.trigger()
        wait_idle(self.session)
        self.assertEqual(self.ctx.selection.file, FileSelection("b.txt", I))
        # A partly staged file: Unstage moves to its unstaged row.
        self.select(("a.txt", I))
        self.ctx.actions.unstage.trigger()
        wait_idle(self.session)
        self.assertEqual(self.ctx.selection.file, FileSelection("a.txt", W))

    def test_several_rows_stage_together_and_stay_selected(self):
        for name in ("x.txt", "y.txt"):
            (self.repo / name).write_text(name)
        self.open()
        self.select(("x.txt", W), ("y.txt", W))
        self.ctx.actions.stage.trigger()
        wait_idle(self.session)
        self.assertEqual(self.ctx.selection.files, (FileSelection("x.txt", I), FileSelection("y.txt", I)))

    def test_follow_waits_for_a_status_that_has_the_row(self):
        (self.repo / "a.txt").write_text("changed")
        self.open()
        self.select(("a.txt", W))
        self.panel._follow_to(I)  # as if Stage ran, but the status hasn't changed yet
        self.refresh()
        self.assertEqual(self.ctx.selection.file, FileSelection("a.txt", W))
        git(self.repo, "add", "a.txt")
        (self.repo / "a.txt").write_text("changed again")  # in both groups now
        self.refresh()
        self.assertEqual(self.ctx.selection.file, FileSelection("a.txt", I))

    def test_choosing_a_row_cancels_the_follow(self):
        (self.repo / "a.txt").write_text("changed")
        (self.repo / "b.txt").write_text("new")
        self.open()
        self.select(("a.txt", W))
        self.panel._follow_to(I)
        self.select(("b.txt", W))
        self.assertEqual(self.panel._follow, {})

    def test_enter_toggles_the_selected_rows(self):
        (self.repo / "a.txt").write_text("changed")
        self.open()
        self.select(("a.txt", W))
        with mock.patch.object(self.session, "stage") as stage:
            self.panel._toggle(self.tree.selectedItems())
        stage.assert_called_once_with(["a.txt"])

    def test_context_menu_shows_the_staging_actions(self):
        actions = self.ctx.actions
        menu = self.panel._menu()
        self.addCleanup(menu.deleteLater)
        self.assertEqual([a for a in menu.actions() if not a.isSeparator()],
                         [actions.stage, actions.unstage, actions.discard,
                          actions.stage_all, actions.unstage_all])


if __name__ == "__main__":
    unittest.main()
