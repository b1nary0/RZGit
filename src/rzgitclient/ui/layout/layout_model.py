"""The window layout as plain, immutable data (spec §6.1).

No Qt here: ``LayoutHost`` turns a state into widgets. An edit returns a new
state or tree. The window has three areas (Controls, Sidebar, Main pane), each
holding a free tree of splits and tab groups.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

FORMAT_VERSION = 3  # 1: one tree, no "hidden"; 2: one tree (no areas)
_LEGACY_VERSIONS = (1, 2)


class LayoutError(ValueError):
    """A saved layout is structurally wrong. Callers fall back to the default."""


class Area(StrEnum):
    """Where panels are arranged (spec §6.1). Every layout has all three."""

    CONTROLS = "controls"  # the bar across the top
    SIDEBAR = "sidebar"
    MAIN = "main"  # the Main pane


class Arrangement(StrEnum):
    """How the Sidebar and the Main pane sit, below Controls."""

    SIDEBAR_LEFT = "sidebar_left"
    SIDEBAR_RIGHT = "sidebar_right"
    MAIN_TOP = "main_top"  # the Main pane above the Sidebar


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


@dataclass(frozen=True)
class Anchor:
    """Where a hidden panel sat, so it can come back there (spec §6.6)."""

    neighbors: tuple[str, ...]  # the panels of the node it sat beside; () alone in its area
    orientation: Orientation | None  # the split it was in; None: a tab beside them
    before: bool  # left of / above / the tab before them
    share: float | None = None  # its part of the space the two had
    area: Area | None = None  # the area it was in; None: its default area


@dataclass(frozen=True)
class LayoutState:
    """What's saved: each area's tree (None: empty), how the areas sit, the
    split between the Sidebar and the Main pane (Sidebar first; None: even), and
    the hidden panels with their anchors.

    ``areas`` always holds every ``Area``: missing ones are filled in as empty.
    """

    areas: Mapping[Area, LayoutNode | None] = field(default_factory=dict)
    arrangement: Arrangement = Arrangement.SIDEBAR_LEFT
    sizes: tuple[int, int] | None = None
    hidden: Mapping[str, Anchor] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not all(isinstance(a, Area) for a in self.areas):
            raise TypeError(f"not areas: {list(self.areas)!r}")
        areas = {a: self.areas.get(a) for a in Area}
        object.__setattr__(self, "areas", MappingProxyType(areas))
        object.__setattr__(self, "hidden", MappingProxyType(dict(self.hidden)))

    def tree(self, area: Area) -> LayoutNode | None:
        return self.areas[area]

    def panel_ids(self) -> list[str]:
        """Every shown panel: Controls, then the Sidebar, then the Main pane."""
        return [pid for area in Area for pid in panel_ids(self.areas[area])]

    def area_of(self, panel_id: str) -> Area | None:
        """The area ``panel_id`` is shown in; None if it isn't shown."""
        return next((a for a in Area if panel_id in panel_ids(self.areas[a])), None)

    def with_tree(self, area: Area, tree: LayoutNode | None) -> LayoutState:
        return dataclasses.replace(self, areas={**self.areas, area: tree})

    def normalized(self, known_ids: Collection[str]) -> LayoutState:
        """Each area normalized (see ``normalize``), with one set of seen ids: a
        panel in two areas keeps its first place. Hidden panels that are shown
        or unknown are dropped."""
        known, seen = frozenset(known_ids), set()
        areas = {a: _normalize(t, known, seen) if t is not None else None
                 for a, t in self.areas.items()}
        hidden = {pid: a for pid, a in self.hidden.items() if pid in known and pid not in seen}
        return dataclasses.replace(self, areas=areas, hidden=hidden)

    def hide(self, panel_id: str) -> LayoutState:
        """Without ``panel_id``, remembering where it sat (its area too, spec §6.6).
        The area stays, empty if it was the area's last panel."""
        area = self.area_of(panel_id)
        if area is None:
            return self
        tree, anchor = remove_panel(self.areas[area], panel_id)
        anchor = dataclasses.replace(anchor or Anchor((), None, False), area=area)
        return dataclasses.replace(self.with_tree(area, tree),
                                   hidden={**self.hidden, panel_id: anchor})

    def show(self, panel_id: str, fallback: Anchor | None = None) -> LayoutState:
        """With ``panel_id`` back where it sat when hidden, else at ``fallback``."""
        return self.insert(panel_id, self.hidden.get(panel_id), fallback)

    def insert(self, panel_id: str, anchor: Anchor | None, fallback: Anchor | None = None,
               make_current: bool = True) -> LayoutState:
        """With ``panel_id`` placed by ``anchor`` in its area, else by ``fallback``
        in its; failing both, at the right-hand edge of the first of their areas
        (the Main pane if neither has one). It is no longer hidden. A panel
        already shown stays put."""
        if self.area_of(panel_id) is not None:
            return self
        hidden = {pid: a for pid, a in self.hidden.items() if pid != panel_id}
        state = dataclasses.replace(self, hidden=hidden)
        for a in (anchor, fallback):
            if a is None:
                continue
            tree = self.areas[a.area or Area.MAIN]
            if tree is not None and any(n in panel_ids(tree) for n in a.neighbors):
                return state.with_tree(a.area or Area.MAIN, insert_panel(
                    tree, panel_id, a, make_current=make_current))
        area = next((a.area for a in (anchor, fallback) if a is not None and a.area), Area.MAIN)
        return state.with_tree(area, insert_panel(self.areas[area], panel_id, None,
                                                  make_current=make_current))

    def move(self, panel_id: str, target: str, zone: DropZone) -> LayoutState:
        """With ``panel_id`` moved to ``target``'s ``zone``, in ``target``'s area
        (spec §6.1, §6.2). Returns ``self`` when nothing would change."""
        source, area = self.area_of(panel_id), self.area_of(target)
        if source is None or area is None:
            return self
        if source == area:
            tree = self.areas[area]
            moved = move_panel(tree, panel_id, target, zone)
            return self if moved is tree else self.with_tree(area, moved)
        if panel_id == target:
            return self
        rest = remove_panel(self.areas[source], panel_id)[0]
        tree = insert_panel(self.areas[area], panel_id, zone_anchor(target, zone))
        return self.with_tree(source, rest).with_tree(area, tree)

    def move_to_area(self, panel_id: str, area: Area) -> LayoutState:
        """With ``panel_id`` alone in ``area``, if that area is empty; else ``self``."""
        source = self.area_of(panel_id)
        if source is None or source == area or self.areas[area] is not None:
            return self
        rest = remove_panel(self.areas[source], panel_id)[0]
        return self.with_tree(source, rest).with_tree(area, PanelNode(panel_id))


def area_order(arrangement: Arrangement) -> tuple[Area, Area]:
    """The Sidebar and the Main pane in the order they sit (left to right, or
    top to bottom)."""
    if arrangement is Arrangement.SIDEBAR_LEFT:
        return Area.SIDEBAR, Area.MAIN
    return Area.MAIN, Area.SIDEBAR


def area_orientation(arrangement: Arrangement) -> Orientation:
    """How the Sidebar and the Main pane sit: side by side, or stacked."""
    if arrangement is Arrangement.MAIN_TOP:
        return Orientation.VERTICAL
    return Orientation.HORIZONTAL


def flatten(state: LayoutState) -> LayoutNode | None:
    """``state`` as one tree, the way versions 1 and 2 saved it: Controls above
    the Sidebar and the Main pane, empty areas left out."""
    order = area_order(state.arrangement)
    sizes = state.sizes
    if sizes is not None and order[0] is Area.MAIN:
        sizes = (sizes[1], sizes[0])
    body = [state.areas[a] for a in order]
    below: LayoutNode | None
    if all(t is not None for t in body):
        below = SplitNode(area_orientation(state.arrangement), tuple(body), sizes)
    else:
        below = next((t for t in body if t is not None), None)
    parts = tuple(t for t in (state.areas[Area.CONTROLS], below) if t is not None)
    if len(parts) <= 1:
        return parts[0] if parts else None
    return SplitNode(Orientation.VERTICAL, parts)


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


# ---- editing (spec §6.6) -----------------------------------------------------

_Path = tuple[int, ...]  # child indexes from the root


def anchor_of(root: LayoutNode, panel_id: str) -> Anchor | None:
    """Where ``panel_id`` sits in ``root``; None if it's the root (or the only tab
    of the root) or not there. The only tab of a group sits where its group does."""
    path = _path(root, panel_id)
    if path and len(_at(root, path[:-1]).children) == 1:  # type: ignore[union-attr]
        path = path[:-1]
    if not path:
        return None
    parent, i = _at(root, path[:-1]), path[-1]
    assert isinstance(parent, (SplitNode, TabsNode))
    j = i + 1 if i + 1 < len(parent.children) else i - 1  # the next node, else the previous
    share = None
    if isinstance(parent, SplitNode) and parent.sizes is not None:
        mine, theirs = parent.sizes[i], parent.sizes[j]
        if mine > 0 and theirs > 0:
            share = round(mine / (mine + theirs), 4)
    orientation = parent.orientation if isinstance(parent, SplitNode) else None
    return Anchor(tuple(panel_ids(parent.children[j])), orientation, j > i, share)


def remove_panel(root: LayoutNode, panel_id: str) -> tuple[LayoutNode | None, Anchor | None]:
    """``root`` without ``panel_id``, and where it sat.

    Its share of space goes to its neighbours; a split left with one child
    collapses into it. Returns ``(None, None)`` if it was the only panel.
    """
    return _without(root, panel_id), anchor_of(root, panel_id)


def _without(node: LayoutNode, panel_id: str) -> LayoutNode | None:
    match node:
        case PanelNode():
            return None if node.panel_id == panel_id else node
        case SplitNode():
            kept = [(k, i) for i, c in enumerate(node.children)
                    if (k := _without(c, panel_id)) is not None]
            if len(kept) <= 1:
                return kept[0][0] if kept else None
            sizes = tuple(node.sizes[i] for _, i in kept) if node.sizes is not None else None
            return SplitNode(node.orientation, tuple(k for k, _ in kept), sizes)
        case TabsNode():
            ids = [t.panel_id for t in node.children]
            if panel_id not in ids:
                return node
            gone = ids.index(panel_id)
            tabs = node.children[:gone] + node.children[gone + 1:]
            if not tabs:
                return None
            current = node.current - 1 if gone < node.current else node.current
            return TabsNode(tabs, min(current, len(tabs) - 1))
    raise TypeError(f"not a layout node: {node!r}")


def insert_panel(
    root: LayoutNode | None,
    panel_id: str,
    anchor: Anchor | None,
    fallback: Anchor | None = None,
    make_current: bool = True,
) -> LayoutNode:
    """``root`` with ``panel_id`` placed by ``anchor`` (spec §6.6).

    Uses ``fallback`` if none of the anchor's neighbours is shown, and the
    right-hand edge if neither fits. A panel already shown is left where it is.
    Added to tabs, it becomes the current tab unless ``make_current`` is False.
    """
    if root is None:
        return PanelNode(panel_id)
    shown = panel_ids(root)
    if panel_id in shown:
        return root
    for a in (anchor, fallback):
        if a is not None and (present := [n for n in a.neighbors if n in shown]):
            return _insert(root, panel_id, a, present, make_current)
    return _insert(root, panel_id, Anchor(tuple(shown), Orientation.HORIZONTAL, False), shown,
                   make_current)


class DropZone(StrEnum):
    """Where a dragged panel lands on the pane under the cursor (spec §6.1, §6.2)."""

    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"
    CENTER = "center"  # in tabs with it


EDGE = 0.25  # the outer part of a pane, on each side, that is an edge zone


def drop_zone(x: float, y: float) -> DropZone:
    """The zone of a point inside a pane, as fractions of its width and height:
    the nearest edge if within ``EDGE`` of it, else the centre."""
    distances = {DropZone.LEFT: x, DropZone.RIGHT: 1 - x,
                 DropZone.TOP: y, DropZone.BOTTOM: 1 - y}
    zone = min(distances, key=distances.__getitem__)
    return zone if distances[zone] < EDGE else DropZone.CENTER


_ZONE_SPLITS = {
    DropZone.LEFT: (Orientation.HORIZONTAL, True),
    DropZone.RIGHT: (Orientation.HORIZONTAL, False),
    DropZone.TOP: (Orientation.VERTICAL, True),
    DropZone.BOTTOM: (Orientation.VERTICAL, False),
}


def zone_anchor(target: str, zone: DropZone) -> Anchor:
    """Where a panel dropped on ``target``'s ``zone`` goes: beside it, sharing its
    space evenly, or a tab after it."""
    if zone is DropZone.CENTER:
        return Anchor((target,), None, False)
    orientation, before = _ZONE_SPLITS[zone]
    return Anchor((target,), orientation, before, 0.5)


def move_panel(root: LayoutNode, panel_id: str, target: str, zone: DropZone) -> LayoutNode:
    """``root`` with ``panel_id`` moved to ``target``'s ``zone``.

    Returns ``root`` itself when nothing would change: either panel isn't
    shown, it's dropped on itself, or on the centre of its own tab group.
    """
    shown = panel_ids(root)
    if panel_id == target or panel_id not in shown or target not in shown:
        return root
    if zone is DropZone.CENTER:
        mine, theirs = _path(root, panel_id), _path(root, target)
        if mine and theirs and mine[:-1] == theirs[:-1] \
                and isinstance(_at(root, mine[:-1]), TabsNode):
            return root
    rest = remove_panel(root, panel_id)[0]
    if rest is None:
        return root
    return insert_panel(rest, panel_id, zone_anchor(target, zone))


def _insert(
    root: LayoutNode, panel_id: str, anchor: Anchor, present: list[str], make_current: bool
) -> LayoutNode:
    new = PanelNode(panel_id)
    path = _common_prefix([_path(root, n) or () for n in present])
    node = _at(root, path)
    parent = _at(root, path[:-1]) if path else None

    if anchor.orientation is None:  # a tab beside the neighbour
        if isinstance(node, PanelNode) and isinstance(parent, TabsNode):
            i = path[-1] + (0 if anchor.before else 1)
            tabs = parent.children[:i] + (new,) + parent.children[i:]
            current = parent.current + (1 if i <= parent.current else 0)
            return _replace(root, path[:-1], TabsNode(tabs, i if make_current else current))
        if isinstance(node, PanelNode):
            pair = (new, node) if anchor.before else (node, new)
            return _replace(root, path, TabsNode(pair, pair.index(new if make_current else node)))
        if isinstance(node, TabsNode):
            tabs = (new, *node.children) if anchor.before else (*node.children, new)
            current = node.current + (1 if anchor.before else 0)
            return _replace(root, path, TabsNode(tabs, tabs.index(new) if make_current else current))
        # Its neighbours are now spread over a split: go beside them instead.
        anchor = Anchor(anchor.neighbors, Orientation.HORIZONTAL, anchor.before, anchor.share)
    orientation = anchor.orientation
    assert orientation is not None

    if isinstance(parent, TabsNode):  # a tab can't be split: go beside the whole group
        path, node = path[:-1], parent
        parent = _at(root, path[:-1]) if path else None
    share = anchor.share if anchor.share is not None else 0.5
    if isinstance(parent, SplitNode) and parent.orientation == orientation:
        i = path[-1]
        at = i if anchor.before else i + 1
        children = parent.children[:at] + (new,) + parent.children[at:]
        sizes = None
        if parent.sizes is not None:
            mine = round(parent.sizes[i] * share)
            rest = list(parent.sizes)
            rest[i] -= mine
            sizes = tuple(rest[:at] + [mine] + rest[at:])
        return _replace(root, path[:-1], SplitNode(orientation, children, sizes))
    mine = round(1000 * share)
    if anchor.before:
        children, sizes = (new, node), (mine, 1000 - mine)
    else:
        children, sizes = (node, new), (1000 - mine, mine)
    split = SplitNode(orientation, children, sizes if anchor.share is not None else None)
    return _replace(root, path, split)


def _path(node: LayoutNode, panel_id: str) -> _Path | None:
    if isinstance(node, PanelNode):
        return () if node.panel_id == panel_id else None
    for i, child in enumerate(node.children):
        if (rest := _path(child, panel_id)) is not None:
            return (i, *rest)
    return None


def _at(node: LayoutNode, path: _Path) -> LayoutNode:
    for i in path:
        assert isinstance(node, (SplitNode, TabsNode))
        node = node.children[i]
    return node


def _replace(node: LayoutNode, path: _Path, new: LayoutNode) -> LayoutNode:
    if not path:
        return new
    assert isinstance(node, (SplitNode, TabsNode))
    children = list(node.children)
    children[path[0]] = _replace(children[path[0]], path[1:], new)
    return dataclasses.replace(node, children=tuple(children))  # type: ignore[arg-type]


def _common_prefix(paths: list[_Path]) -> _Path:
    prefix = paths[0]
    for path in paths[1:]:
        n = 0
        while n < min(len(prefix), len(path)) and prefix[n] == path[n]:
            n += 1
        prefix = prefix[:n]
    return prefix


# ---- serialisation ----------------------------------------------------------


def to_dict(state: LayoutState) -> dict[str, Any]:
    """JSON-ready form: ``{"version": 3, "arrangement": ..., "sizes": [s, m],
    "areas": {"controls": ..., "sidebar": ..., "main": ...}, "hidden": {...}}``."""
    data: dict[str, Any] = {
        "version": FORMAT_VERSION,
        "arrangement": str(state.arrangement),
        "areas": {str(a): _node_to_dict(t) if t is not None else None
                  for a, t in state.areas.items()},
        "hidden": {pid: _anchor_to_dict(a) for pid, a in sorted(state.hidden.items())},
    }
    if state.sizes is not None:
        data["sizes"] = list(state.sizes)
    return data


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


def _anchor_to_dict(anchor: Anchor) -> dict[str, Any]:
    data: dict[str, Any] = {
        "neighbors": list(anchor.neighbors),
        "container": str(anchor.orientation) if anchor.orientation else _TABS,
        "before": anchor.before,
    }
    if anchor.share is not None:
        data["share"] = anchor.share
    if anchor.area is not None:
        data["area"] = str(anchor.area)
    return data


_TABS = "tabs"


def from_dict(data: Any,
              default_area: Callable[[str], Area | None] | None = None) -> LayoutState:
    """Parse ``to_dict`` output. Raises ``LayoutError`` on structural errors.

    Versions 1 and 2 (one tree) are migrated to areas by ``migrate_tree`` with
    ``default_area``, and need it. ``sizes`` and ``share`` that don't fit are
    dropped, not rejected. The result is not normalized: callers run
    ``LayoutState.normalized`` with the known ids.
    """
    if not isinstance(data, dict):
        raise LayoutError("layout is not an object")
    version = data.get("version")
    if version in _LEGACY_VERSIONS:
        if default_area is None:
            raise LayoutError(f"a version {version} layout needs migrating")
        root = _node_from_dict(_field(data, "root", dict))
        hidden = _hidden_from_dict(data) if version == 2 else {}
        return migrate_tree(root, hidden, default_area)
    if version != FORMAT_VERSION:
        raise LayoutError(f"unsupported layout version: {version!r}")
    name = _field(data, "arrangement", str)
    try:
        arrangement = Arrangement(name)
    except ValueError as exc:
        raise LayoutError(f"unknown arrangement: {name!r}") from exc
    raw = _field(data, "areas", dict)
    areas: dict[Area, LayoutNode | None] = {}
    for name, node in raw.items():
        try:
            area = Area(name)
        except ValueError as exc:
            raise LayoutError(f"unknown area: {name!r}") from exc
        if node is not None and not isinstance(node, dict):
            raise LayoutError(f"area {name!r} must be a node or null")
        areas[area] = _node_from_dict(node) if node is not None else None
    sizes = _valid_sizes(data.get("sizes"), 2)
    return LayoutState(areas, arrangement, sizes, _hidden_from_dict(data))  # type: ignore[arg-type]


def _hidden_from_dict(data: dict[str, Any]) -> dict[str, Anchor]:
    hidden = _field(data, "hidden", dict)
    if not all(isinstance(v, dict) for v in hidden.values()):
        raise LayoutError("hidden panels' anchors must be objects")
    return {pid: _anchor_from_dict(a) for pid, a in hidden.items()}


def _anchor_from_dict(data: dict[str, Any]) -> Anchor:
    neighbors = _field(data, "neighbors", list)
    if not all(isinstance(n, str) for n in neighbors):
        raise LayoutError("an anchor's neighbors must be panel ids")
    container = _field(data, "container", str)
    if container == _TABS:
        orientation = None
    else:
        try:
            orientation = Orientation(container)
        except ValueError as exc:
            raise LayoutError(f"unknown anchor container: {container!r}") from exc
    before = _field(data, "before", bool)
    share = data.get("share")
    if not (type(share) in (int, float) and 0 < share < 1):
        share = None
    area = data.get("area")
    area = Area(area) if area in tuple(Area) else None  # unknown: its default area
    return Anchor(tuple(neighbors), orientation, before, share, area)


# ---- migration (spec §6.3) -----------------------------------------------------


def migrate_tree(root: LayoutNode, hidden: Mapping[str, Anchor],
                 default_area: Callable[[str], Area | None]) -> LayoutState:
    """A layout saved as one tree (versions 1 and 2), in areas.

    Each panel goes to its ``default_area`` (panels with none are dropped), and
    each area's tree is ``root`` without the other areas' panels, so it keeps its
    structure and sizes. The arrangement, and the split between the areas, come
    from the smallest split holding both Sidebar and Main pane panels. A hidden
    panel's anchor is kept, in its default area, when one of its neighbours is
    in that area.
    """
    ids = panel_ids(root)
    areas: dict[Area, LayoutNode | None] = {}
    for area in Area:
        tree: LayoutNode | None = root
        for pid in ids:
            if tree is not None and default_area(pid) is not area:
                tree = _without(tree, pid)
        areas[area] = tree
    arrangement, sizes = _arrangement(root, default_area)
    anchors = {}
    for pid, anchor in hidden.items():
        area = default_area(pid)
        if area is not None and any(default_area(n) is area for n in anchor.neighbors):
            anchors[pid] = dataclasses.replace(anchor, area=area)
    return LayoutState(areas, arrangement, sizes, anchors)


def _arrangement(root: LayoutNode, default_area: Callable[[str], Area | None]
                 ) -> tuple[Arrangement, tuple[int, int] | None]:
    """How the Sidebar and the Main pane sat in ``root``, and their sizes when
    the split holding them has one child for each."""
    paths = {area: [p for pid in panel_ids(root) if default_area(pid) is area
                    and (p := _path(root, pid)) is not None]
             for area in (Area.SIDEBAR, Area.MAIN)}
    if not all(paths.values()):
        return Arrangement.SIDEBAR_LEFT, None
    prefix = _common_prefix(paths[Area.SIDEBAR] + paths[Area.MAIN])
    node = _at(root, prefix)
    if not isinstance(node, SplitNode):  # mixed in one tab group
        return Arrangement.SIDEBAR_LEFT, None
    side = {p[len(prefix)] for p in paths[Area.SIDEBAR]}
    main = {p[len(prefix)] for p in paths[Area.MAIN]}
    if node.orientation is Orientation.VERTICAL:
        arrangement = Arrangement.MAIN_TOP
    elif min(main) > min(side):
        arrangement = Arrangement.SIDEBAR_LEFT
    else:
        arrangement = Arrangement.SIDEBAR_RIGHT
    sizes = None
    if len(node.children) == 2 and len(side) == len(main) == 1 and side != main \
            and node.sizes is not None:
        sizes = (node.sizes[next(iter(side))], node.sizes[next(iter(main))])
    return arrangement, sizes


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
