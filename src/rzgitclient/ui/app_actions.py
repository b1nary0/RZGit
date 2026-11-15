"""Every QAction, defined once (spec §8).

Menus, the toolbar, context menus and panels all show these same objects, so
text, icon, shortcut and enabled state are set in one place.
"""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QIcon, QKeySequence

from rzgitclient.core.repo_session import RepoSession


class AppActions(QObject):
    """Holds the actions and their enabled state. Window-level handlers (the
    Open dialog, closing the window) are connected by MainWindow."""

    def __init__(self, session: RepoSession, parent: QObject | None = None):
        super().__init__(parent)
        self.open_repo = self._action(
            "document-open", "&Open Repository…", QKeySequence.StandardKey.Open
        )
        self.refresh = self._action(
            "view-refresh", "&Refresh", QKeySequence.StandardKey.Refresh
        )
        self.quit = self._action(
            "application-exit", "&Quit", QKeySequence.StandardKey.Quit
        )

        # triggered(bool) would land in refresh's `parts` argument.
        self.refresh.triggered.connect(lambda: session.refresh())
        session.repo_changed.connect(self._on_repo_changed)
        self._on_repo_changed(session.repo)

    def _action(self, icon: str, text: str, shortcut: QKeySequence.StandardKey) -> QAction:
        action = QAction(QIcon.fromTheme(icon), text, self)
        action.setShortcuts(shortcut)  # every platform binding, e.g. F5 and the Refresh key
        return action

    def _on_repo_changed(self, repo: object) -> None:
        self.refresh.setEnabled(repo is not None)
