from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QMessageBox

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.git.repo import Repo
from rzgitclient.ui.layout.layout_host import LayoutHost
from rzgitclient.ui.layout.layout_menu import LayoutMenu
from rzgitclient.ui.layout.layout_model import to_dict
from rzgitclient.ui.layout.layout_presets import DEFAULT_LAYOUT, restore_layout
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
        self._build_ui()
        self._build_actions()

        session.repo_changed.connect(self._on_repo_changed)
        for signal in (session.head_changed, session.history_changed, session.status_changed):
            signal.connect(self._update_status_bar)
        session.error_occurred.connect(self._show_error)
        session.busy_changed.connect(self._busy_label.setVisible)

    # ---- construction -------------------------------------------------

    def _build_actions(self) -> None:
        actions = self.ctx.actions
        actions.open_repo.triggered.connect(self.choose_repo)
        actions.quit.triggered.connect(self.close)

        toolbar = self.addToolBar("Main")
        toolbar.setObjectName("main-toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))
        toolbar.addActions([actions.open_repo, actions.refresh])

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addActions([actions.open_repo, actions.refresh])
        file_menu.addSeparator()
        file_menu.addAction(actions.quit)

        actions.reset_layout.triggered.connect(self.reset_layout)
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addMenu(LayoutMenu(self.layout_host, view_menu))
        view_menu.addMenu(PanelsMenu(self.layout_host, view_menu))
        view_menu.addSeparator()
        view_menu.addAction(actions.reset_layout)

    def _build_ui(self) -> None:
        self.layout_host = LayoutHost(self.ctx)
        self.layout_host.apply_state(restore_layout(self.settings.layout))
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
        self.settings.window_geometry = self.saveGeometry()
        super().closeEvent(event)

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
        self.statusBar().showMessage(
            f"{s.repo.root}  ·  {s.head or 'detached HEAD'}  ·  "
            f"{len(s.history)} commits  ·  {len(s.status)} changed files"
        )
