"""A panel's Sort By menu and its sort order (spec §5.1, RZG-43).

The options of the panel's ``SortOptions`` as radio items, a separator, then
Reversed. The panel puts ``menuAction()`` in its header actions and renders
with ``apply`` whenever ``order_changed`` is emitted. The order is read from
``SortPrefs`` when the menu is made and saved there on each change.
``attach`` makes a ``ColumnHeader``'s labels choose the options of their columns.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QWidget

from rzgitclient.core.sort_options import SortOptions, SortOrder
from rzgitclient.core.sort_prefs import SortPrefs
from rzgitclient.ui.widgets.column_header import ColumnHeader

SORT_BY = "Sort &By"
REVERSED = "&Reversed"


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
        """Sort by the option ``key``, in its default direction."""
        self.set_order(SortOrder(key))

    def set_reversed(self, on: bool) -> None:
        self.set_order(SortOrder(self._order.key, bool(on)))

    def apply(self, items: Iterable[Any], of: Callable[[Any], Any] | None = None) -> list[Any]:
        """``items`` in the current order (``SortOptions.apply``)."""
        return self.options.apply(items, self._order, of)

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
        column = self.options.option(self._order.key).column
        descending = self.options.descending(self._order)
        for header in self._headers:
            header.set_sort_indicator(column, descending)
