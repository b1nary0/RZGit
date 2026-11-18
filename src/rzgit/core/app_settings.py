"""Typed access to the app's saved settings (spec §6.3).

The only code that touches QSettings. Values are plain Python types; the
layout is stored as a JSON string so its format is ours, not a QVariant tree.

It holds options, settings and general app state (preferences, layout and theme
customisation, the last and the recent repositories, window geometry). State a feature
explicitly preserves as its own result, such as the commit drafts, is in files of its own.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QSettings

from rzgit.git.repo import canonical_path

log = logging.getLogger(__name__)

_LAST_REPO = "last_repo"
_LAYOUT = "layout"
_LAYOUT_PRESET = "layout_preset"
_WINDOW_GEOMETRY = "window/geometry"
_SIDE_BY_SIDE_DIFF = "diff/side_by_side"
_WHOLE_FILE_DIFF = "diff/whole_file"
_HUNK_HEADERS = "diff/hunk_headers"
_FILE_TREE = "files/tree"
_TOPO_ORDER = "history/topo_order"
_THEME_MODE = "theme/mode"
_COLOUR_BLIND = "theme/colour_blind"
_COMMIT_DRAFTS = "commit/drafts"  # older versions only; moved to files
_SAVED_LAYOUTS = "layouts/saved"
_COLUMN_PREFS = "columns/prefs"
_SORT_PREFS = "sort/prefs"
_RECENT_REPOS = "recent/repos"
_PREFS = "prefs/"  # the Settings window's settings, by key (spec §8.2)


class AppSettings:
    def __init__(self, path: str | Path | None = None):
        """``path``: an INI file to use instead of the user's settings (tests)."""
        self._settings = (
            QSettings(str(path), QSettings.Format.IniFormat) if path is not None else QSettings()
        )

    @property
    def last_repo(self) -> str | None:
        """The repository open when the app last closed; saved as its canonical path."""
        return self._settings.value(_LAST_REPO, "", str) or None

    @last_repo.setter
    def last_repo(self, path: str | Path | None) -> None:
        self._set(_LAST_REPO, str(canonical_path(path)) if path else None)

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
    def layout_preset(self) -> str | None:
        """The title of the built-in preset the saved layout is, or None for a
        custom layout (spec §6.3)."""
        return self._settings.value(_LAYOUT_PRESET, "", str) or None

    @layout_preset.setter
    def layout_preset(self, title: str | None) -> None:
        self._set(_LAYOUT_PRESET, title)

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
    def whole_file_diff(self) -> bool:
        """Whether the Diff panel shows the whole file (default: the changes only)."""
        return self._settings.value(_WHOLE_FILE_DIFF, False, bool)

    @whole_file_diff.setter
    def whole_file_diff(self, on: bool) -> None:
        self._set(_WHOLE_FILE_DIFF, bool(on))

    @property
    def hunk_headers(self) -> str:
        """The whole-file view's hunk headers: "full", "slim" (default) or "off".
        Anything else stored reads as "slim"."""
        value = self._settings.value(_HUNK_HEADERS, "slim", str)
        return value if value in ("full", "slim", "off") else "slim"

    @hunk_headers.setter
    def hunk_headers(self, mode: str) -> None:
        self._set(_HUNK_HEADERS, str(mode))

    @property
    def file_tree(self) -> bool:
        """Whether the file panels show a folder tree (default) or flat paths."""
        return self._settings.value(_FILE_TREE, True, bool)

    @file_tree.setter
    def file_tree(self, on: bool) -> None:
        self._set(_FILE_TREE, bool(on))

    @property
    def topo_order(self) -> bool:
        """Whether History lists commits in topological order (default: date order)."""
        return self._settings.value(_TOPO_ORDER, False, bool)

    @topo_order.setter
    def topo_order(self, on: bool) -> None:
        self._set(_TOPO_ORDER, bool(on))

    @property
    def theme_mode(self) -> str:
        """The theme the user chose: "light", "dark" or "system" (default).
        Anything else stored reads as "system"."""
        value = self._settings.value(_THEME_MODE, "system", str)
        return value if value in ("light", "dark", "system") else "system"

    @theme_mode.setter
    def theme_mode(self, mode: str) -> None:
        self._set(_THEME_MODE, str(mode))

    @property
    def colour_blind(self) -> bool:
        """Whether the theme uses its colour-blind friendly colours (off by default)."""
        return self._settings.value(_COLOUR_BLIND, False, bool)

    @colour_blind.setter
    def colour_blind(self, on: bool) -> None:
        self._set(_COLOUR_BLIND, bool(on))

    @property
    def old_commit_drafts(self) -> dict[str, str]:
        """The commit drafts older versions kept here, by repository root ({} if none,
        or not valid). They now live in files of their own, kept by CommitDrafts
        (spec §5.4, §6.3), which moves them once and then calls
        ``drop_old_commit_drafts``."""
        raw = self._settings.value(_COMMIT_DRAFTS, "", str)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError as exc:
            log.warning("Saved commit drafts aren't valid JSON (%s); ignoring them", exc)
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}

    def drop_old_commit_drafts(self) -> None:
        self._settings.remove(_COMMIT_DRAFTS)

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

    @property
    def column_prefs(self) -> dict[str, dict[str, Any]]:
        """Each panel's column choices by panel id, as ``ColumnPrefs`` wrote them
        ({} if none, or not a JSON object; spec §6.3). ``ColumnPrefs`` checks the entries."""
        raw = self._settings.value(_COLUMN_PREFS, "", str)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError as exc:
            log.warning("Saved column choices aren't valid JSON (%s); ignoring them", exc)
            return {}
        return data if isinstance(data, dict) else {}

    @column_prefs.setter
    def column_prefs(self, prefs: dict[str, dict[str, Any]]) -> None:
        self._set(_COLUMN_PREFS, json.dumps(prefs) if prefs else None)

    @property
    def sort_prefs(self) -> dict[str, dict[str, Any]]:
        """Each panel's sort order by panel id, as ``SortPrefs`` wrote it ({} if
        none, or not a JSON object; spec §6.3). ``SortPrefs`` checks the entries."""
        raw = self._settings.value(_SORT_PREFS, "", str)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError as exc:
            log.warning("Saved sort orders aren't valid JSON (%s); ignoring them", exc)
            return {}
        return data if isinstance(data, dict) else {}

    @sort_prefs.setter
    def sort_prefs(self, prefs: dict[str, dict[str, Any]]) -> None:
        self._set(_SORT_PREFS, json.dumps(prefs) if prefs else None)

    @property
    def recent_repos(self) -> list[Any]:
        """The recently opened worktrees, newest first, as ``RecentRepos`` wrote them
        ([] if none, or not a JSON list; spec §6.3). ``RecentRepos`` checks the entries."""
        raw = self._settings.value(_RECENT_REPOS, "", str)
        try:
            data = json.loads(raw) if raw else []
        except ValueError as exc:
            log.warning("Saved recent repositories aren't valid JSON (%s); ignoring them", exc)
            return []
        return data if isinstance(data, list) else []

    @recent_repos.setter
    def recent_repos(self, entries: list[dict[str, Any]]) -> None:
        self._set(_RECENT_REPOS, json.dumps(entries) if entries else None)

    def pref(self, key: str) -> object | None:
        """The stored value of the setting ``key`` (spec §8.2), as QSettings gives it
        back (often a string), or None. ``Preferences`` checks it."""
        return self._settings.value(_PREFS + key)

    def set_pref(self, key: str, value: object) -> None:
        """Store a setting's value; None removes it (the default)."""
        self._set(_PREFS + key, value)

    def sync(self) -> None:
        """Write pending changes to disk now (Qt also does this on exit)."""
        self._settings.sync()

    def _set(self, key: str, value: object) -> None:
        if value is None:
            self._settings.remove(key)
        else:
            self._settings.setValue(key, value)
