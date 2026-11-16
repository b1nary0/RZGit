"""View → Layout: switch between the built-in presets (spec §6.1, §6.4)."""

from __future__ import annotations

from PySide6.QtWidgets import QMenu, QWidget

from rzgitclient.ui.layout.layout_host import LayoutHost
from rzgitclient.ui.layout.layout_presets import PRESETS, Preset


class LayoutMenu(QMenu):
    def __init__(self, host: LayoutHost, parent: QWidget | None = None):
        super().__init__("&Layout", parent)
        self._host = host
        for preset in PRESETS:
            action = self.addAction(preset.title)
            action.triggered.connect(lambda _checked=False, p=preset: self.apply(p))

    def apply(self, preset: Preset) -> None:
        """Show ``preset``. Presets show every panel, so nothing stays hidden."""
        self._host.apply(preset.root)
