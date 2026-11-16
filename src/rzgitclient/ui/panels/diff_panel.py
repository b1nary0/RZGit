"""The diff of the selected file, unified or side by side (spec §5.4)."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollBar,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.git.git_models import FileDiff
from rzgitclient.ui.panels.diff_rows import split_rows, unified_rows
from rzgitclient.ui.panels.diff_view import DiffView, Numbers
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.app_theme import AppTheme
from rzgitclient.ui.theme.style_roles import Role, set_role

LOADING_DELAY_MS = 150  # a diff that takes longer shows "Loading…"

NOTHING_SELECTED = "Select a file to see its diff."
NO_CHANGES = "No changes to show."
BINARY = "Binary file: there's no text diff to show."
UNREADABLE = "Couldn't read this diff."
LOADING = "Loading…"
_SOURCES = {FileSource.WORKTREE: "Unstaged", FileSource.INDEX: "Staged"}


class DiffPanel(Panel):
    panel_id = "diff"
    title = "Diff"

    _path: QLabel
    _stack: QStackedWidget
    _message: QLabel
    _show_anyway: QPushButton
    _message_page: QWidget
    # The views are built when first needed: with no file selected (every
    # start) they'd only cost time. The side-by-side pair is _split's.
    _unified: DiffView | None
    _split: QSplitter | None
    _left: DiffView | None
    _right: DiffView | None
    _loading: QTimer

    def _setup(self) -> None:
        self._file: FileSelection | None = None  # selected: shown, or on its way
        self._shown: FileSelection | None = None  # whose diff is on screen
        self._diff: FileDiff | None = None
        self._unified = self._split = self._left = self._right = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(self._build_bar())
        self._stack = QStackedWidget()
        self._message_page = self._build_message()
        self._stack.addWidget(self._message_page)
        layout.addWidget(self._stack, 1)

        self._loading = QTimer(self)
        self._loading.setSingleShot(True)
        self._loading.setInterval(LOADING_DELAY_MS)
        self._loading.timeout.connect(lambda: self._show_message(LOADING))

        ctx = self.ctx
        ctx.selection.file_changed.connect(self._on_file)
        ctx.session.diff_changed.connect(self._on_diff)
        ctx.actions.side_by_side_diff.toggled.connect(lambda _on: self._render())

        self._file = file = ctx.selection.file
        if file is None:
            self._show_message(NOTHING_SELECTED)
        elif ctx.session.diff_file == file:
            self._show(file, ctx.session.diff)
        else:
            ctx.session.request_diff(file)
            self._show_message("")
            self._loading.start()

    def _build_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 2, 4, 2)
        self._path = QLabel()
        self._path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bar.addWidget(self._path, 1)
        toggle = QToolButton()
        toggle.setDefaultAction(self.ctx.actions.side_by_side_diff)
        toggle.setAutoRaise(True)
        toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        toggle.setIconSize(QSize(16, 16))
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonFollowStyle)
        bar.addWidget(toggle)
        return bar

    def _build_message(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.addStretch(1)
        self._message = QLabel()
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        set_role(self._message, Role.PLACEHOLDER)
        box.addWidget(self._message)
        self._show_anyway = QPushButton("Show Anyway")
        self._show_anyway.clicked.connect(self._load_full)
        box.addWidget(self._show_anyway, 0, Qt.AlignmentFlag.AlignHCenter)
        box.addStretch(1)
        return page

    def _unified_view(self) -> DiffView:
        if self._unified is None:
            self._unified = self._add_view(DiffView(Numbers.BOTH))
        return self._unified

    def _split_views(self) -> tuple[DiffView, DiffView]:
        if self._left is None or self._right is None:
            split = QSplitter(Qt.Orientation.Horizontal)
            split.setChildrenCollapsible(False)
            left, right = DiffView(Numbers.OLD), DiffView(Numbers.NEW)
            # The rows line up, so the sides scroll together; the right one's bar serves both.
            left.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            for side in (left, right):
                split.addWidget(side)
                side.apply_theme(self.ctx.theme.current)
            for get in (DiffView.verticalScrollBar, DiffView.horizontalScrollBar):
                _sync(get(left), get(right))
            split.setStretchFactor(0, 1)
            split.setStretchFactor(1, 1)
            self._stack.addWidget(split)
            self._split, self._left, self._right = split, left, right
        return self._left, self._right

    def _add_view(self, view: DiffView) -> DiffView:
        view.apply_theme(self.ctx.theme.current)
        self._stack.addWidget(view)
        return view

    # ---- behaviour -------------------------------------------------------

    def apply_theme(self, theme: AppTheme) -> None:
        for view in (self._unified, self._left, self._right):
            if view is not None:
                view.apply_theme(theme)

    def _on_file(self, file: FileSelection | None) -> None:
        old, self._file = self._file, file
        session = self.ctx.session
        session.request_diff(file)
        if file is None:
            self._loading.stop()
            self._shown = None
            self._path.setText("")
            self._show_message(NOTHING_SELECTED)
            return
        if old is None or old.path != file.path:
            self.request_activation()  # the user picked another file
        if session.diff_file == file:  # already read: an equal result won't be sent again
            self._show(file, session.diff)
        else:
            self._loading.start()

    def _on_diff(self, file: FileSelection | None, diff: FileDiff | None) -> None:
        if file is not None and file == self._file:
            self._show(file, diff)

    def _load_full(self) -> None:
        if self._file is not None:
            self.ctx.session.request_diff(self._file, full=True)
            self._loading.start()

    def _show(self, file: FileSelection, diff: FileDiff | None) -> None:
        self._loading.stop()
        keep_scroll = file == self._shown
        self._shown, self._diff = file, diff
        self._path.setText(_describe(file, diff))
        if diff is None:
            self._show_message(UNREADABLE)
        elif diff.binary:
            self._show_message(BINARY)
        elif diff.too_large:
            self._show_message(f"Large diff ({diff.line_count:,} lines).", show_anyway=True)
        elif not diff.hunks:
            self._show_message(NO_CHANGES)
        else:
            self._render(keep_scroll)

    def _render(self, keep_scroll: bool = False) -> None:
        """Show ``_diff``'s rows in the chosen view (a new view starts at the top)."""
        diff = self._diff
        if diff is None or not diff.hunks or self._shown != self._file:  # another is on its way
            return
        if self.ctx.actions.side_by_side_diff.isChecked():
            left_rows, right_rows = split_rows(diff)
            left, right = self._split_views()
            left.set_rows(left_rows, keep_scroll)
            right.set_rows(right_rows, keep_scroll)
            self._stack.setCurrentWidget(self._split)
        else:
            view = self._unified_view()
            view.set_rows(unified_rows(diff), keep_scroll)
            self._stack.setCurrentWidget(view)

    def _show_message(self, text: str, show_anyway: bool = False) -> None:
        self._message.setText(text)
        self._show_anyway.setVisible(show_anyway)
        self._stack.setCurrentWidget(self._message_page)


def _describe(file: FileSelection, diff: FileDiff | None) -> str:
    path = file.path
    if diff is not None and diff.old_path and diff.old_path != path:
        path = f"{diff.old_path} → {path}"
    source = _SOURCES.get(file.source) or (file.commit or "")[:8]
    return f"{path}  ·  {source}"


def _sync(a: QScrollBar, b: QScrollBar) -> None:
    """Keep two scroll bars at the same value (setValue doesn't emit when equal)."""
    a.valueChanged.connect(b.setValue)
    b.valueChanged.connect(a.setValue)
