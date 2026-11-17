"""The settings the Settings window shows (spec §8.2).

A module that uses a setting declares it at import, ``declare(Setting(...))``, and
reads its value from the app's one ``Preferences``, which panels get as
``PanelContext.prefs``. Values are saved through ``AppSettings``, under ``prefs/``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from rzgitclient.core.app_settings import AppSettings

log = logging.getLogger(__name__)

Value = bool | int | float | str


@dataclass(frozen=True)
class Setting:
    """One setting: its type is its default's."""

    key: str  # saved as prefs/<key>
    default: Value
    label: str
    page: str = "General"
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: int | float | None = None
    special: str = ""  # a text field's placeholder: "Automatic"
    choices: tuple[tuple[str, str], ...] = ()  # (value, label), for a str
    # "font": a monospaced font family; "font_size": its size. Their default ("", 0)
    # means the theme's, and the editor shows the font and size the theme has.
    editor: str = ""
    help: str = ""  # the editor's tooltip

    @property
    def kind(self) -> type:
        return type(self.default)

    def check(self, value: object) -> Value | None:
        """``value`` as this setting's type, clamped to its range; None if it isn't
        one (or isn't one of its choices)."""
        kind = self.kind
        try:
            if kind is bool:
                if isinstance(value, bool):
                    result: Value = value
                elif isinstance(value, str) and value.lower() in ("true", "false"):
                    result = value.lower() == "true"
                else:
                    return None
            elif kind is int:
                if isinstance(value, bool) or isinstance(value, float):
                    return None
                result = int(value) if isinstance(value, (int, str)) else None
            elif kind is float:
                if isinstance(value, bool):
                    return None
                result = float(value) if isinstance(value, (int, float, str)) else None
            else:
                result = value if isinstance(value, str) else None
        except ValueError:
            return None
        if result is None:
            return None
        if isinstance(result, (int, float)) and not isinstance(result, bool):
            if self.minimum is not None:
                result = max(result, self.minimum)
            if self.maximum is not None:
                result = min(result, self.maximum)
            result = kind(result)
        if self.choices and result not in {v for v, _label in self.choices}:
            return None
        return result


_DECLARED: dict[str, Setting] = {}


def declare(setting: Setting) -> Setting:
    """Add ``setting`` to the ones the Settings window shows; returns it."""
    if setting.key in _DECLARED:
        raise ValueError(f"setting {setting.key!r} is declared twice")
    if setting.check(setting.default) != setting.default:
        raise ValueError(f"setting {setting.key!r}: its default isn't a valid value")
    _DECLARED[setting.key] = setting
    return setting


def declared() -> tuple[Setting, ...]:
    """Every declared setting, in declaration order."""
    return tuple(_DECLARED.values())


class Preferences(QObject):
    changed = Signal(str, object)  # key, the new value

    def __init__(self, settings: AppSettings | None = None, parent: QObject | None = None):
        """``settings``: where values are saved; None keeps them in memory (tests)."""
        super().__init__(parent)
        self._settings = settings
        self._values: dict[str, Value] = {}

    def value(self, setting: Setting) -> Value:
        if setting.key not in self._values:
            raw = self._settings.pref(setting.key) if self._settings is not None else None
            value = setting.check(raw) if raw is not None else setting.default
            if value is None:
                log.warning("Setting %s isn't valid (%.80r); using its default",
                            setting.key, raw)
                value = setting.default
            self._values[setting.key] = value
        return self._values[setting.key]

    def set(self, setting: Setting, value: object) -> None:
        """Save ``value`` (checked; an invalid one is ignored, logged) and emit
        ``changed`` if it is new."""
        checked = setting.check(value)
        if checked is None:
            log.warning("Not a valid value for %s: %.80r", setting.key, value)
            return
        if checked == self.value(setting):
            return
        self._values[setting.key] = checked
        if self._settings is not None:
            self._settings.set_pref(setting.key, None if checked == setting.default else checked)
        self.changed.emit(setting.key, checked)

    def reset(self, settings: Iterable[Setting]) -> None:
        """Set each of ``settings`` back to its default."""
        for setting in settings:
            self.set(setting, setting.default)
