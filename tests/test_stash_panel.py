import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest

from rzgitclient.git.git_models import Stash
from rzgitclient.ui.app_actions import StashOp
from rzgitclient.ui.panels import stash_panel
from rzgitclient.ui.panels.stash_panel import StashPanel, age
from rzgitclient.ui.widgets.column_header import ColumnHeader
from tests.support import commit_file, git, make_panel_context, make_repo, qt_app, wait_idle


class AgeTests(unittest.TestCase):
    def test_roughly_how_long_ago(self):
        now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
        cases = {timedelta(seconds=30): "now", timedelta(minutes=5): "5 min",
                 timedelta(hours=3, minutes=59): "3 h", timedelta(days=1): "1 day",
                 timedelta(days=6): "6 days", timedelta(days=15): "2 weeks",
                 timedelta(days=65): "2 months", timedelta(days=400): "1 year",
                 timedelta(days=-1): "now"}  # a clock ahead of ours
        for ago, text in cases.items():
            self.assertEqual(age(now - ago, now), text, ago)


class StashPanelTests(unittest.TestCase):
    """The stashes, selecting one, and its menu (RZG-36, spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    @classmethod
    def tearDownClass(cls):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.repo, "a.txt", "1\n", "first")
        (self.repo / "a.txt").write_text("older\n")
        git(self.repo, "stash", "push", "-q", "-m", "older")
        git(self.repo, "switch", "-q", "-c", "side")
        (self.repo / "new.txt").write_text("new\n")
        git(self.repo, "stash", "push", "-q", "-u", "-m", "newer")
        self.ctx = make_panel_context()
        self.session, self.selection = self.ctx.session, self.ctx.selection
        self.panel = StashPanel(self.ctx)
        self.addCleanup(self.panel.deleteLater)
        self.tree = self.panel._tree
        self.session.open(self.repo)
        wait_idle(self.session)
        self.newer, self.older = self.session.stashes

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    def rows(self):
        return [[self.tree.topLevelItem(r).text(c) for c in range(3)]
                for r in range(self.tree.topLevelItemCount())]

    def test_lists_message_branch_and_age_newest_first(self):
        self.assertEqual(self.rows(), [["newer", "side", "now"], ["older", "main", "now"]])
        self.assertEqual(self.panel.header, "Stashes (2)")
        self.assertIn("stash@{1}", self.tree.topLevelItem(1).toolTip(0))
        self.assertFalse(self.panel._placeholder.isVisibleTo(self.panel))

    def test_columns_are_labelled(self):
        """RZG-41: Message, Branch and Age, on a ColumnHeader."""
        header = self.tree.header()
        self.assertIsInstance(header, ColumnHeader)
        self.assertFalse(self.tree.isHeaderHidden())
        self.assertEqual([header.label(c) for c in range(3)], ["Message", "Branch", "Age"])

    def test_a_new_panel_hides_what_was_hidden(self):
        """The labels are set after the header is: the choice still applies (a restart)."""
        self.ctx.columns.set_hidden("stash", "Age", True)
        panel = StashPanel(self.ctx)
        self.addCleanup(panel.deleteLater)
        self.assertTrue(panel._tree.isColumnHidden(2))
        self.assertFalse(panel._tree.isColumnHidden(1))

    def test_a_hidden_column_stays_hidden_through_a_refresh(self):
        self.tree.header().set_column_shown("Branch", False)
        self.assertEqual(self.ctx.columns.hidden("stash"), {"Branch"})
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        self.assertTrue(self.tree.isColumnHidden(1))

    def test_branch_and_age_fit_and_the_message_fills(self):
        """RZG-40: the columns always fill the width; the list never scrolls sideways."""
        self.panel.resize(300, 300)
        self.panel.show()
        QTest.qWaitForWindowExposed(self.panel)
        header = self.tree.header()
        self.assertEqual(sum(header.sectionSize(c) for c in range(3)), header.width())
        self.assertGreaterEqual(header.sectionSize(1), header.sectionSizeHint(1))
        self.assertEqual(self.tree.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def test_no_stashes(self):
        git(self.repo, "stash", "clear")
        self.session.refresh()
        wait_idle(self.session)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.panel.header, "Stashes")
        self.assertTrue(self.panel._placeholder.isVisibleTo(self.panel))
        self.assertEqual(self.panel._placeholder.text(), stash_panel.NO_STASHES)

    def test_selecting_a_stash_selects_it_everywhere_and_history_takes_it_back(self):
        self.tree.setCurrentItem(self.tree.topLevelItem(1))
        self.assertEqual(self.selection.commit, self.older)
        self.assertIsInstance(self.selection.commit, Stash)
        self.selection.commit = self.session.history[0]  # chosen in History
        self.assertEqual(self.tree.selectedItems(), [])
        self.selection.commit = self.newer  # back from elsewhere: shown selected
        self.assertEqual(self.tree.selectedItems(), [self.tree.topLevelItem(0)])

    def test_the_selection_follows_the_stash_when_another_is_made(self):
        self.tree.setCurrentItem(self.tree.topLevelItem(0))
        (self.repo / "a.txt").write_text("newest\n")
        self.session.stash_changes("newest")
        wait_idle(self.session)
        self.assertEqual(self.selection.commit.sha, self.newer.sha)
        self.assertEqual(self.selection.commit.ref, "stash@{1}")
        self.assertEqual(self.tree.selectedItems(), [self.tree.topLevelItem(1)])

    def test_the_menu_asks_for_the_operations(self):
        asked = []
        self.ctx.actions.stash_requested.connect(lambda op, stash: asked.append((op, stash)))
        menu = self.panel.menu_for(self.older)
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertEqual(texts, ["Apply", "Pop", "Drop…"])
        for action in menu.actions():
            if not action.isSeparator():
                action.trigger()
        self.assertEqual(asked, [(StashOp.APPLY, self.older), (StashOp.POP, self.older),
                                 (StashOp.DROP, self.older)])

    def test_apply_and_pop_need_a_clean_working_tree(self):
        (self.repo / "dirty.txt").write_text("x")
        self.session.refresh()
        wait_idle(self.session)
        menu = self.panel.menu_for(self.older)
        state = {a.text(): a.isEnabled() for a in menu.actions() if not a.isSeparator()}
        self.assertEqual(state, {"Apply" + stash_panel.NOT_CLEAN: False,
                                 "Pop" + stash_panel.NOT_CLEAN: False, "Drop…": True})

    def test_the_stash_changes_button_is_the_action(self):
        action = self.ctx.actions.stash_changes
        self.assertIs(self.panel._stash_button.defaultAction(), action)
        self.assertFalse(action.isEnabled())  # nothing to stash
        (self.repo / "dirty.txt").write_text("x")
        self.session.refresh()
        wait_idle(self.session)
        self.assertTrue(action.isEnabled())


if __name__ == "__main__":
    unittest.main()
