"""Shared test helpers (spec §10)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

# Must be set before Qt picks a platform plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if TYPE_CHECKING:
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from rzgitclient.core.repo_session import RepoSession
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


def make_branched_repo(path: str | Path) -> Path:
    """A repo whose date order and topological order differ (RZG-58): ``a``; then
    ``s1`` on a side branch, ``m1`` on main, ``s2`` on the side branch, each a
    minute later; then ``merge`` of the side branch into main."""
    repo = make_repo(path)
    minute = iter(range(1, 10))

    def commit(name: str, message: str) -> None:
        date = f"2026-01-01T00:0{next(minute)}:00Z"
        (repo / name).write_text(message)
        env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        subprocess.run(["git", "add", name], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True,
                       capture_output=True, env=env)

    commit("a.txt", "a")
    git(repo, "switch", "-q", "-c", "side")
    commit("s.txt", "s1")
    git(repo, "switch", "-q", "main")
    commit("m.txt", "m1")
    git(repo, "switch", "-q", "side")
    commit("s.txt", "s2")
    git(repo, "switch", "-q", "main")
    date = "2026-01-01T00:09:00Z"
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(["git", "merge", "-q", "--no-ff", "-m", "merge", "side"], cwd=repo,
                   check=True, capture_output=True, env=env)
    return repo


def wait_idle(session: RepoSession, timeout: float = 10.0) -> None:
    """Process events until the session's git worker has nothing left to do."""
    from PySide6.QtCore import QCoreApplication, QThread

    deadline = time.monotonic() + timeout
    while session.busy:
        if time.monotonic() > deadline:
            raise AssertionError(f"session still busy after {timeout}s")
        QCoreApplication.processEvents()
        QThread.msleep(1)


def pump(ms: int) -> None:
    """Process events for ``ms`` milliseconds."""
    from PySide6.QtCore import QCoreApplication, QThread

    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        QThread.msleep(1)


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
    """A PanelContext with a fresh session (no repo open), the light theme and
    commit drafts and column choices kept in memory."""
    from rzgitclient.core.column_prefs import ColumnPrefs
    from rzgitclient.core.commit_drafts import CommitDrafts
    from rzgitclient.core.repo_session import RepoSession
    from rzgitclient.core.selection import Selection
    from rzgitclient.ui.app_actions import AppActions
    from rzgitclient.ui.panels.panel import PanelContext
    from rzgitclient.ui.theme.app_theme import LIGHT_THEME
    from rzgitclient.ui.theme.theme_manager import ThemeManager

    session = RepoSession()
    selection = Selection(session)
    return PanelContext(
        session=session,
        selection=selection,
        theme=ThemeManager(qt_app(), LIGHT_THEME),
        actions=AppActions(session, selection),
        drafts=CommitDrafts(),
        columns=ColumnPrefs(),
    )


def drag_column_edge(header, logical: int, by: int) -> None:
    """Drag the right edge of ``header``'s column ``logical`` by ``by`` pixels, as
    the user would (press, move, release on its viewport)."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    viewport = header.viewport()
    x = header.sectionViewportPosition(logical) + header.sectionSize(logical) - 1
    left, none = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
    for kind, at, button, buttons in ((QEvent.Type.MouseButtonPress, x, left, left),
                                      (QEvent.Type.MouseMove, x + by, none, left),
                                      (QEvent.Type.MouseButtonRelease, x + by, left, none)):
        pos = QPointF(at, viewport.height() / 2)
        QApplication.sendEvent(viewport, QMouseEvent(kind, pos, viewport.mapToGlobal(pos), button,
                                                     buttons, Qt.KeyboardModifier.NoModifier))
