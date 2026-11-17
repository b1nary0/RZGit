"""Each panel's column choices (spec §5.2, §6.3): the columns it hides and the
widths the user gave them.

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
_WIDTHS = "widths"


def _valid_hidden(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(c, str) for c in value)


def _valid_widths(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(c, str) and type(w) is int and w > 0 for c, w in value.items())


class ColumnPrefs:
    def __init__(self, settings: AppSettings | None = None):
        """``settings``: where the choices are saved; None keeps them in memory (tests)."""
        self._settings = settings
        self._hidden: dict[str, set[str]] = {}
        self._widths: dict[str, dict[str, int]] = {}
        for panel_id, entry in (settings.column_prefs if settings is not None else {}).items():
            hidden = entry.get(_HIDDEN, []) if isinstance(entry, dict) else None
            widths = entry.get(_WIDTHS, {}) if isinstance(entry, dict) else None
            if not (_valid_hidden(hidden) and _valid_widths(widths)):
                log.warning("Dropping malformed column choices for %r: %.80r", panel_id, entry)
                continue
            if hidden:
                self._hidden[panel_id] = set(hidden)
            if widths:
                self._widths[panel_id] = dict(widths)

    # ---- hidden columns (RZG-41) ------------------------------------------

    def hidden(self, panel_id: str) -> frozenset[str]:
        """The labels of the columns ``panel_id`` hides."""
        return frozenset(self._hidden.get(panel_id, ()))

    def set_hidden(self, panel_id: str, column: str, hidden: bool) -> None:
        """Hide or show ``column`` (its label) in ``panel_id``; saved at once."""
        columns = self._hidden.get(panel_id, set())
        if (column in columns) == hidden:
            return
        if hidden:
            columns.add(column)
        else:
            columns.discard(column)
        if columns:
            self._hidden[panel_id] = columns
        else:
            self._hidden.pop(panel_id, None)
        self._save()

    # ---- widths (RZG-40) --------------------------------------------------------

    def widths(self, panel_id: str) -> dict[str, int]:
        """The widths the user gave ``panel_id``'s columns, by label."""
        return dict(self._widths.get(panel_id, {}))

    def set_width(self, panel_id: str, column: str, width: int | None) -> None:
        """Keep the user's ``width`` for ``column``; None forgets it. Saved at once."""
        widths = self._widths.get(panel_id, {})
        if widths.get(column) == width:
            return
        if width is None:
            del widths[column]
        else:
            widths[column] = width
        if widths:
            self._widths[panel_id] = widths
        else:
            self._widths.pop(panel_id, None)
        self._save()

    def reset_widths(self, panel_id: str) -> None:
        """Forget every width the user gave ``panel_id``'s columns."""
        if self._widths.pop(panel_id, None) is not None:
            self._save()

    # ---- saving -----------------------------------------------------------------

    def _save(self) -> None:
        if self._settings is not None:
            self._settings.column_prefs = self._as_dict()

    def _as_dict(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for panel_id, columns in self._hidden.items():
            result.setdefault(panel_id, {})[_HIDDEN] = sorted(columns)
        for panel_id, widths in self._widths.items():
            result.setdefault(panel_id, {})[_WIDTHS] = dict(sorted(widths.items()))
        return dict(sorted(result.items()))
