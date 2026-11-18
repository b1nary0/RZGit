import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QLineEdit, QToolButton

from rzgit.core.repo_session import Refresh
from rzgit.ui.app_actions import BranchOp
from rzgit.ui.panels.controls_panel import (
    FILTER_AT, LABEL_WIDTH, MIN_LABEL_WIDTH, ControlsPanel,
)
from tests.support import commit_file, git, make_panel_context, make_repo, qt_app, wait_idle

NAV = 4  # Open, Refresh, Fetch and Pull, ahead of the branch button


class ControlsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.repo, "a.txt", "1", "first")
        self.ctx = make_panel_context()
        self.addCleanup(self.ctx.session.shutdown)
        self.panel = ControlsPanel(self.ctx)

    def open(self):
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)

    def test_a_bar_without_header_fixed_and_always_shown(self):
        self.assertFalse(ControlsPanel.show_header)
        self.assertTrue(ControlsPanel.fixed_height)
        self.assertTrue(ControlsPanel.always_shown)

    def test_buttons_are_the_app_actions(self):
        buttons = self.panel.findChildren(QToolButton)[:NAV]
        actions = self.ctx.actions
        self.assertEqual([b.defaultAction() for b in buttons],
                         [actions.open_repo, actions.refresh, actions.fetch, actions.pull])
        self.assertEqual([b.text() for b in buttons],
                         ["Open Repository", "Refresh", "Fetch", "Pull"])  # no & or …
        self.assertFalse(buttons[1].isEnabled())  # Refresh, with no repo open
        self.open()
        self.assertTrue(buttons[1].isEnabled())

    def refresh(self):
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)

    def menu(self):
        """The branch menu as it is when opened: (text, checked, enabled) per action."""
        self.panel._menu.aboutToShow.emit()
        return [(a.text(), a.isChecked(), a.isEnabled())
                for a in self.panel._menu.actions() if not a.isSeparator()]

    def requests(self):
        seen = []
        self.ctx.actions.branch_requested.connect(lambda op, name: seen.append((op, name)))
        return seen

    def action(self, text):
        self.panel._menu.aboutToShow.emit()
        return next(a for a in self.panel._menu.actions() if a.text() == text)

    # ---- the label --------------------------------------------------------

    def test_branch_button_is_an_instant_menu_at_the_right_end(self):
        button = self.panel._branch
        self.assertIs(self.panel.findChildren(QToolButton)[-1], button)
        self.assertEqual(button.popupMode(), QToolButton.ToolButtonPopupMode.InstantPopup)
        self.assertIs(button.menu(), self.panel._menu)
        self.assertEqual(button.property("role"), "branch-button")

    def test_label_names_the_branch_and_is_disabled_without_repo(self):
        button = self.panel._branch
        self.assertEqual(button.text(), "No repository")
        self.assertFalse(button.isEnabled())
        self.open()
        self.assertEqual((button.text(), button.toolTip()), ("main", "main"))
        self.assertTrue(button.isEnabled())
        self.ctx.session.close()
        self.assertEqual(button.text(), "No repository")
        self.assertFalse(button.isEnabled())

    def test_label_follows_a_switch_outside_the_app(self):
        git(self.repo, "branch", "feature")
        self.open()
        git(self.repo, "switch", "-q", "feature")
        self.refresh()
        self.assertEqual(self.panel._branch.text(), "feature")

    def test_label_of_a_detached_head_names_its_commit(self):
        commit_file(self.repo, "b.txt", "1", "second")
        git(self.repo, "checkout", "-q", "--detach")
        self.open()
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        self.assertEqual(self.panel._branch.text(), f"detached at {sha[:8]}")
        git(self.repo, "checkout", "-q", "--detach", "HEAD~1")  # HEAD stays detached
        self.ctx.session.refresh(Refresh.HEAD)  # only HEAD: its sha alone moves the label
        wait_idle(self.ctx.session)
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        self.assertEqual(self.panel._branch.text(), f"detached at {sha[:8]}")

    def test_label_of_a_fresh_repo(self):
        fresh = make_repo(Path(self._tmp.name) / "fresh")
        self.ctx.session.open(fresh)
        wait_idle(self.ctx.session)
        self.assertEqual(self.panel._branch.text(), "main (no commits)")

    def test_a_long_name_is_cut_short_with_the_full_name_in_the_tooltip(self):
        name = "feature/" + "a-very-long-branch-name-" * 6 + "end"
        git(self.repo, "switch", "-q", "-c", name)
        self.open()
        button = self.panel._branch
        self.assertNotEqual(button.text(), name)
        self.assertIn("…", button.text())
        self.assertTrue(button.text().endswith("end"))  # cut in the middle
        self.assertLessEqual(button.fontMetrics().horizontalAdvance(button.text()), LABEL_WIDTH)
        self.assertEqual(button.toolTip(), name)

    def test_in_a_narrow_bar_the_branch_button_gives_up_width_first(self):
        name = "feature/a-rather-long-branch-name-end"
        git(self.repo, "switch", "-q", "-c", name)
        self.open()
        button = self.panel._branch
        self.assertEqual(button.text(), name)
        self.assertLess(button.minimumSizeHint().width(), button.sizeHint().width())
        self.assertLessEqual(self.panel.minimumSizeHint().width(),
                             self.panel.sizeHint().width() - button.sizeHint().width()
                             + button.minimumSizeHint().width())
        self.panel.resize(self.panel.minimumSizeHint().width(), 40)
        self.panel.show()
        self.addCleanup(self.panel.hide)
        qt_app().processEvents()
        self.assertIn("…", button.text())
        self.assertTrue(button.text().endswith("end"))
        self.assertLessEqual(button.fontMetrics().horizontalAdvance(button.text()),
                             MIN_LABEL_WIDTH)
        self.panel.resize(self.panel.sizeHint().width() + 100, 40)  # room again: the full name
        qt_app().processEvents()
        self.assertEqual(button.text(), name)
        self.assertEqual(button.toolTip(), name)

    def test_ampersand_in_a_name_is_not_a_shortcut_mark(self):
        git(self.repo, "switch", "-q", "-c", "this&that")
        self.open()
        self.assertEqual(self.panel._branch.text(), "this&&that")
        self.assertIn(("this&&that", True, False), self.menu())

    # ---- the menu ---------------------------------------------------------

    def test_menu_lists_local_branches_current_checked_then_new_branch(self):
        git(self.repo, "branch", "feature")
        self.open()
        self.assertEqual(self.menu(), [("feature", False, True), ("main", True, False),
                                       ("New Branch…", False, True)])
        self.assertIsNone(self.panel._filter)  # few branches: no filter box

    def test_menu_follows_branches_made_elsewhere(self):
        self.open()
        self.assertEqual([t for t, _, _ in self.menu()], ["main", "New Branch…"])
        git(self.repo, "branch", "later")
        self.refresh()
        self.assertEqual([t for t, _, _ in self.menu()], ["later", "main", "New Branch…"])

    def test_choosing_a_branch_requests_a_switch(self):
        git(self.repo, "branch", "feature")
        self.open()
        seen = self.requests()
        self.action("feature").trigger()
        self.assertEqual(seen, [(BranchOp.SWITCH, "feature")])

    def test_new_branch_starts_from_the_current_branch(self):
        self.open()
        seen = self.requests()
        self.action("New Branch…").trigger()
        self.assertEqual(seen, [(BranchOp.NEW, "main")])

    def test_new_branch_starts_from_heads_commit_when_detached(self):
        git(self.repo, "checkout", "-q", "--detach")
        self.open()
        seen = self.requests()
        self.action("New Branch…").trigger()
        self.assertEqual(seen, [(BranchOp.NEW, git(self.repo, "rev-parse", "HEAD").strip())])
        self.assertTrue(all(not checked for _, checked, _ in self.menu()))

    def test_fresh_repo_menu_is_its_branch_checked_and_disabled(self):
        fresh = make_repo(Path(self._tmp.name) / "fresh")
        self.ctx.session.open(fresh)
        wait_idle(self.ctx.session)
        self.assertEqual(self.menu(), [("main (no commits)", True, False),
                                       ("New Branch…", False, False)])

    def test_many_branches_get_a_filter_box(self):
        for i in range(FILTER_AT):
            git(self.repo, "branch", f"topic-{i:02}")
        self.open()  # FILTER_AT + 1 branches, with main
        self.menu()
        box = self.panel._filter
        self.assertIsInstance(box, QLineEdit)
        box.setText("TOPIC-1")  # ignores case
        shown = [a.text() for a in self.panel._branch_actions if a.isVisible()]
        self.assertEqual(shown, [f"topic-{i}" for i in range(10, FILTER_AT)])
        seen = self.requests()
        box.returnPressed.emit()  # five left: Enter does nothing
        self.assertEqual(seen, [])
        box.setText("topic-07")
        box.returnPressed.emit()  # one left: Enter switches to it
        self.assertEqual(seen, [(BranchOp.SWITCH, "topic-07")])

    def test_filter_box_only_above_the_threshold(self):
        for i in range(FILTER_AT - 1):
            git(self.repo, "branch", f"topic-{i:02}")
        self.open()  # FILTER_AT branches, with main
        self.menu()
        self.assertIsNone(self.panel._filter)

if __name__ == "__main__":
    unittest.main()
