"""The theme model (spec §7.1): the only place colours, fonts and metrics live."""

from __future__ import annotations

from dataclasses import dataclass, field

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
    diff_filler_bg: str
    diff_gutter_text: str
    diff_hunk_bg: str  # the "@@ -1,4 +1,5 @@" row
    diff_hunk_text: str


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


@dataclass(frozen=True)
class ThemeFonts:
    ui_point_size: float = 9
    mono_family: str = "monospace"
    header_point_size: float = 8


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
        font.setPointSizeF(self.fonts.ui_point_size)
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
        diff_filler_bg="#f0f0f0",
        diff_gutter_text="#9a9a9a",
        diff_hunk_bg="#e8eef6",
        diff_hunk_text="#5a6f8a",
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
        diff_filler_bg="#27282e",
        diff_gutter_text="#757783",
        diff_hunk_bg="#2f3447",
        diff_hunk_text="#93a6cc",
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
