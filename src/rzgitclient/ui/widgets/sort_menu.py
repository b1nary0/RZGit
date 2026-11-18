"""A panel's Sort By and Group By menus and its sort order (spec §5.1, RZG-43, RZG-53).

The options of the panel's ``SortOptions`` as radio items, a separator, then
Reversed. A panel with groupings also has ``group_menu``: None, then its
groupings. The panel puts ``header_actions()`` in its header actions and
renders with ``apply`` (or ``grouped``) whenever ``order_changed`` is emitted.
The order is read from ``SortPrefs`` when the menu is made and saved there on
each change. ``attach`` makes a ``ColumnHeader``'s labels choose the options
of their columns.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QWidget

from rzgitclient.core.sort_options import Group, SortOptions, SortOrder
from rzgitclient.core.sort_prefs import SortPrefs
from rzgitclient.ui.widgets.column_header import ColumnHeader

SORT_BY = "Sort &By"
REVERSED = "&Reversed"
GROUP_BY = "&Group By"
NO_GROUPS = "&None"


class SortMenu(QMenu):
    order_changed = Signal()  # the panel renders again with apply()

    def __init__(self, panel_id: str, options: SortOptions, prefs: SortPrefs,
                 parent: QWidget | None = None):
        super().__init__(SORT_BY, parent)
        self.panel_id, self.options, self._prefs = panel_id, options, prefs
        self._order = options.resolve(prefs.order(panel_id))
        self._headers: list[ColumnHeader] = []
        self._group = QActionGroup(self)
        self._group.setExclusive(True)
        self._actions: dict[str, QAction] = {}
        for option in options.options:
            action = self.addAction(option.label)
            action.setCheckable(True)
            self._group.addAction(action)
            action.triggered.connect(lambda _=False, key=option.key: self.choose(key))
            self._actions[option.key] = action
        self.addSeparator()
        self.reversed_action = self.addAction(REVERSED)
        self.reversed_action.setCheckable(True)
        self.reversed_action.triggered.connect(self.set_reversed)
        self.group_menu: QMenu | None = None
        self._group_actions: dict[str | None, QAction] = {}
        if options.groups:
            self.group_menu = QMenu(GROUP_BY, self)
            groups = QActionGroup(self.group_menu)
            groups.setExclusive(True)
            choices = [(None, NO_GROUPS)] + [(g.key, g.label) for g in options.groups]
            for key, label in choices:
                action = self.group_menu.addAction(label)
                action.setCheckable(True)
                groups.addAction(action)
                action.triggered.connect(lambda _=False, key=key: self.set_group(key))
                self._group_actions[key] = action
                if key is None:
                    self.group_menu.addSeparator()
        self._show_order()

    @property
    def order(self) -> SortOrder:
        return self._order

    def set_order(self, order: SortOrder) -> None:
        """Sort by ``order`` (an unknown key: the natural order); saved at once."""
        order = self.options.resolve(order)
        if order == self._order:
            return
        self._order = order
        default = order == SortOrder(self.options.default.key)
        self._prefs.set_order(self.panel_id, None if default else order)
        self._show_order()
        self.order_changed.emit()

    def choose(self, key: str) -> None:
        """Sort by the option ``key``, in its default direction; the grouping stays."""
        self.set_order(SortOrder(key, group=self._order.group))

    def set_reversed(self, on: bool) -> None:
        self.set_order(replace(self._order, reversed=bool(on)))

    def set_group(self, key: str | None) -> None:
        """Group by the grouping ``key`` (None: not grouped); the sort stays."""
        self.set_order(replace(self._order, group=key))

    def header_actions(self) -> list[QAction]:
        """What the panel puts last in its header menu: Group By (if it has
        groupings), then Sort By."""
        group = [self.group_menu.menuAction()] if self.group_menu is not None else []
        return group + [self.menuAction()]

    def apply(self, items: Iterable[Any], of: Callable[[Any], Any] | None = None) -> list[Any]:
        """``items`` in the current order (``SortOptions.apply``)."""
        return self.options.apply(items, self._order, of)

    def grouped(self, items: Iterable[Any],
                of: Callable[[Any], Any] | None = None) -> list[Group] | None:
        """``items`` under their groups, in the current order; None when not grouped."""
        return self.options.grouped(items, self._order, of)

    # ---- column labels ---------------------------------------------------------

    def attach(self, header: ColumnHeader) -> None:
        """Make ``header``'s labels of the options' columns sort: a click picks
        that option, again flips Reversed; the sorted one shows an arrow."""
        self._headers.append(header)
        header.set_sortable(o.column for o in self.options.options if o.column)
        header.sort_clicked.connect(self._on_label_clicked)
        header.destroyed.connect(lambda _=None, h=header: self._forget(h))
        self._show_order()

    def _forget(self, header: ColumnHeader) -> None:
        self._headers = [h for h in self._headers if h is not header]

    def _on_label_clicked(self, label: str) -> None:
        option = self.options.for_column(label)
        if option is None:
            return
        if option.key == self._order.key:
            self.set_reversed(not self._order.reversed)
        else:
            self.choose(option.key)

    def _show_order(self) -> None:
        self._actions[self._order.key].setChecked(True)
        self.reversed_action.setChecked(self._order.reversed)
        if self._group_actions:
            self._group_actions[self._order.group].setChecked(True)
        column = self.options.option(self._order.key).column
        descending = self.options.descending(self._order)
        for header in self._headers:
            header.set_sort_indicator(column, descending)
