"""New Worktree…, the Remove and Prune confirmations, Open Folder and Open
Terminal (spec §8).

The Worktrees and Branches panels ask ``actions.request_worktree(op, target)``;
WorktreeDialogs asks the user, then calls the session, and refreshes the
window's worktree reader when a worktree command finishes. MainWindow makes it,
to stay thin. Opening a folder or a terminal runs no git.
"""

from __future__ import annotations

import os
import shlex
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QUrl
from PySide6.QtGui import QDesktopServices, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from rzgit.core.git_commands import WORKTREE_COMMANDS, CommandKind, GitCommand
from rzgit.core.preferences import Preferences, Setting, declare
from rzgit.core.repo_session import RepoSession
from rzgit.core.worktree_reader import WorktreeReader
from rzgit.git.git_models import Branch, Worktree
from rzgit.ui.app_actions import AppActions, WorktreeOp
from rzgit.ui.theme.style_roles import Role, set_role

_Role = QMessageBox.ButtonRole
REMOVE, REMOVE_ANYWAY, PRUNE = "Remove", "Remove Anyway", "Prune"
CREATE = "Create"
TERMINALS = ("konsole", "gnome-terminal", "xfce4-terminal", "x-terminal-emulator")
NOT_EMPTY = "That folder isn't empty."
NOT_ABSOLUTE = "Give the folder's full path."
_LISTED = 10  # worktrees named in the prune confirmation

# The Tools page of the Settings window (spec §8.2).
TERMINAL_COMMAND = declare(Setting(
    "tools/terminal", "", "Terminal command", page="Tools", special="Automatic",
    help="What Open in Terminal starts, e.g. \"kitty --single-instance\". Empty: $TERMINAL, "
         "else the first of " + ", ".join(TERMINALS) + "."))


def find_terminal(env: Mapping[str, str] | None = None,
                  which: Callable[[str], str | None] = shutil.which,
                  command: str = "") -> list[str] | None:
    """The command line of a terminal to start: ``command`` (the setting), else
    ``$TERMINAL``, each split as a shell would and used if its program is found;
    else the first of TERMINALS on the PATH."""
    env = os.environ if env is None else env
    for line in (command, env.get("TERMINAL", "")):
        try:
            argv = shlex.split(line)
        except ValueError:  # an unbalanced quote
            argv = []
        if argv and which(argv[0]):
            return argv
    for name in TERMINALS:
        if found := which(name):
            return [found]
    return None


def default_folder(main_root: str | Path, branch: str) -> str:
    """``<main repository folder>-<branch>`` beside the main repository; a ``/``
    in the branch becomes ``-``. Empty without a branch."""
    if not branch:
        return ""
    root = Path(main_root)
    return str(root.parent / f"{root.name}-{branch.replace('/', '-')}")


def folder_problem(path: str) -> str | None:
    """Why ``path`` can't take a new worktree, or None: it must be absolute and
    not exist, or be an empty folder. An empty text is a problem with no words."""
    if not path.strip():
        return ""
    folder = Path(path).expanduser()
    if not folder.is_absolute():
        return NOT_ABSOLUTE
    if folder.is_dir():
        try:
            empty = next(folder.iterdir(), None) is None
        except OSError:
            empty = False
        return None if empty else NOT_EMPTY
    return NOT_EMPTY if folder.exists() else None  # a file is in the way


@dataclass(frozen=True)
class NewWorktree:
    """What the New Worktree dialog asked for."""

    path: str
    branch: str  # checked out, or the new branch's name
    start: str | None  # a new branch from here; None: the existing ``branch``
    open: bool


class NewWorktreeDialog(QDialog):
    """A branch (an existing one, or a new one from a start point), a folder,
    and Open it (spec §8)."""

    def __init__(self, parent: QWidget | None, branches: Sequence[Branch],
                 used: Mapping[str, str], main_root: str | Path, head: str | None,
                 start: str = ""):
        """``used``: branch → the name of the worktree it is checked out in.
        ``start``: New Worktree from *X*: X itself if it is free, else a new branch from X."""
        super().__init__(parent)
        self.setWindowTitle("New Worktree")
        self._main_root = main_root
        self._folder_auto = True  # the folder follows the branch until it is edited

        self.existing_radio = QRadioButton("&Existing branch:")
        self.existing_combo = QComboBox()
        model = QStandardItemModel(self.existing_combo)
        self.existing_combo.setModel(model)
        for branch in branches:
            where = used.get(branch.name)
            self.existing_combo.addItem(
                branch.name + (f" (in worktree {where})" if where else ""), branch.name)
            if where:
                model.item(self.existing_combo.count() - 1).setEnabled(False)
        free = [b.name for b in branches if b.name not in used]

        self.new_radio = QRadioButton("&New branch:")
        self.name_edit = QLineEdit()
        self.start_combo = QComboBox()
        self.start_combo.setEditable(True)
        self.start_combo.addItems([b.name for b in branches])
        self.start_combo.setCurrentText(start or head or "HEAD")

        self.folder_edit = QLineEdit()
        browse = QPushButton("&Browse…")
        browse.clicked.connect(self._browse)
        self.problem_label = QLabel()
        set_role(self.problem_label, Role.HINT_WARNING)
        self.open_check = QCheckBox("&Open it")
        self.open_check.setChecked(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create_button = self.buttons.addButton(CREATE, QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow(self.existing_radio, self.existing_combo)
        form.addRow(self.new_radio, self.name_edit)
        start_label = QLabel("&From:")
        start_label.setBuddy(self.start_combo)
        form.addRow(start_label, self.start_combo)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        folder_label = QLabel("Fol&der:")
        folder_label.setBuddy(self.folder_edit)
        form.addRow(folder_label, folder_row)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.problem_label)
        layout.addWidget(self.open_check)
        layout.addWidget(self.buttons)

        # Where it starts: X if free, else a new branch from X; with no X, the first free one.
        self.existing_radio.setEnabled(bool(free))
        pick = start if start in free else (None if start else next(iter(free), None))
        shown = pick or next(iter(free), None)  # never a branch that can't be picked
        if shown is not None:
            self.existing_combo.setCurrentIndex(self.existing_combo.findData(shown))
        if pick is not None:
            self.existing_radio.setChecked(True)
        else:
            self.new_radio.setChecked(True)

        for signal in (self.existing_radio.toggled, self.existing_combo.currentIndexChanged,
                       self.name_edit.textChanged):
            signal.connect(self._branch_changed)
        self.start_combo.currentTextChanged.connect(self._update)
        self.folder_edit.textChanged.connect(self._update)
        self.folder_edit.textEdited.connect(self._folder_edited)
        self._branch_changed()
        self.resize(max(self.sizeHint().width(), 480), self.sizeHint().height())

    def branch(self) -> str:
        """The branch chosen: an existing one, or the new one's name."""
        if self.existing_radio.isChecked():
            index = self.existing_combo.currentIndex()
            model = self.existing_combo.model()
            if index < 0 or not model.item(index).isEnabled():
                return ""
            return self.existing_combo.itemData(index)
        return self.name_edit.text().strip()

    def result_value(self) -> NewWorktree | None:
        """What was asked for, if it can be created; else None."""
        branch, path = self.branch(), self.folder_edit.text().strip()
        new = self.new_radio.isChecked()
        start = self.start_combo.currentText().strip() if new else None
        if not branch or (new and not start) or folder_problem(path) is not None:
            return None
        return NewWorktree(str(Path(path).expanduser()), branch, start,
                           self.open_check.isChecked())

    def _branch_changed(self, *_args: object) -> None:
        new = self.new_radio.isChecked()
        self.existing_combo.setEnabled(not new)
        self.name_edit.setEnabled(new)
        self.start_combo.setEnabled(new)
        if new:
            self.name_edit.setFocus()
        if self._folder_auto:
            self.folder_edit.setText(default_folder(self._main_root, self.branch()))
        self._update()

    def _folder_edited(self, _text: str) -> None:
        self._folder_auto = False

    def _browse(self) -> None:
        start = self.folder_edit.text().strip() or str(Path(self._main_root).parent)
        path = QFileDialog.getExistingDirectory(self, "Worktree Folder", start)
        if path:
            self._folder_auto = False
            self.folder_edit.setText(path)

    def _update(self, *_args: object) -> None:
        problem = folder_problem(self.folder_edit.text())
        self.problem_label.setText(problem or "")
        self.problem_label.setVisible(bool(problem))
        self.create_button.setEnabled(self.result_value() is not None)


class WorktreeDialogs(QObject):
    def __init__(self, window: QWidget, session: RepoSession, actions: AppActions,
                 reader: WorktreeReader | None, *,
                 open_in_window: Callable[[str], object],
                 window_for: Callable[[str], QWidget | None],
                 prefs: Preferences | None = None):
        """``open_in_window``: show a worktree in a window of its own (§8.1).
        ``window_for``: the window showing a worktree, if any. ``prefs``: the
        terminal command setting's (§8.2)."""
        super().__init__(window)
        self._prefs = prefs
        self._window, self._session, self._reader = window, session, reader
        self._open_in_window, self._window_for = open_in_window, window_for
        self._open_after: dict[GitCommand, str] = {}  # add commands, and what to open
        actions.worktree_requested.connect(self.handle)
        session.command_finished.connect(self._on_command_finished)

    def handle(self, op: WorktreeOp, target: str) -> None:
        {
            WorktreeOp.NEW: self.new_worktree,
            WorktreeOp.REMOVE: self.remove,
            WorktreeOp.PRUNE: lambda _target: self.prune(),
            WorktreeOp.OPEN_FOLDER: self.open_folder,
            WorktreeOp.OPEN_TERMINAL: self.open_terminal,
        }[op](target)

    @property
    def worktrees(self) -> tuple[Worktree, ...]:
        return self._reader.worktrees if self._reader is not None else ()

    def _worktree(self, path: str) -> Worktree | None:
        return next((w for w in self.worktrees if w.path == path), None)

    def _own_path(self) -> str | None:
        repo = self._session.repo
        return str(repo.root) if repo is not None else None

    # ---- operations ----------------------------------------------------

    def new_worktree(self, start: str = "") -> None:
        repo = self._session.repo
        if repo is None:
            return
        used = {w.branch: w.name for w in self.worktrees if w.branch is not None}
        answer = self.ask_new(self._session.branches, used, repo.main_root,
                              self._session.head, start)
        if answer is None:
            return
        command = self._session.add_worktree(answer.path, answer.branch, answer.start)
        if command is not None and answer.open:
            self._open_after[command] = answer.path

    def remove(self, path: str) -> None:
        worktree = self._worktree(path)
        if worktree is None or worktree.main or worktree.locked or path == self._own_path():
            return  # the menu refuses these too (spec §5.4)
        if worktree.missing:
            info = (f"Its folder {path} is already gone: only git's record of it is "
                    "removed.")
        else:
            info = f"Its folder {path} is deleted, with any ignored files in it."
        if worktree.branch is not None:
            info += f" The branch {worktree.branch} is kept."
        if self.ask("Remove Worktree", f"Remove worktree {worktree.name}?", info,
                    [(REMOVE, _Role.DestructiveRole)], warn=True) != REMOVE:
            return
        state = self._reader.state(path) if self._reader is not None else None
        force = False
        if state is not None and state.changed and not worktree.missing:
            files = ("1 changed file" if state.changed == 1
                     else f"{state.changed:,} changed files")
            if self.ask("Remove Worktree", f"{worktree.name} has {files}.",
                        "Its uncommitted changes, new files included, will be lost. "
                        "Remove it anyway?",
                        [(REMOVE_ANYWAY, _Role.DestructiveRole)], warn=True) != REMOVE_ANYWAY:
                return
            force = True
        if (other := self._window_for(path)) is not None:  # never this one: refused above
            other.close()  # it saves and shuts down first (§8.1)
        self._session.remove_worktree(path, force=force)

    def prune(self) -> None:
        missing = [w for w in self.worktrees if w.missing]
        gone = [w for w in missing if not w.locked]
        if not gone:
            return
        kept = [w for w in missing if w.locked]
        count = "1 missing worktree" if len(gone) == 1 else f"{len(gone):,} missing worktrees"
        info = _listed(gone)
        info += ("\n\nTheir folders are gone: this removes git's records of them. "
                 "Their branches are kept.")
        if kept:
            info += f"\n\nLocked, so kept:\n{_listed(kept)}"
        if self.ask("Prune Worktrees", f"Prune {count}?", info,
                    [(PRUNE, _Role.DestructiveRole)], warn=True) == PRUNE:
            self._session.prune_worktrees()

    def open_folder(self, path: str) -> None:
        if not self.open_url(QUrl.fromLocalFile(path)):
            self.warn("Open Folder", f"Couldn't open {path}.",
                      "No file manager is set up to open folders.")

    def open_terminal(self, path: str) -> None:
        argv = self.terminal()
        if argv is None:
            self.warn("Open Terminal", "No terminal found.",
                      "Set the terminal command in Settings (Tools), or $TERMINAL, "
                      "or install one of " + ", ".join(TERMINALS) + ".")
            return
        if not self.start_detached(argv, path):
            self.warn("Open Terminal", f"Couldn't start {argv[0]} in {path}.", "")

    def _on_command_finished(self, command: GitCommand, ok: bool) -> None:
        if command.kind not in WORKTREE_COMMANDS:
            return
        if self._reader is not None:
            self._reader.refresh()  # even without inotify (spec §4.4)
        path = self._open_after.pop(command, None)
        if ok and path is not None and command.kind is CommandKind.WORKTREE_ADD:
            self._open_in_window(path)

    # ---- the outside world (tests replace these) -------------------------

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

    def ask_new(self, branches: Sequence[Branch], used: Mapping[str, str],
                main_root: str | Path, head: str | None, start: str) -> NewWorktree | None:
        dialog = NewWorktreeDialog(self._window, branches, used, main_root, head, start)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        answer = dialog.result_value()
        dialog.deleteLater()
        return answer if accepted else None

    def warn(self, title: str, text: str, info: str) -> None:
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=self._window)
        if info:
            box.setInformativeText(info)
        box.exec()

    def open_url(self, url: QUrl) -> bool:
        return QDesktopServices.openUrl(url)

    def terminal(self) -> list[str] | None:
        command = self._prefs.value(TERMINAL_COMMAND) if self._prefs is not None else ""
        return find_terminal(command=command)

    def start_detached(self, argv: list[str], folder: str) -> bool:
        started = QProcess.startDetached(argv[0], argv[1:], folder)
        return started[0] if isinstance(started, tuple) else bool(started)


def _listed(worktrees: Sequence[Worktree]) -> str:
    shown = "\n".join(f"{w.name}  ({w.path})" for w in worktrees[:_LISTED])
    if len(worktrees) > _LISTED:
        shown += f"\n… and {len(worktrees) - _LISTED:,} more"
    return shown
