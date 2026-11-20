"""A commit or stash in a window of its own (RZG-84, spec §8.1)."""

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QPoint

from rzgit.core.app_settings import AppSettings
from rzgit.core.selection import FileSelection, FileSource
from rzgit.ui.app_actions import HunkHeaders
from rzgit.ui.commit_window import DEFAULT_SIZE, GONE, CommitWindow, commit_window_title
from rzgit.ui.layout.layout_model import Area
from rzgit.ui.main_window import MainWindow
from tests.support import commit_file, git, make_panel_context, make_repo, qt_app, wait_idle


class CommitWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)  # last: after every window is gone
        self.tmp = Path(self._tmp.name)
        self.repo = make_repo(self.tmp / "repo")
        commit_file(self.repo, "a.txt", "1\n", "first")
        commit_file(self.repo, "b.txt", "2\n", "second")
        self.settings = AppSettings(self.tmp / "settings.ini")
        self.main = MainWindow(make_panel_context(), self.settings)
        self.addCleanup(self.main.deleteLater)
        self.addCleanup(self.main.session.shutdown)
        self.addCleanup(self.main.close_commit_windows)  # runs first
        self.main.session.open(self.repo)
        wait_idle(self.main.session)
        self.second, self.first = self.main.session.history

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def open(self, commit):
        window = self.main.open_commit_window(commit)
        wait_idle(window.session)
        return window

    def test_it_shows_the_commit_with_its_own_session_and_selection(self):
        window = self.open(self.second)
        self.assertIsNot(window.session, self.main.session)
        self.assertEqual(window.selection.commit, self.second)
        self.assertIsNone(self.main.ctx.selection.commit)  # the main window's is untouched
        self.assertEqual(window.session.commit_files_sha, self.second.sha)
        self.assertEqual([f.path for f in window.session.commit_files], ["b.txt"])
        self.assertEqual(window.session.commit_details_sha, self.second.sha)
        # It reads only what its panels ask for: no history, status or branches.
        self.assertEqual(window.session.history, ())
        self.assertEqual(window.session.branches, ())
        file = FileSelection("b.txt", FileSource.COMMIT, self.second.sha)
        window.selection.select((file,), file)
        wait_idle(window.session)
        self.assertEqual(window.session.diff_file, file)
        self.assertIsNone(self.main.ctx.selection.file)
        self.assertIsNone(self.main.session.diff_file)

    def test_the_layout_is_fixed(self):
        window = self.open(self.second)
        host = window.layout_host
        self.assertEqual(set(host.panels), {"commit_files", "commit_details", "diff"})
        self.assertEqual(host.area_of("diff"), Area.MAIN)
        self.assertEqual(host.area_of("commit_files"), Area.SIDEBAR)
        self.assertIsNone(host.drop_target("diff", QPoint(10, 10)))

    def test_opening_it_again_raises_the_same_window(self):
        window = self.open(self.second)
        self.assertIs(self.main.open_commit_window(self.second), window)
        other = self.open(self.first)
        self.assertIsNot(other, window)
        self.assertEqual(self.main.commit_windows, (window, other))
        window.close()
        self.assertEqual(self.main.commit_windows, (other,))
        self.assertIsNot(self.open(self.second), window)  # closed: a new one

    def test_the_title(self):
        window = self.open(self.second)
        self.assertEqual(window.windowTitle(), f"{self.second.short_sha} second — repo")

    def test_a_commit_that_leaves_the_history_stays_and_says_so(self):
        window = self.open(self.second)
        git(self.repo, "commit", "-q", "--amend", "-m", "second, amended")
        self.main.session.refresh()
        wait_idle(self.main.session)
        self.assertTrue(window.gone)
        self.assertTrue(window.windowTitle().endswith(GONE))
        self.assertEqual(window.selection.commit, self.second)  # still shown
        git(self.repo, "reset", "-q", "--hard", self.second.sha)  # back again
        self.main.session.refresh()
        wait_idle(self.main.session)
        self.assertFalse(window.gone)

    def test_a_stash_its_title_and_dropping_it(self):
        (self.repo / "a.txt").write_text("changed\n")
        git(self.repo, "stash", "push", "-q", "-m", "work")
        (self.repo / "a.txt").write_text("again\n")
        git(self.repo, "stash", "push", "-q", "-m", "more")
        self.main.session.refresh()
        wait_idle(self.main.session)
        more, work = self.main.session.stashes
        window = self.open(work)
        self.assertEqual(window.windowTitle(), "stash@{1} work — repo")
        self.assertEqual([f.path for f in window.session.commit_files], ["a.txt"])
        git(self.repo, "stash", "drop", "-q", "stash@{0}")  # `work` moves to stash@{0}
        self.main.session.refresh()
        wait_idle(self.main.session)
        self.assertEqual(window.windowTitle(), "stash@{0} work — repo")
        git(self.repo, "stash", "drop", "-q")
        self.main.session.refresh()
        wait_idle(self.main.session)
        self.assertEqual(window.windowTitle(), "stash@{0} work — repo" + GONE)

    def test_it_closes_with_its_main_window_and_on_a_repository_change(self):
        window = self.open(self.second)
        closed = []
        window.closed.connect(lambda: closed.append(True))
        other = make_repo(self.tmp / "other")
        commit_file(other, "c.txt", "3\n", "third")
        self.main.session.open(other)
        self.assertEqual(closed, [True])
        self.assertEqual(self.main.commit_windows, ())
        wait_idle(self.main.session)
        window = self.open(self.main.session.history[0])
        window.closed.connect(lambda: closed.append(True))
        self.main.close()
        self.assertEqual(closed, [True, True])

    def test_its_geometry_is_saved_and_restored(self):
        window = self.open(self.second)
        self.assertEqual((window.width(), window.height()), DEFAULT_SIZE)
        window.resize(700, 500)
        window.close()
        self.assertIsNotNone(self.settings.commit_window_geometry)
        again = self.open(self.second)
        self.assertEqual((again.width(), again.height()), (700, 500))

    def test_view_toggles_start_from_the_main_windows_and_stay_its_own(self):
        main = self.main.ctx.actions
        main.side_by_side_diff.setChecked(True)
        main.hunk_headers[HunkHeaders.OFF].setChecked(True)
        window = self.open(self.second)
        self.assertTrue(window.actions.side_by_side_diff.isChecked())
        self.assertIs(window.actions.hunk_headers_mode(), HunkHeaders.OFF)
        window.actions.side_by_side_diff.setChecked(False)
        self.assertTrue(main.side_by_side_diff.isChecked())

    def test_open_with_vs_code_goes_to_the_main_windows_launcher(self):
        window = self.open(self.second)
        asked = []
        self.main.ctx.actions.open_in_vscode_requested.connect(
            lambda paths, line: asked.append((paths, line)))
        window.actions.open_in_vscode_requested.emit(("b.txt",), 3)
        self.assertEqual(asked, [(("b.txt",), 3)])

    def test_history_and_stashes_open_it_through_the_actions(self):
        self.main.ctx.actions.request_open_commit(self.first)
        (window,) = self.main.commit_windows
        self.assertIsInstance(window, CommitWindow)
        self.assertEqual(window.commit, self.first)
        wait_idle(window.session)

    def test_no_repository_no_window(self):
        self.main.session.close()
        self.assertIsNone(self.main.open_commit_window(self.first))


class TitleTests(unittest.TestCase):
    def test_commit_and_gone(self):
        from datetime import datetime, timezone

        from rzgit.git.git_models import Commit, Stash
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        commit = Commit("0123456789abcdef", "A", when, "Fix it")
        self.assertEqual(commit_window_title(commit, "repo"), "01234567 Fix it — repo")
        self.assertEqual(commit_window_title(commit, "repo", gone=True),
                         "01234567 Fix it — repo" + GONE)
        stash = Stash("fedcba9876543210", "A", when, "WIP", index=2, branch="main",
                      message="half done", untracked=False)
        self.assertEqual(commit_window_title(stash, "wt"), "stash@{2} half done — wt")
