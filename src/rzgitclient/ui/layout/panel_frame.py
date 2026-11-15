"""A panel with its section header, as placed in a split (spec §6.2)."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.style_roles import set_surface
from rzgitclient.ui.widgets.section_header import SectionHeader


class PanelFrame(QWidget):
    """Section header above the panel, on the panel's surface.

    The header follows the panel's ``header`` text. The frame is layout chrome:
    the panel stays unaware of it (contract rule 5).
    """

    def __init__(self, panel: Panel, parent: QWidget | None = None):
        super().__init__(parent)
        self.panel = panel
        set_surface(self, panel.surface)

        self.header = SectionHeader(panel.header)
        panel.header_changed.connect(self.header.set_title)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(panel)
