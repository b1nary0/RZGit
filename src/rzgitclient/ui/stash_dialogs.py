"""Stash Changes… and the Drop confirmation (spec §8).

The Stash panel asks ``actions.request_stash(op, stash)``; StashDialogs runs Apply
and Pop at once (they need a clean tree and roll back on a conflict) and asks
before Drop. MainWindow makes it, to stay thin.
"""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from rzgitclient.core.repo_session import RepoSession
from rzgitclient.git.git_models import Stash
from rzgitclient.ui.app_actions import AppActions, StashOp

DROP = "Drop"
UNTRACKED = "Include untracked files"


class StashDialogs(QObject):
    def __init__(self, window: QWidget, session: RepoSession, actions: AppActions):
        super().__init__(window)
        self._window, self._session = window, session
        actions.stash_requested.connect(self.handle)
        actions.stash_changes.triggered.connect(lambda _checked=False: self.stash_changes())

    def handle(self, op: StashOp, stash: Stash) -> None:
        if op is StashOp.DROP:
            if self.confirm_drop(stash):
                self._session.drop_stash(stash.sha)
        else:
            self._session.apply_stash(stash.sha, pop=op is StashOp.POP)

    def stash_changes(self) -> None:
        answer = self.ask_stash()
        if answer is not None:
            message, untracked = answer
            self._session.stash_changes(message, untracked)

    # ---- boxes (tests replace these) -----------------------------------

    def confirm_drop(self, stash: Stash) -> bool:
        box = QMessageBox(QMessageBox.Icon.Warning, "Drop Stash",
                          f"Drop stash “{stash.message}”?", parent=self._window)
        made = f", made on {stash.branch}" if stash.branch else ""
        box.setInformativeText(f"{stash.ref}{made}. Its changes are lost: this can't be "
                               "undone.")
        drop = box.addButton(DROP, QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is drop

    def ask_stash(self) -> tuple[str, bool] | None:
        """The message (stripped; may be empty) and whether to include untracked
        files; None if cancelled."""
        dialog = QDialog(self._window)
        dialog.setWindowTitle("Stash Changes")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Message (optional):"))
        edit = QLineEdit()
        layout.addWidget(edit)
        box = QCheckBox(UNTRACKED)
        box.setChecked(True)
        layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Stash")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(max(dialog.sizeHint().width(), 360), dialog.sizeHint().height())
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        answer = (edit.text().strip(), box.isChecked())
        dialog.deleteLater()
        return answer if accepted else None
