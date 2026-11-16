"""View → Panels: show and hide each panel (spec §6.6)."""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from rzgitclient.ui.layout.layout_host import LayoutHost
from rzgitclient.ui.panels.panel_registry import PANEL_TYPES


class PanelsMenu(QMenu):
    """A checkable entry per registered panel, following the host's layout."""

    def __init__(self, host: LayoutHost, parent: QWidget | None = None):
        super().__init__("&Panels", parent)
        self._host = host
        self._actions: dict[str, QAction] = {}
        for panel_type in PANEL_TYPES:
            action = self.addAction(panel_type.title)
            action.setCheckable(True)
            # triggered, not toggled: only the user's clicks, not _sync's setChecked.
            action.triggered.connect(
                lambda checked, pid=panel_type.panel_id: self._set_shown(pid, checked)
            )
            self._actions[panel_type.panel_id] = action
        host.layout_changed.connect(self._sync)
        self._sync()

    def _set_shown(self, panel_id: str, shown: bool) -> None:
        if shown:
            self._host.show_panel(panel_id)
        else:
            self._host.hide_panel(panel_id)

    def _sync(self) -> None:
        shown = self._host.panels
        for panel_id, action in self._actions.items():
            action.setChecked(panel_id in shown)
            # The last visible panel can't be hidden.
            action.setEnabled(not (panel_id in shown and len(shown) == 1))
