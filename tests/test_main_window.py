import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QToolBar, QToolButton

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.core.commit_drafts import CommitDrafts
from rzgitclient.core.diff_patch import hunk_lines
from rzgitclient.core.network_worker import NetworkCommand, NetworkKind
from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.git.git_models import DiffSource, HistoryOrder
from rzgitclient.git.repo import Repo
from rzgitclient.ui.layout.layout_model import (
    Area,
    LayoutState,
    Orientation,
    PanelNode,
    SplitNode,
    TabsNode,
    to_dict,
)
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT
from rzgitclient.ui.app_actions import HunkHeaders, StashOp
from rzgitclient.ui.main_window import MainWindow, commit_summary
from rzgitclient.ui.theme.app_theme import DARK_CB_THEME, DARK_THEME, LIGHT_THEME
from rzgitclient.ui.theme.theme_manager import ThemeManager, ThemeMode
from tests.support import commit_file, git, make_panel_context, make_repo, qt_app, wait_idle


def bright_share(image, step=3):
    """The share of pixels that are light: text is, a light background would be."""
    total = bright = 0
    for y in range(0, image.height(), step):
        for x in range(0, image.width(), step):
            total += 1
            bright += image.pixelColor(x, y).lightness() > 160
    return bright / max(total, 1)


def strip_sizes(node):
    """A state's arrangement, areas and hidden panels, without sizes (the host
    reports the splitters' real pixel sizes)."""
    if isinstance(node, LayoutState):
        return (node.arrangement, {a: strip_sizes(t) for a, t in node.areas.items()},
                dict(node.hidden))
    if isinstance(node, SplitNode):
        return SplitNode(node.orientation, tuple(strip_sizes(c) for c in node.children))
    return node


def legacy(tree):
    """``tree`` saved as versions before areas saved a layout (version 2)."""
    root = to_dict(LayoutState({Area.MAIN: tree}))["areas"]["main"]
    return {"version": 2, "root": root, "hidden": {}}


class MainWindowTests(unittest.TestCase):
    """Startup and shutdown persistence (spec §6.3)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)  # last: after every window is gone
        self.tmp = Path(self._tmp.name)
        self.settings_path = self.tmp / "settings.ini"

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def start(self):
        window = MainWindow(make_panel_context(), AppSettings(self.settings_path))
        window.show()
        self.app.processEvents()
        self.addCleanup(window.deleteLater)
        # Runs first: no late git result may reach a window being deleted.
        self.addCleanup(window.session.shutdown)
        return window

    def quit(self, window):
        window.close()
        window.settings.sync()

    def sizes(self, window):
        state = window.layout_host.state()
        return state.sizes, state.tree(Area.SIDEBAR).sizes

    def body(self, window):
        return window.layout_host.layout().itemAt(0).widget().widget(1)

    def test_the_last_repository_is_reopened(self):
        repo = make_repo(self.tmp / "repo")
        AppSettings(self.settings_path).last_repo = repo
        window = self.start()
        window.open_last_repo()
        self.assertEqual(window.session.repo.root, repo.resolve())

    def test_opening_by_any_route_moves_it_to_the_top_of_the_recent_list(self):
        """RZG-87: the last repository on start, Open… and a session open all count."""
        one, two = make_repo(self.tmp / "one"), make_repo(self.tmp / "two")
        AppSettings(self.settings_path).last_repo = one
        window = self.start()
        recent = window.ctx.recent
        window.open_last_repo()
        self.assertEqual(recent.repositories(), (one.resolve(),))
        with mock.patch("rzgitclient.ui.main_window.QFileDialog.getExistingDirectory",
                        return_value=str(two)):
            window.ctx.actions.open_repo.trigger()
        self.assertEqual(recent.repositories(), (two.resolve(), one.resolve()))
        window.session.open(one)
        self.assertEqual(recent.repositories(), (one.resolve(), two.resolve()))

    def test_recent_repositories_follow_the_open_actions_in_the_file_menu(self):
        window = self.start()
        file_menu = window.menuBar().actions()[0].menu()
        texts = [a.text() for a in file_menu.actions()]
        start = texts.index("&Open Repository…")
        self.assertEqual(texts[start:start + 4],
                         ["&Open Repository…", "Open Repository in &New Window…",
                          "Re&cent Repositories", "&Refresh"])

    def test_a_last_repository_that_is_gone_is_skipped_quietly(self):
        (self.tmp / "plain").mkdir()
        for missing in (self.tmp / "moved", self.tmp / "plain"):  # gone; no longer a repo
            settings = AppSettings(self.settings_path)
            settings.last_repo = missing
            settings.sync()
            window = self.start()
            errors = []
            window.session.error_occurred.connect(errors.append)
            with mock.patch("rzgitclient.ui.main_window.QMessageBox") as box:
                window.open_last_repo()
            self.assertIsNone(window.session.repo)
            self.assertEqual(errors, [])
            box.warning.assert_not_called()
            self.assertEqual(window.statusBar().currentMessage(),
                             f"Last repository not found: {missing}")
            self.quit(window)
            self.assertEqual(AppSettings(self.settings_path).last_repo, str(missing))  # kept

    def test_a_non_ascii_repository_keeps_its_draft_and_is_reopened(self):
        repo = make_repo(self.tmp / "café 日本")
        commit_file(repo, "a.txt", "1\n", "first")
        window = self.start()
        self.assertTrue(window.session.open(repo))
        wait_idle(window.session)
        CommitDrafts(self.tmp / "drafts").set(window.session.repo.root, "Fix é 日本")
        self.quit(window)
        again = self.start()
        again.open_last_repo()
        self.assertEqual(again.session.repo.root, repo.resolve())
        self.assertEqual(again.windowTitle(), "café 日本 — RZGitClient")
        self.assertEqual(CommitDrafts(self.tmp / "drafts").get(repo), "Fix é 日本")

    def test_first_start_uses_the_default_layout(self):
        window = self.start()
        self.assertEqual(strip_sizes(window.layout_host.state()), strip_sizes(DEFAULT_LAYOUT))

    def test_layout_and_window_size_come_back_after_a_restart(self):
        window = self.start()
        window.resize(700, 500)  # fits the 800x800 offscreen screen; larger is clamped
        self.app.processEvents()
        root = self.body(window)
        root.setSizes([250, 449])
        root.widget(0).content.setSizes([150, 200])
        before = self.sizes(window)
        self.quit(window)

        again = self.start()
        self.assertEqual((again.width(), again.height()), (700, 500))
        self.assertEqual(self.sizes(again), before)

    def test_corrupt_saved_layout_starts_with_the_default(self):
        self.settings_path.write_text('[General]\nlayout="{broken"\n')
        with self.assertLogs("rzgitclient.core.app_settings", "WARNING"):
            window = self.start()
        self.assertEqual(strip_sizes(window.layout_host.state()), strip_sizes(DEFAULT_LAYOUT))

    def test_reset_layout_restores_the_default_and_keeps_panels(self):
        window = self.start()
        history = window.layout_host.panels["history"]
        self.body(window).setSizes([500, 599])
        window.ctx.actions.reset_layout.trigger()
        self.app.processEvents()
        self.assertIs(window.layout_host.panels["history"], history)
        read = window.layout_host.state()
        self.assertEqual(strip_sizes(read), strip_sizes(DEFAULT_LAYOUT))
        self.assertNotEqual(read.sizes[0], 500)

    def test_a_hidden_panel_stays_hidden_after_a_restart(self):
        window = self.start()
        window.layout_host.hide_panel("branches")
        self.quit(window)

        again = self.start()
        self.assertEqual(set(again.layout_host.panels), {"controls", "worktrees", "files", "history", "diff", "staged", "commit", "commit_details", "commit_files", "stash"})
        again.layout_host.show_panel("branches")  # and comes back where it was
        self.assertEqual(strip_sizes(again.layout_host.state()), strip_sizes(DEFAULT_LAYOUT))

    def test_no_toolbar_the_controls_panel_has_its_buttons(self):
        window = self.start()
        self.assertEqual(window.findChildren(QToolBar), [])
        buttons = window.layout_host.panels["controls"].findChildren(QToolButton)
        actions = window.ctx.actions
        # The action buttons; the branch button (a menu, no action) is last.
        self.assertEqual([b.defaultAction() for b in buttons],
                         [actions.open_repo, actions.refresh, actions.fetch, actions.pull,
                          actions.push, None])

    def test_the_status_bar_shows_a_network_commands_progress_while_it_runs(self):
        window = self.start()
        label = window._network_label
        self.assertFalse(label.isVisibleTo(window))
        command = NetworkCommand(NetworkKind.FETCH)
        window.session.network_changed.emit(command)
        self.assertTrue(label.isVisibleTo(window))
        self.assertEqual(label.text(), "Fetching…")
        window.session.network_changed.emit(NetworkCommand(NetworkKind.PULL, remote="origin"))
        self.assertEqual(label.text(), "Pulling…")
        window.session.network_changed.emit(NetworkCommand(NetworkKind.PUSH, remote="origin"))
        self.assertEqual(label.text(), "Pushing…")
        window.session.network_progress.emit("Receiving objects:  50% (1/2)")
        self.assertEqual(label.text(), "Receiving objects:  50% (1/2)")
        window.session.network_changed.emit(None)
        self.assertFalse(label.isVisibleTo(window))

    def test_view_menu_has_layout_panels_and_reset(self):
        window = self.start()
        view = next(a.menu() for a in window.menuBar().actions() if a.text() == "&View")
        self.assertEqual([a.text() for a in view.actions()],
                         ["&Layout", "&Panels", "", "Show Files as &Tree",
                          "&Side by Side Diff", "Show &Whole File", "&Hunk Headers",
                          "&Topological Order", "", "&Theme", "", "&Reset Layout"])

    def test_the_theme_menu_switches_live_and_is_remembered(self):
        self.addCleanup(ThemeManager, self.app, LIGHT_THEME)  # leave the shared app light
        window = self.start()
        actions = window.ctx.actions.theme_modes
        view = next(a.menu() for a in window.menuBar().actions() if a.text() == "&View")
        theme_menu = next(a.menu() for a in view.actions() if a.text() == "&Theme")
        self.assertEqual(theme_menu.actions()[:3], list(actions.values()))
        self.assertTrue(theme_menu.actions()[3].isSeparator())
        self.assertEqual(theme_menu.actions()[4:], [window.ctx.actions.colour_blind])
        self.assertTrue(actions[ThemeMode.LIGHT].isChecked())  # the context's theme
        actions[ThemeMode.DARK].trigger()
        self.assertIs(window.ctx.theme.current, DARK_THEME)
        self.assertEqual(self.app.styleSheet(), DARK_THEME.stylesheet())
        self.quit(window)
        self.assertEqual(AppSettings(self.settings_path).theme_mode, "dark")

    def test_colour_blind_colours_switch_live_and_are_remembered(self):
        self.addCleanup(ThemeManager, self.app, LIGHT_THEME)
        window = self.start()
        action = window.ctx.actions.colour_blind
        self.assertFalse(action.isChecked())
        window.ctx.theme.set_mode(ThemeMode.DARK)
        action.trigger()
        self.assertIs(window.ctx.theme.current, DARK_CB_THEME)
        self.assertEqual(self.app.styleSheet(), DARK_CB_THEME.stylesheet())
        self.quit(window)
        self.assertTrue(AppSettings(self.settings_path).colour_blind)

    def test_the_colour_blind_action_starts_checked_from_the_theme(self):
        self.addCleanup(ThemeManager, self.app, LIGHT_THEME)
        ctx = make_panel_context()
        ctx.theme.set_colour_blind(True)
        window = MainWindow(ctx, AppSettings(self.settings_path))
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.session.shutdown)
        self.assertTrue(ctx.actions.colour_blind.isChecked())

    def test_every_panel_is_dark_in_the_dark_theme(self):
        self.addCleanup(ThemeManager, self.app, LIGHT_THEME)
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "one\ntwo\n", "first")
        (repo / "a.txt").write_text("one\nthree\n")
        (repo / "new.txt").write_text("new\n")
        window = self.start()
        window.resize(1200, 800)
        window.ctx.theme.set_mode(ThemeMode.DARK)  # live, after the panels were built
        window.session.open(repo)
        wait_idle(window.session)
        shown = FileSelection("a.txt", FileSource.WORKTREE)
        window.ctx.selection.select([shown], shown)  # the Diff panel shows it
        wait_idle(window.session)
        self.app.processEvents()
        for name, panel in window.layout_host.panels.items():
            if panel.isVisible() and panel.width() > 0 and panel.height() > 0:
                self.assertLess(bright_share(panel.grab().toImage()), 0.08, name)
        self.assertLess(bright_share(window.grab().toImage()), 0.08)

    def test_a_saved_layout_survives_a_restart(self):
        window = self.start()
        window.layout_host.hide_panel("branches")
        layout_menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "&View"
                           ).actions()[0].menu()
        self.assertTrue(layout_menu.save("Review"))
        window.ctx.actions.reset_layout.trigger()
        self.quit(window)

        again = self.start()
        layout_menu = next(a.menu() for a in again.menuBar().actions() if a.text() == "&View"
                           ).actions()[0].menu()
        self.assertEqual(again.layout_host.hidden, {})
        next(a for a in layout_menu.actions() if a.text() == "Review").trigger()
        self.assertEqual(set(again.layout_host.hidden), {"branches"})

    def test_a_commit_s_file_shows_its_diff_from_history_to_diff(self):
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "1\n", "first")
        commit_file(repo, "a.txt", "2\n", "second")
        window = self.start()
        window.session.open(repo)
        wait_idle(window.session)
        panels = window.layout_host.panels
        self.assertTrue(panels["commit_files"].collapsed)  # Uncommitted changes
        sidebar = panels["branches"].parentWidget().parentWidget()
        branches_height = sidebar.sizes()[0]
        working = sidebar.sizes()
        main = panels["commit_details"].parentWidget().parentWidget()
        self.assertEqual(main.sizes()[1], 0)  # collapsed: the tabs have its space
        main_height = sum(main.sizes())
        details_height = main.sizes()
        history = panels["history"]
        history._view.setCurrentIndex(history._model.index(2, 0))  # "first"
        wait_idle(window.session)
        self.app.processEvents()
        first = window.ctx.selection.commit
        self.assertEqual(first.subject, "first")
        # Commit Files alone takes the place of the working tree's panels (RZG-65).
        swapped = ("files", "staged", "commit", "commit_files")
        self.assertEqual([pid for pid in swapped if not panels[pid].collapsed],
                         ["commit_files"])
        self.assertTrue(panels["commit_files"].isVisible())
        files_at = sidebar.indexOf(panels["commit_files"].parentWidget())
        self.assertEqual(sidebar.sizes()[files_at], sum(working[1:files_at + 1]))
        # Commit Details below History and Diff, at its height from the tabs above it.
        details = panels["commit_details"]
        self.assertTrue(details.isVisible())
        main = details.parentWidget().parentWidget()
        self.assertEqual(main.indexOf(details.parentWidget()), 1)
        self.assertIs(main.widget(0), panels["history"].parentWidget().parentWidget())
        self.assertLessEqual(abs(sum(main.sizes()) - main_height), 2)  # the divider
        self.assertEqual(main.sizes()[1], 220)  # MAIN's size for it
        shown = main.sizes()
        for _ in range(3):  # back and forth, with no drift
            history._view.setCurrentIndex(history._model.index(0, 0))
            self.app.processEvents()
            self.assertEqual(main.sizes()[1], 0)
            history._view.setCurrentIndex(history._model.index(2, 0))
            wait_idle(window.session)
            self.app.processEvents()
            self.assertLessEqual(abs(main.sizes()[0] - shown[0]), 1)  # the divider
            self.assertEqual(main.sizes()[1], 220)
        self.assertEqual(panels["commit_details"]._summary.text(), "first")
        self.assertFalse(panels["files"].isVisible())
        self.assertEqual(sidebar.sizes()[0], branches_height)
        tree = panels["commit_files"]._list._tree
        tree.setCurrentItem(tree.topLevelItem(0))
        wait_idle(window.session)
        self.assertEqual(window.session.diff_file,
                         FileSelection("a.txt", FileSource.COMMIT, first.sha))
        self.assertEqual([l.text for h in window.session.diff.hunks for l in h.lines], ["1"])
        history._view.setCurrentIndex(history._model.index(0, 0))  # back to Uncommitted
        self.app.processEvents()
        self.assertTrue(panels["commit_files"].collapsed)
        self.assertTrue(panels["commit_details"].collapsed)
        self.assertEqual(main.sizes()[1], 0)  # the tabs have it all back
        self.assertLessEqual(abs(main.sizes()[0] - details_height[0]), 1)  # the divider
        self.assertTrue(panels["files"].isVisible())
        self.assertEqual(sidebar.sizes(), working)
        self.assertIsNone(window.ctx.selection.file)

    def test_a_stash_shows_like_a_commit_and_history_lets_it_be(self):
        """RZG-36: selected in Stashes, it fills Commit Details, Commit Files and Diff;
        History has no row meanwhile, even when the status changes."""
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "1\n", "first")
        commit_file(repo, "a.txt", "2\n", "second")
        (repo / "a.txt").write_text("3\n")
        (repo / "new.txt").write_text("new\n")
        window = self.start()
        window.session.open(repo)
        wait_idle(window.session)
        window.session.stash_changes("saved")
        wait_idle(window.session)
        panels = window.layout_host.panels
        stashes, history = panels["stash"], panels["history"]
        stashes._tree.setCurrentItem(stashes._tree.topLevelItem(0))
        wait_idle(window.session)
        self.app.processEvents()
        [stash] = window.session.stashes
        self.assertEqual(window.ctx.selection.commit, stash)
        self.assertFalse(history._view.currentIndex().isValid())
        self.assertTrue(panels["files"].collapsed)
        self.assertEqual(panels["commit_files"].header, "Files in stash@{0} (2)")
        details = panels["commit_details"]
        self.assertEqual(details._parents_name.text(), "Based on")
        self.assertEqual([lbl.property("sha") for lbl in details._parents],
                         [window.session.history[0].sha])
        (repo / "other.txt").write_text("x")  # the status changes: History stays as it is
        window.session.refresh()
        wait_idle(window.session)
        self.assertEqual(window.ctx.selection.commit, stash)
        self.assertFalse(history._view.currentIndex().isValid())
        history._view.setCurrentIndex(history._model.index(1, 0))  # "second"
        self.assertEqual(window.ctx.selection.commit.subject, "second")
        self.assertEqual(stashes._tree.selectedItems(), [])
        self.assertEqual(panels["commit_files"].header, "Files in "
                         f"{window.session.history[0].short_sha}")
        self.assertEqual(details._parents_name.text(), "Parents")

    def test_a_popped_stash_goes_back_to_uncommitted_changes_with_a_notice(self):
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "1\n2\n3\n4\n5\n6\n", "first")
        (repo / "a.txt").write_text("ONE\n2\n3\n4\n5\n6\n")
        Repo(repo).stage(["a.txt"])
        window = self.start()
        window.session.open(repo)
        wait_idle(window.session)
        window.session.stash_changes("saved")
        wait_idle(window.session)
        commit_file(repo, "a.txt", "1\n2\nTHREE\n4\n5\n6\n", "three")
        window.session.refresh()
        wait_idle(window.session)
        stashes = window.layout_host.panels["stash"]
        stashes._tree.setCurrentItem(stashes._tree.topLevelItem(0))
        with mock.patch("rzgitclient.ui.main_window.QMessageBox.information") as info:
            window.ctx.actions.request_stash(StashOp.POP, window.session.stashes[0])
            wait_idle(window.session)
        info.assert_called_once()
        self.assertIn("unstaged", info.call_args.args[2])
        self.assertIsNone(window.ctx.selection.commit)
        history = window.layout_host.panels["history"]
        self.assertEqual(history._view.currentIndex().row(), 0)  # Uncommitted changes
        self.assertEqual(window.session.stashes, ())

    def test_a_layout_saved_while_a_commit_is_selected_is_the_working_tree_s(self):
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "1\n", "first")
        window = self.start()
        window.session.open(repo)
        wait_idle(window.session)
        sidebar = window.layout_host.panels["branches"].parentWidget().parentWidget()
        working = sidebar.sizes()
        history = window.layout_host.panels["history"]
        history._view.setCurrentIndex(history._model.index(1, 0))
        wait_idle(window.session)
        self.app.processEvents()
        self.assertTrue(window.layout_host.panels["files"].collapsed)
        self.quit(window)

        again = self.start()
        again.session.open(repo)
        wait_idle(again.session)
        self.app.processEvents()
        sidebar = again.layout_host.panels["branches"].parentWidget().parentWidget()
        self.assertEqual(sidebar.sizes(), working)

    def test_a_built_in_layout_follows_the_code_and_a_custom_one_stays(self):
        window = self.start()
        self.quit(window)
        settings = AppSettings(self.settings_path)
        self.assertEqual(settings.layout_preset, "Standard")
        # As if an older version had saved Standard with History and Diff side by side.
        old = SplitNode(Orientation.VERTICAL, (PanelNode("controls"), SplitNode(
            Orientation.HORIZONTAL, (PanelNode("branches"), PanelNode("history"),
                                     PanelNode("diff")))))
        settings.layout = legacy(old)
        settings.sync()
        again = self.start()
        self.assertEqual(strip_sizes(again.layout_host.state()), strip_sizes(DEFAULT_LAYOUT))
        self.quit(again)

        settings = AppSettings(self.settings_path)
        settings.layout, settings.layout_preset = legacy(old), None  # custom
        settings.sync()
        third = self.start()
        main = third.layout_host.state().tree(Area.MAIN)  # Commit Details, new, below
        self.assertEqual(main.children[0], SplitNode(  # still History beside Diff
            Orientation.HORIZONTAL, (PanelNode("history"), PanelNode("diff")), sizes=(
                main.children[0].sizes)))
        self.assertEqual(third.layout_host.area_of("branches"), Area.SIDEBAR)
        self.quit(third)
        self.assertIsNone(AppSettings(self.settings_path).layout_preset)

    # ---- windows open on History (RZG-55) ------------------------------------

    def tabs_of(self, window, panel_id):
        """The tab group holding ``panel_id``, on screen."""
        state = window.layout_host.state()
        nodes = [state.tree(state.area_of(panel_id))]
        while nodes:
            node = nodes.pop()
            if isinstance(node, TabsNode) and PanelNode(panel_id) in node.children:
                return node
            nodes.extend(getattr(node, "children", ()))
        return None

    def start_with(self, state):
        settings = AppSettings(self.settings_path)
        settings.layout, settings.layout_preset = to_dict(state), None
        settings.sync()
        return self.start()

    def test_a_window_opens_on_history_when_it_is_on_the_main_pane(self):
        sidebar = TabsNode((PanelNode("branches"), PanelNode("worktrees"), PanelNode("files"),
                            PanelNode("stash")), current=2)
        saved = DEFAULT_LAYOUT.with_tree(Area.SIDEBAR, sidebar).with_current_tab("diff")
        window = self.start_with(saved)
        self.assertEqual(self.tabs_of(window, "history"),
                         TabsNode((PanelNode("history"), PanelNode("diff")), current=0))
        self.assertTrue(window.layout_host.panels["history"].isVisible())
        self.assertEqual(self.tabs_of(window, "files").current, 2)  # other groups keep theirs
        # Applying a layout to an open window shows it as saved.
        window.layout_host.apply(saved)
        self.assertEqual(window.layout_host.state().tree(Area.MAIN).children[0].current, 1)
        self.quit(window)
        self.assertEqual(  # and the current tab is saved as it is
            AppSettings(self.settings_path).layout["areas"]["main"]["children"][0]["current"], 1)

    def test_history_elsewhere_keeps_the_saved_tab(self):
        sidebar = TabsNode((PanelNode("branches"), PanelNode("history")), current=0)
        main = TabsNode((PanelNode("diff"), PanelNode("commit_details")), current=1)
        saved = LayoutState({Area.CONTROLS: PanelNode("controls"), Area.SIDEBAR: sidebar,
                             Area.MAIN: main})
        window = self.start_with(saved)
        self.assertEqual(window.layout_host.area_of("history"), Area.SIDEBAR)
        self.assertEqual(self.tabs_of(window, "history").current, 0)  # Branches, not History
        self.assertEqual(self.tabs_of(window, "diff").current, 1)

    def test_whole_file_and_hunk_headers_are_remembered(self):
        window = self.start()
        actions = window.ctx.actions
        self.assertFalse(actions.whole_file_diff.isChecked())
        self.assertEqual(actions.hunk_headers_mode(), HunkHeaders.SLIM)
        actions.whole_file_diff.setChecked(True)
        actions.hunk_headers[HunkHeaders.FULL].setChecked(True)
        self.quit(window)
        again = self.start()
        self.assertTrue(again.ctx.actions.whole_file_diff.isChecked())
        self.assertEqual(again.ctx.actions.hunk_headers_mode(), HunkHeaders.FULL)
        view = next(a.menu() for a in again.menuBar().actions() if a.text() == "&View")
        headers = next(a.menu() for a in view.actions() if a.text() == "&Hunk Headers")
        self.assertEqual([a.text() for a in headers.actions()], ["&Full", "&Slim", "&Off"])

    def test_side_by_side_diff_is_remembered(self):
        window = self.start()
        self.assertFalse(window.ctx.actions.side_by_side_diff.isChecked())
        window.ctx.actions.side_by_side_diff.setChecked(True)
        self.quit(window)
        again = self.start()
        self.assertTrue(again.ctx.actions.side_by_side_diff.isChecked())
        diff = again.layout_host.panels["diff"]
        self.assertIs(diff._stack.currentWidget(), diff._message_page)  # no file yet

    def test_file_tree_is_remembered(self):
        window = self.start()
        self.assertTrue(window.ctx.actions.file_tree.isChecked())
        window.ctx.actions.file_tree.setChecked(False)
        self.quit(window)
        self.assertFalse(self.start().ctx.actions.file_tree.isChecked())

    def test_topological_order_is_remembered(self):
        window = self.start()
        self.assertFalse(window.ctx.actions.topo_order.isChecked())
        window.ctx.actions.topo_order.setChecked(True)
        self.quit(window)
        again = self.start()
        self.assertTrue(again.ctx.actions.topo_order.isChecked())
        self.assertIs(again.session.history_order, HistoryOrder.TOPO)

    def test_discard_lines_asks_first(self):
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "a\nb\n", "first")
        (repo / "a.txt").write_text("a\nB\n")
        window = self.start()
        window.session.open(repo)
        wait_idle(window.session)
        diff = Repo(repo).diff("a.txt", DiffSource.WORKTREE)
        with mock.patch.object(window, "confirm_discard_lines", return_value=False) as ask:
            window.ctx.actions.request_discard_lines(diff, hunk_lines(diff, 0))
            wait_idle(window.session)
        ask.assert_called_once_with("a.txt", 2)
        self.assertEqual((repo / "a.txt").read_text(), "a\nB\n")  # declined
        with mock.patch.object(window, "confirm_discard_lines", return_value=True):
            window.ctx.actions.request_discard_lines(diff, hunk_lines(diff, 0))
            wait_idle(window.session)
        self.assertEqual((repo / "a.txt").read_text(), "a\nb\n")

    def test_repository_menu_has_the_staging_and_commit_actions(self):
        window = self.start()
        menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "&Repository")
        actions = window.ctx.actions
        self.assertEqual([a for a in menu.actions() if not a.isSeparator()],
                         [actions.stage, actions.unstage, actions.discard,
                          actions.stage_all, actions.unstage_all, actions.commit, actions.amend,
                          actions.stash_changes, actions.fetch, actions.pull, actions.push,
                          actions.worktrees,
                          actions.new_worktree])

    def test_discard_asks_first_and_acts_only_on_unstaged_rows(self):
        window = self.start()
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "1", "first")
        (repo / "a.txt").write_text("2")
        window.session.open(repo)
        wait_idle(window.session)
        window.ctx.selection.files = (FileSelection("a.txt", FileSource.WORKTREE),
                                      FileSelection("b.txt", FileSource.INDEX))
        with mock.patch.object(window, "confirm_discard", return_value=False) as confirm, \
                mock.patch.object(window.session, "discard") as discard:
            window.ctx.actions.discard.trigger()
            confirm.assert_called_once_with(("a.txt",))
            discard.assert_not_called()
            confirm.return_value = True
            window.ctx.actions.discard.trigger()
            discard.assert_called_once_with(("a.txt",))

    def test_the_discard_confirmation_names_the_files_and_defaults_to_cancel(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QMessageBox
        window = self.start()
        paths = tuple(f"f{n}.txt" for n in range(12))
        seen = {}

        def answer(discard):
            box = QApplication.activeModalWidget()
            try:
                seen["text"], seen["info"] = box.text(), box.informativeText()
                seen["default_is_cancel"] = (
                    box.defaultButton() is box.button(QMessageBox.StandardButton.Cancel))
                if discard:
                    next(b for b in box.buttons()
                         if box.buttonRole(b) is QMessageBox.ButtonRole.DestructiveRole).click()
            finally:
                if box.isVisible():
                    box.reject()  # never leave the test waiting on a dialog

        QTimer.singleShot(0, lambda: answer(False))
        self.assertFalse(window.confirm_discard(paths))
        self.assertEqual(seen["text"], "Discard the unstaged changes to 12 files?")
        self.assertIn("f9.txt\n… and 2 more", seen["info"])
        self.assertIn("trash", seen["info"])
        self.assertTrue(seen["default_is_cancel"])
        QTimer.singleShot(0, lambda: answer(True))
        self.assertTrue(window.confirm_discard(paths[:1]))

    def test_branch_requests_reach_the_branch_dialogs(self):
        from rzgitclient.ui.app_actions import BranchOp
        window = self.start()
        with mock.patch.object(window.branch_dialogs, "ask", return_value=None) as ask:
            window.ctx.actions.request_branch(BranchOp.MERGE, "feature")
        ask.assert_called_once()

    def test_amending_asks_only_for_a_pushed_commit(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QMessageBox
        from rzgitclient.git.git_models import LastCommit
        window = self.start()
        self.assertTrue(window.ctx.actions.confirm_amend(LastCommit("a" * 40, "m", False)))
        seen = {}

        def answer():
            box = QApplication.activeModalWidget()
            try:
                seen["text"] = box.text()
                seen["default_is_cancel"] = (
                    box.defaultButton() is box.button(QMessageBox.StandardButton.Cancel))
                box.button(QMessageBox.StandardButton.Cancel).click()
            finally:
                if box.isVisible():
                    box.reject()
        QTimer.singleShot(0, answer)
        self.assertFalse(window.ctx.actions.confirm_amend(LastCommit("b" * 40, "m", True)))
        self.assertEqual(seen, {"text": f"Amend {'b' * 8}, a pushed commit?",
                                "default_is_cancel": True})

    def test_show_worktrees_shows_the_panel_and_brings_its_tab_forward(self):
        window = self.start()
        action = window.ctx.actions.worktrees
        self.assertEqual(action.text(), "Show &Worktrees")
        self.assertEqual(action.shortcut().toString(), "Ctrl+Shift+W")
        window.layout_host.hide_panel("worktrees")
        self.assertNotIn("worktrees", window.layout_host.panels)
        window.show_worktrees()
        self.assertIn("worktrees", window.layout_host.panels)
        self.assertEqual(window.layout_host.hidden, {})
        window.layout_host.apply(LayoutState({
            Area.SIDEBAR: TabsNode((PanelNode("branches"), PanelNode("worktrees"))),
            Area.MAIN: PanelNode("history")}))
        tabs = window.layout_host.panels["worktrees"].parentWidget().parentWidget()
        self.assertEqual(tabs.currentIndex(), 0)
        window.show_worktrees()
        self.assertEqual(tabs.currentIndex(), 1)

    def test_open_in_this_window_opens_the_worktree_here(self):
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "1", "first")
        git(repo, "worktree", "add", "-q", "-b", "other", str(self.tmp / "other"))
        window = self.start()
        window.session.open(repo)
        wait_idle(window.session)
        window.ctx.actions.request_open_worktree_here(str(self.tmp / "other"))
        wait_idle(window.session)
        self.assertEqual(window.session.repo.root, (self.tmp / "other").resolve())

    def test_reset_layout_shows_hidden_panels(self):
        window = self.start()
        window.layout_host.hide_panel("files")
        window.ctx.actions.reset_layout.trigger()
        self.assertEqual(set(window.layout_host.panels), {"controls", "branches", "worktrees", "files", "history", "diff", "staged", "commit", "commit_details", "commit_files", "stash"})
        self.assertEqual(window.layout_host.hidden, {})

    def test_status_bar_counts_every_commit_not_just_the_loaded_page(self):
        repo = make_repo(self.tmp / "repo")
        for n in range(1, 6):
            commit_file(repo, "a.txt", str(n), f"c{n}")
        with mock.patch("rzgitclient.core.repo_session.HISTORY_PAGE", 2):
            window = self.start()
            window.session.open(repo)
            wait_idle(window.session)
        self.assertEqual(len(window.session.history), 2)  # one page loaded…
        self.assertIn("  ·  5 commits  ·  ", window.statusBar().currentMessage())  # …all counted

    def test_opened_repo_is_remembered(self):
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "1", "first")
        window = self.start()
        window.session.open(repo)
        wait_idle(window.session)
        self.quit(window)
        self.assertEqual(
            Path(AppSettings(self.settings_path).last_repo).resolve(), repo.resolve()
        )


    def test_loading_label_shows_while_git_is_busy(self):
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "1", "first")
        window = self.start()
        label = window._busy_label
        self.assertFalse(label.isVisible())
        window.session.open(repo)
        self.assertTrue(label.isVisible())
        self.assertEqual(label.text(), "Loading…")
        wait_idle(window.session)
        self.assertFalse(label.isVisible())
        self.assertIn("1 commit ", window.statusBar().currentMessage())  # summary kept



class CommitSummaryTests(unittest.TestCase):
    def test_exact_count_with_separators_and_singular(self):
        self.assertEqual(commit_summary(500, False, 2421), "2,421 commits")
        self.assertEqual(commit_summary(1, True, 1), "1 commit")
        self.assertEqual(commit_summary(0, True, 0), "0 commits")

    def test_until_counted_the_loaded_count_with_a_plus_if_more_exist(self):
        self.assertEqual(commit_summary(500, False, None), "500+ commits")
        self.assertEqual(commit_summary(2421, True, None), "2,421 commits")
        self.assertEqual(commit_summary(1, True, None), "1 commit")


if __name__ == "__main__":
    unittest.main()
