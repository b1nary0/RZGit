"""The confirmations and name boxes of branch operations (spec §8).

The Branches panel asks ``actions.request_branch(op, name)``; BranchDialogs asks
the user, then calls the session. MainWindow makes it, to stay thin.
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

from rzgitclient.core.git_commands import stash_message
from rzgitclient.core.repo_session import RepoSession
from rzgitclient.ui.app_actions import AppActions, BranchOp

_Role = QMessageBox.ButtonRole
STASH, BRING = "Stash and Switch", "Bring Changes"


class BranchDialogs(QObject):
    def __init__(self, window: QWidget, session: RepoSession, actions: AppActions):
        super().__init__(window)
        self._window, self._session = window, session
        actions.branch_requested.connect(self.handle)

    def handle(self, op: BranchOp, name: str) -> None:
        {
            BranchOp.SWITCH: self.switch,
            BranchOp.NEW: self.new_branch,
            BranchOp.RENAME: self.rename,
            BranchOp.DELETE: self.delete,
            BranchOp.MERGE: self.merge,
            BranchOp.REBASE: self.rebase,
        }[op](name)

    # ---- operations ----------------------------------------------------

    def switch(self, name: str) -> None:
        if name == self._session.head:
            return
        changes = len(self._session.status)
        if not changes:
            if self.ask("Switch Branch", f"Switch to {name}?", "",
                        [("Switch", _Role.AcceptRole)], default="Switch") == "Switch":
                self._session.switch_branch(name)
            return
        files = "1 changed file" if changes == 1 else f"{changes:,} changed files"
        choice = self.ask(
            "Switch Branch", f"Switch to {name}?",
            f"You have uncommitted changes ({files}, counting new ones).\n\n"
            f"{STASH} puts all of them in a stash named “{stash_message(name)}”; "
            f"get them back later with git stash pop. {BRING} takes them to {name}, "
            "if git can do that without losing any.",
            [(STASH, _Role.AcceptRole), (BRING, _Role.AcceptRole)], default=STASH)
        if choice in (STASH, BRING):
            self._session.switch_branch(name, stash=choice == STASH)

    def new_branch(self, start: str) -> None:
        answer = self.ask_name("New Branch", f"New branch from {start}:", "",
                               check="Switch to it")
        if answer is not None:
            new, switch = answer
            self._session.create_branch(new, start, switch=switch)

    def rename(self, name: str) -> None:
        answer = self.ask_name("Rename Branch", f"New name for {name}:", name)
        if answer is not None and answer[0] != name:
            self._session.rename_branch(name, answer[0])

    def delete(self, name: str) -> None:
        if self.ask("Delete Branch", f"Delete branch {name}?", "",
                    [("Delete", _Role.DestructiveRole)], warn=True) != "Delete":
            return
        branch = next((b for b in self._session.branches if b.name == name), None)
        if branch is not None and branch.merged:
            self._session.delete_branch(name)
            return
        if self.ask("Delete Branch", f"{name} isn't merged into {self._current()}.",
                    f"Its commits that aren't in {self._current()} will be lost. "
                    "Delete it anyway?",
                    [("Delete Anyway", _Role.DestructiveRole)], warn=True) == "Delete Anyway":
            self._session.delete_branch(name, force=True)

    def merge(self, name: str) -> None:
        current = self._current()
        if self.ask("Merge", f"Merge {name} into {current}?",
                    "If there are conflicts, the merge is aborted and nothing changes.",
                    [("Merge", _Role.AcceptRole)], default="Merge") == "Merge":
            self._session.merge(name)

    def rebase(self, onto: str) -> None:
        current = self._current()
        if self.ask("Rebase", f"Rebase {current} onto {onto}?",
                    f"This rewrites the commits of {current} that aren't in {onto}, so "
                    "their hashes change. If there are conflicts, the rebase is aborted "
                    "and nothing changes.",
                    [("Rebase", _Role.DestructiveRole)], warn=True) == "Rebase":
            self._session.rebase(onto)

    # ---- boxes (tests replace these) -----------------------------------

    def ask(self, title: str, text: str, info: str, buttons: list[tuple[str, _Role]],
            *, default: str | None = None, warn: bool = False) -> str | None:
        """A box with ``buttons`` and Cancel; the label clicked, or None.
        Cancel is the default unless ``default`` names a button."""
        icon = QMessageBox.Icon.Warning if warn else QMessageBox.Icon.Question
        box = QMessageBox(icon, title, text, parent=self._window)
        if info:
            box.setInformativeText(info)
        made = {box.addButton(label, role): label for label, role in buttons}
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(next((b for b, label in made.items() if label == default), cancel))
        box.exec()
        return made.get(box.clickedButton())

    def ask_name(self, title: str, label: str, text: str,
                 check: str | None = None) -> tuple[str, bool] | None:
        """A branch name (stripped, not empty) and, with ``check``, a checked
        box's state; None if cancelled."""
        dialog = QDialog(self._window)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(label))
        edit = QLineEdit(text)
        edit.selectAll()
        layout.addWidget(edit)
        box = QCheckBox(check) if check else None
        if box is not None:
            box.setChecked(True)
            layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        edit.textChanged.connect(lambda t: ok.setEnabled(bool(t.strip())))
        ok.setEnabled(bool(text.strip()))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(max(dialog.sizeHint().width(), 360), dialog.sizeHint().height())
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        name = edit.text().strip()
        dialog.deleteLater()
        if not accepted or not name:
            return None
        return name, box.isChecked() if box is not None else False

    def _current(self) -> str:
        return self._session.head or "HEAD"
