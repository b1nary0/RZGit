"""The diff of the selected file, unified or side by side, its changes or the
whole file (spec §5.4)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollBar,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from rzgitclient.core.diff_patch import Line, can_patch, hunk_lines
from rzgitclient.core.selection import FileSelection, FileSource
from rzgitclient.git.git_models import FileDiff, LineKind
from rzgitclient.ui.panels.diff_rows import (
    DiffRow,
    FoldKey,
    Stretch,
    fold,
    split_partners,
    split_rows,
    stretches,
    unified_partners,
    unified_rows,
    whole_file,
    whole_rows,
    whole_split_rows,
)
from rzgitclient.ui.panels.diff_view import DiffView, Numbers, Place
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
    _hint: QLabel  # why a side-by-side diff shows unified
    _stack: QStackedWidget
    _message: QLabel
    _show_anyway: QPushButton
    _changes_only: QPushButton  # a whole file too large: back to the changes
    _headers: QToolButton  # the whole-file view's hunk headers
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
        self._whole = False  # the shown diff is of the whole file
        self._folded: set[FoldKey] = set()  # the whole file's folded stretches
        self._stretches: list[Stretch] = []  # the shown rows' foldable stretches
        self._place: list[Place] | None = None  # to go back to once the toggled diff is in
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
        ctx.actions.whole_file_diff.toggled.connect(self._on_whole_toggled)
        for action in ctx.actions.hunk_headers.values():
            action.toggled.connect(lambda on: on and self._rerender())

        self._file = file = ctx.selection.file
        if file is None:
            self._show_message(NOTHING_SELECTED)
        elif self._cached(file):
            self._show(file, ctx.session.diff)
        else:
            self._request(file)
            self._show_message("")
            self._loading.start()

    def _build_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 2, 4, 2)
        self._path = QLabel()
        self._path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bar.addWidget(self._path, 1)
        self._hint = QLabel()
        set_role(self._hint, Role.HINT)
        self._hint.hide()
        bar.addWidget(self._hint)
        actions = self.ctx.actions
        self._headers = _tool_button()
        self._headers.setIcon(QIcon.fromTheme("view-list-text"))
        self._headers.setText("Hunk Headers")
        self._headers.setToolTip("Hunk headers in the whole file")
        menu = QMenu(self._headers)
        menu.addActions(list(actions.hunk_headers.values()))
        self._headers.setMenu(menu)
        self._headers.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._headers.setVisible(actions.whole_file_diff.isChecked())
        bar.addWidget(self._headers)
        for action in (actions.whole_file_diff, actions.side_by_side_diff):
            toggle = _tool_button()
            toggle.setDefaultAction(action)
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
        self._changes_only = QPushButton("Show Changes Only")
        self._changes_only.clicked.connect(
            lambda: self.ctx.actions.whole_file_diff.setChecked(False))
        self._changes_only.hide()
        box.addWidget(self._changes_only, 0, Qt.AlignmentFlag.AlignHCenter)
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
                self._connect(side)
            for get in (DiffView.verticalScrollBar, DiffView.horizontalScrollBar):
                _sync(get(left), get(right))
            split.setStretchFactor(0, 1)
            split.setStretchFactor(1, 1)
            self._stack.addWidget(split)
            self._split, self._left, self._right = split, left, right
        return self._left, self._right

    def _add_view(self, view: DiffView) -> DiffView:
        view.apply_theme(self.ctx.theme.current)
        self._connect(view)
        self._stack.addWidget(view)
        return view

    def _connect(self, view: DiffView) -> None:
        view.hunk_clicked.connect(self._on_hunk_link)
        view.lines_clicked.connect(self._apply)
        view.fold_clicked.connect(self._toggle_fold)
        view.customContextMenuRequested.connect(
            lambda pos, view=view: self.menu_for(view, pos).exec(
                view.viewport().mapToGlobal(pos)))

    # ---- behaviour -------------------------------------------------------

    def apply_theme(self, theme: AppTheme) -> None:
        for view in (self._unified, self._left, self._right):
            if view is not None:
                view.apply_theme(theme)

    def _on_file(self, file: FileSelection | None) -> None:
        old, self._file = self._file, file
        session = self.ctx.session
        self._place = None
        if file is None:
            session.request_diff(None)
            self._loading.stop()
            self._shown = None
            self._path.setText("")
            self._show_message(NOTHING_SELECTED)
            return
        self._request(file)
        if old is None or old.path != file.path:
            self.request_activation()  # the user picked another file
        if self._cached(file):  # already read: an equal result won't be sent again
            self._show(file, session.diff)
        else:
            self._loading.start()

    def _on_diff(self, file: FileSelection | None, diff: FileDiff | None) -> None:
        if file is not None and file == self._file and self._cached(file):
            self._show(file, diff)

    def _cached(self, file: FileSelection) -> bool:
        """The session's latest diff is ``file``'s, read the way the panel wants it."""
        session = self.ctx.session
        return (session.diff_file == file
                and session.diff_whole == self.ctx.actions.whole_file_diff.isChecked())

    def _request(self, file: FileSelection, full: bool = False) -> None:
        whole = self.ctx.actions.whole_file_diff.isChecked()
        self.ctx.session.request_diff(file, full=full, whole=whole)

    def _load_full(self) -> None:
        if self._file is not None:
            self._request(self._file, full=True)
            self._loading.start()

    def _on_whole_toggled(self, on: bool) -> None:
        self._headers.setVisible(on)
        if self._file is None:
            return
        # Find the same lines in the other diff once it's in.
        shown = self._shown_views() if self._shown == self._file else []
        self._place = [view.place() for view in shown] or None
        self._request(self._file)
        if self._cached(self._file):
            self._show(self._file, self.ctx.session.diff)
        else:
            self._loading.start()

    def _show(self, file: FileSelection, diff: FileDiff | None) -> None:
        self._loading.stop()
        keep_scroll = file == self._shown
        whole = self.ctx.session.diff_whole
        if (file, diff, whole) != (self._shown, self._diff, self._whole):
            self._folded.clear()  # a new diff: its stretches are new
        self._shown, self._diff, self._whole = file, diff, whole
        self._path.setText(_describe(file, diff))
        if diff is None:
            self._show_message(UNREADABLE)
        elif diff.binary:
            self._show_message(BINARY)
        elif diff.too_large:
            self._show_message(f"Large diff ({diff.line_count:,} lines).", show_anyway=True,
                               changes_only=whole)
        elif not diff.hunks:
            self._show_message(NO_CHANGES)
        else:
            self._render(keep_scroll)
            place, self._place = self._place, None
            if place is not None:
                views = self._shown_views()
                if len(views) == len(place):
                    for view, at in zip(views, place):
                        view.go_to(at)

    def _shown_views(self) -> list[DiffView]:
        """The view or views on screen now (side by side, left then right)."""
        current = self._stack.currentWidget()
        if current is self._split and self._left is not None and self._right is not None:
            return [self._left, self._right]
        return [current] if isinstance(current, DiffView) else []

    def _rerender(self) -> None:
        """Show the same diff with other rows (headers, folds), staying where it was."""
        views = self._shown_views()
        place = [view.place() for view in views]
        self._render(keep_scroll=True)
        if self._shown_views() == views:
            for view, at in zip(views, place):
                view.go_to(at, show_selection=False)

    def _render(self, keep_scroll: bool = False) -> None:
        """Show ``_diff``'s rows in the chosen view (a new view starts at the top)."""
        diff = self._diff
        if diff is None or not diff.hunks or self._shown != self._file:  # another is on its way
            return
        verb, why = self._patching()
        link = f"{verb} Hunk" if verb and not why else ""
        side_by_side = self.ctx.actions.side_by_side_diff.isChecked()
        added = whole_file(diff) if side_by_side else None
        self._hint.setText(f"Unified: whole file {added}" if added else "")
        self._hint.setVisible(bool(added))
        headers = self.ctx.actions.hunk_headers_mode()
        sides: tuple[tuple[DiffRow, ...], ...]
        if side_by_side and not added:
            sides = whole_split_rows(diff, headers) if self._whole else split_rows(diff)
        else:
            sides = (whole_rows(diff, headers) if self._whole else unified_rows(diff),)
        self._stretches = stretches(sides[0]) if self._whole else []
        folded = [fold(rows, self._stretches, self._folded) for rows in sides]
        marks = folded[0][1]
        if len(folded) == 2:
            (left_rows, _), (right_rows, _) = folded
            left_partners, right_partners = split_partners(left_rows, right_rows)
            left, right = self._split_views()
            for view, rows, partners in ((left, left_rows, left_partners),
                                         (right, right_rows, right_partners)):
                view.set_line_buttons(verb if link else "")
                view.set_rows(rows, keep_scroll, partners=partners, fold_marks=marks)
                view.set_hunk_link(link, why or "")
            self._stack.setCurrentWidget(self._split)
        else:
            view = self._unified_view()
            view.set_line_buttons(verb if link else "")
            rows = folded[0][0]
            view.set_rows(rows, keep_scroll, partners=unified_partners(rows), fold_marks=marks)
            view.set_hunk_link(link, why or "")
            self._stack.setCurrentWidget(view)

    # ---- folding the whole file (spec §5.4) ----------------------------------

    def _toggle_fold(self, key: FoldKey) -> None:
        self._folded ^= {key}
        self._rerender()

    def fold_all(self) -> None:
        self._folded = {s.key for s in self._stretches}
        self._rerender()

    def unfold_all(self) -> None:
        self._folded = set()
        self._rerender()

    # ---- hunks and lines (spec §5.4) ---------------------------------------

    def _patching(self) -> tuple[str | None, str | None]:
        """("Stage" or "Unstage", or None for a commit's diff), and why the
        shown diff's hunks and lines can't be, or None if they can."""
        file, diff = self._shown, self._diff
        if file is None or diff is None or file.source is FileSource.COMMIT:
            return None, None
        verb = "Stage" if file.source is FileSource.WORKTREE else "Unstage"
        return verb, can_patch(diff)

    def _can_discard(self) -> bool:
        """Lines of the shown diff can be discarded: a tracked file's unstaged diff."""
        file = self._shown
        if file is None or file.source is not FileSource.WORKTREE:
            return False
        return not any(f.path == file.path and f.is_untracked for f in self.ctx.session.status)

    def _apply(self, lines: frozenset[Line]) -> None:
        diff, file = self._diff, self._shown
        if diff is None or file is None or not lines:
            return
        if file.source is FileSource.WORKTREE:
            self.ctx.session.stage_lines(diff, lines)
        else:
            self.ctx.session.unstage_lines(diff, lines)

    def _discard(self, lines: frozenset[Line]) -> None:
        if self._diff is not None:
            self.ctx.actions.request_discard_lines(self._diff, lines)

    def _on_hunk_link(self, hunk: int) -> None:
        if self._diff is not None:
            self._apply(hunk_lines(self._diff, hunk))

    def menu_for(self, view: DiffView, pos: QPoint) -> QMenu:
        """The right-click menu at ``pos`` (viewport coordinates) of ``view``."""
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        verb, why = self._patching()
        diff = self._diff
        if verb is not None and diff is not None:
            row = view.row_at(pos)
            hunk = row.hunk if row is not None else None
            hunk_set = hunk_lines(diff, hunk) if hunk is not None else frozenset()
            chosen = view.selected_lines()
            if not chosen and row is not None and row.line is not None and row.hunk is not None:
                if diff.hunks[row.hunk].lines[row.line].kind is not LineKind.CONTEXT:
                    chosen = frozenset({(row.hunk, row.line)})
            entries = [(f"{verb} Hunk", hunk_set, self._apply),
                       (f"{verb} Selected Lines", chosen, self._apply)]
            if self._can_discard():
                entries += [("Discard Hunk…", hunk_set, self._discard),
                            ("Discard Selected Lines…", chosen, self._discard)]
            for text, lines, run in entries:
                action = menu.addAction(text)
                action.setEnabled(bool(lines) and why is None)
                if why:
                    action.setToolTip(why)
                action.triggered.connect(lambda _=False, run=run, lines=lines: run(lines))
            if why:
                note = menu.addAction(why)
                note.setEnabled(False)
            menu.addSeparator()
        if self._whole:
            fold_all = menu.addAction("Fold All Unchanged")
            fold_all.setEnabled(any(s.key not in self._folded for s in self._stretches))
            fold_all.triggered.connect(self.fold_all)
            unfold_all = menu.addAction("Unfold All")
            unfold_all.setEnabled(bool(self._folded))
            unfold_all.triggered.connect(self.unfold_all)
            menu.addSeparator()
        copy = menu.addAction("Copy")
        copy.setEnabled(bool(view.selection) or view.text_range() is not None)
        copy.triggered.connect(view.copy)
        return menu

    def _show_message(self, text: str, show_anyway: bool = False,
                      changes_only: bool = False) -> None:
        self._message.setText(text)
        self._show_anyway.setVisible(show_anyway)
        self._changes_only.setVisible(changes_only)
        self._hint.hide()
        self._stack.setCurrentWidget(self._message_page)


def _describe(file: FileSelection, diff: FileDiff | None) -> str:
    path = file.path
    if diff is not None and diff.old_path and diff.old_path != path:
        path = f"{diff.old_path} → {path}"
    source = _SOURCES.get(file.source) or (file.commit or "")[:8]
    return f"{path}  ·  {source}"


def _tool_button() -> QToolButton:
    button = QToolButton()
    button.setAutoRaise(True)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setIconSize(QSize(16, 16))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonFollowStyle)
    # Without an icon theme the button shows its text: let it be cut short then,
    # so the bar never makes the panel wider than its icons would.
    button.setMinimumWidth(button.sizeHint().height())
    button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return button


def _sync(a: QScrollBar, b: QScrollBar) -> None:
    """Keep two scroll bars at the same value (setValue doesn't emit when equal)."""
    a.valueChanged.connect(b.setValue)
    b.valueChanged.connect(a.setValue)
