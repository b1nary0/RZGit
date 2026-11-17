"""Commit Details: the commit selected in History, in full (spec §5.4).

The panel asks the session for the commit's details when ``selection.commit``
changes, and collapses while it is None (Uncommitted changes). It sits below
History and Diff, at the bottom of the main pane (RZG-65). Three segments: the
summary pinned at the top, the body scrolling below it, and the details pinned
under their own header: below the message when the panel is narrow, a column
on its right when it is wide.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QResizeEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from rzgitclient.git.git_models import Commit, CommitDetails, Stash
from rzgitclient.ui.panels.commit_message_text import reflow
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.app_theme import AppTheme
from rzgitclient.ui.theme.style_roles import Role, set_role
from rzgitclient.ui.widgets.panel_segment import PanelSegment, PanelSegments

NO_COMMIT = "No commit selected"
DETAILS = "Details"
PARENTS, BASED_ON = "Parents", "Based on"  # a stash's other parents are its own parts
COPY = "Copy"
COPY_SHA = "Copy SHA"
COPY_SELECTION = "Copy Selection"
COPY_MESSAGE = "Copy Message (Git Format)"  # not the reflowed text: git's own lines
SELECT_ALL = "Select All"
_SELECTABLE = (Qt.TextInteractionFlag.TextSelectableByMouse
               | Qt.TextInteractionFlag.TextSelectableByKeyboard)
_SHORT = 8  # as Commit.short_sha
WIDE = 700  # px: wider than this, Details is a column beside the message (RZG-65)
DETAILS_WIDTH = 320  # px: that column's width
MESSAGE_CHARS = 100  # the summary and body are at most this many average characters wide


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

    # Built when a commit is first selected: the panel starts collapsed.
    _content: QWidget | None  # holds the segments, in one stack or two
    _stacks: tuple[PanelSegments, ...]  # (all three) narrow; (message, details) wide
    _wide: bool
    _placeholder: QLabel
    _summary: QLabel
    _body: QLabel
    _summary_segment: PanelSegment
    _body_segment: PanelSegment
    _details_segment: PanelSegment
    _sha: QLabel
    _copy: QToolButton
    _parents_box: QHBoxLayout
    _parents_name: QLabel
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
        summary = PanelSegment(header="")
        self._summary = summary.header_label  # type: ignore[assignment]

        self._body = self._label()
        body = QWidget()
        body_row = QHBoxLayout(body)  # the label (capped width), then the space left over
        body_row.setContentsMargins(8, 6, 8, 6)
        body_row.addWidget(self._body, 1)
        body_row.addStretch(0)
        self._body_segment = PanelSegment(body, fill=True)

        details = QWidget()
        grid = QGridLayout(details)
        grid.setContentsMargins(8, 6, 8, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)

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
        self._parents_name = self._add_row(grid, PARENTS, self._parents_box)
        self._refs = self._label()
        set_role(self._refs, Role.HINT)
        self._refs_name = self._add_row(grid, "Refs", self._refs)
        self._author = self._label()
        self._add_row(grid, "Author", self._author)
        self._committer = self._label()
        self._committer_name = self._add_row(grid, "Committer", self._committer)

        for label in (self._summary, self._body):
            label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            label.customContextMenuRequested.connect(
                lambda pos, lbl=label: self.message_menu(lbl).popup(lbl.mapToGlobal(pos)))

        self._summary_segment = summary
        self._details_segment = PanelSegment(details, header=DETAILS)
        self._content = QWidget()
        box = QHBoxLayout(self._content)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        self._stacks = ()
        self._arrange(self._likely_width() > WIDE)
        self.layout().addWidget(self._content)
        self.apply_theme(self.ctx.theme.current)

    @property
    def segments(self) -> tuple[PanelSegment, ...]:
        """The summary, body and Details segments, wherever they are."""
        return self._summary_segment, self._body_segment, self._details_segment

    @property
    def wide(self) -> bool:
        """Details is a column beside the message (else below it)."""
        return self._wide

    def _arrange(self, wide: bool) -> None:
        """Stack the segments in one column (narrow), or put Details in a column of its
        own on the right (wide). The segments are kept; only their stacks are new."""
        if self._stacks and wide == self._wide:
            return
        self._wide = wide
        assert self._content is not None
        box = self._content.layout()
        for old in self._stacks:
            box.removeWidget(old)
        summary, body, details = self.segments
        if wide:
            message, side = PanelSegments((summary, body)), PanelSegments((details,))
            side.setFixedWidth(DETAILS_WIDTH)
            box.addWidget(message, 1)
            box.addWidget(side)
            new: tuple[PanelSegments, ...] = (message, side)
        else:
            new = (PanelSegments((summary, body, details)),)
            box.addWidget(new[0])
        for old in self._stacks:  # emptied: the segments are in the new stacks
            old.deleteLater()
        self._stacks = new

    def _likely_width(self) -> int:
        """The width it will be shown at. Built while collapsed, its own width is stale:
        the nearest shown container's (the main pane's split) is the better guess, so
        the segments are seldom arranged twice."""
        widget: QWidget | None = self
        while widget is not None and not widget.isVisible():
            widget = widget.parentWidget()
        return (widget or self).width()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._content is not None:
            self._arrange(event.size().width() > WIDE)

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
        stash = isinstance(commit, Stash)
        self._parents_name.setText(BASED_ON if stash else PARENTS)
        details = self.details()
        if details is None:  # on its way: what History already knows
            self._summary.setText(commit.subject)
            self._body.setText("")
            self._body_segment.hide()
            self._sha.setText(commit.sha)
            self._sha.setToolTip(commit.sha)
            self._set_parents(())
            self._set_row(self._refs_name, self._refs, "")
            self._author.setText(f"{commit.author}\n{format_date(commit.date)}")
            self._author.setToolTip(full_date(commit.date))
            self._set_row(self._committer_name, self._committer, "")
            return
        self._summary.setText(details.summary)
        self._body.setText(reflow(details.body))  # the label wraps it to the panel
        self._body_segment.setVisible(bool(details.body))
        self._sha.setText(details.sha)
        self._sha.setToolTip(details.sha)
        self._set_parents(details.parents[:1] if stash else details.parents)
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

    def message_menu(self, label: QLabel) -> QMenu:
        """The right-click menu of the summary or the body: the selection as shown,
        or the whole message with git's own line breaks (spec §5.4)."""
        menu = QMenu(label)
        copy = menu.addAction(COPY_SELECTION,
                              lambda: QGuiApplication.clipboard().setText(label.selectedText()))
        copy.setShortcut(QKeySequence(QKeySequence.StandardKey.Copy))
        copy.setEnabled(label.hasSelectedText())
        details = self.details()
        message = menu.addAction(COPY_MESSAGE)
        if details is not None:
            text = details.message
            message.triggered.connect(lambda: QGuiApplication.clipboard().setText(text))
        message.setEnabled(details is not None)
        menu.addSeparator()
        select = menu.addAction(SELECT_ALL, lambda: label.setSelection(0, len(label.text())))
        select.setShortcut(QKeySequence(QKeySequence.StandardKey.SelectAll))
        return menu

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
        for label in (self._summary, self._body):  # lines short enough to read
            label.setMaximumWidth(MESSAGE_CHARS * label.fontMetrics().averageCharWidth())
        mono = theme.mono_font()
        self._sha.setFont(mono)
        for label in self._parents:
            label.setFont(mono)
