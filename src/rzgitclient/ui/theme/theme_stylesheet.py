"""Builds the app-wide Qt stylesheet from an AppTheme (spec §7.1).

Each ``$name`` is a field of the theme's colours, fonts or metrics. Surfaces and
roles (style_roles.py) are matched as dynamic properties, e.g.
``QWidget[surface="sidebar"]``.
"""

from __future__ import annotations

from dataclasses import asdict
from string import Template
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rzgitclient.ui.theme.app_theme import AppTheme

_TEMPLATE = Template(
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
QWidget[surface="chrome"] {
    background: $toolbar_bg;
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
QToolButton:disabled {
    color: $text_muted;
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
    color: $accent_text;
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
QPlainTextEdit[role="diff-view"] {
    border: none;
    padding: 0;
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
    width: ${splitter_width}px;
}
QSplitter::handle:vertical {
    height: ${splitter_width}px;
}
QWidget[surface="sidebar"] {
    background: $sidebar_bg;
}
QWidget[surface="content"] {
    background: $content_bg;
}
QLabel[role="placeholder"] {
    color: $text_muted;
}
QLabel[role="section-header"] {
    font-weight: bold;
    font-size: ${header_point_size}pt;
    padding: 7px 8px 4px 8px;
}

/* ---- lists, trees & tables ------------------------------------------ */
QListView, QTreeView, QTableView {
    border: none;
    outline: 0;
    background: $content_bg;
    show-decoration-selected: 1;
}
QWidget[surface="sidebar"] QListView, QWidget[surface="sidebar"] QTreeView,
QWidget[surface="sidebar"] QTableView {
    background: $sidebar_bg;
}
QListView::item, QTreeView::item, QTableView::item {
    padding: ${row_padding}px 4px;
    min-height: ${row_min_height}px;
    border: none;
}
QListView::item:hover:!selected, QTreeView::item:hover:!selected,
QTableView::item:hover:!selected {
    background: $hover;
}
QListView::item:selected, QTreeView::item:selected, QTableView::item:selected {
    background: $selection;
    color: $text;
}
QWidget[surface="content"] QTreeView::item:hover:!selected,
QWidget[surface="content"] QTableView::item:hover:!selected {
    background: $content_hover;
}
QWidget[surface="content"] QTreeView::item:selected,
QWidget[surface="content"] QTableView::item:selected {
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
QWidget[surface="sidebar"] QHeaderView, QWidget[surface="sidebar"] QHeaderView::section {
    background: $sidebar_bg;
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
    width: ${scrollbar_width}px;
    margin: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: ${scrollbar_width}px;
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
)


def template_values(theme: AppTheme) -> dict[str, str]:
    """The ``$name`` values: the colour, font and metric fields combined.

    Field names must be unique across the three groups (checked by a test).
    """
    values: dict[str, str] = {}
    for group in (theme.colors, theme.fonts, theme.metrics):
        for name, value in asdict(group).items():
            values[name] = format(value, "g") if isinstance(value, float) else str(value)
    return values


def build_stylesheet(theme: AppTheme) -> str:
    return _TEMPLATE.substitute(template_values(theme))
