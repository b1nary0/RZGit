"""The diff of the selected file, unified or side by side, its changes or the
whole file (spec §5.4)."""

from __future__ import annotations

from pathlib import PurePosixPath

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon, QKeySequence, QShortcut
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

from rzgit.core.diff_patch import Line, all_lines, can_patch, copy_patch, hunk_lines
from rzgit.core.diff_request import DIFF_MAX_LINES, GIT_CONTEXT
from rzgit.core.preferences import Setting, declare
from rzgit.core.selection import FileSelection, FileSource
from rzgit.git.git_models import FileDiff, LineKind
from rzgit.ui.panels.diff_rows import (
    FOLD_KEEP,
    FOLD_MIN_LINES,
    DiffRow,
    FoldKey,
    Stretch,
    TextPos,
    fold,
    new_line_at,
    symbol_line_at,
    split_partners,
    split_rows,
    stretches,
    unified_partners,
    unified_rows,
    whole_file,
    whole_rows,
    whole_split_rows,
)
from rzgit.ui.panels.diff_view import TAB_WIDTH, DiffView, Numbers, Place
from rzgit.ui.panels.find_bar import FindBar
from rzgit.ui.panels.find_query import MAX_MATCHES, FindMatch, find_matches, word_around
from rzgit.ui.panels.panel import Panel
from rzgit.ui.theme.app_theme import AppTheme
from rzgit.ui.theme.style_roles import Role, set_role

LOADING_DELAY_MS = 150  # a diff that takes longer shows "Loading…"

NOTHING_SELECTED = "Select a file to see its diff."
NO_CHANGES = "No changes to show."
BINARY = "Binary file: there's no text diff to show."
UNREADABLE = "Couldn't read this diff."
LOADING = "Loading…"
COPY_PATCH_KEYS = "Ctrl+Shift+C"  # Copy Selected Lines as Patch
_SOURCES = {FileSource.WORKTREE: "Unstaged", FileSource.INDEX: "Staged"}

# The Diff page of the Settings window (spec §8.2).
FOLD_MIN = declare(Setting(
    "diff/fold_min_lines", FOLD_MIN_LINES, "Fold unchanged stretches with more lines than",
    page="Diff", minimum=4, maximum=10_000,
    help="In the whole file, a longer run of unchanged lines can be folded."))
FOLD_KEPT = declare(Setting(
    "diff/fold_keep_lines", FOLD_KEEP, "Lines kept next to a change", page="Diff",
    minimum=0, maximum=100,
    help="The unchanged lines still shown on each side of a change when a stretch is folded."))
FOLD_ON_OPEN = declare(Setting(
    "diff/fold_on_open", False, "Fold unchanged stretches when a whole file opens",
    page="Diff"))
TAB_SPACES = declare(Setting(
    "diff/tab_width", TAB_WIDTH, "Tab width", page="Diff", minimum=1, maximum=16,
    help="How many spaces wide a tab is."))
LARGE_DIFF = declare(Setting(
    "diff/max_lines", DIFF_MAX_LINES, "Ask before showing a diff with more lines than",
    page="Diff", minimum=10, maximum=10_000_000,
    help='A longer diff shows "Large diff" with Show Anyway.'))
CONTEXT_LINES = declare(Setting(
    "diff/context_lines", GIT_CONTEXT, "Context lines above and below a change", page="Diff",
    minimum=GIT_CONTEXT, maximum=1000,
    help="The unchanged lines shown around each hunk's changes, unless the whole file is shown."))
_FOLD_KEYS = (FOLD_MIN.key, FOLD_KEPT.key, FOLD_ON_OPEN.key)


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
    find_bar: FindBar

    def _setup(self) -> None:
        self._file: FileSelection | None = None  # selected: shown, or on its way
        self._shown: FileSelection | None = None  # whose diff is on screen
        self._diff: FileDiff | None = None
        self._whole = False  # the shown diff is of the whole file
        self._folded: set[FoldKey] = set()  # the whole file's folded stretches
        self._stretches: list[Stretch] = []  # the shown rows' foldable stretches
        self._fold_new = True  # the next render sets the folds as a diff opens
        self._place: list[Place] | None = None  # to go back to once the toggled diff is in
        self._by_hunk = False  # ... by its top hunk (a new context count), not its top line
        self._unified = self._split = self._left = self._right = None
        # Find (RZG-74): the matches in the shown views, as (side, match), in order.
        self._matches: list[tuple[int, FindMatch]] = []
        self._match_index: dict[tuple[int, int, int, int], int] = {}  # a shown match's place
        self._current: int | None = None  # the match that is the text selection
        self._invalid = False  # the query doesn't compile
        self._more = False  # there are more than MAX_MATCHES matches
        self._find_jump = False  # Keep is on and another file was picked: go to its first match

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(self._build_bar())
        self._stack = QStackedWidget()
        self._message_page = self._build_message()
        self._stack.addWidget(self._message_page)
        layout.addWidget(self._stack, 1)
        self.find_bar = FindBar()
        self.find_bar.hide()
        self.find_bar.changed.connect(lambda: self._search(jump=True))
        self.find_bar.next_requested.connect(lambda: self.find_step(1))
        self.find_bar.previous_requested.connect(lambda: self.find_step(-1))
        self.find_bar.closed.connect(self._on_find_closed)
        layout.addWidget(self.find_bar)
        context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for keys, slot in ((QKeySequence.StandardKey.Find, self.open_find),
                           (QKeySequence("F3"), lambda: self.find_step(1)),
                           (QKeySequence("Shift+F3"), lambda: self.find_step(-1)),
                           (QKeySequence(COPY_PATCH_KEYS), self.copy_selected_patch),
                           (QKeySequence("F12"), self.go_to_definition)):  # RZG-75
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(context)
            shortcut.activated.connect(slot)

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
        ctx.prefs.changed.connect(self._on_pref_changed)

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
        view.set_tab_width(self.ctx.prefs.value(TAB_SPACES))
        view.hunk_clicked.connect(self._on_hunk_link)
        view.selectionChanged.connect(self._sync_current)
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
            if not self.find_bar.keep.isChecked():
                self.find_bar.close_bar()
            session.request_diff(None)
            self._loading.stop()
            self._shown = None
            self._path.setText("")
            self._show_message(NOTHING_SELECTED)
            return
        self._request(file)
        if old is None or old.path != file.path:
            self.request_activation()  # the user picked another file
        if file != old and self.find_bar.isVisible():
            if self.find_bar.keep.isChecked():  # the search goes on in the new file
                self._find_jump = True
            else:  # a search never spills into another file
                self.find_bar.close_bar()
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
        whole = self.ctx.actions.whole_file_diff.isChecked()
        context = None if whole else self.ctx.prefs.value(CONTEXT_LINES)
        return (session.diff_file == file and session.diff_whole == whole
                and session.diff_context == context)

    def _request(self, file: FileSelection, full: bool = False) -> None:
        whole = self.ctx.actions.whole_file_diff.isChecked()
        self.ctx.session.request_diff(file, full=full, whole=whole,
                                      max_lines=self.ctx.prefs.value(LARGE_DIFF),
                                      context=self.ctx.prefs.value(CONTEXT_LINES))

    def _on_pref_changed(self, key: str, value: object) -> None:
        if key == TAB_SPACES.key:
            for view in (self._unified, self._left, self._right):
                if view is not None:
                    view.set_tab_width(value)
        elif key in _FOLD_KEYS:
            self._fold_new = True  # the stretches may be others: start their folds again
            self._rerender()
        elif key == LARGE_DIFF.key and self._file is not None:
            self._request(self._file)  # the diff on screen stays until the new one is in
        elif (key == CONTEXT_LINES.key and self._file is not None
              and not self.ctx.actions.whole_file_diff.isChecked()):
            self._keep_place(by_hunk=True)  # the change stays put, its context grows around it
            self._request(self._file)

    def _load_full(self) -> None:
        if self._file is not None:
            self._request(self._file, full=True)
            self._loading.start()

    def _on_whole_toggled(self, on: bool) -> None:
        self._headers.setVisible(on)
        if self._file is None:
            return
        self._keep_place()
        self._request(self._file)
        if self._cached(self._file):
            self._show(self._file, self.ctx.session.diff)
        else:
            self._loading.start()

    def _keep_place(self, by_hunk: bool = False) -> None:
        """Find the same lines in the diff asked for next, once it's in; with
        ``by_hunk``, keep the top hunk's header where it is (spec §5.4)."""
        shown = self._shown_views() if self._shown == self._file else []
        self._place = [view.place() for view in shown] or None
        self._by_hunk = by_hunk

    def _show(self, file: FileSelection, diff: FileDiff | None) -> None:
        self._loading.stop()
        keep_scroll = file == self._shown
        whole = self.ctx.session.diff_whole
        if (file, diff, whole) != (self._shown, self._diff, self._whole):
            self._folded.clear()  # a new diff: its stretches are new
            self._fold_new = True
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
                        view.go_to(at, by_hunk=self._by_hunk)

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
        prefs = self.ctx.prefs
        self._stretches = (stretches(sides[0], prefs.value(FOLD_MIN), prefs.value(FOLD_KEPT))
                           if self._whole else [])
        if self._fold_new:
            self._fold_new = False
            on = prefs.value(FOLD_ON_OPEN)
            self._folded = {s.key for s in self._stretches} if on else set()
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
        jump, self._find_jump = self._find_jump, False
        self._search(jump=jump)

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
        hunk_set: frozenset[Line] = frozenset()
        chosen: frozenset[Line] = frozenset()
        if diff is not None:
            row = view.row_at(pos)
            hunk = row.hunk if row is not None else None
            hunk_set = hunk_lines(diff, hunk) if hunk is not None else frozenset()
            chosen = view.selected_lines()
            if not chosen and row is not None and row.line is not None and row.hunk is not None:
                if diff.hunks[row.hunk].lines[row.line].kind is not LineKind.CONTEXT:
                    chosen = frozenset({(row.hunk, row.line)})
        if verb is not None and diff is not None:
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
        if diff is not None:  # Copy and Save as Patch (RZG-76, spec §5.4)
            reason = can_patch(diff)
            scopes = (("Hunk", hunk_set), ("Selected Lines", chosen), ("File", all_lines(diff)))
            for title, run, suffix, keys in (("Copy as Patch", self.copy_patch, "", True),
                                             ("Save as Patch", self.save_patch, "…", False)):
                submenu = menu.addMenu(title)
                submenu.menuAction().setEnabled(reason is None)
                if reason:
                    submenu.menuAction().setToolTip(reason)
                for name, lines in scopes:
                    text = name + suffix
                    if keys and name == "Selected Lines":
                        text += f"\t{COPY_PATCH_KEYS}"  # shown, not bound: the panel's shortcut is
                    action = submenu.addAction(text)
                    action.setEnabled(bool(lines) and reason is None)
                    action.triggered.connect(lambda _=False, run=run, lines=lines: run(lines))
        if self._shown is not None:  # RZG-96: the working-tree file, at the line under the pointer
            menu.addSeparator()
            line = new_line_at(view.rows, view.cursorForPosition(pos).blockNumber())
            menu.addAction(self.ctx.actions.open_in_vscode((self._shown.path,), menu, line))
            symbol, line = symbol_line_at(view.rows, view.text_pos_at(pos))  # RZG-75
            menu.addAction(self.ctx.actions.go_to_definition(
                symbol, self._shown.path, line, menu))
        return menu

    def _patch(self, lines: frozenset[Line]) -> str | None:
        """The patch of ``lines`` of the shown diff (``copy_patch``), or None."""
        diff = self._diff
        if diff is None or not lines or can_patch(diff) is not None:
            return None
        return copy_patch(diff, lines)

    def copy_patch(self, lines: frozenset[Line]) -> None:
        """Put the patch of ``lines`` of the shown diff on the clipboard."""
        if (patch := self._patch(lines)) is not None:
            QGuiApplication.clipboard().setText(patch)

    def save_patch(self, lines: frozenset[Line]) -> None:
        """Ask where to save the patch of ``lines`` of the shown diff, and save it (§8.3)."""
        if (patch := self._patch(lines)) is None or self._diff is None:
            return
        repo = self.ctx.session.repo
        self.ctx.saver.ask_and_save(
            self, patch, kind="patch", title="Save Patch",
            suggested_name=f"{PurePosixPath(self._diff.path).name}.patch",
            filters="Patches (*.patch *.diff);;All Files (*)",
            start=repo.root if repo is not None else None)

    def copy_selected_patch(self) -> None:
        """Ctrl+Shift+C: copy the focused view's selected lines as a patch."""
        views = self._shown_views()
        view = next((v for v in views if v.hasFocus()), None)
        if view is None:
            view = next((v for v in views if v.selected_lines()), None)
        if view is not None:
            self.copy_patch(view.selected_lines())

    def go_to_definition(self) -> bool:
        """F12 (RZG-75): the symbol at the text cursor of the focused view (the
        right one when none is), in VS Code; False if there's none."""
        views = self._shown_views()
        if self._shown is None or not views:
            return False
        view = next((v for v in views if v.hasFocus()), views[-1])
        symbol, line = symbol_line_at(view.rows, view.text_cursor())
        return self.ctx.actions.request_definition(symbol, self._shown.path, line)

    def _show_message(self, text: str, show_anyway: bool = False,
                      changes_only: bool = False) -> None:
        self._message.setText(text)
        self._show_anyway.setVisible(show_anyway)
        self._changes_only.setVisible(changes_only)
        self._hint.hide()
        self._stack.setCurrentWidget(self._message_page)
        self._search()  # nothing to find in a message


    # ---- find (spec §5.4, RZG-74) --------------------------------------------

    def open_find(self) -> None:
        """Ctrl+F: open the find bar, filled with the selected text when that is
        within one row and isn't the current match, and search."""
        was_open = self.find_bar.isVisible()
        text = None
        for view in self._shown_views():
            span = view.text_range()
            if span is not None and span[0].row == span[1].row:
                if self._place_of_selection(view) is None:
                    text = view.rows[span[0].row].text[span[0].col:span[1].col]
                break
        changed = text is not None and text != self.find_bar.field.text()
        self.find_bar.open(text)
        if changed or not was_open:
            self._search(jump=True)

    def find_step(self, delta: int) -> None:
        """F3 (``delta`` 1) or Shift+F3 (-1): the next or previous match, wrapping."""
        bar = self.find_bar
        if not bar.isVisible():
            if not bar.field.text():
                self.open_find()
                return
            bar.show()
            self._search()
        if bar.flush():  # the waiting search went to the first match itself
            return
        if not self._matches:
            return
        current = self._selected_match()
        if current is None:
            index = self._from_anchor(forward=delta > 0)
        else:
            index = (current + delta) % len(self._matches)
        self._go(index)

    def _search(self, jump: bool = False) -> None:
        """Find the bar's query in the shown views and fill the matches; with
        ``jump``, go to the first one at or after the selection or caret."""
        views = self._shown_views() if self.find_bar.isVisible() else []
        self._invalid = False
        query = self.find_bar.query()
        pattern = None
        if views:
            try:
                pattern = query.pattern()
            except ValueError:
                self._invalid = True
        matches = [(side, m) for side, view in enumerate(views)
                   for m in find_matches(view.rows, pattern, query.joinable, MAX_MATCHES + 1)]
        matches.sort(key=lambda sm: (*sm[1].order[:2], sm[0], sm[1].start))
        self._more = len(matches) > MAX_MATCHES
        del matches[MAX_MATCHES:]
        self._matches = matches
        self._match_index = {(side, m.row, m.start, m.end): i
                             for i, (side, m) in enumerate(matches) if m.hidden is None}
        for side, view in enumerate(views):
            spans: dict[int, list[tuple[int, int]]] = {}
            for s, m in matches:
                if s == side and m.hidden is None:
                    spans.setdefault(m.row, []).append((m.start, m.end))
            view.set_find_spans({row: tuple(found) for row, found in spans.items()})
        self._current = self._selected_match()
        if jump and matches:
            self._go(self._from_anchor(forward=True))
        else:
            self._update_count()
            self._outline_current()

    def _go(self, index: int) -> None:
        """Make match ``index`` current: the text selection, in view, unfolding its
        stretch first if it's in a fold row."""
        side, match = self._matches[index]
        views = self._shown_views()
        if match.hidden is not None:
            key = views[side].rows[match.row].fold
            match = FindMatch(match.row + match.hidden, match.start, match.end)
            if key is not None:
                self._folded.discard(key)
            self._rerender()  # searches again
            views = self._shown_views()
            index = self._match_index.get((side, match.row, match.start, match.end), -1)
            if index < 0:
                return
        for n, view in enumerate(views):
            if n != side:
                view.clear_text_selection()
        views[side].select_text(TextPos(match.row, match.start), TextPos(match.row, match.end))
        self._current = index
        self._update_count()
        self._outline_current()

    def _from_anchor(self, forward: bool) -> int:
        """The first match at or after the selection's start (or the caret), or with
        ``forward`` False the last before it; wrapping."""
        views = self._shown_views()
        side = next((n for n, v in enumerate(views) if v.text_range() is not None),
                    next((n for n, v in enumerate(views) if v.hasFocus()), 0))
        view = views[side]
        span = view.text_range()
        at = span[0] if span is not None else view.text_cursor()
        anchor = (at.row, -1, side, at.col)
        keys = [(*m.order[:2], s, m.start) for s, m in self._matches]
        if forward:
            return next((i for i, k in enumerate(keys) if k >= anchor), 0)
        return next((i for i in range(len(keys) - 1, -1, -1) if keys[i] < anchor),
                    len(keys) - 1)

    def _place_of_selection(self, view: DiffView) -> int | None:
        views = self._shown_views()
        span = view.text_range()
        if span is None or view not in views or span[0].row != span[1].row:
            return None
        start, end = span
        return self._match_index.get((views.index(view), start.row, start.col, end.col))

    def _selected_match(self) -> int | None:
        """The match that is a shown view's text selection, if one is."""
        for view in self._shown_views():
            index = self._place_of_selection(view)
            if index is not None:
                return index
        return None

    def _sync_current(self) -> None:
        if self.find_bar.isVisible() and self._matches:
            self._current = self._selected_match()
            self._update_count()
            self._outline_current()

    def _outline_current(self) -> None:
        """Outline the whole word of the current match on its side, and nothing elsewhere."""
        side, match = (self._matches[self._current] if self._current is not None
                       else (None, None))
        views = self._shown_views()
        for view in (self._unified, self._left, self._right):
            current = None
            if view is None:
                continue
            if match is not None and view in views and views.index(view) == side:
                current = (match.row, *word_around(view.rows[match.row].text,
                                                   match.start, match.end))
            view.set_find_current(current)

    def _update_count(self) -> None:
        self.find_bar.set_count(self._current, len(self._matches), self._invalid, self._more)

    def _on_find_closed(self) -> None:
        self._find_jump = False
        self._search()  # the bar is hidden: no matches
        for view in (self._unified, self._left, self._right):
            if view is not None:
                view.set_find_spans({})
                view.set_find_current(None)
        views = self._shown_views()
        if views:
            side = next((n for n, v in enumerate(views) if v.text_range() is not None), 0)
            views[side].setFocus(Qt.FocusReason.OtherFocusReason)


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
