"""Each panel's column choices (spec §5.2, §6.3): the columns it hides.

``ColumnHeader`` reads and writes them. Panels don't get AppSettings; this holds
only the column choices, keyed by panel id and then by column label, the same
in every layout and repository.
"""

from __future__ import annotations

import logging
from typing import Any

from rzgitclient.core.app_settings import AppSettings

log = logging.getLogger(__name__)

_HIDDEN = "hidden"


class ColumnPrefs:
    def __init__(self, settings: AppSettings | None = None):
        """``settings``: where the choices are saved; None keeps them in memory (tests)."""
        self._settings = settings
        self._hidden: dict[str, set[str]] = {}
        for panel_id, entry in (settings.column_prefs if settings is not None else {}).items():
            hidden = entry.get(_HIDDEN) if isinstance(entry, dict) else None
            if (isinstance(hidden, list) and all(isinstance(c, str) for c in hidden)):
                if hidden:
                    self._hidden[panel_id] = set(hidden)
            else:
                log.warning("Dropping malformed column choices for %r: %.80r", panel_id, entry)

    def hidden(self, panel_id: str) -> frozenset[str]:
        """The labels of the columns ``panel_id`` hides."""
        return frozenset(self._hidden.get(panel_id, ()))

    def set_hidden(self, panel_id: str, column: str, hidden: bool) -> None:
        """Hide or show ``column`` (its label) in ``panel_id``; saved at once."""
        columns = self._hidden.setdefault(panel_id, set())
        if (column in columns) == hidden:
            return
        if hidden:
            columns.add(column)
        else:
            columns.discard(column)
            if not columns:
                del self._hidden[panel_id]
        self._save()

    def _save(self) -> None:
        if self._settings is not None:
            self._settings.column_prefs = self._as_dict()

    def _as_dict(self) -> dict[str, dict[str, Any]]:
        return {panel_id: {_HIDDEN: sorted(columns)}
                for panel_id, columns in sorted(self._hidden.items())}
