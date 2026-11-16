"""Semantic style hooks (spec §7.2).

Widgets say *what they are* through these values, set as Qt dynamic properties;
the stylesheet decides what that looks like. Add a value when a widget first
needs it: a test checks that the stylesheet uses every one.
"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

SURFACE_PROPERTY = "surface"
ROLE_PROPERTY = "role"


class Surface(StrEnum):
    """The background a pane sits on."""

    SIDEBAR = "sidebar"
    CONTENT = "content"
    CHROME = "chrome"  # the bar of controls across the top


class Role(StrEnum):
    """What a single widget is for."""

    SECTION_HEADER = "section-header"
    PLACEHOLDER = "placeholder"  # a muted "nothing to show" message
    DIFF_VIEW = "diff-view"  # the diff's text: no frame, rows reach the edges
    HINT = "hint"  # a small muted note, e.g. the commit subject's length
    HINT_WARNING = "hint-warning"  # the same note past its limit


def set_surface(widget: QWidget, surface: Surface) -> None:
    # A plain QWidget only paints a stylesheet background with this attribute.
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    _set_style_property(widget, SURFACE_PROPERTY, surface)


def set_role(widget: QWidget, role: Role) -> None:
    _set_style_property(widget, ROLE_PROPERTY, role)


def _set_style_property(widget: QWidget, name: str, value: StrEnum) -> None:
    text = str(value)
    if widget.property(name) == text:
        return
    widget.setProperty(name, text)
    # Once polished, Qt doesn't re-evaluate property selectors by itself.
    if widget.testAttribute(Qt.WidgetAttribute.WA_WState_Polished):
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()
