"""Each panel's sort order and grouping (spec §5.1, §6.3, RZG-43, RZG-53).

``SortMenu`` reads and writes it. Panels don't get AppSettings; this holds
only the sort orders, by panel id, the same in every layout and repository.
A panel on its default order (``None``) has no entry; the grouping is in the
same entry ("group"), left out when there is none.
"""

from __future__ import annotations

import logging
from typing import Any

from rzgit.core.app_settings import AppSettings
from rzgit.core.sort_options import SortOrder

log = logging.getLogger(__name__)

_BY = "by"
_REVERSED = "reversed"
_GROUP = "group"


class SortPrefs:
    def __init__(self, settings: AppSettings | None = None):
        """``settings``: where the orders are saved; None keeps them in memory (tests)."""
        self._settings = settings
        self._orders: dict[str, SortOrder] = {}
        for panel_id, entry in (settings.sort_prefs if settings is not None else {}).items():
            by = entry.get(_BY) if isinstance(entry, dict) else None
            reversed_ = entry.get(_REVERSED, False) if isinstance(entry, dict) else None
            group = entry.get(_GROUP) if isinstance(entry, dict) else None
            if not (isinstance(by, str) and by and isinstance(reversed_, bool)
                    and (group is None or (isinstance(group, str) and group))):
                log.warning("Dropping a malformed sort order for %r: %.80r", panel_id, entry)
                continue
            self._orders[panel_id] = SortOrder(by, reversed_, group)

    def order(self, panel_id: str) -> SortOrder | None:
        """The order ``panel_id`` was last sorted by; None for its default order."""
        return self._orders.get(panel_id)

    def set_order(self, panel_id: str, order: SortOrder | None) -> None:
        """Keep ``order`` for ``panel_id`` (None: its default order). Saved at once."""
        if self._orders.get(panel_id) == order:
            return
        if order is None:
            del self._orders[panel_id]
        else:
            self._orders[panel_id] = order
        if self._settings is not None:
            self._settings.sort_prefs = self._as_dict()

    def _as_dict(self) -> dict[str, dict[str, Any]]:
        return {panel_id: {_BY: o.key, _REVERSED: o.reversed}
                | ({_GROUP: o.group} if o.group is not None else {})
                for panel_id, o in sorted(self._orders.items())}
