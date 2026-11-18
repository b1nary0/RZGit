import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTreeWidgetItemIterator

from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.ui.panels.files_panel import FilesPanel
from rzgitclient.ui.panels.staged_panel import StagedPanel
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from rzgitclient.ui.widgets.column_header import ColumnHeader
from tests.support import (click_column_label, commit_file, drag_column_edge, git,
                           make_panel_context, make_repo, qt_app, wait_idle)

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

    def all_items(self, source):
        found, it = [], QTreeWidgetItemIterator(self.tree(source))
        while (item := it.value()) is not None:
            found.append(item)
            it += 1
        return found

    def item(self, path, source):
        for item in self.all_items(source):
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == FileSelection(path, source):
                return item
        self.fail(f"no row {path!r} in {source}")

    def folder(self, path, source):
        for item in self.all_items(source):
            if item.data(0, Qt.ItemDataRole.UserRole + 1) == path:
                return item
        self.fail(f"no folder {path!r} in {source}")

    def nested(self, source, parent=None):
        """The rows as nested lists: a folder as (name, [...]), a file as its text."""
        tree = self.tree(source)
        count = parent.childCount() if parent else tree.topLevelItemCount()
        at = parent.child if parent else tree.topLevelItem
        return [(item.text(0), self.nested(source, item)) if item.childCount() else item.text(0)
                for item in map(at, range(count))]

    def highlighted(self, source):
        """Paths of the highlighted file rows (folder rows left out)."""
        tree = self.tree(source)
        return sorted(data[0].path for i in tree.selectedItems()
                      if (data := i.data(0, Qt.ItemDataRole.UserRole)))

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

    def test_columns_are_labelled_name_and_status(self):
        """RZG-41: on a ColumnHeader, keyed by the panel's id."""
        tree = self.unstaged._list._tree
        header = tree.header()
        self.assertIsInstance(header, ColumnHeader)
        self.assertFalse(tree.isHeaderHidden())
        self.assertEqual([header.label(c) for c in range(2)], ["Name", "Status"])
        self.assertEqual(header.panel_id, "files")

    def test_a_new_list_hides_what_was_hidden(self):
        self.ctx.columns.set_hidden("files", "Status", True)
        panel = FilesPanel(self.ctx)
        self.addCleanup(panel.deleteLater)
        self.assertTrue(panel._list._tree.isColumnHidden(1))

    def test_the_columns_fill_the_panel_and_a_dragged_status_width_stays(self):
        """RZG-40: Name and Status always fill the width; the separator between them
        moves only those two, and the dragged Status width survives a refresh."""
        (self.repo / "a_file_with_a_long_name_that_does_not_fit.txt").write_text("hi")
        self.open()
        self.unstaged.resize(250, 300)
        self.unstaged.show()
        QTest.qWaitForWindowExposed(self.unstaged)
        tree = self.unstaged._list._tree
        header = tree.header()
        self.assertEqual(header.sectionSize(0) + header.sectionSize(1), header.width())
        name, status = header.sectionSize(0), header.sectionSize(1)
        drag_column_edge(header, 0, -30)
        self.assertEqual((header.sectionSize(0), header.sectionSize(1)), (name - 30, status + 30))
        QCoreApplication.processEvents()
        self.assertEqual(tree.horizontalScrollBar().maximum(), 0)  # never wider than the panel
        self.assertEqual(self.ctx.columns.widths("files"), {"Status": status + 30})
        (self.repo / "other.txt").write_text("more")
        self.refresh()
        self.assertEqual(header.sectionSize(1), status + 30)
        self.assertEqual(header.sectionSize(0) + header.sectionSize(1), header.width())

    def test_each_panel_lists_its_side(self):
        (self.repo / "a.txt").write_text("staged")
        git(self.repo, "add", "a.txt")
        (self.repo / "a.txt").write_text("staged, then changed")  # in both panels
        (self.repo / "new.txt").write_text("hi")
        git(self.repo, "mv", "old.txt", "renamed.txt")
        self.open()
        self.assertEqual(self.shown(W), [("a.txt", "M"), ("new.txt", "U")])
        self.assertEqual(self.shown(I), [("a.txt", "M"), ("old.txt → renamed.txt", "R")])
        self.assertEqual((self.unstaged.header, self.staged.header),
                         ("Unstaged Files (2)", "Staged Files (2)"))
        self.assertFalse(self.staged.collapsed)
        status = LIGHT_THEME.status
        self.assertEqual(self.item("new.txt", W).foreground(1).color().name(), status.untracked)
        self.assertEqual(self.item("renamed.txt", I).foreground(1).color().name(), status.renamed)
        # Only the letter is coloured; the full word is in the tooltip.
        self.assertIsNone(self.item("new.txt", W).data(0, Qt.ItemDataRole.ForegroundRole))
        self.assertEqual(self.item("new.txt", W).toolTip(0), "new.txt · Untracked")
        self.assertEqual(self.item("new.txt", W).toolTip(1), "new.txt · Untracked")

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
        self.assertEqual((self.shown(W), self.shown(I)), ([], [("a.txt", "M")]))
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


    # ---- folder tree (RZG-25) -----------------------------------------------

    def make_nested_changes(self):
        for path in ("src/app/ui/panel.py", "src/app/ui/theme.py", "src/app/core.py", "docs/a.md"):
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(path)
        (self.repo / "a.txt").write_text("changed")

    def test_files_sit_under_their_folders_by_name(self):
        self.make_nested_changes()
        self.open()
        self.assertEqual(self.nested(W), [
            ("docs", ["a.md"]),
            ("src/app", [("ui", ["panel.py", "theme.py"]), "core.py"]),
            "a.txt",
        ])
        self.assertEqual(self.item("src/app/ui/panel.py", W).text(1), "U")
        self.assertEqual(self.item("src/app/ui/panel.py", W).toolTip(0),
                         "src/app/ui/panel.py · Untracked")
        folder = self.folder("src/app", W)
        self.assertEqual(folder.text(1), "")
        self.assertEqual(folder.foreground(0).color().name(), LIGHT_THEME.colors.text_muted)
        self.assertEqual(folder.toolTip(0), "src/app")
        self.assertEqual(self.unstaged.header, "Unstaged Files (5)")  # files, not folders

    def test_a_rename_shows_both_names(self):
        (self.repo / "dir").mkdir()
        git(self.repo, "mv", "old.txt", "dir/new.txt")
        git(self.repo, "mv", "a.txt", "b.txt")
        self.open()
        self.assertEqual(self.nested(I), [("dir", ["old.txt → new.txt"]), "a.txt → b.txt"])

    def test_the_toggle_switches_to_flat_paths_and_back(self):
        self.make_nested_changes()
        self.open()
        self.ctx.actions.file_tree.setChecked(False)
        self.assertEqual(self.nested(W), ["a.txt", "docs/a.md", "src/app/core.py",
                                          "src/app/ui/panel.py", "src/app/ui/theme.py"])
        self.ctx.actions.file_tree.setChecked(True)
        self.assertEqual(self.nested(W)[0], ("docs", ["a.md"]))

    def test_folders_start_expanded_and_a_collapsed_one_stays_collapsed(self):
        self.make_nested_changes()
        self.open()
        self.assertTrue(all(self.folder(p, W).isExpanded()
                            for p in ("docs", "src/app", "src/app/ui")))
        self.folder("src/app/ui", W).setExpanded(False)  # as the user would
        (self.repo / "docs/b.md").write_text("more")
        self.refresh()
        self.assertFalse(self.folder("src/app/ui", W).isExpanded())
        self.assertTrue(self.folder("docs", W).isExpanded())
        self.ctx.actions.file_tree.setChecked(False)
        self.ctx.actions.file_tree.setChecked(True)
        self.assertFalse(self.folder("src/app/ui", W).isExpanded())
        self.folder("src/app/ui", W).setExpanded(True)
        self.refresh()
        self.assertTrue(self.folder("src/app/ui", W).isExpanded())

    def test_indent_guides_are_drawn_beside_nested_rows(self):
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        window = QWidget()
        self.addCleanup(window.deleteLater)
        QVBoxLayout(window).addWidget(self.unstaged)
        self.ctx.theme.set_theme(LIGHT_THEME)  # the stylesheet, as the app has it
        self.make_nested_changes()
        self.open()
        window.resize(300, 400)
        window.show()
        self.addCleanup(window.hide)
        QCoreApplication.processEvents()
        tree = self.tree(W)
        image = tree.viewport().grab().toImage()
        guide, indent = LIGHT_THEME.colors.tree_guide, tree.indentation()
        row = tree.visualItemRect(self.item("src/app/ui/panel.py", W))  # depth 2
        colour = lambda x, r: image.pixelColor(x, r.center().y()).name()
        self.assertEqual([colour(level * indent + indent // 2, row) for level in (0, 1)],
                         [guide, guide])
        top = tree.visualItemRect(self.item("a.txt", W))  # top level: no guide
        self.assertNotEqual(colour(indent // 2, top), guide)

    def test_collapsed_folders_are_forgotten_for_another_repository(self):
        self.make_nested_changes()
        self.open()
        self.folder("docs", W).setExpanded(False)
        self.session.open(self.repo)  # opened again: a new repository for the panel
        wait_idle(self.session)
        self.assertTrue(self.folder("docs", W).isExpanded())

    def test_selecting_a_folder_selects_the_files_under_it(self):
        self.make_nested_changes()
        self.open()
        tree = self.tree(W)
        tree.selectionModel().setCurrentIndex(tree.indexFromItem(self.folder("src/app", W)),
                                              QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.assertEqual([f.path for f in self.ctx.selection.files],
                         ["src/app/ui/panel.py", "src/app/ui/theme.py", "src/app/core.py"])
        self.assertIsNone(self.ctx.selection.file)  # a folder: nothing for Diff
        self.assertEqual(self.ctx.actions.selected(W), (
            "src/app/ui/panel.py", "src/app/ui/theme.py", "src/app/core.py"))

    def test_staging_a_folder_stages_its_files_and_the_selection_follows(self):
        self.make_nested_changes()
        self.open()
        tree = self.tree(W)
        tree.selectionModel().setCurrentIndex(tree.indexFromItem(self.folder("src/app/ui", W)),
                                              QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.ctx.actions.stage.trigger()
        wait_idle(self.session)
        self.assertEqual(self.nested(I), [("src/app/ui", ["panel.py", "theme.py"])])
        self.assertEqual(self.highlighted(I), ["src/app/ui/panel.py", "src/app/ui/theme.py"])
        self.assertEqual(self.highlighted(W), [])

    def test_double_click_on_a_folder_does_not_stage(self):
        self.make_nested_changes()
        self.open()
        with mock.patch.object(self.session, "stage") as stage:
            self.tree(W).itemDoubleClicked.emit(self.folder("docs", W), 0)
        stage.assert_not_called()

    def test_enter_on_a_folder_moves_its_files(self):
        self.make_nested_changes()
        self.open()
        with mock.patch.object(self.session, "stage") as stage:
            self.unstaged._list._move([self.folder("docs", W)])
        stage.assert_called_once_with(("docs/a.md",))


    # ---- Sort By (RZG-43) --------------------------------------------------------

    def make_sortable_changes(self):
        self.make_nested_changes()
        (self.repo / "old.txt").unlink()  # D, which sorts before M and U

    def test_sort_by_is_the_header_menu(self):
        self.assertEqual(self.unstaged.header_actions, tuple(self.unstaged.sort.header_actions()))
        self.assertEqual([a.text() for a in self.unstaged.sort.actions()[:2]], ["Name", "Status"])

    def test_flat_by_status_then_git_s_order(self):
        self.make_sortable_changes()
        self.open()
        self.ctx.actions.file_tree.setChecked(False)
        self.addCleanup(self.ctx.actions.file_tree.setChecked, True)
        self.unstaged.sort.choose("status")
        self.assertEqual(self.shown(W), [
            ("old.txt", "D"), ("a.txt", "M"), ("docs/a.md", "U"), ("src/app/core.py", "U"),
            ("src/app/ui/panel.py", "U"), ("src/app/ui/theme.py", "U")])
        self.unstaged.sort.set_reversed(True)
        self.assertEqual([path for path, _ in self.shown(W)][:2], ["docs/a.md", "src/app/core.py"])
        self.assertEqual([path for path, _ in self.shown(W)][-2:], ["a.txt", "old.txt"])

    def test_in_the_tree_the_order_applies_inside_each_folder(self):
        self.make_sortable_changes()
        self.open()
        self.unstaged.sort.choose("status")
        self.assertEqual(self.nested(W), [
            ("docs", ["a.md"]),
            ("src/app", [("ui", ["panel.py", "theme.py"]), "core.py"]),
            "old.txt", "a.txt",
        ])

    def test_name_reversed_reverses_the_folders_too_they_stay_first(self):
        self.make_sortable_changes()
        self.open()
        self.unstaged.sort.set_reversed(True)
        self.assertEqual(self.nested(W), [
            ("src/app", [("ui", ["theme.py", "panel.py"]), "core.py"]),
            ("docs", ["a.md"]),
            "old.txt", "a.txt",
        ])

    def test_the_status_label_sorts_and_the_selection_stays(self):
        self.make_sortable_changes()
        self.open()
        self.select(W, "a.txt")
        labels = self.unstaged._list.labels
        click_column_label(labels, 1)
        self.assertEqual(self.unstaged.sort.order.key, "status")
        self.assertEqual(labels.sort_indicator, ("Status", False))
        self.assertEqual(self.nested(W)[-2:], ["old.txt", "a.txt"])
        self.assertEqual(self.highlighted(W), ["a.txt"])
        self.assertEqual(self.tree(W).currentItem(), self.item("a.txt", W))
        self.refresh()  # a refresh keeps the order
        self.assertEqual(self.nested(W)[-2:], ["old.txt", "a.txt"])

    def test_staged_files_has_its_menu_before_its_list_and_the_list_uses_it(self):
        self.assertIsNone(self.staged._list)
        self.assertEqual(self.staged.header_actions, tuple(self.staged.sort.header_actions()))
        self.staged.sort.set_reversed(True)
        for name in ("a.txt", "old.txt"):
            (self.repo / name).write_text("changed")
        git(self.repo, "add", "a.txt", "old.txt")
        self.open()
        self.assertEqual(self.nested(I), ["old.txt", "a.txt"])
        self.assertEqual(self.ctx.sorts.order("staged").reversed, True)
        self.assertIsNone(self.ctx.sorts.order("files"))  # each panel its own


    # ---- Group By (RZG-53) -------------------------------------------------------

    UNTRACKED = ["docs/a.md", "src/app/core.py", "src/app/ui/panel.py", "src/app/ui/theme.py"]

    def heading(self, name, source=W):
        tree = self.tree(source)
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.text(0).startswith(f"{name} ("):
                return item
        self.fail(f"no heading {name!r} in {source}")

    def flat(self):
        self.ctx.actions.file_tree.setChecked(False)
        self.addCleanup(self.ctx.actions.file_tree.setChecked, True)

    def test_group_by_status_comes_before_sort_by(self):
        sort = self.unstaged.sort
        self.assertEqual(self.unstaged.header_actions,
                         (sort.group_menu.menuAction(), sort.menuAction()))
        self.assertEqual([a.text() for a in sort.group_menu.actions() if not a.isSeparator()],
                         ["&None", "Status"])

    def test_flat_grouped_by_status_in_the_status_order(self):
        self.make_sortable_changes()
        self.open()
        self.flat()
        self.unstaged.sort.set_group("status")
        self.assertEqual(self.nested(W), [
            ("Modified (1)", ["a.txt"]), ("Deleted (1)", ["old.txt"]),
            ("Untracked (4)", self.UNTRACKED)])
        tree = self.tree(W)
        self.assertTrue(tree.rootIsDecorated())
        self.assertTrue(self.heading("Modified").isFirstColumnSpanned())
        self.assertTrue(self.heading("Modified").font(0).bold())
        self.assertEqual(self.unstaged.header, "Unstaged Files (6)")  # files, not headings
        self.unstaged.sort.set_reversed(True)  # inside the groups only
        self.assertEqual(self.nested(W)[2], ("Untracked (4)", self.UNTRACKED[::-1]))
        self.assertEqual(self.ctx.sorts.order("files").group, "status")
        self.unstaged.sort.set_group(None)
        self.assertFalse(tree.rootIsDecorated())
        self.assertEqual(tree.indentation(), 0)

    def test_in_the_tree_each_group_has_a_folder_tree_of_its_own(self):
        self.make_sortable_changes()
        commit_file(self.repo, "docs/b.md", "1", "add b")
        (self.repo / "docs/b.md").write_text("changed")
        self.open()
        self.unstaged.sort.set_group("status")
        self.assertEqual(self.nested(W), [
            ("Modified (2)", [("docs", ["b.md"]), "a.txt"]),
            ("Deleted (1)", ["old.txt"]),
            ("Untracked (4)", [("docs", ["a.md"]),
                               ("src/app", [("ui", ["panel.py", "theme.py"]), "core.py"])]),
        ])
        self.assertEqual(self.tree(W).guides_from, 1)  # no guide under the headings' arrows
        # A folder folded under one heading stays open under the other, through a refresh.
        docs = self.heading("Untracked").child(0)
        docs.setExpanded(False)
        (self.repo / "new.txt").write_text("new")  # so the refresh renders again
        self.refresh()
        self.assertEqual(self.heading("Untracked").text(0), "Untracked (5)")
        self.assertFalse(self.heading("Untracked").child(0).isExpanded())
        self.assertTrue(self.heading("Modified").child(0).isExpanded())

    def test_a_folded_heading_stays_folded_until_another_repository(self):
        self.make_sortable_changes()
        self.open()
        self.unstaged.sort.set_group("status")
        self.heading("Untracked").setExpanded(False)
        self.refresh()
        self.assertFalse(self.heading("Untracked").isExpanded())
        self.assertTrue(self.heading("Modified").isExpanded())
        self.assertTrue(self.folder("docs", W).isExpanded())  # its folders keep theirs
        self.session.open(self.repo)
        wait_idle(self.session)
        self.assertTrue(self.heading("Untracked").isExpanded())

    def test_selecting_a_heading_selects_its_files_and_stage_acts_on_them(self):
        self.make_sortable_changes()
        self.open()
        self.unstaged.sort.set_group("status")
        tree = self.tree(W)
        heading = self.heading("Untracked")
        self.assertTrue(heading.flags() & Qt.ItemFlag.ItemIsSelectable)
        tree.selectionModel().setCurrentIndex(tree.indexFromItem(heading),
                                              QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.assertEqual([f.path for f in self.ctx.selection.files],  # in tree order
                         ["docs/a.md", "src/app/ui/panel.py", "src/app/ui/theme.py",
                          "src/app/core.py"])
        self.assertIsNone(self.ctx.selection.file)  # a heading: nothing for Diff
        self.ctx.actions.stage.trigger()
        wait_idle(self.session)
        self.assertEqual(self.highlighted(I), self.UNTRACKED)

    def test_double_click_on_a_heading_does_not_stage(self):
        self.make_sortable_changes()
        self.open()
        self.unstaged.sort.set_group("status")
        with mock.patch.object(self.session, "stage") as stage:
            self.tree(W).itemDoubleClicked.emit(self.heading("Modified"), 0)
            self.unstaged._list._move([self.heading("Deleted")])  # Enter: its files
        stage.assert_called_once_with(("old.txt",))

    def test_the_selection_stays_through_grouping(self):
        self.make_sortable_changes()
        self.open()
        self.select(W, "a.txt", "docs/a.md")
        self.unstaged.sort.set_group("status")
        self.assertEqual(self.highlighted(W), ["a.txt", "docs/a.md"])
        self.assertEqual(self.tree(W).currentItem(), self.item("a.txt", W))

    def test_staged_files_group_on_their_own(self):
        self.staged.sort.set_group("status")
        (self.repo / "a.txt").write_text("changed")
        (self.repo / "new.txt").write_text("new")
        git(self.repo, "add", "a.txt", "new.txt")
        git(self.repo, "mv", "old.txt", "moved.txt")
        self.open()
        self.assertEqual(self.nested(I), [
            ("Modified (1)", ["a.txt"]), ("Added (1)", ["new.txt"]),
            ("Renamed (1)", ["old.txt → moved.txt"])])
        self.assertIsNone(self.ctx.sorts.order("files"))

    def test_headings_take_the_theme(self):
        from rzgitclient.ui.theme.app_theme import DARK_THEME
        self.make_sortable_changes()
        self.open()
        self.unstaged.sort.set_group("status")
        for theme in (DARK_THEME, LIGHT_THEME):
            self.ctx.theme.set_theme(theme)
            heading = self.heading("Modified")
            self.assertEqual(heading.foreground(0).color().name(), theme.colors.group_header_text)


if __name__ == "__main__":
    unittest.main()
