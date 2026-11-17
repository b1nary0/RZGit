"""Write a commit: summary, description and the Commit button (spec §5.4).

The panel sits below Files and collapses while nothing is staged, unless one
of its fields has focus. The message is the open repository's draft
(``ctx.drafts``).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from rzgitclient.core.git_commands import CommandKind, GitCommand
from rzgitclient.core.repo_session import Refresh
from rzgitclient.git.git_models import LastCommit
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.style_roles import Role, set_role

SUBJECT_GUIDE = 50  # shown as "n/50"
SUBJECT_WARNING = 72  # past this the counter turns to the warning colour
SUMMARY_PLACEHOLDER = "Commit summary"
DESCRIPTION_PLACEHOLDER = "Description"
STAGE_FIRST = "Stage Files to Commit"
TYPE_MESSAGE = "Type a Message to Commit"
COMMITTING = "Committing…"
AMENDING = "Amending…"
LOADING_MESSAGE = "Loading the Last Message…"
AMEND_ALONE = "Amend Last Commit"  # nothing staged: the message only


def join_message(summary: str, description: str) -> str:
    """What git gets: the summary, and the description after a blank line."""
    return f"{summary}\n\n{description}" if description.strip() else summary


def split_message(message: str) -> tuple[str, str]:
    """``join_message`` reversed: (the first line, the rest without the blank line)."""
    summary, _, rest = message.partition("\n")
    return summary, rest.removeprefix("\n")


class CommitPanel(Panel):
    panel_id = "commit"
    title = "Commit"

    # Built when the panel is first shown: it starts collapsed (no repo yet),
    # and many sessions never stage anything.
    _summary: QLineEdit | None
    _count: QLabel
    _description: QPlainTextEdit
    _button: QPushButton
    _amend_box: QCheckBox

    def _setup(self) -> None:
        self._root: str | None = None  # the repository whose draft is in the fields
        self._focused: set[int] = set()  # ids of the fields that have focus
        self._pending: GitCommand | None = None  # our commit, still running
        self._pending_text = ""
        self._loading = False  # filling the fields: not an edit to save
        self._summary = None
        self._amend_sha: str | None = None  # amending: the commit Amend was turned on for
        self._amend_message: str | None = None  # its message, once read

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)

        ctx, action = self.ctx, self.ctx.actions.commit
        action.changed.connect(self._update_button)
        action.triggered.connect(self._commit)
        ctx.actions.amend.toggled.connect(self._on_amend)
        ctx.session.last_commit_changed.connect(self._on_last_commit)
        ctx.session.repo_changed.connect(self._on_repo)
        ctx.session.status_changed.connect(self._on_status)
        ctx.session.command_finished.connect(self._on_finished)
        ctx.selection.commit_changed.connect(self._update_collapsed)
        self.destroyed.connect(ctx.actions.clear_commit_ready)
        self._on_repo()

    def _build(self) -> None:
        summary = self._summary = QLineEdit()
        summary.setPlaceholderText(SUMMARY_PLACEHOLDER)
        # The counter sits inside the field, on the right.
        self._count = QLabel(summary)
        set_role(self._count, Role.HINT)
        self._count.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        inside = QHBoxLayout(summary)
        inside.setContentsMargins(0, 0, 6, 0)
        inside.addStretch(1)
        inside.addWidget(self._count)
        summary.setTextMargins(0, 0, summary.fontMetrics().horizontalAdvance("000/50") + 8, 0)

        self._description = QPlainTextEdit()
        self._description.setPlaceholderText(DESCRIPTION_PLACEHOLDER)
        self._description.setTabChangesFocus(True)
        self._button = QPushButton()
        self._button.setDefault(True)  # the accent colour: the panel's one action
        self._button.setToolTip("Commit the staged changes (Ctrl+Enter)")
        # Shows the Amend action, checked both ways (spec §5.4).
        amend = self.ctx.actions.amend
        self._amend_box = QCheckBox("Amend")
        self._amend_box.setToolTip("Replace the last commit with the staged changes and "
                                   "this message")
        self._amend_box.setChecked(amend.isChecked())
        self._amend_box.setEnabled(amend.isEnabled())
        self._amend_box.toggled.connect(amend.setChecked)
        amend.toggled.connect(self._amend_box.setChecked)
        amend.changed.connect(lambda: self._amend_box.setEnabled(amend.isEnabled()))

        layout = self.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.addWidget(summary)
        layout.addWidget(self._description, 1)
        row = QHBoxLayout()
        row.addWidget(self._amend_box)
        row.addWidget(self._button, 1)
        layout.addLayout(row)

        for field in (summary, self._description):
            field.installEventFilter(self)
        summary.returnPressed.connect(self._description.setFocus)
        summary.textChanged.connect(self._on_text)
        self._description.textChanged.connect(self._on_text)
        self._button.clicked.connect(self.ctx.actions.commit.trigger)
        if self._amending():
            self._fill(self._amend_message or "")
        else:
            self._load_draft()

    # ---- state -----------------------------------------------------------

    def _on_repo(self, *_args: object) -> None:
        repo = self.ctx.session.repo
        self._pending = None  # a commit for the old repo reports nothing
        self._root = str(repo.root) if repo is not None else None
        if self._summary is not None:
            self._load_draft()
        self._on_status()

    def _on_status(self, *_args: object) -> None:
        self._update_collapsed()
        self._update_ready()

    def _draft(self) -> str:
        return self.ctx.drafts.get(self._root) if self._root else ""

    def _load_draft(self) -> None:
        """Put the open repository's draft in the fields."""
        self._fill(self._draft())

    def _fill(self, message: str) -> None:
        assert self._summary is not None
        summary, description = split_message(message)
        self._loading = True
        try:
            for field in (self._summary, self._description):
                field.setEnabled(self._root is not None)
            self._summary.setText(summary)
            self._description.setPlainText(description)
        finally:
            self._loading = False
        self._on_text()

    def _message(self) -> str:
        """The message: from the fields, or the draft while there are none."""
        if self._summary is None:
            return self._draft()
        return join_message(self._summary.text(), self._description.toPlainText())

    def _on_text(self) -> None:
        if self._loading:
            return
        message = self._message()
        if self._root is not None and not self._amending():  # the draft waits meanwhile
            self.ctx.drafts.set(self._root, message)
        subject = len(split_message(message)[0])
        self._count.setText(f"{subject}/{SUBJECT_GUIDE}" if subject else "")
        set_role(self._count, Role.HINT_WARNING if subject > SUBJECT_WARNING else Role.HINT)
        self._update_ready()

    def _update_ready(self) -> None:
        ready = (bool(split_message(self._message())[0].strip()) and self._pending is None
                 and (not self._amending() or self._amend_message is not None))
        self.ctx.actions.set_commit_ready(ready)
        self._update_button()

    def _update_button(self) -> None:
        if self._summary is None:
            return
        staged = sum(f.is_staged for f in self.ctx.session.status)
        if self._pending is not None:
            text = AMENDING if self._pending.kind is CommandKind.AMEND else COMMITTING
        elif self._amending():
            if self._amend_message is None:
                text = LOADING_MESSAGE
            elif not self._summary.text().strip():
                text = TYPE_MESSAGE
            elif staged:
                text = f"Amend with {staged} File{'s' if staged != 1 else ''}"
            else:
                text = AMEND_ALONE
        elif not staged:
            text = STAGE_FIRST
        elif not self._summary.text().strip():
            text = TYPE_MESSAGE
        else:
            text = f"Commit {staged} File{'s' if staged != 1 else ''}"
        self._button.setText(text)
        self._button.setEnabled(self.ctx.actions.commit.isEnabled())

    def _update_collapsed(self, *_args: object) -> None:
        staged = any(f.is_staged for f in self.ctx.session.status)
        # Kept while typing: unstaging everything mid-sentence doesn't snatch it away.
        # Commit Files takes its place while a commit is selected (§5.4); the draft stays.
        # Amending keeps it too: the message alone can be changed (spec §5.4).
        collapsed = ((not self._focused and not staged and not self._amending())
                     or self.ctx.selection.commit is not None)
        if not collapsed and self._summary is None:
            self._build()
        self.set_collapsed(collapsed)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind in (QEvent.Type.FocusIn, QEvent.Type.FocusOut) and watched in (
                self._summary, self._description):
            if kind == QEvent.Type.FocusIn:
                self._focused.add(id(watched))
                self._update_collapsed()
            else:
                self._focused.discard(id(watched))
                # Later: moving to the other field brings its FocusIn first.
                QTimer.singleShot(0, self, self._update_collapsed)
        return super().eventFilter(watched, event)

    # ---- amending (spec §5.4) ---------------------------------------------

    def _amending(self) -> bool:
        return self._amend_sha is not None

    def _on_amend(self, checked: bool) -> None:
        session = self.ctx.session
        self._amend_message = None
        self._amend_sha = None
        if checked and session.history:
            self._amend_sha = session.history[0].sha
            last = session.last_commit
            if last is not None and last.sha == self._amend_sha:
                self._amend_message = last.message
            session.refresh(Refresh.LAST_COMMIT)  # again: whether it's pushed may have changed
        self._update_collapsed()  # builds the fields, the first time
        if self._summary is not None:
            if self._amending():
                self._fill(self._amend_message or "")
                self._summary.setFocus()
            else:
                self._load_draft()
        self._update_ready()

    def _on_last_commit(self, last: LastCommit | None) -> None:
        if (not self._amending() or last is None or last.sha != self._amend_sha
                or self._amend_message is not None):
            return
        self._amend_message = last.message
        if self._summary is not None:
            self._fill(last.message)
        self._update_ready()

    # ---- committing ------------------------------------------------------

    def _commit(self) -> None:
        message = self._message()
        if not split_message(message)[0].strip() or self._pending is not None:
            return
        if self._amending():
            last = self.ctx.session.last_commit
            if (self._amend_message is None or last is None or last.sha != self._amend_sha
                    or not self.ctx.actions.confirm_amend(last)):
                return
            self._pending = self.ctx.session.amend(message)
        else:
            self._pending = self.ctx.session.commit(message)
        self._pending_text = message
        self._update_ready()

    def _on_finished(self, command: GitCommand, ok: bool) -> None:
        if command is not self._pending:
            return
        self._pending = None
        # Kept on failure (the error is shown), and if the user went on typing. An
        # amend that worked has turned Amend off already, and the draft is back.
        if (ok and command.kind is CommandKind.COMMIT and self._summary is not None
                and self._message() == self._pending_text):
            self._summary.clear()
            self._description.clear()
        self._update_ready()
