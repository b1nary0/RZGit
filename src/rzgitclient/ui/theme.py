"""Application look: a flat, light, low-chrome theme.

All colours live in ``COLORS`` so widgets (and the future diff view) can
reference the same tokens the stylesheet uses.
"""

from __future__ import annotations

from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

COLORS = {
    # chrome
    "menubar_bg": "#33363a",
    "menubar_text": "#e6e6e6",
    "menubar_hover": "#4a4e54",
    "toolbar_bg": "#d9d9d9",
    "tab_bg": "#dedede",
    "sidebar_bg": "#e9e9e9",
    "content_bg": "#ffffff",
    "border": "#c6c6c6",
    "border_light": "#e4e4e4",
    # text
    "text": "#2b2b2b",
    "text_muted": "#8a8a8a",
    "placeholder": "#a8a8a8",
    # interaction
    "hover": "#dcdcdc",
    "selection": "#cbcbcb",
    "content_selection": "#bcd3ec",
    "text_selection": "#b5d0f0",
    "accent": "#4a7fc1",
    "button_bg": "#d2d2d2",
    "button_hover": "#c6c6c6",
    "button_pressed": "#b9b9b9",
    "scroll_handle": "#c4c4c4",
    "scroll_handle_hover": "#a9a9a9",
    "tooltip_bg": "#fbfbfb",
    # diff (for the upcoming diff view)
    "diff_added_bg": "#c8efc0",
    "diff_added_word": "#9fe08f",
    "diff_removed_bg": "#f2c4c4",
    "diff_removed_word": "#e79a9a",
    "diff_filler_bg": "#f0f0f0",
    "diff_gutter_text": "#9a9a9a",
}

# Colours for git status codes (porcelain X/Y letters).
STATUS_COLORS = {
    "M": "#4a7fc1",  # modified
    "A": "#3d9a3d",  # added
    "D": "#c44b4b",  # deleted
    "R": "#8a5cc2",  # renamed
    "C": "#8a5cc2",  # copied
    "T": "#4a7fc1",  # type change
    "U": "#d9822b",  # conflict
    "?": "#3a9a9a",  # untracked
}

_STYLESHEET = Template(
    """
QWidget {
    color: $text;
}
QMainWindow, QDialog {
    background: $toolbar_bg;
}

/* ---- menus ---------------------------------------------------------- */
QMenuBar {
    background: $menubar_bg;
    color: $menubar_text;
    padding: 1px 2px;
}
QMenuBar::item {
    background: transparent;
    padding: 4px 8px;
}
QMenuBar::item:selected, QMenuBar::item:pressed {
    background: $menubar_hover;
}
QMenu {
    background: $content_bg;
    border: 1px solid $border;
    padding: 4px 0;
}
QMenu::item {
    padding: 5px 28px 5px 24px;
}
QMenu::item:selected {
    background: $selection;
}
QMenu::item:disabled {
    color: $text_muted;
}
QMenu::separator {
    height: 1px;
    background: $border_light;
    margin: 4px 0;
}

/* ---- toolbar & buttons --------------------------------------------- */
QToolBar {
    background: $toolbar_bg;
    border: none;
    border-bottom: 1px solid $border;
    padding: 3px 6px;
    spacing: 2px;
}
QToolBar::separator {
    background: $border;
    width: 1px;
    margin: 4px 6px;
}
QToolButton {
    background: transparent;
    border: none;
    padding: 4px;
}
QToolButton:hover {
    background: $button_hover;
}
QToolButton:pressed, QToolButton:checked {
    background: $button_pressed;
}
QPushButton {
    background: $button_bg;
    border: none;
    padding: 5px 18px;
    min-height: 16px;
}
QPushButton:hover {
    background: $button_hover;
}
QPushButton:pressed {
    background: $button_pressed;
}
QPushButton:disabled {
    color: $text_muted;
}
QPushButton:default {
    background: $accent;
    color: #ffffff;
}

/* ---- text inputs ---------------------------------------------------- */
QLineEdit, QTextEdit, QPlainTextEdit {
    background: $content_bg;
    border: 1px solid $border;
    padding: 3px 5px;
    selection-background-color: $text_selection;
    selection-color: $text;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border-color: $accent;
}
QComboBox {
    background: $content_bg;
    border: 1px solid $border;
    padding: 3px 6px;
}
QComboBox::drop-down {
    border: none;
    width: 16px;
}
QComboBox QAbstractItemView {
    background: $content_bg;
    border: 1px solid $border;
    selection-background-color: $selection;
    selection-color: $text;
}

/* ---- panes ---------------------------------------------------------- */
QSplitter::handle {
    background: $border;
}
QSplitter::handle:horizontal {
    width: 1px;
}
QSplitter::handle:vertical {
    height: 1px;
}
QWidget#sidebar {
    background: $sidebar_bg;
}
QWidget#content {
    background: $content_bg;
}
QLabel#sectionHeader {
    font-weight: bold;
    font-size: 8pt;
    padding: 7px 8px 4px 8px;
}

/* ---- lists & trees -------------------------------------------------- */
QListView, QTreeView {
    border: none;
    outline: 0;
    background: $content_bg;
    show-decoration-selected: 1;
}
QWidget#sidebar QListView, QWidget#sidebar QTreeView {
    background: $sidebar_bg;
}
QListView::item, QTreeView::item {
    padding: 2px 4px;
    min-height: 18px;
    border: none;
}
QListView::item:hover:!selected, QTreeView::item:hover:!selected {
    background: $hover;
}
QListView::item:selected, QTreeView::item:selected {
    background: $selection;
    color: $text;
}
QWidget#content QTreeView::item:hover:!selected {
    background: #f1f5fa;
}
QWidget#content QTreeView::item:selected {
    background: $content_selection;
}
QHeaderView {
    background: $content_bg;
    border: none;
}
QHeaderView::section {
    background: $content_bg;
    color: $text_muted;
    border: none;
    border-bottom: 1px solid $border_light;
    padding: 3px 6px;
}
QHeaderView::section:hover {
    color: $text;
}

/* ---- tabs ----------------------------------------------------------- */
QTabWidget::pane {
    border: none;
    background: $content_bg;
}
QTabBar {
    background: $tab_bg;
}
QTabBar::tab {
    background: $tab_bg;
    border: none;
    border-right: 1px solid $border;
    padding: 5px 14px;
}
QTabBar::tab:hover:!selected {
    background: $hover;
}
QTabBar::tab:selected {
    background: $content_bg;
}

/* ---- scrollbars ----------------------------------------------------- */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: $scroll_handle;
    min-height: 32px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: $scroll_handle;
    min-width: 32px;
    margin: 2px;
}
QScrollBar::handle:hover {
    background: $scroll_handle_hover;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: none;
}

/* ---- misc ----------------------------------------------------------- */
QStatusBar {
    background: $toolbar_bg;
    color: $text_muted;
    border-top: 1px solid $border;
}
QStatusBar QLabel {
    color: $text_muted;
}
QToolTip {
    background: $tooltip_bg;
    color: $text;
    border: 1px solid $border;
    padding: 4px;
}
"""
).substitute(COLORS)


def _palette() -> QPalette:
    c = {k: QColor(v) for k, v in COLORS.items()}
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, c["toolbar_bg"])
    p.setColor(QPalette.ColorRole.WindowText, c["text"])
    p.setColor(QPalette.ColorRole.Base, c["content_bg"])
    p.setColor(QPalette.ColorRole.AlternateBase, c["sidebar_bg"])
    p.setColor(QPalette.ColorRole.Text, c["text"])
    p.setColor(QPalette.ColorRole.PlaceholderText, c["placeholder"])
    p.setColor(QPalette.ColorRole.Button, c["button_bg"])
    p.setColor(QPalette.ColorRole.ButtonText, c["text"])
    p.setColor(QPalette.ColorRole.Highlight, c["text_selection"])
    p.setColor(QPalette.ColorRole.HighlightedText, c["text"])
    p.setColor(QPalette.ColorRole.ToolTipBase, c["tooltip_bg"])
    p.setColor(QPalette.ColorRole.ToolTipText, c["text"])
    p.setColor(QPalette.ColorRole.Link, c["accent"])
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, c["text_muted"])
    return p


def monospace_font() -> QFont:
    font = QFont("monospace")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSizeF(QApplication.font().pointSizeF())
    return font


def apply_theme(app: QApplication) -> None:
    # Fusion is a neutral base that honours stylesheets fully; without it the
    # desktop style (e.g. Breeze) would fight with the custom look.
    app.setStyle("Fusion")
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt >= 6.8
        hints.setColorScheme(Qt.ColorScheme.Light)
    app.setPalette(_palette())

    font = app.font()
    font.setPointSizeF(9)
    app.setFont(font)

    app.setStyleSheet(_STYLESHEET)
