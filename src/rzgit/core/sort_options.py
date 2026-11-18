"""A panel's sort orders and groupings (spec §5.1, RZG-43, RZG-53). Plain Python, no Qt.

A panel declares its orders as a ``SortOptions``: the first option is the
natural order (git's or the app's own, no key function): the way back, and the
default unless ``default_key`` names another. ``apply`` is a stable sort, so ties
keep the natural order.
``groups`` are its groupings: ``grouped`` sorts, then puts the rows under
their groups, in the grouping's order. ``SortMenu`` (ui/widgets) is the menu
and column labels that choose them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class SortOrder:
    """What a panel is sorted by: an option's key, and whether it is reversed
    (relative to that option's default direction)."""

    key: str
    reversed: bool = False
    group: str | None = None  # a GroupOption's key; None: not grouped (RZG-53)


@dataclass(frozen=True)
class SortOption(Generic[T]):
    key: str  # saved in SortPrefs: "name"
    label: str  # in the Sort By menu: "Name"
    sort_key: Callable[[T], Any] | None = None  # None: the natural order
    descending: bool = False  # its default direction (newest first)
    column: str | None = None  # the column label that picks it


@dataclass(frozen=True)
class GroupOption(Generic[T]):
    """A way to put a panel's rows under headings (RZG-53)."""

    key: str  # saved in SortPrefs: "status"
    label: str  # in the Group By menu: "Status"
    group_of: Callable[[T], str]  # an item's group: the heading's name
    order: tuple[str, ...] = ()  # these groups first, in this order; then the rest by name
    last: tuple[str, ...] = ()  # these groups after all others ("Other")

    def rank(self, name: str) -> tuple:
        """Where the group ``name`` comes among the others."""
        if name in self.order:
            return (0, self.order.index(name))
        if name in self.last:
            return (2, self.last.index(name))
        return (1, name.casefold(), name)


@dataclass(frozen=True)
class Group(Generic[T]):
    """One heading's rows: its name, and its items in the panel's order."""

    key: str  # the grouping's key, so the same name under two groupings differs
    name: str
    items: list = field(default_factory=list)


@dataclass(frozen=True)
class SortOptions(Generic[T]):
    options: tuple[SortOption[T], ...]  # the first is the natural order
    first: Callable[[T], bool] | None = None  # rows kept at the top in every order
    default_key: str | None = None  # the default option; None: the natural order
    groups: tuple[GroupOption[T], ...] = ()  # the groupings (RZG-53); none: not groupable

    def __post_init__(self) -> None:
        if not self.options or self.options[0].sort_key is not None:
            raise ValueError("the first option must be the natural order (no sort_key)")
        keys = [o.key for o in self.options]
        if len(set(keys)) != len(keys):
            raise ValueError(f"duplicate sort keys: {keys}")
        if self.default_key is not None and self.default_key not in keys:
            raise ValueError(f"unknown default sort key: {self.default_key!r}")
        group_keys = [g.key for g in self.groups]
        if len(set(group_keys)) != len(group_keys):
            raise ValueError(f"duplicate group keys: {group_keys}")

    @property
    def natural(self) -> SortOption[T]:
        return self.options[0]

    @property
    def default(self) -> SortOption[T]:
        """What a panel is sorted by until the user picks another order."""
        return next((o for o in self.options if o.key == self.default_key), self.natural)

    def option(self, key: str) -> SortOption[T]:
        """The option saved as ``key``; the default for an unknown one."""
        return next((o for o in self.options if o.key == key), self.default)

    def for_column(self, label: str) -> SortOption[T] | None:
        return next((o for o in self.options if o.column == label), None)

    def grouping(self, key: str | None) -> GroupOption[T] | None:
        """The grouping saved as ``key``; None for none or an unknown one."""
        return next((g for g in self.groups if g.key == key), None)

    def resolve(self, order: SortOrder | None) -> SortOrder:
        """``order`` with known keys: the default when it's None or its key is
        unknown, and no grouping when its group is unknown."""
        if order is None:
            return SortOrder(self.default.key)
        group = order.group if self.grouping(order.group) is not None else None
        if self.option(order.key).key != order.key:
            return SortOrder(self.default.key, group=group)
        return order if group == order.group else replace(order, group=group)

    def descending(self, order: SortOrder) -> bool:
        """Whether ``order`` runs against its option's values (Z to A, oldest first)."""
        return self.option(order.key).descending != order.reversed

    def apply(self, items: Iterable[Any], order: SortOrder,
              of: Callable[[Any], T] | None = None) -> list[Any]:
        """``items`` in ``order``: stable, so ties keep the given (natural) order.
        ``of`` gives the value the keys read, when the items wrap them."""
        items = list(items)
        option = self.option(order.key)
        descending = self.descending(order)
        value = of or (lambda item: item)
        if option.sort_key is None:
            result = items[::-1] if descending else items
        else:
            sort_key = option.sort_key
            result = sorted(items, key=lambda item: sort_key(value(item)), reverse=descending)
        if self.first is not None:
            first = self.first
            result = ([i for i in result if first(value(i))]
                      + [i for i in result if not first(value(i))])
        return result

    def grouped(self, items: Iterable[Any], order: SortOrder,
                of: Callable[[Any], T] | None = None) -> list[Group[T]] | None:
        """``items`` in ``order``, under their groups in the grouping's order (none
        empty); None when ``order`` isn't grouped. The sort applies inside each group."""
        grouping = self.grouping(order.group)
        if grouping is None:
            return None
        value = of or (lambda item: item)
        groups: dict[str, Group[T]] = {}
        for item in self.apply(items, order, of):
            name = grouping.group_of(value(item))
            groups.setdefault(name, Group(grouping.key, name)).items.append(item)
        return sorted(groups.values(), key=lambda g: grouping.rank(g.name))
