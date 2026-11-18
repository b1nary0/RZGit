"""Push's box: push a branch with no upstream to a remote and track it (spec §8).

``actions.push`` asks for a push (``push_requested``). A branch with an upstream
to push to is pushed at once; for one without, PushDialogs asks "Push X to
remote and track it?" (with a list of the remotes when there is no default),
then calls ``session.push(remote)``. MainWindow makes it, to stay thin.
"""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QLabel, QMessageBox, QVBoxLayout, QWidget,
)

from rzgit.core.network_worker import pushes_to_upstream
from rzgit.core.repo_session import RepoSession
from rzgit.git.git_models import Branch
from rzgit.ui.app_actions import AppActions

PUSH = "Push"


def default_remote(remotes: tuple[str, ...], push_default: str | None) -> str | None:
    """Where a branch with no upstream is pushed (spec §8): the only remote; else
    ``remote.pushDefault`` if it is one of them, else ``origin``; else None (ask)."""
    if len(remotes) == 1:
        return remotes[0]
    if push_default in remotes:
        return push_default
    return "origin" if "origin" in remotes else None


class PushDialogs(QObject):
    def __init__(self, window: QWidget, session: RepoSession, actions: AppActions):
        super().__init__(window)
        self._window, self._session = window, session
        actions.push_requested.connect(self.push)

    def push(self) -> None:
        """Push the current branch: to its upstream, or, after the box, to a
        remote it then tracks."""
        session = self._session
        branch = next((b for b in session.branches if b.is_current), None)
        if branch is None or branch.name != session.head or not session.remotes:
            return
        if pushes_to_upstream(branch):
            session.push()
            return
        remote = self.ask_track(branch.name, default_remote(session.remotes, session.push_default),
                                session.remotes, _replaces(branch))
        if remote is not None:
            session.push(remote)

    # ---- the box (tests replace this) -----------------------------------

    def ask_track(self, branch: str, remote: str | None, remotes: tuple[str, ...],
                  info: str) -> str | None:
        """"Push *branch* to *remote* and track it?": the remote to push to, or
        None. With no ``remote``, the box lists ``remotes`` to choose from."""
        if remote is not None:
            box = QMessageBox(QMessageBox.Icon.Question, "Push",
                              f"Push {branch} to {remote} and track it?", parent=self._window)
            box.setInformativeText(info)
            push = box.addButton(PUSH, QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(push)
            box.exec()
            return remote if box.clickedButton() is push else None
        dialog = QDialog(self._window)
        dialog.setWindowTitle("Push")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"Push {branch} to which remote, and track it?"))
        combo = QComboBox()
        combo.addItems(list(remotes))
        layout.addWidget(combo)
        note = QLabel(info)
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(PUSH, QDialogButtonBox.ButtonRole.AcceptRole).setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return combo.currentText()


def _replaces(branch: Branch) -> str:
    """The box's note: what tracking does to the branch's upstream."""
    name = branch.name
    if branch.upstream and branch.remote == ".":
        return (f"{name} tracks the local branch {branch.upstream}; pushing makes the "
                f"remote's {name} its upstream instead.")
    if branch.upstream:
        return f"{branch.upstream} is gone; pushing makes the remote's {name} its upstream."
    return f"{name} has no upstream; pushing makes the remote's {name} its upstream."
