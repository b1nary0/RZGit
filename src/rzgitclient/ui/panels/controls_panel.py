"""The bar of controls across the top: Open, Refresh, the current branch (spec §5.4).

It replaces the toolbar: the buttons show the same ``AppActions`` objects as the
menus, so text, shortcut and enabled state come from one place.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton

from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.style_roles import Surface


class ControlsPanel(Panel):
    panel_id = "controls"
    title = "Controls"
    surface = Surface.CHROME
    show_header = False
    fixed_height = True
    always_shown = True

    _branch: QLabel

    def _setup(self) -> None:
        layout = QHBoxLayout(self)
        # The old toolbar's metrics, so the window looks the same: a 39 px bar.
        layout.setContentsMargins(6, 6, 6, 5)
        layout.setSpacing(2)
        actions = self.ctx.actions
        for action in (actions.open_repo, actions.refresh):
            layout.addWidget(_button(action))
        layout.addStretch(1)
        self._branch = QLabel()
        layout.addWidget(self._branch)

        session = self.ctx.session
        session.repo_changed.connect(self._render_branch)
        session.head_changed.connect(self._render_branch)
        self._render_branch()

    def _render_branch(self, *_args: object) -> None:
        session = self.ctx.session
        if session.repo is None:
            self._branch.setText("")
        else:
            self._branch.setText(session.head or "detached HEAD")


def _button(action: QAction) -> QToolButton:
    """A button like a toolbar's: flat, no keyboard focus, the action's icon text."""
    button = QToolButton()
    button.setDefaultAction(action)
    button.setAutoRaise(True)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setIconSize(QSize(16, 16))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonFollowStyle)
    return button
