"""The open main windows (spec §8.1).

Made once by app.py. Each main window gets its own RepoSession, RepoWatcher,
WorktreeReader, Selection and AppActions; the settings, theme, commit drafts, column
choices and sort orders are shared.
A worktree is shown in at most one window: opening it again raises that one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QSize, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QStyle

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.core.column_prefs import ColumnPrefs
from rzgitclient.core.commit_drafts import CommitDrafts
from rzgitclient.core.repo_session import RepoSession
from rzgitclient.core.repo_watcher import RepoWatcher
from rzgitclient.core.selection import Selection
from rzgitclient.core.sort_prefs import SortPrefs
from rzgitclient.core.worktree_reader import WorktreeReader
from rzgitclient.git.repo import GitError, Repo, canonical_path
from rzgitclient.ui.app_actions import AppActions
from rzgitclient.ui.main_window import MainWindow
from rzgitclient.ui.panels.panel import PanelContext
from rzgitclient.ui.theme.theme_manager import ThemeManager


@dataclass(eq=False)
class _Window:
    """One main window and the objects that are its own."""

    window: MainWindow
    session: RepoSession
    watcher: RepoWatcher
    worktrees: WorktreeReader
    selection: Selection
    actions: AppActions
    shut_down: bool = False

    def shutdown(self) -> None:
        """Stop its work: the session waits for a git command still running."""
        if not self.shut_down:
            self.shut_down = True
            self.watcher.shutdown()
            self.worktrees.shutdown()
            self.session.shutdown()


class AppWindows(QObject):
    def __init__(self, app: QApplication, settings: AppSettings, theme: ThemeManager,
                 drafts: CommitDrafts, columns: ColumnPrefs, sorts: SortPrefs,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._app = app
        self._settings = settings
        self._theme = theme
        self._drafts = drafts
        self._columns = columns
        self._sorts = sorts
        self._open: list[_Window] = []
        # Every window follows its repository while the app is active (spec §4.3).
        app.applicationStateChanged.connect(self._on_app_state)

    # ---- the API (spec §8.1) --------------------------------------------------

    def windows(self) -> tuple[MainWindow, ...]:
        """The open main windows, in the order opened."""
        return tuple(w.window for w in self._open)

    def window_for(self, root: str | Path) -> MainWindow | None:
        """The window showing the worktree at ``root``, if one does."""
        root = canonical_path(root)
        for w in self._open:
            repo = w.session.repo
            if repo is not None and repo.root == root:
                return w.window
        return None

    def open(self, path: str | Path, *,
             from_window: MainWindow | None = None) -> MainWindow | None:
        """The window showing the worktree at ``path``, raised; else a new window
        opened on it. None if ``path`` isn't a repository (the error is shown)."""
        try:
            repo = Repo(path)
        except GitError as exc:
            QMessageBox.warning(from_window, "Git error", str(exc))
            return None
        if (window := self.window_for(repo.root)) is not None:
            window.bring_to_front()
            return window
        window = self.new_window(from_window=from_window)
        window.session.open(repo)
        window.show()
        return window

    def new_window(self, *, from_window: MainWindow | None = None) -> MainWindow:
        """A main window with no repository open, not shown yet. The first one
        starts at the saved geometry; later ones are placed by the window they're
        opened from (``from_window``, else the active one)."""
        anchor = from_window or self._anchor()
        session = RepoSession()
        watcher = RepoWatcher(session)
        worktrees = WorktreeReader(session)
        selection = Selection(session)
        actions = AppActions(session, selection)
        ctx = PanelContext(session=session, selection=selection, theme=self._theme,
                           actions=actions, drafts=self._drafts, columns=self._columns,
                           sorts=self._sorts, worktrees=worktrees)
        window = MainWindow(ctx, self._settings, self, restore_geometry=anchor is None)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # Its own objects are deleted with it (after its panels, which are connected
        # to them), the session last.
        for owned in (watcher, worktrees, actions, selection, session):
            owned.setParent(window)
        if anchor is not None:
            place_beside(window, anchor)
        record = _Window(window, session, watcher, worktrees, selection, actions)
        self._open.append(record)
        window.closed.connect(lambda: self._on_closed(record))
        active = self._app.applicationState() == Qt.ApplicationState.ApplicationActive
        watcher.set_active(active)
        worktrees.set_active(active)
        return window

    def shutdown(self) -> None:
        """Stop every window's work (app.py, after the event loop)."""
        for w in self._open:
            w.shutdown()

    # ---- internals ------------------------------------------------------------

    def _anchor(self) -> MainWindow | None:
        active = self._app.activeWindow()
        for w in self._open:
            if w.window is active:
                return w.window
        return self._open[-1].window if self._open else None

    def _on_app_state(self, state: Qt.ApplicationState) -> None:
        active = state == Qt.ApplicationState.ApplicationActive
        for w in self._open:
            w.watcher.set_active(active)
            w.worktrees.set_active(active)

    def _on_closed(self, record: _Window) -> None:
        if record not in self._open:
            return
        self._open.remove(record)
        record.shutdown()  # then deleted with the window (WA_DeleteOnClose)


def place_beside(window: MainWindow, anchor: MainWindow) -> None:
    """Half the width and height of the screen's available area (a quarter of a
    maximized window), down and right of ``anchor`` by a title bar's height, kept
    on the screen. Where windows can't be placed (Wayland) only the size applies."""
    screen = anchor.screen()
    area = screen.availableGeometry()
    size = QSize(area.width() // 2, area.height() // 2)
    window.resize(size)
    step = anchor.style().pixelMetric(QStyle.PixelMetric.PM_TitleBarHeight)
    pos = anchor.pos() + QPoint(step, step)
    x = max(area.left(), min(pos.x(), area.right() + 1 - size.width()))
    y = max(area.top(), min(pos.y(), area.bottom() + 1 - size.height()))
    window.move(x, y)
