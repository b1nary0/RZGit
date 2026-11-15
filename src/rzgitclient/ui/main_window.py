from __future__ import annotations

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rzgitclient.core.repo_session import RepoSession
from rzgitclient.git.git_models import Commit, FileStatus
from rzgitclient.git.repo import Repo
from rzgitclient.ui.theme.style_roles import Role, Surface, set_role, set_surface
from rzgitclient.ui.theme.theme_manager import ThemeManager

STATUS_LABELS = {
    "M": "Modified",
    "A": "Added",
    "D": "Deleted",
    "R": "Renamed",
    "C": "Copied",
    "U": "Conflict",
    "T": "Type changed",
    "?": "Untracked",
}


def _titled(title: str, widget: QWidget, surface: Surface) -> QWidget:
    """Wrap a widget with a section header on the given surface."""
    box = QWidget()
    set_surface(box, surface)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    label = QLabel(title.upper())
    set_role(label, Role.SECTION_HEADER)
    layout.addWidget(label)
    layout.addWidget(widget)
    return box


class MainWindow(QMainWindow):
    def __init__(self, theme: ThemeManager, session: RepoSession):
        super().__init__()
        self.theme = theme
        self.session = session
        self.settings = QSettings()

        self.setWindowTitle("RZGitClient")
        self.resize(1100, 700)
        self._build_actions()
        self._build_ui()

        session.repo_changed.connect(self._on_repo_changed)
        session.head_changed.connect(self._show_branches)
        session.branches_changed.connect(self._show_branches)
        session.history_changed.connect(self._show_history)
        session.status_changed.connect(self._show_status)
        session.error_occurred.connect(self._show_error)

    # ---- construction -------------------------------------------------

    def _build_actions(self) -> None:
        toolbar = self.addToolBar("Main")
        toolbar.setObjectName("main-toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))

        open_act = QAction(QIcon.fromTheme("document-open"), "&Open Repository…", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self.choose_repo)

        refresh_act = QAction(QIcon.fromTheme("view-refresh"), "&Refresh", self)
        refresh_act.setShortcut(QKeySequence.StandardKey.Refresh)
        refresh_act.triggered.connect(lambda: self.session.refresh())

        quit_act = QAction(QIcon.fromTheme("application-exit"), "&Quit", self)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self.close)

        toolbar.addAction(open_act)
        toolbar.addAction(refresh_act)

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(open_act)
        file_menu.addAction(refresh_act)
        file_menu.addSeparator()
        file_menu.addAction(quit_act)

    def _build_ui(self) -> None:
        self.branch_list = QListWidget()
        self.branch_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.branch_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.commit_tree = QTreeWidget()
        self.commit_tree.setHeaderLabels(["Subject", "Author", "Date", "Commit"])
        self.commit_tree.setRootIsDecorated(False)
        self.commit_tree.setUniformRowHeights(True)
        header = self.commit_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)

        self.changes_tree = QTreeWidget()
        self.changes_tree.setHeaderLabels(["Path", "Staged", "Unstaged"])
        self.changes_tree.setRootIsDecorated(False)
        self.changes_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.changes_tree.header().setStretchLastSection(False)

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(_titled("History", self.commit_tree, Surface.CONTENT))
        right.addWidget(_titled("Working Tree Changes", self.changes_tree, Surface.CONTENT))
        right.setSizes([450, 250])

        main = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(_titled("Branches", self.branch_list, Surface.SIDEBAR))
        main.addWidget(right)
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

    def _show_branches(self, _value: object = None) -> None:
        # Depends on both the branches and HEAD, so it reads the session.
        branches, head = self.session.branches, self.session.head
        self.branch_list.clear()
        for b in branches:
            item = QListWidgetItem(b.name)
            if b.is_current:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
            self.branch_list.addItem(item)
        if not branches and head:
            # Fresh repo: the branch exists in HEAD but has no ref yet.
            self.branch_list.addItem(QListWidgetItem(f"{head} (no commits)"))
        self._update_status_bar()

    def _show_history(self, commits: tuple[Commit, ...]) -> None:
        self.commit_tree.clear()
        theme = self.theme.current
        mono = theme.mono_font()
        muted = QColor(theme.colors.text_muted)
        for c in commits:
            item = QTreeWidgetItem(
                [c.subject, c.author, c.date.strftime("%Y-%m-%d %H:%M"), c.short_sha]
            )
            item.setFont(3, mono)
            item.setToolTip(3, c.sha)
            for col in (1, 2, 3):
                item.setForeground(col, muted)
            self.commit_tree.addTopLevelItem(item)
        for col in (1, 2, 3):
            self.commit_tree.resizeColumnToContents(col)
        self._update_status_bar()

    def _show_status(self, changes: tuple[FileStatus, ...]) -> None:
        theme = self.theme.current
        self.changes_tree.clear()
        for f in changes:
            path = f"{f.orig_path} → {f.path}" if f.orig_path else f.path
            staged = STATUS_LABELS.get(f.index, "") if f.is_staged else ""
            unstaged = (
                "Untracked" if f.is_untracked
                else STATUS_LABELS.get(f.worktree, "") if f.has_unstaged
                else ""
            )
            item = QTreeWidgetItem([path, staged, unstaged])
            if staged:
                item.setForeground(1, QColor(theme.status_color(f.index)))
            if unstaged:
                code = "?" if f.is_untracked else f.worktree
                item.setForeground(2, QColor(theme.status_color(code)))
            self.changes_tree.addTopLevelItem(item)
        for col in (1, 2):
            self.changes_tree.resizeColumnToContents(col)
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        s = self.session
        if s.repo is None:
            self.statusBar().showMessage("No repository open")
            return
        self.statusBar().showMessage(
            f"{s.repo.root}  ·  {s.head or 'detached HEAD'}  ·  "
            f"{len(s.history)} commits  ·  {len(s.status)} changed files"
        )
