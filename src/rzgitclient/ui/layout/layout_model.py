"""The window layout as plain, immutable data (spec §6.1).

No Qt here: ``LayoutHost`` turns a tree into widgets. An edit returns a new tree.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

FORMAT_VERSION = 1


class LayoutError(ValueError):
    """A saved layout is structurally wrong. Callers fall back to the default."""


class Orientation(StrEnum):
    HORIZONTAL = "horizontal"  # children side by side
    VERTICAL = "vertical"  # children stacked


@dataclass(frozen=True)
class PanelNode:
    panel_id: str


@dataclass(frozen=True)
class SplitNode:
    orientation: Orientation
    children: tuple[LayoutNode, ...]
    sizes: tuple[int, ...] | None = None  # one per child; None = share evenly


@dataclass(frozen=True)
class TabsNode:
    children: tuple[PanelNode, ...]
    current: int = 0


LayoutNode = PanelNode | SplitNode | TabsNode


def panel_ids(node: LayoutNode | None) -> list[str]:
    """Every panel id in the tree, depth first."""
    match node:
        case PanelNode():
            return [node.panel_id]
        case SplitNode() | TabsNode():
            return [pid for child in node.children for pid in panel_ids(child)]
    return []


# ---- normalize --------------------------------------------------------------


def normalize(node: LayoutNode, known_ids: Collection[str]) -> LayoutNode | None:
    """Repair a tree so it can always be built.

    Drops unknown and duplicate panels (the first one wins), removes empty
    containers, collapses single-child splits, drops ``sizes`` that no longer
    fit and clamps ``TabsNode.current``. Returns ``None`` if nothing is left.
    """
    return _normalize(node, frozenset(known_ids), set())


def _normalize(node: LayoutNode, known: frozenset[str], seen: set[str]) -> LayoutNode | None:
    match node:
        case PanelNode(panel_id=pid):
            if pid not in known or pid in seen:
                return None
            seen.add(pid)
            return node
        case SplitNode():
            kids = tuple(k for c in node.children if (k := _normalize(c, known, seen)) is not None)
            if len(kids) <= 1:
                return kids[0] if kids else None
            sizes = node.sizes if len(kids) == len(node.children) else None
            return SplitNode(node.orientation, kids, _valid_sizes(sizes, len(kids)))
        case TabsNode():
            current_id = (
                node.children[node.current].panel_id
                if 0 <= node.current < len(node.children) else None
            )
            tabs = tuple(t for c in node.children if (t := _normalize(c, known, seen)) is not None)
            if not tabs:
                return None
            ids = [t.panel_id for t in tabs]
            current = (
                ids.index(current_id) if current_id in ids
                else min(max(node.current, 0), len(tabs) - 1)
            )
            return TabsNode(tabs, current)
    raise TypeError(f"not a layout node: {node!r}")


def _valid_sizes(sizes: Any, count: int) -> tuple[int, ...] | None:
    """``sizes`` as a tuple if it fits ``count`` children, else ``None``."""
    if (
        isinstance(sizes, (list, tuple))
        and len(sizes) == count
        and all(type(s) is int and s >= 0 for s in sizes)
    ):
        return tuple(sizes)
    return None


# ---- serialisation ----------------------------------------------------------


def to_dict(root: LayoutNode) -> dict[str, Any]:
    """JSON-ready form: ``{"version": 1, "root": {...}}``."""
    return {"version": FORMAT_VERSION, "root": _node_to_dict(root)}


def _node_to_dict(node: LayoutNode) -> dict[str, Any]:
    match node:
        case PanelNode():
            return {"type": "panel", "panel_id": node.panel_id}
        case SplitNode():
            data: dict[str, Any] = {
                "type": "split",
                "orientation": str(node.orientation),
                "children": [_node_to_dict(c) for c in node.children],
            }
            if node.sizes is not None:
                data["sizes"] = list(node.sizes)
            return data
        case TabsNode():
            return {
                "type": "tabs",
                "current": node.current,
                "children": [_node_to_dict(c) for c in node.children],
            }
    raise TypeError(f"not a layout node: {node!r}")


def from_dict(data: Any) -> LayoutNode:
    """Parse ``to_dict`` output. Raises ``LayoutError`` on structural errors.

    ``sizes`` that don't fit their children are dropped, not rejected. The
    result is not normalized: callers run ``normalize`` with the known ids.
    """
    if not isinstance(data, dict):
        raise LayoutError("layout is not an object")
    if data.get("version") != FORMAT_VERSION:
        raise LayoutError(f"unsupported layout version: {data.get('version')!r}")
    return _node_from_dict(_field(data, "root", dict))


def _node_from_dict(data: dict[str, Any]) -> LayoutNode:
    kind = data.get("type")
    if kind == "panel":
        return PanelNode(_field(data, "panel_id", str))
    if kind == "split":
        try:
            orientation = Orientation(_field(data, "orientation", str))
        except ValueError as exc:
            raise LayoutError(f"unknown orientation: {data['orientation']!r}") from exc
        children = tuple(_children(data))
        return SplitNode(orientation, children, _valid_sizes(data.get("sizes"), len(children)))
    if kind == "tabs":
        tabs = tuple(_children(data))
        if not all(isinstance(t, PanelNode) for t in tabs):
            raise LayoutError("tabs can only hold panels")
        current = data.get("current", 0)
        if type(current) is not int:
            raise LayoutError(f"tabs 'current' must be an integer, not {current!r}")
        return TabsNode(tabs, current)
    raise LayoutError(f"unknown layout node type: {kind!r}")


def _children(data: dict[str, Any]) -> list[LayoutNode]:
    items = _field(data, "children", list)
    if not all(isinstance(item, dict) for item in items):
        raise LayoutError("layout children must be objects")
    return [_node_from_dict(item) for item in items]


def _field(data: dict[str, Any], name: str, kind: type) -> Any:
    value = data.get(name)
    if not isinstance(value, kind):
        raise LayoutError(f"layout field {name!r} must be {kind.__name__}, not {value!r}")
    return value
