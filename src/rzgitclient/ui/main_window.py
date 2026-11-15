from __future__ import annotations

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox, QSplitter

from rzgitclient.git.repo import Repo
from rzgitclient.ui.layout.panel_frame import PanelFrame
from rzgitclient.ui.panels.branches_panel import BranchesPanel
from rzgitclient.ui.panels.files_panel import FilesPanel
from rzgitclient.ui.panels.history_panel import HistoryPanel
from rzgitclient.ui.panels.panel import PanelContext


class MainWindow(QMainWindow):
    def __init__(self, ctx: PanelContext):
        super().__init__()
        self.ctx = ctx
        self.session = session = ctx.session
        self.settings = QSettings()

        self.setWindowTitle("RZGitClient")
        self.resize(1100, 700)
        self._build_actions()
        self._build_ui()

        session.repo_changed.connect(self._on_repo_changed)
        for signal in (session.head_changed, session.history_changed, session.status_changed):
            signal.connect(self._update_status_bar)
        session.error_occurred.connect(self._show_error)

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

    def _build_ui(self) -> None:
        self.branches_panel = BranchesPanel(self.ctx)
        self.files_panel = FilesPanel(self.ctx)
        self.history_panel = HistoryPanel(self.ctx)

        # Matches DEFAULT_LAYOUT (spec §6.1) until LayoutHost builds it (RZG-10).
        sidebar = QSplitter(Qt.Orientation.Vertical)
        sidebar.addWidget(PanelFrame(self.branches_panel))
        sidebar.addWidget(PanelFrame(self.files_panel))
        sidebar.setSizes([300, 400])

        main = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(sidebar)
        main.addWidget(PanelFrame(self.history_panel))
        main.setSizes([220, 880])

        self.setCentralWidget(main)
        self.statusBar().showMessage("No repository open")

    # ---- behaviour ----------------------------------------------------

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
            self.settings.setValue("last_repo", str(repo.root))
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
