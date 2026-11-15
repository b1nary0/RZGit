"""Every panel type the app knows, looked up by ``panel_id`` (spec §5.3)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from rzgitclient.ui.panels.branches_panel import BranchesPanel
from rzgitclient.ui.panels.files_panel import FilesPanel
from rzgitclient.ui.panels.history_panel import HistoryPanel
from rzgitclient.ui.panels.panel import Panel

# Explicit on purpose: one place to see every panel, no import-time magic.
PANEL_TYPES: tuple[type[Panel], ...] = (BranchesPanel, HistoryPanel, FilesPanel)


def index_panel_types(types: Iterable[type[Panel]]) -> Mapping[str, type[Panel]]:
    """Map ``panel_id`` to type. Raises ``ValueError`` on a duplicate id."""
    by_id: dict[str, type[Panel]] = {}
    for panel_type_ in types:
        other = by_id.setdefault(panel_type_.panel_id, panel_type_)
        if other is not panel_type_:
            raise ValueError(
                f"panel id {panel_type_.panel_id!r} is used by both "
                f"{other.__name__} and {panel_type_.__name__}"
            )
    return MappingProxyType(by_id)


_BY_ID = index_panel_types(PANEL_TYPES)


def panel_type(panel_id: str) -> type[Panel]:
    """The panel type for ``panel_id``. Raises ``KeyError`` if there is none."""
    return _BY_ID[panel_id]
