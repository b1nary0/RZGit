from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from rzgit import __version__
from rzgit.core.app_settings import AppSettings
from rzgit.core.column_prefs import ColumnPrefs
from rzgit.core.commit_drafts import CommitDrafts, drafts_folder
from rzgit.core.file_saver import FileSaver
from rzgit.core.gc_on_main_thread import GcOnMainThread
from rzgit.core.old_name_migration import migrate_old_name
from rzgit.core.preferences import Preferences
from rzgit.core.recent_repos import RecentRepos
from rzgit.core.sort_prefs import SortPrefs
from rzgit.ui.app_icon import app_icon
from rzgit.ui.app_windows import AppWindows
from rzgit.ui.save_dialog import SaveDialog
from rzgit.ui.theme.theme_manager import ThemeManager, ThemeMode


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    GcOnMainThread.install(app)  # never collect on the git worker (spec §4.1)
    app.setApplicationName("RZGit")
    app.setOrganizationName("RZGit")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("rzgit")
    app.setWindowIcon(app_icon())  # every window and dialog
    migrate_old_name()  # settings and drafts saved as RZGitClient, copied once (spec §6.3)
    settings = AppSettings()
    prefs = Preferences(settings)  # the Settings window's settings (spec §8.2)
    # The saved theme, before any widget exists: the window never shows another first.
    theme = ThemeManager(app, mode=ThemeMode(settings.theme_mode),
                         colour_blind=settings.colour_blind, prefs=prefs)
    # Shared by every window (spec §8.1); drafts saved by older versions move to files.
    drafts = CommitDrafts(drafts_folder(), legacy=settings)
    windows = AppWindows(app, settings, theme, drafts, ColumnPrefs(settings),
                         SortPrefs(settings), prefs=prefs, recent=RecentRepos(settings),
                         saver=SaveDialog(FileSaver(settings)))  # every save (spec §8.3)

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
