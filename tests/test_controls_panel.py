import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLineEdit, QToolButton, QWidget

from rzgit.core.network_worker import NetworkCommand, NetworkKind
from rzgit.core.repo_session import Refresh
from rzgit.git.git_models import Branch
from rzgit.ui.app_actions import BranchOp
from rzgit.ui.theme.app_theme import LIGHT_THEME
from rzgit.ui.panels.controls_panel import (
    COUNT_GAP, FILTER_AT, LABEL_WIDTH, MIN_LABEL_WIDTH, ControlsPanel,
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


def _colours(widget: QWidget, left: int = 0, right: int | None = None) -> set[tuple[int, int, int]]:
    """The colours ``widget`` paints, between x = left and right."""
    image = widget.grab().toImage()
    right = image.width() if right is None else right
    return {QColor(image.pixel(x, y)).getRgb()[:3]
            for x in range(left, right) for y in range(image.height())}


def _has(colours: set[tuple[int, int, int]], colour: str, slack: int = 30) -> bool:
    """Whether any of ``colours`` is ``colour``, give or take antialiasing."""
    want = QColor(colour).getRgb()[:3]
    return any(sum(abs(a - b) for a, b in zip(c, want)) <= slack for c in colours)


class CountButtonTests(unittest.TestCase):
    """The ↓n on Pull and ↑n on Push, in colours of their own (spec §5.4, RZG-94)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self.ctx = make_panel_context()
        self.addCleanup(self.ctx.session.shutdown)
        self.panel = ControlsPanel(self.ctx)
        self.panel.show()  # a hidden widget's resize events wait until it is shown
        self.addCleanup(self.panel.close)
        actions = self.ctx.actions
        buttons = {b.defaultAction(): b for b in self.panel.findChildren(QToolButton)}
        self.pull, self.push = buttons[actions.pull], buttons[actions.push]
        self.colors = LIGHT_THEME.colors

    def show(self, ahead, behind, network=None):
        session = self.ctx.session
        branch = Branch("main", True, upstream="origin/main", ahead=ahead, behind=behind,
                        remote="origin")
        kind = type(session)
        with mock.patch.object(kind, "branches", (branch,)), \
                mock.patch.object(kind, "remotes", ("origin",)), \
                mock.patch.object(kind, "head", "main"), \
                mock.patch.object(kind, "repo", object()), \
                mock.patch.object(kind, "network", network):
            session.network_changed.emit(network)
        qt_app().processEvents()  # the layout gives the buttons their new size hints

    def test_the_count_is_its_own_label_beside_the_text(self):
        plain = self.pull.sizeHint().width()
        self.show(ahead=2, behind=3)
        for button, count, role in ((self.pull, "↓3", "upstream-behind"),
                                    (self.push, "↑2", "upstream-ahead")):
            self.assertEqual(button.text(), button.defaultAction().iconText())  # no count
            self.assertNotIn(count, button.text())
            label = button.count
            self.assertEqual((label.text(), label.isHidden(), label.property("role")),
                             (count, False, role))
            self.assertTrue(label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertEqual(self.ctx.actions.pull.text(), "&Pull ↓3")  # the menu keeps it all
        self.assertGreater(self.pull.sizeHint().width(), plain + self.pull.count.sizeHint().width())

    def test_the_count_sits_inside_the_button_after_its_text(self):
        self.show(ahead=0, behind=12)
        geometry = self.pull.count.geometry()
        self.assertTrue(self.pull.rect().contains(geometry))
        image = self.pull.grab().toImage()
        text = [x for x in range(geometry.left()) for y in range(image.height())
                if _has({QColor(image.pixel(x, y)).getRgb()[:3]}, self.colors.text, slack=90)]
        gap = geometry.left() - max(text) - 1  # from the end of "Pull" to the count
        self.assertGreaterEqual(gap, COUNT_GAP - 1)
        self.assertLessEqual(gap, COUNT_GAP + 3)

    def test_the_count_is_drawn_in_its_colour_and_the_text_is_not(self):
        self.show(ahead=2, behind=3)
        for button, token in ((self.pull, self.colors.upstream_behind),
                              (self.push, self.colors.upstream_ahead)):
            self.assertTrue(_has(_colours(button.count), token), token)
            text = _colours(button, right=button.count.geometry().left())
            self.assertTrue(_has(text, self.colors.text))
            self.assertFalse(_has(text, token, slack=60))  # the label keeps the text colour

    def test_a_disabled_button_mutes_its_count(self):
        self.show(ahead=2, behind=3, network=NetworkCommand(NetworkKind.FETCH))
        self.assertFalse(self.pull.isEnabled())
        self.assertEqual(self.pull.count.text(), "↓3")
        drawn = _colours(self.pull.count)
        self.assertTrue(_has(drawn, self.colors.text_muted))
        self.assertFalse(_has(drawn, self.colors.upstream_behind, slack=60))

    def test_without_a_count_a_plain_button(self):
        self.show(ahead=2, behind=3)
        self.show(ahead=0, behind=0)
        for button in (self.pull, self.push):
            self.assertTrue(button.count.isHidden())
            self.assertEqual(button.sizeHint(), QToolButton.sizeHint(button))
        pushing = NetworkCommand(NetworkKind.PUSH, remote="origin", branch="main")
        self.show(ahead=2, behind=3, network=pushing)  # Pushing…: no count while it runs
        self.assertTrue(self.push.count.isHidden())
        self.assertEqual(self.push.text(), "Pushing")
