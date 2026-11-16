import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QItemSelectionModel, Qt

from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.ui.panels.files_panel import FilesPanel
from rzgitclient.ui.panels.staged_panel import StagedPanel
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from tests.support import commit_file, git, make_panel_context, make_repo, qt_app, wait_idle

W, I = FileSource.WORKTREE, FileSource.INDEX


class FilesPanelTests(unittest.TestCase):
    """Unstaged Files, and how it shares the selection with Staged Files (§5.4)."""

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
        self.unstaged, self.staged = FilesPanel(self.ctx), StagedPanel(self.ctx)
        for panel in (self.unstaged, self.staged):
            self.addCleanup(panel.deleteLater)

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

    def tree(self, source):
        panel = self.unstaged if source is W else self.staged
        self.assertIsNotNone(panel._list)  # Staged Files builds it when something is staged
        return panel._list._tree

    def shown(self, source):
        """[(path, status), ...] of a panel."""
        tree = self.tree(source)
        return [(tree.topLevelItem(i).text(0), tree.topLevelItem(i).text(1))
                for i in range(tree.topLevelItemCount())]

    def item(self, path, source):
        tree = self.tree(source)
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole)[0] == FileSelection(path, source):
                return item
        self.fail(f"no row {path!r} in {source}")

    def highlighted(self, source):
        tree = self.tree(source)
        return sorted(i.data(0, Qt.ItemDataRole.UserRole)[0].path for i in tree.selectedItems())

    def select(self, source, *paths):
        """Select rows like the user: the first becomes current, the rest are added."""
        tree = self.tree(source)
        # Not setCurrentItem: it reads the keyboard modifiers, which another test may leave.
        tree.selectionModel().setCurrentIndex(
            tree.indexFromItem(self.item(paths[0], source)),
            QItemSelectionModel.SelectionFlag.ClearAndSelect)
        for path in paths[1:]:
            tree.selectionModel().select(tree.indexFromItem(self.item(path, source)),
                                         QItemSelectionModel.SelectionFlag.Select)

    # ---- rows --------------------------------------------------------------

    def test_empty_state(self):
        self.assertEqual(self.shown(W), [])
        self.assertIsNone(self.staged._list)  # built when something is first staged
        self.assertEqual((self.unstaged.header, self.staged.header),
                         ("Unstaged Files", "Staged Files"))
        self.assertTrue(self.staged.collapsed)
        self.assertFalse(self.unstaged.collapsed)

    def test_each_panel_lists_its_side(self):
        (self.repo / "a.txt").write_text("staged")
        git(self.repo, "add", "a.txt")
        (self.repo / "a.txt").write_text("staged, then changed")  # in both panels
        (self.repo / "new.txt").write_text("hi")
        git(self.repo, "mv", "old.txt", "renamed.txt")
        self.open()
        self.assertEqual(self.shown(W), [("a.txt", "Modified"), ("new.txt", "Untracked")])
        self.assertEqual(self.shown(I), [("a.txt", "Modified"), ("old.txt → renamed.txt", "Renamed")])
        self.assertEqual((self.unstaged.header, self.staged.header),
                         ("Unstaged Files (2)", "Staged Files (2)"))
        self.assertFalse(self.staged.collapsed)
        status = LIGHT_THEME.status
        self.assertEqual(self.item("new.txt", W).foreground(1).color().name(), status.untracked)
        self.assertEqual(self.item("renamed.txt", I).foreground(1).color().name(), status.renamed)

    def test_staged_files_collapses_when_the_last_file_is_unstaged(self):
        (self.repo / "a.txt").write_text("2")
        git(self.repo, "add", "a.txt")
        self.open()
        self.assertFalse(self.staged.collapsed)
        git(self.repo, "reset", "-q")
        self.refresh()
        self.assertTrue(self.staged.collapsed)

    def test_a_list_built_after_the_window_shows_has_the_same_rows(self):
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        window = QWidget()
        self.addCleanup(window.deleteLater)
        box = QVBoxLayout(window)
        for panel in (self.unstaged, self.staged):
            box.addWidget(panel)
        window.show()
        self.addCleanup(window.hide)
        (self.repo / "a.txt").write_text("2")
        (self.repo / "b.txt").write_text("b")
        git(self.repo, "add", "a.txt")
        self.open()  # Staged Files builds its list now, while shown
        rect = lambda source, path: self.tree(source).visualItemRect(self.item(path, source))
        self.assertEqual(rect(I, "a.txt").height(), rect(W, "b.txt").height())

    def test_apply_theme_recolours_rows(self):
        (self.repo / "a.txt").write_text("changed")
        self.open()
        status = dataclasses.replace(LIGHT_THEME.status, modified="#123456")
        self.ctx.theme.set_theme(dataclasses.replace(LIGHT_THEME, status=status))
        self.assertEqual(self.item("a.txt", W).foreground(1).color().name(), "#123456")

    # ---- one selection at a time -------------------------------------------

    def test_several_rows_of_a_panel_can_be_selected(self):
        (self.repo / "a.txt").write_text("changed")
        (self.repo / "b.txt").write_text("new")
        self.open()
        self.select(W, "b.txt", "a.txt")
        selection = self.ctx.selection
        self.assertEqual(selection.file, FileSelection("b.txt", W))
        self.assertEqual(selection.files, (FileSelection("a.txt", W), FileSelection("b.txt", W)))

    def test_clicking_one_row_after_another_shows_each(self):
        (self.repo / "a.txt").write_text("changed")
        (self.repo / "b.txt").write_text("new")
        (self.repo / "c.txt").write_text("new")
        self.open()
        tree, flags = self.tree(W), QItemSelectionModel.SelectionFlag
        for path in ("a.txt", "b.txt", "c.txt"):
            with self.subTest(path=path):
                # A mouse press's order: current first, still unselected, then the selection.
                index = tree.indexFromItem(self.item(path, W))
                tree.selectionModel().setCurrentIndex(index, flags.NoUpdate)
                tree.selectionModel().select(index, flags.ClearAndSelect | flags.Rows)
                self.assertEqual(self.ctx.selection.file, FileSelection(path, W))
                self.assertEqual(self.highlighted(W), [path])

    def test_selecting_in_one_panel_clears_the_other(self):
        (self.repo / "a.txt").write_text("changed")
        (self.repo / "s.txt").write_text("x")
        git(self.repo, "add", "s.txt")
        self.open()
        self.select(W, "a.txt")
        self.select(I, "s.txt")
        self.assertEqual(self.ctx.selection.files, (FileSelection("s.txt", I),))
        self.assertEqual((self.highlighted(W), self.highlighted(I)), ([], ["s.txt"]))
        self.select(W, "a.txt")
        self.assertEqual((self.highlighted(W), self.highlighted(I)), (["a.txt"], []))
        self.assertEqual(self.ctx.selection.file, FileSelection("a.txt", W))

    def test_a_row_staged_outside_the_app_moves_to_staged_files(self):
        (self.repo / "a.txt").write_text("changed")
        self.open()
        self.select(W, "a.txt")
        git(self.repo, "add", "a.txt")
        self.refresh()
        self.assertEqual(self.ctx.selection.file, FileSelection("a.txt", I))
        self.assertEqual((self.highlighted(W), self.highlighted(I)), ([], ["a.txt"]))

    def test_selection_cleared_when_file_is_gone(self):
        (self.repo / "a.txt").write_text("changed")
        self.open()
        self.select(W, "a.txt")
        git(self.repo, "commit", "-qam", "commit it")
        self.refresh()
        self.assertIsNone(self.ctx.selection.file)
        self.assertEqual(self.ctx.selection.files, ())

    # ---- staging -----------------------------------------------------------

    def test_double_click_moves_the_file_and_the_selection_follows(self):
        (self.repo / "a.txt").write_text("1\n2\n")
        self.open()
        self.select(W, "a.txt")
        self.tree(W).itemDoubleClicked.emit(self.item("a.txt", W), 0)
        wait_idle(self.session)
        self.assertEqual((self.shown(W), self.shown(I)), ([], [("a.txt", "Modified")]))
        self.assertEqual(self.ctx.selection.file, FileSelection("a.txt", I))
        self.assertEqual(self.highlighted(I), ["a.txt"])
        self.tree(I).itemDoubleClicked.emit(self.item("a.txt", I), 0)
        wait_idle(self.session)
        self.assertEqual(self.ctx.selection.file, FileSelection("a.txt", W))
        self.assertTrue(self.staged.collapsed)

    def test_stage_follows_to_staged_files_though_unstaged_changes_remain(self):
        (self.repo / "a.txt").write_text("staged")
        git(self.repo, "add", "a.txt")
        (self.repo / "a.txt").write_text("more")
        (self.repo / "b.txt").write_text("new")
        self.open()
        self.select(W, "b.txt")
        self.ctx.actions.stage.trigger()
        wait_idle(self.session)
        self.assertEqual(self.ctx.selection.file, FileSelection("b.txt", I))
        # A partly staged file: Unstage moves to its row in Unstaged Files.
        self.select(I, "a.txt")
        self.ctx.actions.unstage.trigger()
        wait_idle(self.session)
        self.assertEqual(self.ctx.selection.file, FileSelection("a.txt", W))
        self.assertEqual((self.highlighted(W), self.highlighted(I)), (["a.txt"], []))

    def test_several_rows_stage_together_and_stay_selected(self):
        for name in ("x.txt", "y.txt"):
            (self.repo / name).write_text(name)
        self.open()
        self.select(W, "x.txt", "y.txt")
        self.ctx.actions.stage.trigger()
        wait_idle(self.session)
        self.assertEqual(self.ctx.selection.files, (FileSelection("x.txt", I), FileSelection("y.txt", I)))
        self.assertEqual(self.highlighted(I), ["x.txt", "y.txt"])

    def test_enter_moves_the_selected_rows(self):
        (self.repo / "a.txt").write_text("changed")
        self.open()
        self.select(W, "a.txt")
        with mock.patch.object(self.session, "stage") as stage:
            self.unstaged._list._move(self.tree(W).selectedItems())
        stage.assert_called_once_with(("a.txt",))

    def test_each_context_menu_shows_its_panel_s_actions(self):
        actions = self.ctx.actions
        (self.repo / "a.txt").write_text("2")
        git(self.repo, "add", "a.txt")
        self.open()
        for panel, want in ((self.unstaged, [actions.stage, actions.discard, actions.stage_all]),
                            (self.staged, [actions.unstage, actions.unstage_all])):
            with self.subTest(panel.title):
                menu = panel._list._menu()
                self.addCleanup(menu.deleteLater)
                self.assertEqual([a for a in menu.actions() if not a.isSeparator()], want)


if __name__ == "__main__":
    unittest.main()
