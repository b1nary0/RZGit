"""Pull's boxes: uncommitted changes first, then a diverged branch (spec §8).

``actions.pull`` asks for a pull (``pull_requested``); PullDialogs asks about the
changes, then calls ``session.pull``. If the branch has diverged from its
upstream, the session says so (``pull_diverged``) and PullDialogs asks how to
integrate. MainWindow makes it, to stay thin.
"""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox, QWidget

from rzgitclient.core.git_commands import GitCommand, PullDiverged, PullMode, pull_stash_message
from rzgitclient.core.repo_session import RepoSession
from rzgitclient.ui.app_actions import AppActions

_Role = QMessageBox.ButtonRole
STASH, WITH_CHANGES = "Stash and Pull", "Pull with Changes"
REBASE, MERGE = "Rebase", "Merge"


class PullDialogs(QObject):
    def __init__(self, window: QWidget, session: RepoSession, actions: AppActions):
        super().__init__(window)
        self._window, self._session = window, session
        actions.pull_requested.connect(self.pull)
        session.pull_diverged.connect(self.diverged)

    def pull(self) -> None:
        """Pull into the current branch, asking first if there are changes."""
        session = self._session
        changes = len(session.status)
        if not changes or session.head is None:
            session.pull()
            return
        branch = session.head
        files = "1 changed file" if changes == 1 else f"{changes:,} changed files"
        choice = self.ask(
            "Pull", f"Pull into {branch} with uncommitted changes?",
            f"There {'is' if changes == 1 else 'are'} {files}. {STASH} puts them in a stash "
            f"named “{pull_stash_message(branch)}” while pulling, then puts them back. "
            f"{WITH_CHANGES} leaves them where they are, if git can do that without losing "
            "any.",
            [(STASH, _Role.AcceptRole), (WITH_CHANGES, _Role.AcceptRole)], default=STASH)
        if choice in (STASH, WITH_CHANGES):
            session.pull(stash=choice == STASH)

    def diverged(self, command: GitCommand, diverged: PullDiverged) -> None:
        """The branch and its upstream have diverged: Rebase, Merge or Cancel."""
        session = self._session
        branch = command.branch
        if branch != session.head:
            return  # switched meanwhile: nothing to integrate into
        current = next((b for b in session.branches if b.name == branch), None)
        upstream = current.upstream if current is not None and current.upstream else "its upstream"
        choice = self.ask(
            "Pull", f"{branch} and {upstream} have diverged.",
            f"{branch} has {_commits(diverged.ahead)} that {upstream} doesn't, and {upstream} "
            f"has {_commits(diverged.behind)} that {branch} doesn't. {REBASE} replays the "
            f"commits of {branch} on top of {upstream}, so their hashes change; {MERGE} "
            "joins them with a merge commit. If there are conflicts, it is aborted and "
            "nothing changes.",
            [(REBASE, _Role.AcceptRole), (MERGE, _Role.AcceptRole)],
            default=REBASE if diverged.rebase else MERGE)
        if choice in (REBASE, MERGE):
            session.pull_integrate(PullMode.REBASE if choice == REBASE else PullMode.MERGE,
                                   stash=command.stash)

    # ---- boxes (tests replace this) ------------------------------------

    def ask(self, title: str, text: str, info: str, buttons: list[tuple[str, _Role]],
            *, default: str) -> str | None:
        """A box with ``buttons`` and Cancel, ``default`` the default; the label
        clicked, or None."""
        box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=self._window)
        box.setInformativeText(info)
        made = {box.addButton(label, role): label for label, role in buttons}
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(next(b for b, label in made.items() if label == default))
        box.exec()
        return made.get(box.clickedButton())


def _commits(n: int) -> str:
    return "1 commit" if n == 1 else f"{n:,} commits"
