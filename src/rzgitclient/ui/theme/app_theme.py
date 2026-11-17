"""The theme model (spec §7.1): the only place colours, fonts and metrics live."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import StrEnum

from PySide6.QtGui import QColor, QFont, QPalette

from rzgitclient.ui.theme.theme_stylesheet import build_stylesheet


@dataclass(frozen=True)
class ThemeColors:
    # chrome
    menubar_bg: str
    menubar_text: str
    menubar_hover: str
    toolbar_bg: str
    tab_bg: str
    header_bg: str  # a panel's section header, above it in a split
    sidebar_bg: str
    content_bg: str
    border: str
    border_light: str
    tree_guide: str  # the indent guides of a folder tree (file panels)
    column_header_bg: str  # a list's column labels (ColumnHeader, spec §5.2)
    column_header_text: str
    column_header_text_hover: str
    column_header_separator: str  # the inset line between two labels
    column_header_border: str  # the line under the row of labels
    # text
    text: str
    text_muted: str
    placeholder: str
    warning: str  # text past a soft limit, e.g. a long commit subject
    # interaction
    hover: str
    content_hover: str
    selection: str
    content_selection: str
    text_selection: str
    accent: str
    accent_text: str
    button_bg: str
    button_hover: str
    button_pressed: str
    scroll_handle: str
    scroll_handle_hover: str
    tooltip_bg: str
    # diff (for the diff view)
    diff_added_bg: str
    diff_added_word: str
    diff_removed_bg: str
    diff_removed_word: str
    diff_added_selected_bg: str  # a selected line: a stronger shade of its own colour
    diff_removed_selected_bg: str
    diff_filler_bg: str
    diff_gutter_text: str
    diff_hunk_bg: str  # the "@@ -1,4 +1,5 @@" row
    diff_hunk_text: str
    diff_line_button: str  # the Stage / Unstage button on a changed line
    diff_line_button_hover: str  # ... with the pointer on it
    diff_line_button_border: str
    diff_line_button_text: str


@dataclass(frozen=True)
class StatusColors:
    """One colour per git status (porcelain X/Y letter)."""

    modified: str
    added: str
    deleted: str
    renamed: str
    copied: str
    type_changed: str
    conflict: str
    untracked: str

    def for_code(self, code: str) -> str | None:
        """Colour for a porcelain status letter, or None for an unknown one."""
        attr = _STATUS_CODES.get(code)
        return getattr(self, attr) if attr else None


_STATUS_CODES = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "T": "type_changed",
    "U": "conflict",
    "?": "untracked",
}


class FontKind(StrEnum):
    """A kind of text with its own size; the value names its ThemeFonts field."""

    UI = "ui"  # buttons, labels, fields, text boxes: the application font
    MENU = "menu"
    STATUS_BAR = "status_bar"
    LIST = "list"  # item views: History, the file lists, Branches
    COLUMN_HEADER = "column_header"
    TAB = "tab"
    TOOLTIP = "tooltip"
    SECTION_HEADER = "section_header"
    SEGMENT_HEADER = "segment_header"  # a segment's strip inside a panel
    MONO = "mono"  # the diff, SHAs


@dataclass(frozen=True)
class ThemeFonts:
    """Point sizes per kind of text: None is ``default_point_size``; a theme sets one
    only to override that kind."""

    default_point_size: float = 10
    mono_family: str = "monospace"
    ui_point_size: float | None = None
    menu_point_size: float | None = None
    status_bar_point_size: float | None = None
    list_point_size: float | None = None
    column_header_point_size: float | None = None
    tab_point_size: float | None = None
    tooltip_point_size: float | None = None
    section_header_point_size: float | None = None
    segment_header_point_size: float | None = None
    mono_point_size: float | None = None

    def point_size(self, kind: FontKind) -> float:
        size = getattr(self, f"{kind.value}_point_size")
        return self.default_point_size if size is None else size


@dataclass(frozen=True)
class ThemeMetrics:
    """Sizes used in more than one place, or that another theme would change."""

    splitter_width: int = 1
    scrollbar_width: int = 10
    row_padding: int = 2
    row_min_height: int = 18


@dataclass(frozen=True)
class AppTheme:
    name: str
    colors: ThemeColors
    status: StatusColors
    fonts: ThemeFonts = field(default_factory=ThemeFonts)
    metrics: ThemeMetrics = field(default_factory=ThemeMetrics)
    dark: bool = False  # the colour scheme Qt is told, so its own drawing matches (§7.3)

    def status_color(self, code: str) -> str:
        """Colour for a porcelain status letter, falling back to plain text."""
        return self.status.for_code(code) or self.colors.text

    def stylesheet(self) -> str:
        return build_stylesheet(self)

    def palette(self) -> QPalette:
        c = self.colors
        p = QPalette()
        role = QPalette.ColorRole
        for r, value in (
            (role.Window, c.toolbar_bg),
            (role.WindowText, c.text),
            (role.Base, c.content_bg),
            (role.AlternateBase, c.sidebar_bg),
            (role.Text, c.text),
            (role.PlaceholderText, c.placeholder),
            (role.Button, c.button_bg),
            (role.ButtonText, c.text),
            (role.Highlight, c.text_selection),
            (role.HighlightedText, c.text),
            (role.ToolTipBase, c.tooltip_bg),
            (role.ToolTipText, c.text),
            (role.Link, c.accent),
        ):
            p.setColor(r, QColor(value))
        for r in (role.Text, role.WindowText, role.ButtonText):
            p.setColor(QPalette.ColorGroup.Disabled, r, QColor(c.text_muted))
        if self.dark:
            # Qt's defaults for the bevel roles are a light theme's: Fusion would
            # draw light frames and arrows. Shade them from the button colour.
            button = QColor(c.button_bg)
            for r, value in (
                (role.Light, button.lighter(150)),
                (role.Midlight, button.lighter(125)),
                (role.Mid, button.darker(120)),
                (role.Dark, button.darker(150)),
                (role.Shadow, QColor(c.sidebar_bg).darker(200)),
            ):
                p.setColor(r, value)
        return p

    def mono_font(self) -> QFont:
        font = QFont(self.fonts.mono_family)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSizeF(self.fonts.point_size(FontKind.MONO))
        return font


LIGHT_THEME = AppTheme(
    name="Light",
    colors=ThemeColors(
        menubar_bg="#33363a",
        menubar_text="#e6e6e6",
        menubar_hover="#4a4e54",
        toolbar_bg="#d9d9d9",
        tab_bg="#dedede",
        header_bg="#dedede",  # same as tab_bg: a header and a tab bar look alike
        sidebar_bg="#e9e9e9",
        content_bg="#ffffff",
        border="#c6c6c6",
        border_light="#e4e4e4",
        tree_guide="#cdcdcd",
        column_header_bg="#f3f3f3",  # between the lists' white and grey, lighter than header_bg
        column_header_text="#2b2b2b",
        column_header_text_hover="#000000",
        column_header_separator="#c6c6c6",
        column_header_border="#c6c6c6",
        text="#2b2b2b",
        text_muted="#8a8a8a",
        placeholder="#a8a8a8",
        warning="#c0640a",
        hover="#dcdcdc",
        content_hover="#f1f5fa",
        selection="#cbcbcb",
        content_selection="#bcd3ec",
        text_selection="#b5d0f0",
        accent="#4a7fc1",
        accent_text="#ffffff",
        button_bg="#d2d2d2",
        button_hover="#c6c6c6",
        button_pressed="#b9b9b9",
        scroll_handle="#c4c4c4",
        scroll_handle_hover="#a9a9a9",
        tooltip_bg="#fbfbfb",
        diff_added_bg="#c8efc0",
        diff_added_word="#9fe08f",
        diff_removed_bg="#f2c4c4",
        diff_removed_word="#e79a9a",
        diff_added_selected_bg="#b0e6a5",
        diff_removed_selected_bg="#ecaeae",
        diff_filler_bg="#f0f0f0",
        diff_gutter_text="#9a9a9a",
        diff_hunk_bg="#e8eef6",
        diff_hunk_text="#5a6f8a",
        diff_line_button="#21803d",
        diff_line_button_hover="#1a6a32",
        diff_line_button_border="#145226",  # dark green: white would vanish on the light gutter
        diff_line_button_text="#ffffff",
    ),
    status=StatusColors(
        modified="#4a7fc1",
        added="#3d9a3d",
        deleted="#c44b4b",
        renamed="#8a5cc2",
        copied="#8a5cc2",
        type_changed="#4a7fc1",
        conflict="#d9822b",
        untracked="#3a9a9a",
    ),
)


DARK_THEME = AppTheme(
    name="Dark",
    dark=True,
    colors=ThemeColors(
        menubar_bg="#24252b",  # the sidebar's: the menu bar blends into the window
        menubar_text="#eaeaf2",
        menubar_hover="#363840",
        toolbar_bg="#24252b",
        tab_bg="#363840",
        header_bg="#363840",  # same as tab_bg, as in the light theme
        sidebar_bg="#24252b",
        content_bg="#2c2d34",
        border="#41424d",
        border_light="#36373f",
        tree_guide="#41424d",
        column_header_bg="#303139",  # between the sidebar and content, darker than header_bg
        column_header_text="#eaeaf2",
        column_header_text_hover="#ffffff",
        column_header_separator="#5b5d6a",  # a step lighter than border: a 1 px line needs ~2:1
        column_header_border="#41424d",
        text="#eaeaf2",
        text_muted="#aaabb1",
        placeholder="#757783",
        warning="#f0bf00",
        hover="#303139",
        content_hover="#34353d",
        selection="#44454e",
        content_selection="#44454e",
        text_selection="#2f4a6e",
        accent="#4ea7fc",
        accent_text="#111216",  # dark on the light blue: white would be hard to read
        button_bg="#363840",
        button_hover="#41424d",
        button_pressed="#4b4d59",
        scroll_handle="#44454e",
        scroll_handle_hover="#5b5d6a",
        tooltip_bg="#363840",
        diff_added_bg="#253a2f",
        diff_added_word="#2f5c42",
        diff_removed_bg="#43272b",
        diff_removed_word="#6e333b",
        diff_added_selected_bg="#2d4b3a",
        diff_removed_selected_bg="#552f35",
        diff_filler_bg="#27282e",
        diff_gutter_text="#757783",
        diff_hunk_bg="#2f3447",
        diff_hunk_text="#93a6cc",
        diff_line_button="#2b8452",
        diff_line_button_hover="#33985f",
        diff_line_button_border="#ffffff",
        diff_line_button_text="#ffffff",
    ),
    status=StatusColors(
        modified="#4ea7fc",
        added="#4cb782",
        deleted="#f27474",
        renamed="#bb87fc",
        copied="#bb87fc",
        type_changed="#4ea7fc",
        conflict="#f0bf00",
        untracked="#2ebdc9",
    ),
)


# Colour-blind friendly variants (RZG-33): the base theme with the colours that tell kinds
# apart swapped for blue and orange (Okabe–Ito) in place of green and red. Every other
# token is the base's. Checked under simulated deuteranopia, protanopia and tritanopia.

def _colour_blind_variant(base: AppTheme, status: StatusColors, **colors: str) -> AppTheme:
    return dataclasses.replace(base, name=f"{base.name}, colour-blind", status=status,
                               colors=dataclasses.replace(base.colors, **colors))


LIGHT_CB_THEME = _colour_blind_variant(
    LIGHT_THEME,
    StatusColors(
        modified="#8f6a00",  # dark gold
        added="#0072b2",  # blue
        deleted="#b84c00",  # vermillion
        renamed="#8250df",  # violet
        copied="#8250df",
        type_changed="#8f6a00",
        conflict="#7a1010",  # dark red: apart from gold and violet by lightness
        untracked="#007079",
    ),
    warning="#b84c00",
    diff_added_bg="#c6e1fb",
    diff_added_word="#9fc8f2",
    diff_added_selected_bg="#b0d4f7",
    diff_removed_bg="#fbe0c4",
    diff_removed_word="#f5bd85",
    diff_removed_selected_bg="#f7cc9f",
    diff_line_button="#0a6ab0",
    diff_line_button_hover="#08568f",
    diff_line_button_border="#063e68",
)


DARK_CB_THEME = _colour_blind_variant(
    DARK_THEME,
    StatusColors(
        modified="#f0e442",  # yellow
        added="#56b4e9",  # sky blue
        deleted="#e69f00",  # orange
        renamed="#c58cff",  # violet
        copied="#c58cff",
        type_changed="#f0e442",
        conflict="#ff7a45",  # vermillion
        untracked="#2ebdc9",
    ),
    warning="#f0e442",
    diff_added_bg="#12364e",
    diff_added_word="#1d5479",
    diff_added_selected_bg="#18435f",
    diff_removed_bg="#42301c",
    diff_removed_word="#6b4a1f",
    diff_removed_selected_bg="#533c22",
    diff_line_button="#2a73b8",
    diff_line_button_hover="#3080c4",
)


def colour_blind_theme(base: AppTheme) -> AppTheme:
    """The colour-blind variant of a base theme; any other theme is returned as is."""
    for plain, variant in ((LIGHT_THEME, LIGHT_CB_THEME), (DARK_THEME, DARK_CB_THEME)):
        if base == plain:
            return variant
    return base
