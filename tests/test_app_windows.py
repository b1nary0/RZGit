import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt
from PySide6.QtWidgets import QStyle

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.core.column_prefs import ColumnPrefs
from rzgitclient.core.commit_drafts import CommitDrafts
from rzgitclient.ui.app_actions import WorktreeOp
from rzgitclient.ui.app_windows import AppWindows
from rzgitclient.ui.layout.layout_model import Area, to_dict
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT
from rzgitclient.ui.worktree_dialogs import REMOVE, NewWorktree
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from rzgitclient.ui.theme.theme_manager import ThemeManager, ThemeMode
from tests.support import commit_file, git, make_repo, qt_app, wait_idle


class AppWindowsTests(unittest.TestCase):
    """More than one main window (spec §8.1, RZG-47)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)  # last: after every window is gone
        self.tmp = Path(self._tmp.name).resolve()
        self.settings = AppSettings(self.tmp / "settings.ini")
        self.theme = ThemeManager(self.app, LIGHT_THEME)
        self.drafts = CommitDrafts(self.tmp / "drafts")
        self.columns = ColumnPrefs()
        self.windows = AppWindows(self.app, self.settings, self.theme, self.drafts,
                                  self.columns)
        self.addCleanup(self.close_all)
        self.one = self.repo("one")
        self.two = self.repo("two")

    def close_all(self):
        for window in self.windows.windows():
            window.close()
        self.windows.shutdown()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.theme.set_mode(ThemeMode.LIGHT)

    def repo(self, name):
        path = make_repo(self.tmp / name)
        commit_file(path, "a.txt", "1\n", "first")
        return path

    def open(self, path, **kwargs):
        window = self.windows.open(path, **kwargs)
        self.assertIsNotNone(window)
        wait_idle(window.session)
        return window

    # ---- one window per worktree, each with its own objects -------------------

    def test_each_window_has_its_own_worktree_reader(self):
        a, b = self.open(self.one), self.open(self.two)
        self.assertIsNot(a.ctx.worktrees, b.ctx.worktrees)
        self.assertIs(a.ctx.worktrees.parent(), a)
        self.assertTrue(a.ctx.worktrees.attached)  # the Worktrees panel is in the layout
        reader = b.ctx.worktrees
        with mock.patch.object(reader, "shutdown", wraps=reader.shutdown) as shutdown:
            b.close()
        shutdown.assert_called_once_with()

    def test_the_worktree_readers_follow_the_app_s_activation(self):
        window = self.open(self.one)
        with mock.patch.object(window.ctx.worktrees, "set_active") as set_active:
            self.windows._on_app_state(Qt.ApplicationState.ApplicationInactive)
            self.windows._on_app_state(Qt.ApplicationState.ApplicationActive)
        self.assertEqual(set_active.call_args_list, [mock.call(False), mock.call(True)])

    def test_open_from_the_worktrees_panel_opens_a_window_of_its_own_or_raises_it(self):
        linked = self.tmp / "one-linked"
        git(self.one, "worktree", "add", "-q", "-b", "linked", str(linked))
        window = self.open(self.one)
        window.ctx.actions.request_open_worktree(str(linked))
        self.assertEqual(len(self.windows.windows()), 2)
        other = self.windows.window_for(linked)
        wait_idle(other.session)
        window.ctx.actions.request_open_worktree(str(linked))
        window.ctx.actions.request_open_worktree(str(self.one))
        self.assertEqual(len(self.windows.windows()), 2)  # raised, not opened again

    def test_new_worktree_with_open_it_opens_it_in_a_window_of_its_own(self):
        window = self.open(self.one)
        path = self.tmp / "one-topic"
        with mock.patch.object(window.worktree_dialogs, "ask_new",
                               return_value=NewWorktree(str(path), "topic", "main", True)):
            window.ctx.actions.new_worktree.trigger()
        wait_idle(window.session)
        other = self.windows.window_for(path)
        self.assertIsNotNone(other)  # once the add finished
        self.assertIsNot(other, window)
        wait_idle(other.session)
        self.assertEqual(other.session.head, "topic")

    def test_removing_a_worktree_closes_the_window_showing_it(self):
        linked = self.tmp / "one-linked"
        git(self.one, "worktree", "add", "-q", "-b", "linked", str(linked))
        window = self.open(self.one)
        window.ctx.actions.request_open_worktree(str(linked))
        other = self.windows.window_for(linked)
        wait_idle(other.session)
        reader = window.ctx.worktrees
        reader.refresh()
        deadline = 200
        while reader.busy and deadline:
            QCoreApplication.processEvents()
            deadline -= 1
            time.sleep(0.01)
        self.assertIn(str(linked), [w.path for w in reader.worktrees])
        with mock.patch.object(window.worktree_dialogs, "ask", return_value=REMOVE):
            window.ctx.actions.request_worktree(WorktreeOp.REMOVE, str(linked))
        wait_idle(window.session)
        self.assertEqual(self.windows.windows(), (window,))
        self.assertFalse(linked.exists())
        self.assertIn("linked", git(self.one, "branch", "--list", "linked"))  # kept

    def test_open_in_this_window_raises_the_window_already_showing_it(self):
        linked = self.tmp / "one-linked"
        git(self.one, "worktree", "add", "-q", "-b", "linked", str(linked))
        window, other = self.open(self.one), self.open(linked)
        with mock.patch.object(other, "bring_to_front") as raised:
            window.ctx.actions.request_open_worktree_here(str(linked))
        raised.assert_called_once_with()
        self.assertEqual(window.session.repo.root, self.one.resolve())  # unchanged
        other.close()
        window.ctx.actions.request_open_worktree_here(str(linked))
        wait_idle(window.session)
        self.assertEqual(window.session.repo.root, linked.resolve())

    def test_each_window_has_its_own_session_and_shares_the_rest(self):
        a, b = self.open(self.one), self.open(self.two)
        self.assertEqual(self.windows.windows(), (a, b))
        for name in ("session", "selection", "actions"):
            self.assertIsNot(getattr(a.ctx, name), getattr(b.ctx, name), name)
        self.assertIs(a.ctx.theme, b.ctx.theme)
        self.assertIs(a.ctx.drafts, b.ctx.drafts)
        self.assertIs(a.ctx.columns, b.ctx.columns)
        self.assertIs(a.settings, b.settings)
        self.assertEqual(a.session.repo.root, self.one)
        self.assertEqual(b.session.repo.root, self.two)
        self.assertIs(self.windows.window_for(self.two), b)
        self.assertIsNone(self.windows.window_for(self.tmp))

    def test_staging_in_one_window_never_touches_another(self):
        a, b = self.open(self.one), self.open(self.two)
        for repo in (self.one, self.two):
            (repo / "a.txt").write_text("2\n")
        a.session.refresh()
        b.session.refresh()
        wait_idle(a.session)
        wait_idle(b.session)
        a.session.stage(["a.txt"])
        wait_idle(a.session)
        b.session.refresh()
        wait_idle(b.session)
        self.assertEqual([(f.path, f.index, f.worktree) for f in a.session.status],
                         [("a.txt", "M", " ")])
        self.assertEqual([(f.path, f.index, f.worktree) for f in b.session.status],
                         [("a.txt", " ", "M")])

    def test_opening_a_worktree_again_raises_its_window(self):
        a = self.open(self.one)
        (self.tmp / "link").symlink_to(self.one)
        (self.one / "sub").mkdir()
        for path in (self.one, self.tmp / "link", self.one / "sub"):
            with mock.patch.object(type(a), "bring_to_front") as front:
                self.assertIs(self.windows.open(path), a)
            front.assert_called_once()
        self.assertEqual(self.windows.windows(), (a,))

    def test_a_folder_that_isnt_a_repository_opens_no_window(self):
        a = self.open(self.one)
        (self.tmp / "plain").mkdir()
        with mock.patch("rzgitclient.ui.app_windows.QMessageBox") as box:
            self.assertIsNone(self.windows.open(self.tmp / "plain", from_window=a))
        box.warning.assert_called_once()
        self.assertIs(box.warning.call_args.args[0], a)
        self.assertEqual(self.windows.windows(), (a,))

    def test_file_open_of_a_repository_shown_elsewhere_raises_that_window(self):
        a, b = self.open(self.one), self.open(self.two)
        with mock.patch.object(type(a), "bring_to_front", autospec=True) as front:
            self.assertTrue(a.open_repo(self.two))
        front.assert_called_once_with(b)
        self.assertEqual(a.session.repo.root, self.one)  # this window is unchanged
        three = self.repo("three")
        self.assertTrue(a.open_repo(three))  # nobody shows it: it opens here
        wait_idle(a.session)
        self.assertEqual(a.session.repo.root, three)
        self.assertEqual(len(self.windows.windows()), 2)

    def test_open_in_window_goes_through_the_windows(self):
        a = self.open(self.one)
        b = a.open_in_window(self.two)
        wait_idle(b.session)
        self.assertIsNot(a, b)
        self.assertEqual(b.session.repo.root, self.two)
        self.assertIs(b.open_in_window(self.one), a)

    # ---- windows open on History (RZG-55) ------------------------------------

    def main_tabs(self, window):
        return window.layout_host.state().tree(Area.MAIN).children[0]

    def test_a_spawned_window_opens_on_history_and_an_open_one_keeps_its_tabs(self):
        self.settings.layout = to_dict(DEFAULT_LAYOUT.with_current_tab("diff"))
        a = self.open(self.one)
        self.assertEqual(self.main_tabs(a).current, 0)  # History
        a.layout_host.apply(a.layout_host.state().with_current_tab("diff"))
        b = self.open(self.two)  # spawned: from the same settings
        self.assertEqual(self.main_tabs(b).current, 0)
        self.assertIs(self.open(self.one), a)  # raised, not built
        self.assertEqual(self.main_tabs(a).current, 1)
        a.session.open(self.repo("three"))  # a repository opened in it
        wait_idle(a.session)
        self.assertEqual(self.main_tabs(a).current, 1)

    # ---- File → Open Repository in New Window… (RZG-52) -------------------------

    def choose_in_new_window(self, window, path):
        with mock.patch("rzgitclient.ui.main_window.QFileDialog.getExistingDirectory",
                        return_value=str(path)) as dialog:
            window.ctx.actions.open_repo_new_window.trigger()
        dialog.assert_called_once()
        for w in self.windows.windows():
            wait_idle(w.session)

    def test_open_in_new_window_leaves_this_window_as_it_is(self):
        a = self.open(self.one)
        self.choose_in_new_window(a, self.two)
        self.assertEqual(len(self.windows.windows()), 2)
        b = self.windows.window_for(self.two)
        self.assertIsNot(b, a)
        self.assertTrue(b.isVisible())
        self.assertEqual(a.session.repo.root, self.one)
        self.assertEqual(self.settings.last_repo, str(self.two))

    def test_open_in_new_window_raises_the_window_already_showing_it(self):
        a, b = self.open(self.one), self.open(self.two)
        with mock.patch.object(type(a), "bring_to_front") as bring:
            self.choose_in_new_window(b, self.one)
        bring.assert_called_once_with()
        self.assertEqual(len(self.windows.windows()), 2)
        self.assertEqual(b.session.repo.root, self.two)

    def test_open_in_new_window_from_an_empty_window_opens_another(self):
        empty = self.windows.new_window()
        empty.show()
        self.choose_in_new_window(empty, self.one)
        self.assertEqual(len(self.windows.windows()), 2)
        self.assertIsNone(empty.session.repo)
        self.assertIsNotNone(self.windows.window_for(self.one))

    def test_open_in_new_window_cancelled_or_not_a_repo_opens_nothing(self):
        a = self.open(self.one)
        self.choose_in_new_window(a, "")  # cancelled
        self.assertEqual(len(self.windows.windows()), 1)
        plain = self.tmp / "plain"
        plain.mkdir()
        with mock.patch("rzgitclient.ui.app_windows.QMessageBox.warning") as warning:
            self.choose_in_new_window(a, plain)
        warning.assert_called_once()
        self.assertEqual(len(self.windows.windows()), 1)
        self.assertEqual(a.session.repo.root, self.one)

    def test_open_in_new_window_is_in_the_file_menu_after_open(self):
        a = self.open(self.one)
        file_menu = a.menuBar().actions()[0].menu()
        actions = a.ctx.actions
        self.assertEqual(file_menu.actions()[:3],
                         [actions.open_repo, actions.open_repo_new_window, actions.refresh])

    # ---- closing and quitting -----------------------------------------------

    def test_closing_a_window_closes_only_it_and_stops_its_work(self):
        a, b = self.open(self.one), self.open(self.two)
        session = b.session
        with mock.patch.object(type(session), "shutdown", autospec=True,
                               side_effect=type(session).shutdown) as shutdown:
            b.close()
        shutdown.assert_called_once_with(session)
        self.assertEqual(self.windows.windows(), (a,))
        self.assertIsNone(self.windows.window_for(self.two))
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.theme.set_mode(ThemeMode.DARK)  # nothing of b is left to restyle
        a.session.refresh()
        wait_idle(a.session)
        self.assertTrue(a.isVisible())

    def test_a_closed_window_stops_its_watcher(self):
        a = self.open(self.one)
        record = self.windows._open[0]
        with mock.patch.object(record.watcher, "shutdown") as shutdown:
            a.close()
        shutdown.assert_called_once()
        self.windows.shutdown()  # after the loop: not shut down twice
        shutdown.assert_called_once()

    def test_quit_closes_every_window(self):
        a, b = self.open(self.one), self.open(self.two)
        a.ctx.actions.quit.trigger()
        self.assertFalse(a.isVisible())
        self.assertFalse(b.isVisible())
        self.assertEqual(self.windows.windows(), ())

    def test_the_last_window_closed_saves_the_layout(self):
        a, b = self.open(self.one), self.open(self.two)
        a.ctx.actions.side_by_side_diff.setChecked(True)
        b.ctx.actions.side_by_side_diff.setChecked(False)
        b.close()
        a.close()
        self.assertTrue(self.settings.side_by_side_diff)  # a closed last

    # ---- last repository and title -----------------------------------------

    def test_a_linked_worktree_saves_its_main_repository_and_is_titled_by_its_name(self):
        git(self.one, "worktree", "add", "-b", "lane", str(self.tmp / "lane-7"))
        a = self.open(self.two)
        b = self.open(self.tmp / "lane-7")
        self.assertEqual(b.windowTitle(), "lane-7 — RZGitClient")
        self.assertEqual(a.windowTitle(), "two — RZGitClient")
        self.assertEqual(self.settings.last_repo, str(self.one))  # not the worktree
        a.changeEvent(QEvent(QEvent.Type.ActivationChange))  # not active: no change
        self.assertEqual(self.settings.last_repo, str(self.one))
        with mock.patch.object(type(a), "isActiveWindow", return_value=True):
            a.changeEvent(QEvent(QEvent.Type.ActivationChange))
        self.assertEqual(self.settings.last_repo, str(self.two))  # focused last

    # ---- the shared theme ---------------------------------------------------

    def test_a_theme_picked_in_one_window_is_checked_in_the_others(self):
        a, b = self.open(self.one), self.open(self.two)
        a.ctx.actions.theme_modes[ThemeMode.DARK].trigger()
        a.ctx.actions.colour_blind.setChecked(True)
        self.assertEqual(self.theme.mode, ThemeMode.DARK)
        self.assertTrue(self.theme.colour_blind)
        with mock.patch.object(type(b), "isActiveWindow", return_value=True):
            b.changeEvent(QEvent(QEvent.Type.ActivationChange))
        self.assertTrue(b.ctx.actions.theme_modes[ThemeMode.DARK].isChecked())
        self.assertTrue(b.ctx.actions.colour_blind.isChecked())
        self.theme.set_colour_blind(False)

    # ---- where windows appear -----------------------------------------------

    def test_the_first_window_restores_the_saved_geometry_and_later_ones_dont(self):
        first = self.open(self.one)
        first.resize(600, 420)
        self.settings.window_geometry = first.saveGeometry()
        first.close()
        again = self.open(self.one)
        self.assertEqual(again.size().toTuple(), (600, 420))
        other = self.open(self.two)
        area = again.screen().availableGeometry()
        self.assertEqual(other.size().toTuple(), (area.width() // 2, area.height() // 2))

    def test_a_new_window_isnt_maximized_like_the_saved_one(self):
        first = self.open(self.one)
        first.showMaximized()
        self.app.processEvents()
        self.settings.window_geometry = first.saveGeometry()
        first.close()
        again = self.open(self.one)
        self.app.processEvents()
        self.assertTrue(again.isMaximized())  # the first window: as saved
        other = self.open(self.two)
        self.app.processEvents()
        self.assertFalse(other.isMaximized())

    def test_a_new_window_is_a_quarter_of_the_screen_beside_the_one_it_came_from(self):
        a = self.open(self.one)
        area = a.screen().availableGeometry()
        a.move(area.left() + 40, area.top() + 30)
        b = self.open(self.two, from_window=a)
        step = a.style().pixelMetric(QStyle.PixelMetric.PM_TitleBarHeight)
        self.assertEqual(b.size().toTuple(), (area.width() // 2, area.height() // 2))
        self.assertEqual(b.pos().toTuple(), (a.pos().x() + step, a.pos().y() + step))

    def test_a_new_window_stays_on_the_screen(self):
        a = self.open(self.one)
        area = a.screen().availableGeometry()
        a.move(area.right() - 10, area.bottom() - 10)
        b = self.open(self.two, from_window=a)
        self.assertTrue(QRect(area).contains(QRect(b.pos(), b.size())))

    # ---- the watchers --------------------------------------------------------

    def test_every_watcher_follows_the_app_being_active(self):
        self.open(self.one), self.open(self.two)
        watchers = [w.watcher for w in self.windows._open]
        with mock.patch.object(type(watchers[0]), "set_active", autospec=True) as set_active:
            self.windows._on_app_state(Qt.ApplicationState.ApplicationInactive)
            self.windows._on_app_state(Qt.ApplicationState.ApplicationActive)
        self.assertEqual(set_active.call_args_list,
                         [mock.call(w, False) for w in watchers]
                         + [mock.call(w, True) for w in watchers])


if __name__ == "__main__":
    unittest.main()
