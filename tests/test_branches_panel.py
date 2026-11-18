import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from rzgitclient.core.sort_options import SortOrder
from rzgitclient.ui.app_actions import BranchOp, WorktreeOp
from rzgitclient.ui.panels.branches_panel import BranchesPanel
from tests.support import commit_file, git, make_panel_context, make_repo, qt_app, wait_idle


class BranchesPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        self.ctx = make_panel_context()
        self.panel = BranchesPanel(self.ctx)
        self.list = self.panel._list

    def tearDown(self):
        self._tmp.cleanup()

    def rows(self):
        return [self.list.topLevelItem(i).text(0) for i in range(self.list.topLevelItemCount())]

    def tree(self):
        """[(heading, [its rows])], or a row's text when it is at the top."""
        result = []
        for i in range(self.list.topLevelItemCount()):
            top = self.list.topLevelItem(i)
            if top.childCount():
                result.append((top.text(0), [top.child(j).text(0)
                                             for j in range(top.childCount())]))
            else:
                result.append(top.text(0))
        return result

    def test_empty_state(self):
        self.assertEqual(self.rows(), [])

    def test_fresh_repo_shows_placeholder(self):
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.assertEqual(self.rows(), ["main (no commits)"])

    def test_lists_branches_with_current_in_bold(self):
        commit_file(self.repo, "a.txt", "1", "first")
        git(self.repo, "branch", "feature")
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.assertEqual(self.rows(), ["feature", "main"])
        bold = {self.list.topLevelItem(i).text(0): self.list.topLevelItem(i).font(0).bold()
                for i in range(2)}
        self.assertEqual(bold, {"feature": False, "main": True})

    def commit_dated(self, name: str, when: str) -> None:
        (self.repo / f"{name}.txt").write_text(name)
        git(self.repo, "add", f"{name}.txt")
        subprocess.run(["git", "commit", "-q", "-m", name], cwd=self.repo, check=True,
                       env={**os.environ, "GIT_COMMITTER_DATE": when})

    def test_sort_by_name_or_last_commit(self):
        """RZG-43: newest first; the selection stays."""
        self.commit_dated("first", "2026-01-01T00:00:00+00:00")
        git(self.repo, "branch", "old")
        self.commit_dated("second", "2026-03-01T00:00:00+00:00")
        git(self.repo, "switch", "-q", "-c", "zed")
        self.commit_dated("third", "2026-02-01T00:00:00+00:00")
        git(self.repo, "switch", "-q", "main")
        git(self.repo, "reset", "-q", "--hard", "HEAD")
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.assertEqual(self.rows(), ["main", "old", "zed"])
        self.list.setCurrentItem(self.item("old"))
        self.panel.sort.choose("last_commit")
        self.assertEqual(self.rows(), ["main", "zed", "old"])  # Mar, Feb, Jan
        self.assertEqual(self.list.currentItem().text(0), "old")
        self.panel.sort.set_reversed(True)
        self.assertEqual(self.rows(), ["old", "zed", "main"])
        self.assertTrue(self.list.topLevelItem(2).font(0).bold())  # the current branch, still bold
        self.assertEqual(self.ctx.sorts.order("branches").key, "last_commit")

    def test_selection_survives_refresh(self):
        commit_file(self.repo, "a.txt", "1", "first")
        git(self.repo, "branch", "feature")
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.list.setCurrentItem(self.item("feature"))
        git(self.repo, "branch", "another")
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        self.assertEqual(self.rows(), ["another", "feature", "main"])
        self.assertEqual(self.list.currentItem().text(0), "feature")

    # ---- Group By (RZG-53) -------------------------------------------------

    def test_header_menu_is_group_by_then_sort_by(self):
        self.assertEqual(self.panel.header_actions, (self.panel.sort.group_menu.menuAction(),
                                                     self.panel.sort.menuAction()))
        self.assertEqual([a.text() for a in self.panel.sort.group_menu.actions()
                          if not a.isSeparator()], ["&None", "Prefix", "Merged"])

    def test_group_by_prefix_puts_others_last(self):
        self.open_with_branches("fix/b", "feature/x", "feature/a", "zed", "Fix/c")
        self.panel.sort.set_group("prefix")
        self.assertEqual(self.tree(), [
            ("feature/ (2)", ["feature/a", "feature/x"]),
            ("Fix/ (1)", ["Fix/c"]),
            ("fix/ (1)", ["fix/b"]),
            ("Other (2)", ["main", "zed"]),
        ])
        self.assertTrue(self.list.isHeaderHidden())
        self.assertTrue(self.list.rootIsDecorated())
        self.assertEqual(self.ctx.sorts.order("branches").group, "prefix")
        self.panel.sort.set_group(None)
        self.assertEqual(self.rows(), ["Fix/c", "feature/a", "feature/x", "fix/b", "main", "zed"])
        self.assertFalse(self.list.rootIsDecorated())

    def test_group_by_merged_sorts_inside_each_group(self):
        commit_file(self.repo, "a.txt", "1", "first")
        git(self.repo, "branch", "done")
        git(self.repo, "switch", "-q", "-c", "ahead")
        commit_file(self.repo, "b.txt", "2", "second")
        git(self.repo, "switch", "-q", "main")
        git(self.repo, "branch", "also")
        git(self.repo, "switch", "-q", "also")
        commit_file(self.repo, "c.txt", "3", "third")
        git(self.repo, "switch", "-q", "main")
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.panel.sort.set_group("merged")
        self.assertEqual(self.tree(), [("Not merged (2)", ["ahead", "also"]),
                                       ("Merged (2)", ["done", "main"])])
        self.panel.sort.set_reversed(True)  # only inside the groups
        self.assertEqual(self.tree(), [("Not merged (2)", ["also", "ahead"]),
                                       ("Merged (2)", ["main", "done"])])
        self.assertEqual(self.panel.sort.order.group, "merged")  # the sort kept the grouping

    def test_headings_fold_and_stay_folded_through_refresh(self):
        self.open_with_branches("feature/x", "zed")
        self.panel.sort.set_group("prefix")
        heading = self.list.topLevelItem(0)
        self.assertTrue(heading.isExpanded())
        heading.setExpanded(False)
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        self.assertFalse(self.list.topLevelItem(0).isExpanded())
        self.assertTrue(self.list.topLevelItem(1).isExpanded())
        self.ctx.session.open(self.repo)  # another repository: open again
        wait_idle(self.ctx.session)
        self.assertTrue(self.list.topLevelItem(0).isExpanded())

    def test_headings_are_not_selected_or_given_a_menu(self):
        self.open_with_branches("feature/x", "zed")
        self.panel.sort.set_group("prefix")
        self.list.setCurrentItem(self.item("feature/x"))
        heading = self.list.topLevelItem(1)  # Other
        self.assertFalse(heading.flags() & Qt.ItemFlag.ItemIsSelectable)
        rect = self.list.visualItemRect(heading)
        with mock.patch.object(self.panel, "menu_for") as menu_for:
            self.panel._show_menu(rect.center())
        menu_for.assert_not_called()
        seen = self.requests()
        self.list.itemDoubleClicked.emit(heading, 0)
        self.assertEqual(seen, [])
        # Down from feature/x skips the Other heading, to main.
        self.list.setFocus()
        QApplication.sendEvent(self.list, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Down,
                                                     Qt.KeyboardModifier.NoModifier))
        self.assertEqual(self.list.currentItem().text(0), "main")
        QApplication.sendEvent(self.list, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Up,
                                                     Qt.KeyboardModifier.NoModifier))
        self.assertEqual(self.list.currentItem().text(0), "feature/x")
        QApplication.sendEvent(self.list, QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Up,
                                                     Qt.KeyboardModifier.NoModifier))
        # Nothing above but a heading: it stays.
        self.assertEqual(self.list.currentItem().text(0), "feature/x")

    def test_grouping_is_read_when_built(self):
        self.ctx.sorts.set_order("branches", SortOrder("name", group="prefix"))
        panel = BranchesPanel(self.ctx)
        self.open_with_branches("feature/x")
        self.assertEqual(panel._list.topLevelItem(0).text(0), "feature/ (1)")
        self.assertTrue(panel.sort.group_menu.actions()[2].isChecked())  # None, ---, Prefix

    def test_selection_is_kept_through_regrouping(self):
        self.open_with_branches("feature/x", "zed")
        self.list.setCurrentItem(self.item("zed"))
        self.panel.sort.set_group("prefix")
        self.assertEqual(self.list.currentItem().text(0), "zed")
        self.panel.sort.set_group(None)
        self.assertEqual(self.list.currentItem().text(0), "zed")


    # ---- operations (spec §5.4) ------------------------------------------

    def open_with_branches(self, *names):
        commit_file(self.repo, "a.txt", "1", "first")
        for name in names:
            git(self.repo, "branch", name)
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)

    def item(self, text):
        return next(i for i in self.list.rows() if i.text(0) == text)

    def entries(self, menu):
        """[(text, enabled)], a submenu as (title, [its entries])."""
        result = []
        for action in menu.actions():
            if action.isSeparator():
                result.append("---")
            elif action.menu() is not None:
                result.append((action.text(), self.entries(action.menu())))
            else:
                result.append((action.text(), action.isEnabled()))
        return result

    def requests(self):
        seen = []
        self.ctx.actions.branch_requested.connect(lambda op, name: seen.append((op, name)))
        return seen

    def test_double_click_asks_to_switch_except_on_the_current_branch(self):
        self.open_with_branches("feature")
        seen = self.requests()
        self.list.itemDoubleClicked.emit(self.item("main"), 0)
        self.assertEqual(seen, [])
        self.list.itemDoubleClicked.emit(self.item("feature"), 0)
        self.assertEqual(seen, [(BranchOp.SWITCH, "feature")])

    def test_double_click_on_a_fresh_repo_s_row_does_nothing(self):
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        seen = self.requests()
        self.list.itemDoubleClicked.emit(self.list.topLevelItem(0), 0)
        self.assertEqual(seen, [])

    def test_menu_of_another_branch(self):
        self.open_with_branches("feature")
        self.assertEqual(self.entries(self.panel.menu_for("feature")), [
            ("Switch to feature", True),
            ("New Branch from feature…", True),
            ("New Worktree from feature…", True),
            ("Rename feature…", True),
            ("Delete feature…", True),
            ("Merge / Rebase", [("Merge feature into main…", True),
                                ("Rebase main onto feature…", True)]),
            "---",
            ("Copy Name", True),
        ])

    def test_menu_of_the_current_branch_disables_what_does_not_apply(self):
        self.open_with_branches("feature")
        self.assertEqual(self.entries(self.panel.menu_for("main")), [
            ("Switch to main (current branch)", False),
            ("New Branch from main…", True),
            ("New Worktree from main…", True),
            ("Rename main…", True),
            ("Delete main… (current branch)", False),
            ("Merge / Rebase", [("Merge main into main… (current branch)", False),
                                ("Rebase main onto main… (current branch)", False)]),
            "---",
            ("Copy Name", True),
        ])

    def test_menu_entries_request_their_operation(self):
        self.open_with_branches("feature")
        seen = self.requests()
        menu = self.panel.menu_for("feature")
        actions = [a for a in menu.actions() if not a.isSeparator()]
        submenu = next(a.menu() for a in actions if a.menu() is not None)
        worktree_seen = []
        self.ctx.actions.worktree_requested.connect(
            lambda op, target: worktree_seen.append((op, target)))
        for action in [*actions[:5], *submenu.actions()]:
            action.trigger()
        self.assertEqual(seen, [(op, "feature") for op in (
            BranchOp.SWITCH, BranchOp.NEW, BranchOp.RENAME, BranchOp.DELETE,
            BranchOp.MERGE, BranchOp.REBASE)])
        # New Worktree from X… asks for a worktree starting from X (spec §5.4).
        self.assertEqual(worktree_seen, [(WorktreeOp.NEW, "feature")])

    def test_copy_name(self):
        self.open_with_branches("feature/x")
        menu = self.panel.menu_for("feature/x")
        next(a for a in menu.actions() if a.text() == "Copy Name").trigger()
        self.assertEqual(QApplication.clipboard().text(), "feature/x")

    def test_detached_head_is_named_head_and_every_branch_applies(self):
        self.open_with_branches("feature")
        git(self.repo, "switch", "-q", "--detach")
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        entries = self.entries(self.panel.menu_for("main"))
        self.assertEqual(entries[5], ("Merge / Rebase", [("Merge main into HEAD…", True),
                                                         ("Rebase HEAD onto main…", True)]))
        self.assertTrue(entries[0][1])

    def test_an_ampersand_in_a_name_is_shown_not_a_shortcut(self):
        self.open_with_branches("a&b")
        self.assertEqual(self.panel.menu_for("a&b").actions()[0].text(), "Switch to a&&b")

    def test_right_click_on_empty_space_shows_no_menu(self):
        self.open_with_branches("feature")
        with mock.patch.object(self.panel, "menu_for") as menu_for:
            self.panel._show_menu(self.list.viewport().rect().bottomLeft())
        menu_for.assert_not_called()


if __name__ == "__main__":
    unittest.main()
