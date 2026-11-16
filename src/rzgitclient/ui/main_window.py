from __future__ import annotations

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QMessageBox

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.core.selection import FileSource
from rzgitclient.git.repo import Repo
from rzgitclient.ui.layout.layout_host import LayoutHost
from rzgitclient.ui.layout.layout_menu import LayoutMenu
from rzgitclient.ui.layout.layout_model import to_dict
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT, matching_preset, restore_layout
from rzgitclient.ui.layout.panels_menu import PanelsMenu
from rzgitclient.ui.panels.panel import PanelContext


class MainWindow(QMainWindow):
    def __init__(self, ctx: PanelContext, settings: AppSettings):
        super().__init__()
        self.ctx = ctx
        self.session = session = ctx.session
        self.settings = settings

        self.setWindowTitle("RZGitClient")
        self.resize(1100, 700)
        if (geometry := settings.window_geometry) is not None:
            self.restoreGeometry(geometry)
        # Before the panels are built, so the Diff panel starts in the saved view.
        ctx.actions.side_by_side_diff.setChecked(settings.side_by_side_diff)
        ctx.actions.file_tree.setChecked(settings.file_tree)
        self._build_ui()
        self._build_actions()

        session.repo_changed.connect(self._on_repo_changed)
        for signal in (session.head_changed, session.history_changed, session.status_changed,
                       session.commit_count_changed):
            signal.connect(self._update_status_bar)
        session.error_occurred.connect(self._show_error)
        session.busy_changed.connect(self._busy_label.setVisible)

    # ---- construction -------------------------------------------------

    def _build_actions(self) -> None:
        actions = self.ctx.actions
        actions.open_repo.triggered.connect(self.choose_repo)
        actions.quit.triggered.connect(self.close)

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addActions([actions.open_repo, actions.refresh])
        file_menu.addSeparator()
        file_menu.addAction(actions.quit)

        actions.discard.triggered.connect(self.discard_selected)
        repo_menu = self.menuBar().addMenu("&Repository")
        repo_menu.addActions([actions.stage, actions.unstage, actions.discard])
        repo_menu.addSeparator()
        repo_menu.addActions([actions.stage_all, actions.unstage_all])
        repo_menu.addSeparator()
        repo_menu.addAction(actions.commit)

        actions.reset_layout.triggered.connect(self.reset_layout)
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addMenu(LayoutMenu(self.layout_host, self.settings, view_menu))
        view_menu.addMenu(PanelsMenu(self.layout_host, view_menu))
        view_menu.addSeparator()
        view_menu.addActions([actions.file_tree, actions.side_by_side_diff])
        view_menu.addSeparator()
        view_menu.addAction(actions.reset_layout)

    def _build_ui(self) -> None:
        self.layout_host = LayoutHost(self.ctx)
        settings = self.settings
        self.layout_host.apply_state(restore_layout(settings.layout, settings.layout_preset))
        self.setCentralWidget(self.layout_host)
        self.statusBar().showMessage("No repository open")
        # Beside the summary, not replacing it.
        self._busy_label = QLabel("Loading…")
        self._busy_label.setVisible(self.session.busy)
        self.statusBar().addPermanentWidget(self._busy_label)

    # ---- behaviour ----------------------------------------------------

    def reset_layout(self) -> None:
        self.layout_host.apply(DEFAULT_LAYOUT)

    def closeEvent(self, event: QCloseEvent) -> None:
        state = self.layout_host.state()
        self.settings.layout = to_dict(state) if state is not None else None
        self.settings.layout_preset = matching_preset(state) if state is not None else None
        self.settings.window_geometry = self.saveGeometry()
        self.settings.side_by_side_diff = self.ctx.actions.side_by_side_diff.isChecked()
        self.settings.file_tree = self.ctx.actions.file_tree.isChecked()
        super().closeEvent(event)

    def discard_selected(self) -> None:
        """Discard the unstaged changes of the selected files, once confirmed."""
        paths = self.ctx.actions.selected(FileSource.WORKTREE)
        if paths and self.confirm_discard(paths):
            self.session.discard(paths)

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

    def choose_repo(self) -> None:
        repo = self.session.repo
        start = str(repo.root) if repo else ""
        path = QFileDialog.getExistingDirectory(self, "Open Git Repository", start)
        if path:
            self.session.open(path)

    def _on_repo_changed(self, repo: Repo | None) -> None:
        if repo is None:
            self.setWindowTitle("RZGitClient")
        else:
            self.setWindowTitle(f"{repo.name} — RZGitClient")
            self.settings.last_repo = str(repo.root)
        self._update_status_bar()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Git error", message)

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
