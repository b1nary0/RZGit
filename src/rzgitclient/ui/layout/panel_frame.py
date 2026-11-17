"""A panel with its section header, as placed in a split (spec §6.2)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QVBoxLayout, QWidget

from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.style_roles import Role, Surface, set_role, set_surface
from rzgitclient.ui.widgets.section_header import SectionHeader


class PanelFrame(QWidget):
    """Section header above the panel, on its area's ``surface`` (spec §6.1).

    The header follows the panel's ``header`` text, and is hidden for a panel
    with ``show_header = False``. At its right, a ⋯ button shows the panel's
    ``header_actions`` (spec §6.2), hidden while it has none. The frame is layout chrome:
    the panel stays unaware of it (contract rule 5).
    """

    def __init__(self, panel: Panel, surface: Surface, parent: QWidget | None = None):
        super().__init__(parent)
        self.panel = panel
        set_surface(self, surface)
        set_surface(panel, surface)

        self.header = SectionHeader(panel.header)
        self.header.setVisible(panel.show_header)
        panel.header_changed.connect(self.header.set_title)

        # The ⋯ button is made the first time the panel has actions (most have none).
        self.menu_button: QToolButton | None = None
        panel.header_actions_changed.connect(self._update_menu)
        self._update_menu()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(panel)
        # A panel that was a background tab was hidden explicitly, and layouts
        # never show explicitly hidden widgets. Now parented, so this doesn't
        # open it as a window.
        panel.show()

    def _update_menu(self) -> None:
        actions = self.panel.header_actions
        button = self.menu_button
        if button is None:
            if not actions:
                return
            button = self.menu_button = self._make_menu_button()
        menu = button.menu()
        menu.clear()
        menu.addActions(list(actions))
        button.setVisible(bool(actions))

    def _make_menu_button(self) -> QToolButton:
        """Inside the header, at its right, so a press on it never starts a drag
        (spec §6.2)."""
        button = QToolButton(self.header)
        set_role(button, Role.HEADER_MENU)
        button.setText("⋯")
        button.setToolTip(f"{self.panel.title} menu")
        button.setMenu(QMenu(button))
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bar = QHBoxLayout(self.header)
        bar.setContentsMargins(0, 0, 2, 0)
        bar.addStretch(1)
        bar.addWidget(button)
        return button
