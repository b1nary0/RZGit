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

    def sync(self) -> None:
        """Write pending changes to disk now (Qt also does this on exit)."""
        self._settings.sync()

    def _set(self, key: str, value: object) -> None:
        if value is None:
            self._settings.remove(key)
        else:
            self._settings.setValue(key, value)
