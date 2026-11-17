"""A panel's sort orders (spec §5.1, RZG-43). Plain Python, no Qt.

A panel declares its orders as a ``SortOptions``: the first option is the
natural order (git's or the app's own, no key function): the way back, and the
default unless ``default_key`` names another. ``apply`` is a stable sort, so ties
keep the natural order.
``SortMenu`` (ui/widgets) is the menu and column labels that choose one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class SortOrder:
    """What a panel is sorted by: an option's key, and whether it is reversed
    (relative to that option's default direction)."""

    key: str
    reversed: bool = False


@dataclass(frozen=True)
class SortOption(Generic[T]):
    key: str  # saved in SortPrefs: "name"
    label: str  # in the Sort By menu: "Name"
    sort_key: Callable[[T], Any] | None = None  # None: the natural order
    descending: bool = False  # its default direction (newest first)
    column: str | None = None  # the column label that picks it


@dataclass(frozen=True)
class SortOptions(Generic[T]):
    options: tuple[SortOption[T], ...]  # the first is the natural order
    first: Callable[[T], bool] | None = None  # rows kept at the top in every order
    default_key: str | None = None  # the default option; None: the natural order

    def __post_init__(self) -> None:
        if not self.options or self.options[0].sort_key is not None:
            raise ValueError("the first option must be the natural order (no sort_key)")
        keys = [o.key for o in self.options]
        if len(set(keys)) != len(keys):
            raise ValueError(f"duplicate sort keys: {keys}")
        if self.default_key is not None and self.default_key not in keys:
            raise ValueError(f"unknown default sort key: {self.default_key!r}")

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

    def resolve(self, order: SortOrder | None) -> SortOrder:
        """``order`` with a known key: the default when it's None or unknown."""
        if order is None or self.option(order.key).key != order.key:
            return SortOrder(self.default.key)
        return order

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
