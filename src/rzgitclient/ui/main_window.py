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

from rzgitclient.git.repo import GitError, Repo
from rzgitclient.ui.theme import COLORS, STATUS_COLORS, monospace_font

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


def _titled(title: str, widget: QWidget, role: str) -> QWidget:
    """Wrap a widget with a section header. ``role`` is "sidebar" or
    "content" and selects the pane styling in the theme."""
    box = QWidget()
    box.setObjectName(role)
    box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    label = QLabel(title.upper())
    label.setObjectName("sectionHeader")
    layout.addWidget(label)
    layout.addWidget(widget)
    return box


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.repo: Repo | None = None
        self.settings = QSettings()

        self.setWindowTitle("RZGitClient")
        self.resize(1100, 700)
        self._build_actions()
        self._build_ui()

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
        refresh_act.triggered.connect(self.refresh)

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
        right.addWidget(_titled("History", self.commit_tree, "content"))
        right.addWidget(_titled("Working Tree Changes", self.changes_tree, "content"))
        right.setSizes([450, 250])

        main = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(_titled("Branches", self.branch_list, "sidebar"))
        main.addWidget(right)
        main.setSizes([220, 880])

        self.setCentralWidget(main)
        self.statusBar().showMessage("No repository open")

    # ---- behaviour ----------------------------------------------------

    def choose_repo(self) -> None:
        start = str(self.repo.root) if self.repo else ""
        path = QFileDialog.getExistingDirectory(self, "Open Git Repository", start)
        if path:
            self.open_repo(path)

    def open_repo(self, path: str) -> bool:
        try:
            self.repo = Repo(path)
        except GitError as exc:
            QMessageBox.warning(self, "Not a git repository", str(exc))
            return False
        self.settings.setValue("last_repo", str(self.repo.root))
        self.refresh()
        return True

    def refresh(self) -> None:
        if not self.repo:
            return
        try:
            branches = self.repo.branches()
            commits = self.repo.log()
            changes = self.repo.status()
            current = self.repo.current_branch()
        except GitError as exc:
            QMessageBox.critical(self, "Git error", str(exc))
            return

        self.setWindowTitle(f"{self.repo.name} — RZGitClient")

        self.branch_list.clear()
        for b in branches:
            item = QListWidgetItem(b.name)
            if b.is_current:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
            self.branch_list.addItem(item)
        if not branches and current:
            # Fresh repo: the branch exists in HEAD but has no ref yet.
            self.branch_list.addItem(QListWidgetItem(f"{current} (no commits)"))

        self.commit_tree.clear()
        mono = monospace_font()
        muted = QColor(COLORS["text_muted"])
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
                item.setForeground(1, QColor(STATUS_COLORS.get(f.index, COLORS["text"])))
            if unstaged:
                code = "?" if f.is_untracked else f.worktree
                item.setForeground(2, QColor(STATUS_COLORS.get(code, COLORS["text"])))
            self.changes_tree.addTopLevelItem(item)
        for col in (1, 2):
            self.changes_tree.resizeColumnToContents(col)

        branch_text = current or "detached HEAD"
        self.statusBar().showMessage(
            f"{self.repo.root}  ·  {branch_text}  ·  "
            f"{len(commits)} commits  ·  {len(changes)} changed files"
        )
