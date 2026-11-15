"""Shared test helpers (spec §10)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

# Must be set before Qt picks a platform plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if TYPE_CHECKING:
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from rzgitclient.ui.panels.panel import PanelContext


def qt_app() -> QApplication:
    """The one QApplication shared by every test in the run.

    Qt is imported here, not at module level, so git-only tests never load it.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ---- throwaway repositories ------------------------------------------------


def git(cwd: str | Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return proc.stdout


def make_repo(path: str | Path) -> Path:
    """Initialise an empty repo on ``main`` at ``path`` with a local identity."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test User")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "commit.gpgsign", "false")
    return path


def commit_file(repo: str | Path, name: str, content: str, message: str) -> None:
    (Path(repo) / name).write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "-m", message)


# ---- signals ---------------------------------------------------------------


class SignalRecorder:
    """Records emissions of several signals of one object, in order.

    ``events`` is a list of ``(signal_name, value)``.
    """

    def __init__(self, obj: QObject, *names: str):
        self.events: list[tuple[str, object]] = []
        for name in names:
            getattr(obj, name).connect(
                lambda *args, name=name: self.events.append(
                    (name, args[0] if args else None)
                )
            )

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def values(self, name: str) -> list[object]:
        return [value for n, value in self.events if n == name]

    def clear(self) -> None:
        self.events.clear()


# ---- panels ----------------------------------------------------------------


def make_panel_context() -> PanelContext:
    """A PanelContext with a fresh session (no repo open) and the light theme."""
    from rzgitclient.core.repo_session import RepoSession
    from rzgitclient.core.selection import Selection
    from rzgitclient.ui.panels.panel import PanelContext
    from rzgitclient.ui.theme.app_theme import LIGHT_THEME
    from rzgitclient.ui.theme.theme_manager import ThemeManager

    session = RepoSession()
    return PanelContext(
        session=session,
        selection=Selection(session),
        theme=ThemeManager(qt_app(), LIGHT_THEME),
    )
