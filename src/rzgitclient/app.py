from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from rzgitclient import __version__
from rzgitclient.core.app_settings import AppSettings
from rzgitclient.core.repo_session import RepoSession
from rzgitclient.core.selection import Selection
from rzgitclient.ui.app_actions import AppActions
from rzgitclient.ui.main_window import MainWindow
from rzgitclient.ui.panels.panel import PanelContext
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from rzgitclient.ui.theme.theme_manager import ThemeManager


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("RZGitClient")
    app.setOrganizationName("RZGitClient")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("rzgitclient")
    theme = ThemeManager(app, LIGHT_THEME)
    session = RepoSession()

    ctx = PanelContext(
        session=session,
        selection=Selection(session),
        theme=theme,
        actions=AppActions(session),
    )

    settings = AppSettings()
    window = MainWindow(ctx, settings)
    window.show()

    # Open a repo from the command line, else the last one used.
    args = [a for a in argv[1:] if not a.startswith("-")]
    target = args[0] if args else settings.last_repo
    if target:
        session.open(target)

    code = app.exec()
    session.shutdown()  # wait for a git command still running
    return code
