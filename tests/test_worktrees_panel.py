import dataclasses
import itertools
import shutil
import tempfile
import time
import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtGui import QColor

from rzgitclient.core.recent_repos import RecentRepos
from rzgitclient.core.worktree_reader import WorktreeReader
from rzgitclient.git.git_models import Worktree, WorktreeState
from rzgitclient.git.repo import canonical_path
from rzgitclient.ui.app_actions import WorktreeOp
from rzgitclient.ui.panels.worktrees_panel import (
    COLUMNS,
    NAME,
    NO_WORKTREES,
    OPEN_FOLDER,
    OPEN_HERE,
    OPEN_NEW,
    OPEN_TERMINAL,
    PRUNE,
    REMOVE,
    WorktreesPanel,
    PATH,
    STATE,
    SYNC,
    branch_text,
    short_path,
    state_text,
    sync_text,
    sync_tooltip,
)
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from rzgitclient.ui.widgets.column_header import ColumnHeader
from tests.support import click_column_label, commit_file, git, make_panel_context, make_repo, qt_app, wait_idle

WT = Worktree("/x/feature", "0123456789abcdef", "feature")


class TextTests(unittest.TestCase):
    def test_branch(self):
        self.assertEqual(branch_text(WT), "feature")
        self.assertEqual(branch_text(dataclasses.replace(WT, branch=None)),
                         "detached at 01234567")
        self.assertEqual(branch_text(Worktree("/x/new", None, None)), "(no commits)")

    def test_state(self):
        self.assertEqual(state_text(WT, None), "…")
        self.assertEqual(state_text(WT, WorktreeState(0)), "clean")
        self.assertEqual(state_text(WT, WorktreeState(1)), "1 changed file")
        self.assertEqual(state_text(WT, WorktreeState(1200)), "1,200 changed files")
        self.assertEqual(state_text(dataclasses.replace(WT, missing=True), None),
                         "missing folder")
        self.assertEqual(state_text(dataclasses.replace(WT, locked=True), WorktreeState(0)),
                         "clean · locked")

    def test_path_shows_home_as_a_tilde(self):
        self.assertEqual(short_path("/home/me/dev/x", "/home/me"), "~/dev/x")
        self.assertEqual(short_path("/home/me", "/home/me/"), "~")
        self.assertEqual(short_path("/home/meet/x", "/home/me"), "/home/meet/x")
        self.assertEqual(short_path("/srv/x", "/"), "/srv/x")

    def test_ahead_and_behind(self):
        self.assertEqual(sync_text(WorktreeState(0, "origin/main", 2, 1)), "↑2 ↓1")
        self.assertEqual(sync_text(WorktreeState(0, "origin/main", 2, 0)), "↑2")
        self.assertEqual(sync_text(WorktreeState(0, "origin/main", 0, 1)), "↓1")
        self.assertEqual(sync_text(WorktreeState(0, "origin/main", 0, 0)), "")
        self.assertEqual(sync_text(WorktreeState(0)), "")
        self.assertEqual(sync_text(None), "")

    def test_ahead_and_behind_tooltip_names_the_upstream(self):
        self.assertEqual(sync_tooltip(WorktreeState(0, "origin/main", 2, 1)),
                         "2 ahead of, 1 behind origin/main")
        self.assertEqual(sync_tooltip(WorktreeState(0, "origin/main", 0, 3)),
                         "3 behind origin/main")
        self.assertEqual(sync_tooltip(WorktreeState(0, "origin/main", 0, 0)),
                         "up to date with origin/main")
        self.assertEqual(sync_tooltip(WorktreeState(0, "origin/gone")), "origin/gone is gone")
        self.assertEqual(sync_tooltip(WorktreeState(0)), "no upstream")


class WorktreesPanelTests(unittest.TestCase):
    """The worktrees, the owner's marked, and opening one (RZG-48, spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    @classmethod
    def tearDownClass(cls):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = canonical_path(self._tmp.name)
        self.main = make_repo(self.tmp / "main")
        commit_file(self.main, "a.txt", "1\n", "first")
        git(self.main, "worktree", "add", "-q", "-b", "feature", str(self.tmp / "feature"))
        git(self.main, "worktree", "add", "-q", "--detach", str(self.tmp / "loose"))
        git(self.main, "worktree", "lock", "--reason", "on usb", str(self.tmp / "loose"))
        git(self.main, "worktree", "add", "-q", "-b", "gone", str(self.tmp / "gone"))
        shutil.rmtree(self.tmp / "gone")
        (self.tmp / "feature" / "new.txt").write_text("n\n")
        base = make_panel_context()
        self.session = base.session
        self.addCleanup(self.session.shutdown)
        self.session.open(self.tmp / "feature")  # this window shows "feature"
        wait_idle(self.session)
        self.reader = WorktreeReader(self.session)
        self.addCleanup(self.reader.shutdown)
        self.ctx = dataclasses.replace(base, worktrees=self.reader)
        self.panel = WorktreesPanel(self.ctx)
        self.addCleanup(self.panel.deleteLater)
        self.opened = []
        self.ctx.actions.open_worktree_requested.connect(self.opened.append)
        self.settle()
        self.tree = self.panel._tree  # built once there were worktrees to choose from

    def settle(self):
        deadline = time.monotonic() + 10
        self.reader.refresh()
        while self.reader.busy:
            if time.monotonic() > deadline:
                raise AssertionError("reader still busy")
            QCoreApplication.processEvents()
            QThread.msleep(2)

    def rows(self):
        return [[self.tree.topLevelItem(r).text(c) for c in range(len(COLUMNS))]
                for r in range(self.tree.topLevelItemCount())]

    def item(self, name):
        for r in range(self.tree.topLevelItemCount()):
            if self.tree.topLevelItem(r).text(0) == name:
                return self.tree.topLevelItem(r)
        raise KeyError(name)

    def test_lists_each_worktree_with_its_state_the_main_one_first(self):
        head = git(self.main, "rev-parse", "HEAD").strip()
        p = str(self.tmp)
        self.assertEqual(self.rows(), [
            ["main", "main", "clean", "", f"{p}/main"],
            ["feature", "feature", "1 changed file", "", f"{p}/feature"],
            ["gone", "gone", "missing folder", "", f"{p}/gone"],
            ["loose", f"detached at {head[:8]}", "clean · locked", "", f"{p}/loose"],
        ])
        self.assertEqual(self.panel.header, "Worktrees (4)")
        self.assertFalse(self.panel._placeholder.isVisibleTo(self.panel))

    def test_columns_are_labelled_and_secondary_text_is_muted(self):
        self.assertIsInstance(self.tree.header(), ColumnHeader)
        labels = [self.tree.headerItem().text(c) for c in range(len(COLUMNS))]
        self.assertEqual(labels, ["Name", "Branch", "State", "↑↓", "Path"])
        muted = QColor(LIGHT_THEME.colors.text_muted)
        item = self.item("main")
        self.assertEqual([item.foreground(c).color() == muted for c in range(5)],
                         [False, True, False, True, True])

    def test_a_long_path_leaves_the_name_room(self):
        long = self.tmp / ("a-rather-long-folder-name-" * 5) / "work"
        git(self.main, "worktree", "add", "-q", "--detach", str(long))
        self.settle()
        self.panel.resize(2000, 300)  # room for every column: nothing shrinks for now
        self.panel.show()
        QCoreApplication.processEvents()
        header = self.tree.header()
        header.set_column_shown("Path", True)
        cap = self.tree.fontMetrics().averageCharWidth() * 48
        self.assertLessEqual(header.sectionSize(PATH), cap)
        self.assertGreaterEqual(header.sectionSize(0), self.tree.sizeHintForColumn(0))
        self.assertEqual(self.item("main").toolTip(PATH).split("\n")[0], str(self.main))

    def test_branch_and_path_start_hidden_and_are_in_the_tooltip(self):
        header = self.tree.header()
        shown = [c for c in range(len(COLUMNS)) if not header.isSectionHidden(c)]
        self.assertEqual(shown, [0, 2, 3])  # Name, State, ↑↓
        tip = self.item("feature").toolTip(0)
        self.assertIn(str(self.tmp / "feature"), tip)
        self.assertIn("feature", tip.split("\n")[1])
        header.set_column_shown("Branch", True)
        self.assertFalse(header.isSectionHidden(1))
        self.assertEqual(self.ctx.columns.hidden("worktrees", frozenset({"Branch", "Path"})),
                         {"Path"})

    def test_attaches_while_it_exists(self):
        reader = WorktreeReader(self.session)
        self.addCleanup(reader.shutdown)
        panel = WorktreesPanel(dataclasses.replace(self.ctx, worktrees=reader))
        self.assertTrue(reader.attached)
        panel.deleteLater()  # as LayoutHost does when the panel is hidden
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(reader.attached)
        self.assertEqual(reader.worktrees, ())

    def test_the_list_is_built_only_once_there_is_a_choice(self):
        single = make_repo(self.tmp / "single")
        commit_file(single, "a.txt", "1\n", "first")
        base = make_panel_context()
        self.addCleanup(base.session.shutdown)
        base.session.open(single)
        wait_idle(base.session)
        reader = WorktreeReader(base.session)
        self.addCleanup(reader.shutdown)
        panel = WorktreesPanel(dataclasses.replace(base, worktrees=reader))
        self.addCleanup(panel.deleteLater)
        deadline = time.monotonic() + 10
        while reader.busy and time.monotonic() < deadline:
            QCoreApplication.processEvents()
        self.assertEqual(len(reader.worktrees), 1)
        self.assertTrue(panel.collapsed)
        self.assertFalse(panel.list_built)
        self.assertIsNone(panel.selected_path())
        self.assertFalse(panel.open_action.isEnabled())
        panel.apply_theme(LIGHT_THEME)  # nothing to theme yet
        git(single, "worktree", "add", "-q", "-b", "two", str(self.tmp / "two"))
        reader.refresh()
        deadline = time.monotonic() + 10
        while reader.busy and time.monotonic() < deadline:
            QCoreApplication.processEvents()
        self.assertTrue(panel.list_built)
        self.assertFalse(panel.collapsed)
        self.assertEqual(panel._tree.topLevelItemCount(), 2)

    def test_collapsed_while_there_is_only_one_worktree(self):
        self.assertFalse(self.panel.collapsed)
        for name in ("feature", "loose"):  # loose is locked: twice
            git(self.main, "worktree", "remove", "--force", "--force", str(self.tmp / name))
        git(self.main, "worktree", "prune")
        self.settle()
        self.assertEqual([r[0] for r in self.rows()], ["main"])
        self.assertTrue(self.panel.collapsed)
        git(self.main, "worktree", "add", "-q", "-b", "again", str(self.tmp / "again"))
        self.settle()
        self.assertFalse(self.panel.collapsed)

    def test_the_right_click_menu_opens_it_in_a_new_window_or_this_one(self):
        here = []
        self.ctx.actions.open_worktree_here_requested.connect(here.append)
        menu = self.panel.menu_for(self.panel.worktree_at(self.item("main")))
        self.assertEqual([a.text() for a in menu.actions()],
                         [OPEN_NEW, OPEN_HERE, "", OPEN_FOLDER, OPEN_TERMINAL, "",
                          REMOVE + " (main worktree)"])
        new_action, here_action = menu.actions()[:2]
        self.assertTrue(new_action.isEnabled() and here_action.isEnabled())
        new_action.trigger()
        here_action.trigger()
        self.assertEqual(self.opened, [str(self.main)])
        self.assertEqual(here, [str(self.main)])

    def test_this_window_s_own_worktree_can_t_be_opened_here(self):
        new_action, here_action = self.panel.menu_for(
            self.panel.worktree_at(self.item("feature"))).actions()[:2]
        self.assertTrue(new_action.isEnabled())  # raises this window
        self.assertFalse(here_action.isEnabled())

    def test_a_missing_worktree_s_menu_is_disabled(self):
        actions = [a for a in self.panel.menu_for(
            self.panel.worktree_at(self.item("gone"))).actions() if not a.isSeparator()]
        # Only Remove: a missing worktree's record can be removed (spec §5.4).
        self.assertEqual([(a.text(), a.isEnabled()) for a in actions],
                         [(OPEN_NEW, False), (OPEN_HERE, False), (OPEN_FOLDER, False),
                          (OPEN_TERMINAL, False), (REMOVE, True)])

    def menu_actions(self, name):
        menu = self.panel.menu_for(self.panel.worktree_at(self.item(name)))
        return {a.text(): a for a in menu.actions() if not a.isSeparator()}

    def worktree_requests(self):
        seen = []
        self.ctx.actions.worktree_requested.connect(lambda op, t: seen.append((op, t)))
        return seen

    def test_remove_says_why_it_is_disabled(self):
        self.assertFalse(self.menu_actions("main")[REMOVE + " (main worktree)"].isEnabled())
        self.assertFalse(self.menu_actions("feature")[REMOVE + " (this window)"].isEnabled())
        self.assertFalse(self.menu_actions("loose")[REMOVE + " (locked)"].isEnabled())

    def test_folder_terminal_and_remove_ask_for_their_operation(self):
        git(self.main, "worktree", "add", "-q", "-b", "other", str(self.tmp / "other"))
        self.settle()
        seen = self.worktree_requests()
        actions = self.menu_actions("other")
        for text in (OPEN_FOLDER, OPEN_TERMINAL, REMOVE):
            self.assertTrue(actions[text].isEnabled(), text)
            actions[text].trigger()
        path = str(self.tmp / "other")
        self.assertEqual(seen, [(WorktreeOp.OPEN_FOLDER, path),
                                (WorktreeOp.OPEN_TERMINAL, path), (WorktreeOp.REMOVE, path)])

    def test_the_owner_s_worktree_is_bold(self):
        self.assertTrue(self.item("feature").font(0).bold())
        self.assertFalse(self.item("main").font(0).bold())
        self.assertIn("(this window)", self.item("feature").toolTip(0))
        self.assertNotIn("(this window)", self.item("main").toolTip(0))

    def test_a_locked_worktree_s_tooltip_gives_the_reason(self):
        self.assertIn("locked: on usb", self.item("loose").toolTip(2))

    def test_follows_the_reader(self):
        (self.main / "b.txt").write_text("b\n")
        self.settle()
        self.assertEqual(self.item("main").text(2), "1 changed file")
        git(self.main, "worktree", "remove", "--force", str(self.tmp / "feature"))
        self.settle()
        self.assertEqual([r[0] for r in self.rows()], ["main", "gone", "loose"])
        self.assertEqual(self.panel.header, "Worktrees (3)")

    def test_double_click_and_open_ask_to_open_the_worktree(self):
        self.tree.itemDoubleClicked.emit(self.item("main"), 0)
        self.assertEqual(self.opened, [str(self.main)])
        self.assertFalse(self.panel.open_action.isEnabled())  # nothing selected
        self.item("loose").setSelected(True)
        self.assertTrue(self.panel.open_action.isEnabled())
        self.panel.open_action.trigger()
        self.assertEqual(self.opened, [str(self.main), str(self.tmp / "loose")])

    def test_sort_by_keeps_the_main_worktree_first_and_the_selection(self):
        """RZG-43: Git Order, Recently Used, Name, Branch and Path; State and ↑↓ don't sort."""
        self.assertEqual([a.text() for a in self.panel.sort.actions()[:5]],
                         ["Git Order", "Recently Used", "Name", "Branch", "Path"])
        self.panel.sort.choose("git")
        names = lambda: [r[0] for r in self.rows()]
        self.assertEqual(names(), ["main", "feature", "gone", "loose"])  # git's order
        self.item("loose").setSelected(True)
        labels = self.panel._labels
        click_column_label(labels, NAME)
        self.assertEqual(names(), ["main", "feature", "gone", "loose"])
        self.assertEqual(labels.sort_indicator, ("Name", False))
        click_column_label(labels, NAME)
        self.assertEqual(names(), ["main", "loose", "gone", "feature"])
        self.assertEqual(self.panel.selected_path(), str(self.tmp / "loose"))
        self.panel.sort.choose("branch")  # "detached at …", feature, gone
        self.assertEqual(names(), ["main", "loose", "feature", "gone"])
        self.assertFalse(labels.is_sortable(STATE))
        self.assertFalse(labels.is_sortable(SYNC))

    def names(self, panel=None):
        tree = (panel or self.panel)._tree
        return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]

    def use(self, recent, name):
        """A window on the worktree ``name`` was opened or activated now (RZG-87)."""
        recent.touch(mock.Mock(root=self.tmp / name, main_root=self.main))

    def test_recently_used_is_the_default_this_window_s_worktree_next_after_main(self):
        """RZG-50: most recent first; never used last, in git's order."""
        self.assertEqual(self.panel.sort.order.key, "recent")
        recent = RecentRepos(clock=itertools.count(1).__next__)
        self.use(recent, "main")
        self.use(recent, "loose")
        self.use(recent, "feature")  # this window's: the most recent anyway
        panel = WorktreesPanel(dataclasses.replace(self.ctx, recent=recent))
        self.addCleanup(panel.deleteLater)
        self.assertEqual(self.names(panel), ["main", "feature", "loose", "gone"])
        self.use(recent, "gone")  # another window activated on it
        self.assertEqual(self.names(panel), ["main", "feature", "gone", "loose"])
        # Reversed: oldest first, never used at the top; this window's last.
        git(self.main, "worktree", "add", "-q", "-b", "fresh", str(self.tmp / "fresh"))
        self.settle()
        self.assertEqual(self.names(panel), ["main", "feature", "gone", "loose", "fresh"])
        panel.sort.set_reversed(True)
        self.assertEqual(self.names(panel), ["main", "fresh", "loose", "gone", "feature"])

    def test_a_new_use_keeps_the_selection_and_only_moves_rows_in_recently_used(self):
        self.item("loose").setSelected(True)
        self.use(self.ctx.recent, "loose")
        self.assertEqual(self.names(), ["main", "feature", "loose", "gone"])
        self.assertEqual(self.panel.selected_path(), str(self.tmp / "loose"))
        self.panel.sort.choose("git")
        self.assertEqual(self.ctx.sorts.order("worktrees").key, "git")  # saved: not the default
        self.use(self.ctx.recent, "gone")
        self.assertEqual(self.names(), ["main", "feature", "gone", "loose"])  # git's order

    def test_states_fill_the_right_rows_after_a_sort(self):
        self.panel.sort.set_reversed(True)  # git's order, reversed: main first still
        self.settle()
        states = {r[0]: r[2] for r in self.rows()}
        self.assertEqual(self.rows()[0][0], "main")
        self.assertEqual(states["feature"], "1 changed file")
        self.assertEqual(states["main"], "clean")
        self.assertEqual(states["gone"], "missing folder")
        # One worktree's new state goes to its own row, wherever the sort put it.
        for name in ("feature", "loose"):
            self.item(name).setText(STATE, "")
        self.panel._render_state(str(self.tmp / "feature"), None)
        self.assertEqual(self.item("feature").text(STATE), "1 changed file")
        self.assertEqual(self.item("loose").text(STATE), "")

    def test_a_missing_worktree_can_t_be_opened(self):
        self.item("gone").setSelected(True)
        self.assertFalse(self.panel.open_action.isEnabled())
        self.tree.itemDoubleClicked.emit(self.item("gone"), 0)
        self.assertEqual(self.opened, [])

    def test_the_header_menu_has_open_new_prune_refresh_and_sort_by(self):
        actions = self.panel.header_actions
        self.assertEqual(actions[:4], (self.panel.open_action, self.ctx.actions.new_worktree,
                                       self.panel.prune_action, self.panel.refresh_action))
        self.assertTrue(actions[4].isSeparator())
        self.assertEqual(actions[5:], (self.panel.sort.menuAction(),))
        self.assertEqual(self.panel.prune_action.text(), PRUNE)
        (self.main / "b.txt").write_text("b\n")
        self.panel.refresh_action.trigger()
        self.assertTrue(self.reader.busy)

    def test_prune_is_enabled_while_an_unlocked_worktree_is_missing(self):
        seen = self.worktree_requests()
        self.assertTrue(self.panel.prune_action.isEnabled())  # "gone"
        self.panel.prune_action.trigger()
        self.assertEqual(seen, [(WorktreeOp.PRUNE, "")])
        git(self.main, "worktree", "prune")
        self.settle()
        self.assertFalse(self.panel.prune_action.isEnabled())
        # A missing locked worktree isn't pruned by git, so it doesn't count.
        shutil.rmtree(self.tmp / "loose")
        self.settle()
        self.assertTrue(self.panel.worktree_at(self.item("loose")).missing)
        self.assertFalse(self.panel.prune_action.isEnabled())

    def test_the_selection_is_kept_through_a_refresh(self):
        self.item("loose").setSelected(True)
        git(self.main, "worktree", "add", "-q", "-b", "aaa", str(self.tmp / "aaa"))
        self.settle()
        self.assertEqual(self.panel.selected_path(), str(self.tmp / "loose"))

    def test_with_no_reader_it_says_so(self):
        panel = WorktreesPanel(dataclasses.replace(self.ctx, worktrees=None))
        self.addCleanup(panel.deleteLater)
        self.assertEqual(panel._placeholder.text(), NO_WORKTREES)
        self.assertTrue(panel._placeholder.isVisibleTo(panel))
        self.assertFalse(panel.refresh_action.isEnabled())
        self.assertEqual(panel.header, "Worktrees")
        self.assertTrue(panel.collapsed)

    def test_a_failed_listing_says_why(self):
        shutil.rmtree(self.main / ".git")
        self.settle()
        self.assertEqual(self.rows(), [])
        self.assertIn("Can't list worktrees", self.panel._placeholder.text())
        self.assertFalse(self.panel.collapsed)  # it says why


if __name__ == "__main__":
    unittest.main()
