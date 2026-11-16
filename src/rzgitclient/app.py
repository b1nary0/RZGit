from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from rzgitclient import __version__
from rzgitclient.core.app_settings import AppSettings
from rzgitclient.core.commit_drafts import CommitDrafts
from rzgitclient.core.repo_session import RepoSession
from rzgitclient.core.repo_watcher import RepoWatcher
from rzgitclient.core.selection import Selection
from rzgitclient.ui.app_actions import AppActions
from rzgitclient.ui.main_window import MainWindow
from rzgitclient.ui.panels.panel import PanelContext
from rzgitclient.ui.theme.theme_manager import ThemeManager, ThemeMode


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("RZGitClient")
    app.setOrganizationName("RZGitClient")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("rzgitclient")
    settings = AppSettings()
    # The saved theme, before any widget exists: the window never shows another first.
    theme = ThemeManager(app, mode=ThemeMode(settings.theme_mode))
    session = RepoSession()
    # The window follows the repository by itself (spec §4.3).
    watcher = RepoWatcher(session)
    app.applicationStateChanged.connect(
        lambda state: watcher.set_active(state == Qt.ApplicationState.ApplicationActive))

    selection = Selection(session)
    ctx = PanelContext(
        session=session,
        selection=selection,
        theme=theme,
        actions=AppActions(session, selection),
        drafts=CommitDrafts(settings),
    )

    window = MainWindow(ctx, settings)
    window.show()

    # Open a repo from the command line, else the last one used.
    args = [a for a in argv[1:] if not a.startswith("-")]
    target = args[0] if args else settings.last_repo
    if target:
        session.open(target)

    code = app.exec()
    watcher.shutdown()
    session.shutdown()  # wait for a git command still running
    return code
