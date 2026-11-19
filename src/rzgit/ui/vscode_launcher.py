"""Open with VS Code: the command setting, and starting it (spec §8, §8.2, RZG-96).

Context menus make the entry with ``actions.open_in_vscode(paths, menu)``;
VSCodeLauncher sets the check that enables it, and starts the command when it is
triggered, in the window of this worktree (``VSCodeTarget``). MainWindow makes
it. It runs no git: the workspace files are the session's.
"""

from __future__ import annotations

import shlex
import shutil
from collections.abc import Callable

from PySide6.QtCore import QObject, QProcess
from PySide6.QtWidgets import QMessageBox, QWidget

from rzgit.core.preferences import Preferences, Setting, declare
from rzgit.core.repo_session import RepoSession
from rzgit.core.vscode_target import VSCodeTarget
from rzgit.ui.app_actions import AppActions

DEFAULT_COMMAND = "code"
NO_COMMAND = "No VS Code command found. Set it in Settings (Tools)."
NOT_PRESENT = "Not in the working tree."
NO_REPO = "No repository is open."

# The Tools page of the Settings window (spec §8.2).
VSCODE_COMMAND = declare(Setting(
    "tools/vscode", "", "VS Code command", page="Tools", special=DEFAULT_COMMAND,
    help="What Open with VS Code starts, e.g. \"codium\" or \"flatpak run "
         "com.visualstudio.code\". Empty: " + DEFAULT_COMMAND + "."))


def find_vscode(command: str = "",
                which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    """The command line to start: ``command`` (the setting; empty means ``code``),
    split as a shell would; None if its program isn't found."""
    try:
        argv = shlex.split(command or DEFAULT_COMMAND)
    except ValueError:  # an unbalanced quote
        return None
    return argv if argv and which(argv[0]) else None


class VSCodeLauncher(QObject):
    def __init__(self, window: QWidget, session: RepoSession, actions: AppActions,
                 prefs: Preferences | None = None):
        """``prefs``: the VS Code command setting's (§8.2)."""
        super().__init__(window)
        self._window, self._session, self._prefs = window, session, prefs
        actions.set_vscode_check(self.problem)
        actions.open_in_vscode_requested.connect(self.open)

    def command(self) -> list[str] | None:
        setting = self._prefs.value(VSCODE_COMMAND) if self._prefs is not None else ""
        return find_vscode(str(setting))

    def target(self) -> VSCodeTarget | None:
        repo = self._session.repo
        return VSCodeTarget(repo.root, self._session.workspace_files) if repo else None

    def problem(self, paths: tuple[str, ...]) -> str | None:
        """Why ``paths`` can't be opened, or None if one of them can."""
        target = self.target()
        if target is None:
            return NO_REPO
        if self.command() is None:
            return NO_COMMAND
        return None if target.present(paths) else NOT_PRESENT

    def open(self, paths: tuple[str, ...], line: int | None = None) -> None:
        """Open those of ``paths`` that are in the working tree, in one call; with
        ``line`` (the Diff panel's, with its one file), at that line."""
        target, argv = self.target(), self.command()
        if target is None or argv is None:
            return
        present = target.present(paths)
        if not present:
            return
        if not self.start_detached(target.argv(argv, present, line), str(target.root)):
            self.warn("Open with VS Code", f"Couldn't start {argv[0]}.",
                      "Check the VS Code command in Settings (Tools).")

    def start_detached(self, argv: list[str], folder: str) -> bool:
        started = QProcess.startDetached(argv[0], argv[1:], folder)
        return started[0] if isinstance(started, tuple) else bool(started)

    def warn(self, title: str, text: str, info: str) -> None:
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=self._window)
        box.setInformativeText(info)
        box.exec()
