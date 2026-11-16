import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtWidgets import QApplication

from rzgitclient.ui.app_actions import BranchOp
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
        return [self.list.item(i).text() for i in range(self.list.count())]

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
        bold = {self.list.item(i).text(): self.list.item(i).font().bold() for i in range(2)}
        self.assertEqual(bold, {"feature": False, "main": True})

    def test_selection_survives_refresh(self):
        commit_file(self.repo, "a.txt", "1", "first")
        git(self.repo, "branch", "feature")
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.list.setCurrentRow(0)  # feature
        git(self.repo, "branch", "another")
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        self.assertEqual(self.rows(), ["another", "feature", "main"])
        self.assertEqual(self.list.currentItem().text(), "feature")


    # ---- operations (spec §5.4) ------------------------------------------

    def open_with_branches(self, *names):
        commit_file(self.repo, "a.txt", "1", "first")
        for name in names:
            git(self.repo, "branch", name)
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)

    def item(self, text):
        return next(self.list.item(i) for i in range(self.list.count())
                    if self.list.item(i).text() == text)

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
        self.list.itemDoubleClicked.emit(self.item("main"))
        self.assertEqual(seen, [])
        self.list.itemDoubleClicked.emit(self.item("feature"))
        self.assertEqual(seen, [(BranchOp.SWITCH, "feature")])

    def test_double_click_on_a_fresh_repo_s_row_does_nothing(self):
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        seen = self.requests()
        self.list.itemDoubleClicked.emit(self.list.item(0))
        self.assertEqual(seen, [])

    def test_menu_of_another_branch(self):
        self.open_with_branches("feature")
        self.assertEqual(self.entries(self.panel.menu_for("feature")), [
            ("Switch to feature", True),
            ("New Branch from feature…", True),
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
        for action in [*actions[:4], *submenu.actions()]:
            action.trigger()
        self.assertEqual(seen, [(op, "feature") for op in (
            BranchOp.SWITCH, BranchOp.NEW, BranchOp.RENAME, BranchOp.DELETE,
            BranchOp.MERGE, BranchOp.REBASE)])

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
        self.assertEqual(entries[4], ("Merge / Rebase", [("Merge main into HEAD…", True),
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
