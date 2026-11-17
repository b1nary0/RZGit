from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from rzgitclient import __version__
from rzgitclient.core.app_settings import AppSettings
from rzgitclient.core.column_prefs import ColumnPrefs
from rzgitclient.core.commit_drafts import CommitDrafts, drafts_folder
from rzgitclient.ui.app_windows import AppWindows
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
    theme = ThemeManager(app, mode=ThemeMode(settings.theme_mode),
                         colour_blind=settings.colour_blind)
    # Shared by every window (spec §8.1); drafts saved by older versions move to files.
    drafts = CommitDrafts(drafts_folder(), legacy=settings)
    windows = AppWindows(app, settings, theme, drafts, ColumnPrefs(settings))

    window = windows.new_window()
    window.show()

    # Open a repo from the command line (its errors shown), else the last one used.
    args = [a for a in argv[1:] if not a.startswith("-")]
    if args:
        window.session.open(args[0])
    else:
        window.open_last_repo()

    code = app.exec()
    windows.shutdown()  # wait for a git command still running
    return code
