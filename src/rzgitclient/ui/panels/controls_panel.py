"""The bar of controls across the top: Open, Refresh, Fetch, Pull, the branch button (spec §5.4).

It replaces the toolbar: the buttons show the same ``AppActions`` objects as the
menus, so text, shortcut and enabled state come from one place.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout, QLineEdit, QMenu, QSizePolicy, QToolButton, QWidgetAction,
)

from rzgitclient.ui.app_actions import BranchOp
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.style_roles import Role, set_role

FILTER_AT = 15  # more branches than this: a filter box on top of the menu
LABEL_WIDTH = 360  # px; a longer label is cut short in the middle
MIN_LABEL_WIDTH = 60  # px: in a narrow window, the label gives up width down to this
SHA_LENGTH = 8
NO_REPO = "No repository"


class ControlsPanel(Panel):
    panel_id = "controls"
    title = "Controls"
    show_header = False
    fixed_height = True
    always_shown = True
    movable = False

    _branch: _BranchButton
    _menu: QMenu
    _label: str  # the branch button's full label, before it is cut short
    _filter: QLineEdit | None
    _branch_actions: list[QAction]

    def _setup(self) -> None:
        layout = QHBoxLayout(self)
        # The old toolbar's metrics, so the window looks the same: a 39 px bar.
        layout.setContentsMargins(6, 6, 6, 5)
        layout.setSpacing(2)
        actions = self.ctx.actions
        for action in (actions.open_repo, actions.refresh, actions.fetch, actions.pull):
            layout.addWidget(_button(action))
        layout.addStretch(1)

        self._branch = _BranchButton()
        set_role(self._branch, Role.BRANCH_BUTTON)
        self._branch.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._branch.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._branch.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self._branch)
        self._menu.aboutToShow.connect(self._fill_menu)
        self._branch.setMenu(self._menu)
        layout.addWidget(self._branch)
        self._label = ""
        self._filter = None
        self._branch_actions = []

        session = self.ctx.session
        session.repo_changed.connect(self._render_branch)
        session.head_changed.connect(self._render_branch)
        session.head_sha_changed.connect(self._render_branch)
        self._render_branch()

    # ---- the label ------------------------------------------------------

    def _render_branch(self, *_args: object) -> None:
        session = self.ctx.session
        self._label = branch_label(session.repo is not None, session.head, session.head_sha)
        self._branch.set_label(self._label)
        self._branch.setToolTip(self._label)
        self._branch.setEnabled(session.repo is not None)

    # ---- the menu (spec §5.4): built each time it opens ---------------------

    def _fill_menu(self) -> None:
        session, menu = self.ctx.session, self._menu
        menu.clear()
        self._filter = None
        self._branch_actions = []
        branches = session.branches
        if len(branches) > FILTER_AT:
            self._filter = QLineEdit(menu)
            self._filter.setPlaceholderText("Filter branches")
            self._filter.setClearButtonEnabled(True)
            self._filter.textChanged.connect(self._apply_filter)
            self._filter.returnPressed.connect(self._switch_to_only_match)
            box = QWidgetAction(menu)
            box.setDefaultWidget(self._filter)
            menu.addAction(box)
            QTimer.singleShot(0, self._filter.setFocus)  # once the menu is up
        for b in branches:
            action = menu.addAction(_menu_text(b.name))
            action.setCheckable(True)
            action.setChecked(b.is_current)
            action.setEnabled(not b.is_current)
            action.setData(b.name)
            action.triggered.connect(lambda _=False, name=b.name: self._switch(name))
            self._branch_actions.append(action)
        if not branches and session.head:
            # Fresh repo: the branch is in HEAD but has no ref yet.
            fresh = menu.addAction(_menu_text(f"{session.head} (no commits)"))
            fresh.setCheckable(True)
            fresh.setChecked(True)
            fresh.setEnabled(False)
        menu.addSeparator()
        new = menu.addAction("New Branch…")
        start = session.head or session.head_sha
        new.setEnabled(start is not None and session.head_sha is not None)
        if start is not None:
            new.triggered.connect(
                lambda _=False: self.ctx.actions.request_branch(BranchOp.NEW, start))

    def _apply_filter(self, text: str) -> None:
        needle = text.casefold()
        for action in self._branch_actions:
            action.setVisible(needle in action.data().casefold())

    def _switch_to_only_match(self) -> None:
        shown = [a for a in self._branch_actions if a.isVisible() and a.isEnabled()]
        if len(shown) == 1:
            self._menu.close()
            shown[0].trigger()

    def _switch(self, name: str) -> None:
        self.ctx.actions.request_branch(BranchOp.SWITCH, name)


class _BranchButton(QToolButton):
    """The branch button. It asks for its label's width, up to LABEL_WIDTH, but
    gives it up first when the bar is narrow: the label is then cut short in the
    middle to fit, down to MIN_LABEL_WIDTH (spec §5.4)."""

    def __init__(self) -> None:
        super().__init__()
        # Shrinkable (a tool button is Fixed): the layout may give it less than its hint.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._label = ""
        self._hint: QSize | None = None  # for the label cut at LABEL_WIDTH
        self._chrome = 0  # px around the text: padding and the menu arrow

    def set_label(self, label: str) -> None:
        self._label = label
        self._hint = None
        self.setText(self._elided(LABEL_WIDTH))
        self._hint = super().sizeHint()
        self._chrome = self._hint.width() - self._text_width()
        self.updateGeometry()
        if self.isVisible():
            self._fit()

    def sizeHint(self) -> QSize:
        return self._hint if self._hint is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        hint = self.sizeHint()
        return QSize(min(hint.width(), self._chrome + MIN_LABEL_WIDTH), hint.height())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        text = self._elided(min(LABEL_WIDTH, self.width() - self._chrome))
        if text != self.text():
            self.setText(text)

    def _elided(self, width: int) -> str:
        return _menu_text(self.fontMetrics().elidedText(
            self._label, Qt.TextElideMode.ElideMiddle, max(width, 0)))

    def _text_width(self) -> int:
        return self.fontMetrics().size(Qt.TextFlag.TextShowMnemonic, self.text()).width()


def branch_label(has_repo: bool, head: str | None, head_sha: str | None) -> str:
    """The branch button's label: the branch, a detached HEAD, or a fresh repo's branch."""
    if not has_repo:
        return NO_REPO
    if head is not None:
        return head if head_sha is not None else f"{head} (no commits)"
    if head_sha is not None:
        return f"detached at {head_sha[:SHA_LENGTH]}"
    return "detached HEAD"  # not loaded yet, or a repo git can't read HEAD of


def _menu_text(name: str) -> str:
    """``name`` as button or menu text: "&" would be read as a shortcut mark."""
    return name.replace("&", "&&")


def _button(action: QAction) -> QToolButton:
    """A button like a toolbar's: flat, no keyboard focus, the action's icon text."""
    button = QToolButton()
    button.setDefaultAction(action)
    button.setAutoRaise(True)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setIconSize(QSize(16, 16))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonFollowStyle)
    return button
