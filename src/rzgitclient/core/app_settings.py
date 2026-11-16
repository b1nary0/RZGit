"""Typed access to the app's saved settings (spec §6.3).

The only code that touches QSettings. Values are plain Python types; the
layout is stored as a JSON string so its format is ours, not a QVariant tree.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QSettings

log = logging.getLogger(__name__)

_LAST_REPO = "last_repo"
_LAYOUT = "layout"
_WINDOW_GEOMETRY = "window/geometry"
_SIDE_BY_SIDE_DIFF = "diff/side_by_side"
_COMMIT_DRAFTS = "commit/drafts"
_SAVED_LAYOUTS = "layouts/saved"


class AppSettings:
    def __init__(self, path: str | Path | None = None):
        """``path``: an INI file to use instead of the user's settings (tests)."""
        self._settings = (
            QSettings(str(path), QSettings.Format.IniFormat) if path is not None else QSettings()
        )

    @property
    def last_repo(self) -> str | None:
        return self._settings.value(_LAST_REPO, "", str) or None

    @last_repo.setter
    def last_repo(self, path: str | None) -> None:
        self._set(_LAST_REPO, path)

    @property
    def layout(self) -> dict[str, Any] | None:
        """The saved layout as ``layout_model.to_dict`` wrote it; ``None`` if
        there is none or it isn't valid JSON (logged)."""
        raw = self._settings.value(_LAYOUT, "", str)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError as exc:
            log.warning("Saved layout isn't valid JSON (%s); ignoring it", exc)
            return None
        return data if isinstance(data, dict) else None

    @layout.setter
    def layout(self, data: dict[str, Any] | None) -> None:
        self._set(_LAYOUT, None if data is None else json.dumps(data))

    @property
    def window_geometry(self) -> QByteArray | None:
        value = self._settings.value(_WINDOW_GEOMETRY)
        return value if isinstance(value, QByteArray) and not value.isEmpty() else None

    @window_geometry.setter
    def window_geometry(self, geometry: QByteArray | None) -> None:
        self._set(_WINDOW_GEOMETRY, geometry)

    @property
    def side_by_side_diff(self) -> bool:
        """Whether the Diff panel shows old and new side by side (default: unified)."""
        return self._settings.value(_SIDE_BY_SIDE_DIFF, False, bool)

    @side_by_side_diff.setter
    def side_by_side_diff(self, on: bool) -> None:
        self._set(_SIDE_BY_SIDE_DIFF, bool(on))

    @property
    def commit_drafts(self) -> dict[str, str]:
        """Unfinished commit messages by repository root ({} if none, or not valid)."""
        raw = self._settings.value(_COMMIT_DRAFTS, "", str)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError as exc:
            log.warning("Saved commit drafts aren't valid JSON (%s); ignoring them", exc)
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}

    @commit_drafts.setter
    def commit_drafts(self, drafts: dict[str, str]) -> None:
        self._set(_COMMIT_DRAFTS, json.dumps(drafts) if drafts else None)

    @property
    def saved_layouts(self) -> list[tuple[str, dict[str, Any]]]:
        """The user's named layouts, in the order saved, as ``(name, layout dict)``
        (spec §6.3). Entries that aren't a name and an object are dropped (logged)."""
        raw = self._settings.value(_SAVED_LAYOUTS, "", str)
        try:
            data = json.loads(raw) if raw else []
        except ValueError as exc:
            log.warning("Saved layouts aren't valid JSON (%s); ignoring them", exc)
            return []
        if not isinstance(data, list):
            log.warning("Saved layouts aren't a list; ignoring them")
            return []
        result = []
        for entry in data:
            name = entry.get("name") if isinstance(entry, dict) else None
            layout = entry.get("layout") if isinstance(entry, dict) else None
            if isinstance(name, str) and name and isinstance(layout, dict):
                result.append((name, layout))
            else:
                log.warning("Dropping a malformed saved layout: %.80r", entry)
        return result

    @saved_layouts.setter
    def saved_layouts(self, layouts: list[tuple[str, dict[str, Any]]]) -> None:
        self._set(_SAVED_LAYOUTS, json.dumps(
            [{"name": n, "layout": l} for n, l in layouts]) if layouts else None)

    def sync(self) -> None:
        """Write pending changes to disk now (Qt also does this on exit)."""
        self._settings.sync()

    def _set(self, key: str, value: object) -> None:
        if value is None:
            self._settings.remove(key)
        else:
            self._settings.setValue(key, value)
