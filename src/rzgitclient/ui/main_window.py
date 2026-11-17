from __future__ import annotations

from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QEvent, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMainWindow, QMessageBox

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.core.selection import FileSource
from rzgitclient.git.git_models import FileDiff, LastCommit
from rzgitclient.git.repo import GitError, Repo
from rzgitclient.ui.app_actions import HunkHeaders
from rzgitclient.ui.branch_dialogs import BranchDialogs
from rzgitclient.ui.layout.layout_host import LayoutHost
from rzgitclient.ui.layout.layout_menu import LayoutMenu
from rzgitclient.ui.layout.layout_model import Area, to_dict
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT, matching_preset, restore_layout
from rzgitclient.ui.layout.panels_menu import PanelsMenu
from rzgitclient.ui.panels.history_panel import HistoryPanel
from rzgitclient.ui.panels.panel import PanelContext
from rzgitclient.ui.panels.worktrees_panel import WorktreesPanel
from rzgitclient.ui.stash_dialogs import StashDialogs
from rzgitclient.ui.worktree_dialogs import WorktreeDialogs


class WindowDirectory(Protocol):
    """The open main windows (``AppWindows``, spec §8.1), as a window sees them."""

    def window_for(self, root: str | Path) -> MainWindow | None: ...

    def open(self, path: str | Path, *,
             from_window: MainWindow | None = None) -> MainWindow | None: ...


class MainWindow(QMainWindow):
    closed = Signal()  # after it saved, on a close that was accepted

    def __init__(self, ctx: PanelContext, settings: AppSettings,
                 windows: WindowDirectory | None = None, *, restore_geometry: bool = True):
        """``windows``: the other main windows (§8.1); without it, everything opens
        here. ``restore_geometry``: start at the saved size (the first window)."""
        super().__init__()
        self.ctx = ctx
        self.session = session = ctx.session
        self.settings = settings
        self.windows = windows

        self.setWindowTitle("RZGitClient")
        self.resize(1100, 700)
        if restore_geometry and (geometry := settings.window_geometry) is not None:
            self.restoreGeometry(geometry)
        # Before the panels are built, so the Diff panel starts in the saved view.
        ctx.actions.side_by_side_diff.setChecked(settings.side_by_side_diff)
        ctx.actions.whole_file_diff.setChecked(settings.whole_file_diff)
        ctx.actions.hunk_headers[HunkHeaders(settings.hunk_headers)].setChecked(True)
        ctx.actions.file_tree.setChecked(settings.file_tree)
        ctx.actions.topo_order.setChecked(settings.topo_order)
        self._check_theme_actions()
        self._build_ui()
        self._build_actions()

        session.repo_changed.connect(self._on_repo_changed)
        for signal in (session.head_changed, session.history_changed, session.status_changed,
                       session.commit_count_changed):
            signal.connect(self._update_status_bar)
        session.error_occurred.connect(self._show_error)
        session.notice_occurred.connect(self._show_notice)
        session.busy_changed.connect(self._busy_label.setVisible)
        session.network_changed.connect(self._on_network_changed)
        session.network_progress.connect(self._network_label.setText)

    # ---- construction -------------------------------------------------

    def _build_actions(self) -> None:
        actions = self.ctx.actions
        actions.open_repo.triggered.connect(self.choose_repo)
        actions.open_repo_new_window.triggered.connect(self.choose_repo_new_window)
        actions.quit.triggered.connect(QApplication.closeAllWindows)  # every window (§8.1)

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addActions([actions.open_repo, actions.open_repo_new_window, actions.refresh])
        file_menu.addSeparator()
        file_menu.addAction(actions.quit)

        actions.discard.triggered.connect(self.discard_selected)
        actions.discard_lines_requested.connect(self.discard_lines)
        self.branch_dialogs = BranchDialogs(self, self.session, actions)
        repo_menu = self.menuBar().addMenu("&Repository")
        repo_menu.addActions([actions.stage, actions.unstage, actions.discard])
        repo_menu.addSeparator()
        repo_menu.addActions([actions.stage_all, actions.unstage_all])
        repo_menu.addSeparator()
        repo_menu.addActions([actions.commit, actions.amend])
        repo_menu.addSeparator()
        repo_menu.addAction(actions.stash_changes)
        repo_menu.addSeparator()
        repo_menu.addAction(actions.fetch)
        repo_menu.addSeparator()
        repo_menu.addActions([actions.worktrees, actions.new_worktree])
        actions.worktrees.triggered.connect(self.show_worktrees)
        actions.open_worktree_requested.connect(self.open_in_window)
        actions.open_worktree_here_requested.connect(self.open_repo)
        self.stash_dialogs = StashDialogs(self, self.session, actions)
        self.worktree_dialogs = WorktreeDialogs(
            self, self.session, actions, self.ctx.worktrees,
            open_in_window=self.open_in_window,
            window_for=lambda root: self.windows.window_for(root) if self.windows else None)
        actions.set_amend_confirmer(self.confirm_amend)

        actions.reset_layout.triggered.connect(self.reset_layout)
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addMenu(LayoutMenu(self.layout_host, self.settings, view_menu))
        view_menu.addMenu(PanelsMenu(self.layout_host, view_menu))
        view_menu.addSeparator()
        view_menu.addActions([actions.file_tree, actions.side_by_side_diff,
                              actions.whole_file_diff])
        headers_menu = view_menu.addMenu("&Hunk Headers")
        headers_menu.addActions(list(actions.hunk_headers.values()))
        view_menu.addAction(actions.topo_order)
        view_menu.addSeparator()
        theme_menu = view_menu.addMenu("&Theme")
        for mode, action in actions.theme_modes.items():
            theme_menu.addAction(action)
            action.triggered.connect(lambda _checked=False, mode=mode: self.ctx.theme.set_mode(mode))
        theme_menu.addSeparator()
        theme_menu.addAction(actions.colour_blind)
        actions.colour_blind.toggled.connect(self.ctx.theme.set_colour_blind)
        view_menu.addSeparator()
        view_menu.addAction(actions.reset_layout)

    def _build_ui(self) -> None:
        self.layout_host = LayoutHost(self.ctx)
        settings = self.settings
        state = restore_layout(settings.layout, settings.layout_preset)
        if state.area_of(HistoryPanel.panel_id) is Area.MAIN:  # opens on History (§6.3)
            state = state.with_current_tab(HistoryPanel.panel_id)
        self.layout_host.apply(state)
        self.setCentralWidget(self.layout_host)
        self.statusBar().showMessage("No repository open")
        # Beside the summary, not replacing it.
        self._busy_label = QLabel("Loading…")
        self._busy_label.setVisible(self.session.busy)
        self.statusBar().addPermanentWidget(self._busy_label)
        # A network command's progress (spec §8), while it runs.
        self._network_label = QLabel()
        self._network_label.setVisible(False)
        self.statusBar().addPermanentWidget(self._network_label)

    # ---- behaviour ----------------------------------------------------

    def _on_network_changed(self, command: object) -> None:
        self._network_label.setText("Fetching…")
        self._network_label.setVisible(command is not None)

    def reset_layout(self) -> None:
        self.layout_host.apply(DEFAULT_LAYOUT)

    def closeEvent(self, event: QCloseEvent) -> None:
        state = self.layout_host.state()
        self.settings.layout = to_dict(state)
        self.settings.layout_preset = matching_preset(state)
        self.settings.window_geometry = self.saveGeometry()
        self.settings.side_by_side_diff = self.ctx.actions.side_by_side_diff.isChecked()
        self.settings.whole_file_diff = self.ctx.actions.whole_file_diff.isChecked()
        self.settings.hunk_headers = self.ctx.actions.hunk_headers_mode().value
        self.settings.file_tree = self.ctx.actions.file_tree.isChecked()
        self.settings.topo_order = self.ctx.actions.topo_order.isChecked()
        self.settings.theme_mode = self.ctx.theme.mode.value
        self.settings.colour_blind = self.ctx.theme.colour_blind
        super().closeEvent(event)
        if event.isAccepted():
            self.closed.emit()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            # Another window may have changed the shared theme (§8.1).
            self._check_theme_actions()
            self._save_last_repo()

    def _check_theme_actions(self) -> None:
        actions, theme = self.ctx.actions, self.ctx.theme
        actions.theme_modes[theme.mode].setChecked(True)
        actions.colour_blind.setChecked(theme.colour_blind)

    def _save_last_repo(self) -> None:
        """The main repository of this window's, even for a linked worktree (§6.3)."""
        if (repo := self.session.repo) is not None:
            self.settings.last_repo = str(repo.main_root)

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def discard_selected(self) -> None:
        """Discard the unstaged changes of the selected files, once confirmed."""
        paths = self.ctx.actions.selected(FileSource.WORKTREE)
        if paths and self.confirm_discard(paths):
            self.session.discard(paths)

    def discard_lines(self, diff: FileDiff, lines: frozenset[tuple[int, int]]) -> None:
        """Discard chosen lines of a diff from the working tree, once confirmed."""
        if lines and self.confirm_discard_lines(diff.path, len(lines)):
            self.session.discard_lines(diff, lines)

    def confirm_discard_lines(self, path: str, count: int) -> bool:
        lines = "1 changed line" if count == 1 else f"{count:,} changed lines"
        box = QMessageBox(QMessageBox.Icon.Warning, "Discard Lines",
                          f"Discard {lines} of {path}?", parent=self)
        box.setInformativeText("They are removed from the file in the working tree. "
                               "This can't be undone.")
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is discard

    def confirm_amend(self, last: LastCommit) -> bool:
        """Amend ``last``? Asked only if it's pushed (spec §8)."""
        if not last.pushed:
            return True
        box = QMessageBox(QMessageBox.Icon.Warning, "Amend",
                          f"Amend {last.sha[:8]}, a pushed commit?", parent=self)
        box.setInformativeText(
            "It is already on a remote branch. Amending replaces it here only: the remote "
            "keeps the old commit, so pushing this branch will need a force push.")
        amend = box.addButton("Amend", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is amend

    def confirm_discard(self, paths: tuple[str, ...]) -> bool:
        shown = "\n".join(paths[:_LISTED])
        if len(paths) > _LISTED:
            shown += f"\n… and {len(paths) - _LISTED:,} more"
        files = "1 file" if len(paths) == 1 else f"{len(paths):,} files"
        box = QMessageBox(QMessageBox.Icon.Warning, "Discard Changes",
                          f"Discard the unstaged changes to {files}?", parent=self)
        box.setInformativeText(
            f"{shown}\n\nThis can't be undone. Staged changes are kept, and new "
            "(untracked) files are moved to the trash."
        )
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is discard

    def open_last_repo(self) -> None:
        """Reopen the last repository, quietly: if it's gone, say so in the status bar
        and keep the setting (its drive may be back next time; spec §6.3)."""
        path = self.settings.last_repo
        if path and not self.session.open(path, report=False):
            self.statusBar().showMessage(f"Last repository not found: {path}")

    def choose_repo(self) -> None:
        if path := self._ask_for_repo():
            self.open_repo(path)

    def choose_repo_new_window(self) -> None:
        """Open a chosen folder in a new window, or raise the one showing it (§8)."""
        if path := self._ask_for_repo():
            self.open_in_window(path)

    def _ask_for_repo(self) -> str:
        repo = self.session.repo
        start = str(repo.root) if repo else ""
        return QFileDialog.getExistingDirectory(self, "Open Git Repository", start)

    def open_repo(self, path: str | Path) -> bool:
        """Open ``path`` here, unless another window shows that worktree: then raise
        that one and leave this window as it is (§8.1). False if it isn't a repo."""
        if self.windows is None:
            return self.session.open(path)
        try:
            repo = Repo(path)
        except GitError as exc:
            self._show_error(str(exc))
            return False
        other = self.windows.window_for(repo.root)
        if other is not None and other is not self:
            other.bring_to_front()
            return True
        return self.session.open(repo)

    def open_in_window(self, path: str | Path) -> MainWindow | None:
        """The window showing ``path``, raised, or a new one opened on it (§8.1).
        Without other windows, ``path`` opens here."""
        if self.windows is None:
            return self if self.open_repo(path) else None
        return self.windows.open(path, from_window=self)

    def show_worktrees(self) -> None:
        """Show the Worktrees panel if it is hidden, and bring its tab to the front (§8)."""
        self.layout_host.show_panel(WorktreesPanel.panel_id)
        panel = self.layout_host.panels.get(WorktreesPanel.panel_id)
        if panel is not None:
            panel.request_activation()

    def _on_repo_changed(self, repo: Repo | None) -> None:
        if repo is None:
            self.setWindowTitle("RZGitClient")
        else:
            self.setWindowTitle(f"{repo.worktree_name} — RZGitClient")
            self._save_last_repo()
        self._update_status_bar()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Git error", message)

    def _show_notice(self, message: str) -> None:
        QMessageBox.information(self, "RZGitClient", message)

    def _update_status_bar(self, _value: object = None) -> None:
        s = self.session
        if s.repo is None:
            self.statusBar().showMessage("No repository open")
            return
        commits = commit_summary(len(s.history), s.history_complete, s.commit_count)
        self.statusBar().showMessage(
            f"{s.repo.root}  ·  {s.head or 'detached HEAD'}  ·  "
            f"{commits}  ·  {len(s.status)} changed files"
        )


_LISTED = 10  # files named in the discard confirmation


def commit_summary(loaded: int, complete: bool, count: int | None) -> str:
    """"2,421 commits" from the exact count; until it's known, "500+ commits" (spec §8)."""
    if count is None:
        count = loaded
        if not complete:
            return f"{count:,}+ commits"
    return f"{count:,} commit" + ("" if count == 1 else "s")
