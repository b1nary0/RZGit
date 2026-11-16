"""Commit Details: the commit selected in History, in full (spec §5.4).

The panel asks the session for the commit's details when ``selection.commit``
changes, and collapses while it is None (Uncommitted changes), like Commit
Files, which it sits above.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from rzgitclient.git.git_models import Commit, CommitDetails
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.app_theme import AppTheme
from rzgitclient.ui.theme.style_roles import Role, Surface, set_role, set_surface

NO_COMMIT = "No commit selected"
COPY = "Copy"
COPY_SHA = "Copy SHA"
_SELECTABLE = (Qt.TextInteractionFlag.TextSelectableByMouse
               | Qt.TextInteractionFlag.TextSelectableByKeyboard)
_SHORT = 8  # as Commit.short_sha


def format_date(date: datetime) -> str:
    """Local time, to the minute: ``2026-11-16 21:50``."""
    return date.astimezone().strftime("%Y-%m-%d %H:%M")


def full_date(date: datetime) -> str:
    """The time as git has it, with its offset: ``2026-11-16 21:50:03 +01:00``."""
    text = date.strftime("%Y-%m-%d %H:%M:%S %z")  # ... +0100
    return f"{text[:-2]}:{text[-2:]}"


def person(name: str, email: str, date: datetime) -> str:
    return f"{name} <{email}>\n{format_date(date)}"


class CommitDetailsPanel(Panel):
    panel_id = "commit_details"
    title = "Commit Details"
    surface = Surface.SIDEBAR

    # Built when a commit is first selected: the panel starts collapsed.
    _content: QScrollArea | None
    _placeholder: QLabel
    _summary: QLabel
    _body: QLabel
    _sha: QLabel
    _copy: QToolButton
    _parents_box: QHBoxLayout
    _parents: list[QLabel]
    _refs_name: QLabel
    _refs: QLabel
    _author: QLabel
    _committer_name: QLabel
    _committer: QLabel

    def _setup(self) -> None:
        self._content = None
        self._parents = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._placeholder = QLabel(NO_COMMIT)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_role(self._placeholder, Role.PLACEHOLDER)
        layout.addWidget(self._placeholder)
        self.set_collapsed(True)
        self.ctx.selection.commit_changed.connect(self._on_commit)
        self.ctx.session.commit_details_changed.connect(self._render)
        self.ctx.session.history_changed.connect(self._render)  # which parents are links
        self._on_commit(self.ctx.selection.commit)

    # ---- state ----------------------------------------------------------------

    def _on_commit(self, commit: Commit | None) -> None:
        self.ctx.session.request_commit_details(commit.sha if commit is not None else None)
        if commit is not None and self._content is None:
            self._build()
        self._render()
        self.set_collapsed(commit is None)

    def details(self) -> CommitDetails | None:
        """The selected commit's details, once they've arrived."""
        commit, session = self.ctx.selection.commit, self.ctx.session
        if commit is None or session.commit_details_sha != commit.sha:
            return None
        return session.commit_details

    def _loaded(self, sha: str) -> Commit | None:
        """The commit ``sha`` if History has it loaded."""
        return next((c for c in self.ctx.session.history if c.sha == sha), None)

    # ---- widgets ----------------------------------------------------------------

    def _build(self) -> None:
        inner = QWidget()
        set_surface(inner, Surface.SIDEBAR)
        column = QVBoxLayout(inner)
        column.setContentsMargins(8, 8, 8, 8)
        column.setSpacing(6)
        self._summary = self._label()
        self._body = self._label()
        column.addWidget(self._summary)
        column.addWidget(self._body)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        column.addLayout(grid)

        self._sha = self._label(wrap=False)
        self._copy = QToolButton()
        self._copy.setText(COPY)
        self._copy.setToolTip("Copy the full SHA")
        self._copy.clicked.connect(self._copy_sha)
        sha_row = QHBoxLayout()
        sha_row.setSpacing(6)
        sha_row.addWidget(self._sha, 1)
        sha_row.addWidget(self._copy)
        self._add_row(grid, "SHA", sha_row)

        self._parents_box = QHBoxLayout()
        self._parents_box.setSpacing(8)
        self._parents_box.addStretch(1)
        self._add_row(grid, "Parents", self._parents_box)
        self._refs = self._label()
        set_role(self._refs, Role.HINT)
        self._refs_name = self._add_row(grid, "Refs", self._refs)
        self._author = self._label()
        self._add_row(grid, "Author", self._author)
        self._committer = self._label()
        self._committer_name = self._add_row(grid, "Committer", self._committer)
        column.addStretch(1)

        self._content = QScrollArea()
        self._content.setFrameShape(QFrame.Shape.NoFrame)
        self._content.setWidgetResizable(True)
        self._content.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content.setWidget(inner)
        self.layout().addWidget(self._content)
        self.apply_theme(self.ctx.theme.current)

    def _label(self, wrap: bool = True) -> QLabel:
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(_SELECTABLE)
        label.setWordWrap(wrap)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        # Never wider than the panel: a long word (the SHA, an email) is cut off
        # instead of making the content scroll sideways.
        policy = label.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        label.setSizePolicy(policy)
        return label

    def _add_row(self, grid: QGridLayout, name: str, value: QWidget | QHBoxLayout) -> QLabel:
        row = grid.rowCount()
        label = QLabel(name)
        set_role(label, Role.HINT)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        grid.addWidget(label, row, 0)
        if isinstance(value, QHBoxLayout):
            grid.addLayout(value, row, 1)
        else:
            grid.addWidget(value, row, 1)
        return label

    # ---- rendering ----------------------------------------------------------------

    def _render(self, *_args: object) -> None:
        commit = self.ctx.selection.commit
        if self._content is None:
            return
        self._content.setVisible(commit is not None)
        self._placeholder.setVisible(commit is None)
        if commit is None:
            return
        details = self.details()
        if details is None:  # on its way: what History already knows
            self._summary.setText(commit.subject)
            self._body.setText("")
            self._body.hide()
            self._sha.setText(commit.sha)
            self._sha.setToolTip(commit.sha)
            self._set_parents(())
            self._set_row(self._refs_name, self._refs, "")
            self._author.setText(f"{commit.author}\n{format_date(commit.date)}")
            self._author.setToolTip(full_date(commit.date))
            self._set_row(self._committer_name, self._committer, "")
            return
        self._summary.setText(details.summary)
        self._body.setText(details.body)
        self._body.setVisible(bool(details.body))
        self._sha.setText(details.sha)
        self._sha.setToolTip(details.sha)
        self._set_parents(details.parents)
        self._set_row(self._refs_name, self._refs, ", ".join(details.refs))
        self._author.setText(person(details.author, details.author_email, details.author_date))
        self._author.setToolTip(full_date(details.author_date))
        same = ((details.committer, details.committer_email, details.committer_date)
                == (details.author, details.author_email, details.author_date))
        committer = "" if same else person(details.committer, details.committer_email,
                                           details.committer_date)
        self._set_row(self._committer_name, self._committer, committer)
        self._committer.setToolTip("" if same else full_date(details.committer_date))

    def _set_row(self, name: QLabel, value: QLabel, text: str) -> None:
        """A grid row that is left out when it has nothing to say."""
        value.setText(text)
        name.setVisible(bool(text))
        value.setVisible(bool(text))

    def _set_parents(self, parents: tuple[str, ...]) -> None:
        for label in self._parents:
            self._parents_box.removeWidget(label)
            label.deleteLater()
        self._parents = []
        for i, sha in enumerate(parents):
            label = QLabel()
            label.setFont(self.ctx.theme.current.mono_font())
            label.setProperty("sha", sha)
            label.setToolTip(sha)
            short = escape(sha[:_SHORT])
            if self._loaded(sha) is not None:
                label.setTextFormat(Qt.TextFormat.RichText)
                label.setText(f'<a href="{escape(sha)}">{short}</a>')
                label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse
                                              | _SELECTABLE)
                label.linkActivated.connect(self.select_parent)
            else:
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setText(sha[:_SHORT])
                label.setTextInteractionFlags(_SELECTABLE)
            label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            label.customContextMenuRequested.connect(
                lambda pos, lbl=label: self._parent_menu(lbl, pos).popup(lbl.mapToGlobal(pos)))
            self._parents_box.insertWidget(i, label)
            self._parents.append(label)

    # ---- actions ----------------------------------------------------------------

    def select_parent(self, sha: str) -> None:
        """Select the parent ``sha`` in History, if it's loaded there."""
        commit = self._loaded(sha)
        if commit is not None:
            self.ctx.selection.commit = commit

    def _copy_sha(self) -> None:
        QGuiApplication.clipboard().setText(self._sha.text())

    def _parent_menu(self, label: QLabel, _pos: QPoint) -> QMenu:
        menu = QMenu(label)
        sha = str(label.property("sha"))
        menu.addAction(COPY_SHA, lambda: QGuiApplication.clipboard().setText(sha))
        return menu

    def apply_theme(self, theme: AppTheme) -> None:
        if self._content is None:
            return
        bold = self.font()
        bold.setBold(True)
        self._summary.setFont(bold)
        mono = theme.mono_font()
        self._sha.setFont(mono)
        for label in self._parents:
            label.setFont(mono)
