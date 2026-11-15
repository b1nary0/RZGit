from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from rzgitclient import __version__
from rzgitclient.ui.main_window import MainWindow
from rzgitclient.ui.theme import apply_theme


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("RZGitClient")
    app.setOrganizationName("RZGitClient")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("rzgitclient")
    apply_theme(app)

    window = MainWindow()
    window.show()

    # Open a repo from the command line, else the last one used.
    args = [a for a in argv[1:] if not a.startswith("-")]
    target = args[0] if args else window.settings.value("last_repo", "", str)
    if target:
        window.open_repo(target)

    return app.exec()
