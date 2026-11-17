"""Built-in layouts, and choosing the layout to start with (spec §6.1, §6.3)."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from rzgitclient.ui.layout.layout_model import (
    Anchor,
    Area,
    Arrangement,
    LayoutError,
    LayoutNode,
    LayoutState,
    Orientation,
    PanelNode,
    SplitNode,
    TabsNode,
    anchor_of,
    from_dict,
    panel_ids,
    remove_panel,
)

log = logging.getLogger(__name__)

H, V = Orientation.HORIZONTAL, Orientation.VERTICAL
BRANCHES, FILES, HISTORY = PanelNode("branches"), PanelNode("files"), PanelNode("history")
CONTROLS, DIFF, COMMIT = PanelNode("controls"), PanelNode("diff"), PanelNode("commit")
STAGED, COMMIT_FILES = PanelNode("staged"), PanelNode("commit_files")
COMMIT_DETAILS = PanelNode("commit_details")
STASH = PanelNode("stash")
WORKTREES = PanelNode("worktrees")  # below Branches in every preset (RZG-48)
# History and the diff of the selected file share one tab group in every preset.
CONTENT = TabsNode((HISTORY, DIFF))
# Commit Details sits below them, at the bottom of the main pane (spec §5.4, RZG-65).
# Collapsed for Uncommitted changes, it lends its height to the tabs (§6.2).
MAIN = SplitNode(V, (CONTENT, COMMIT_DETAILS), sizes=(500, 220))


@dataclass(frozen=True)
class Preset:
    title: str  # shown in View → Layout
    state: LayoutState


def _areas(arrangement: Arrangement, sidebar: LayoutNode,
           sizes: tuple[int, int]) -> LayoutState:
    """The Controls bar alone in Controls (its height is fixed), ``sidebar`` in
    the Sidebar and ``MAIN`` in the Main pane; ``sizes`` Sidebar first."""
    return LayoutState({Area.CONTROLS: CONTROLS, Area.SIDEBAR: sidebar, Area.MAIN: MAIN},
                       arrangement, sizes)


# In every preset Staged Files, Commit and Commit Files are directly below
# (Unstaged) Files, as in GitKraken: Commit Files takes the place of the other
# three while a commit is selected (spec §5.4). Its size is 0: it always takes
# theirs (§6.2). Standard: Branches, Worktrees, Unstaged Files, Staged Files,
# Commit and Stashes stacked in the Sidebar, left of the Main pane.
DEFAULT_LAYOUT = _areas(
    Arrangement.SIDEBAR_LEFT,
    SplitNode(V, (BRANCHES, WORKTREES, FILES, STAGED, COMMIT, COMMIT_FILES, STASH),
              sizes=(180, 140, 220, 120, 160, 0, 170)),
    (220, 880))

PRESETS = (
    Preset("Standard", DEFAULT_LAYOUT),
    # Branches, Worktrees, Unstaged Files and Stashes as tabs, above Staged Files and Commit.
    Preset("Sidebar Tabs", _areas(
        Arrangement.SIDEBAR_LEFT,
        SplitNode(V, (TabsNode((BRANCHES, WORKTREES, FILES, STASH)), STAGED, COMMIT,
                      COMMIT_FILES), sizes=(330, 120, 160, 0)),
        (220, 880))),
    # The Main pane across the top; below, Branches (Worktrees and Stashes below
    # it) beside Unstaged Files, with Staged Files, Commit and Commit Files below it.
    Preset("Stacked", _areas(
        Arrangement.MAIN_TOP,
        SplitNode(H, (SplitNode(V, (BRANCHES, WORKTREES, STASH), sizes=(240, 130, 130)),
                      SplitNode(V, (FILES, STAGED, COMMIT, COMMIT_FILES),
                                sizes=(120, 90, 160, 0))),
                  sizes=(400, 700)),
        (370, 380))),
)


def layout_name_problem(name: str, taken: Collection[str] = ()) -> str | None:
    """Why ``name`` (already trimmed) can't name a saved layout, or ``None`` if it can:
    it's empty, a built-in preset's name, or in ``taken`` (all ignoring case; spec §6.3)."""
    if not name:
        return "Enter a name for the layout."
    folded = name.casefold()
    if any(p.title.casefold() == folded for p in PRESETS):
        return f"“{name}” is a built-in layout. Choose another name."
    if any(t.casefold() == folded for t in taken):
        return f"A saved layout is already called “{name}”. Choose another name."
    return None


def default_area(panel_id: str) -> Area | None:
    """The area ``panel_id`` has in ``DEFAULT_LAYOUT``; None for an unknown panel."""
    return DEFAULT_LAYOUT.area_of(panel_id)


def default_anchor(panel_id: str, shown: Collection[str] | None = None) -> Anchor | None:
    """Where ``panel_id`` sits in ``DEFAULT_LAYOUT``, in its default area: the
    fallback place to show it. None for an unknown panel.

    With ``shown``, the panels on screen, its place among those only: so Files
    shown while Commit is hidden goes below Branches, not next to the missing
    Commit (and two panels new in a version, such as Files and Commit, still
    land together). Alone in its area, it has no neighbours: it fills the area.
    """
    area = default_area(panel_id)
    if area is None:
        return None
    reference = DEFAULT_LAYOUT.tree(area)
    if shown is not None:
        for other in panel_ids(reference):
            if other != panel_id and other not in shown and reference is not None:
                reference = remove_panel(reference, other)[0]
    anchor = anchor_of(reference, panel_id) if reference is not None else None
    return dataclasses.replace(anchor or Anchor((), None, False), area=area)


def matching_preset(state: LayoutState) -> str | None:
    """The title of the preset ``state`` is, sizes, current tabs and hidden
    panels aside; None for a custom layout (spec §6.3)."""
    shape = _shape(state)
    for preset in PRESETS:
        if _shape(_without_panels(preset.state, state.hidden)) == shape:
            return preset.title
    return None


def _without_panels(state: LayoutState, panel_ids_: Collection[str]) -> LayoutState:
    """``state`` with the panels ``panel_ids_`` hidden."""
    for pid in panel_ids_:
        state = state.hide(pid)
    return state


def restore_layout(saved: Any, preset: str | None = None) -> LayoutState:
    """The layout to start with, given what ``AppSettings.layout`` and
    ``AppSettings.layout_preset`` returned.

    A built-in preset follows the code: when ``preset`` names a preset whose
    arrangement isn't the saved one any more, that preset is used, with the
    same panels hidden. Otherwise the saved layout is used. Falls back to
    ``DEFAULT_LAYOUT`` when nothing is saved, the data is malformed (logged)
    or nothing known is left. Panels the user hid stay hidden; a panel
    neither shown nor hidden is new in this version and goes to its place in
    the default layout.
    """
    if saved is None:
        return DEFAULT_LAYOUT
    try:
        state = from_dict(saved, default_area)  # an older version is migrated to areas
    except LayoutError as exc:
        log.warning("Saved layout is malformed (%s); using the default", exc)
        return DEFAULT_LAYOUT
    known = DEFAULT_LAYOUT.panel_ids()
    state = state.normalized(known)  # drops panels this version no longer has
    current = next((p for p in PRESETS if p.title == preset), None)
    if current is not None:
        hidden_ids = [pid for pid in state.hidden if pid in known]
        code = _without_panels(current.state, hidden_ids)
        if _shape(code) != _shape(state):
            log.info("The %s layout changed since it was saved; using the new one", preset)
            return code
    if not state.panel_ids():
        log.info("Saved layout has none of this version's panels; using the default")
        return DEFAULT_LAYOUT
    new = {pid for pid in known if state.area_of(pid) is None and pid not in state.hidden}
    for pid in known:
        if pid in new:  # added in a newer version
            # The tab the user had current stays current.
            state = state.insert(pid, default_anchor(pid, state.panel_ids()),
                                 default_anchor(pid), make_current=False)
    if new:
        state = dataclasses.replace(state, areas={
            a: _default_sizes(t, new) if t is not None else None
            for a, t in state.areas.items()})
    return state


def _default_sizes(node: LayoutNode, new: set[str]) -> LayoutNode:
    """``node`` where each split that gained one of the ``new`` panels, and now
    holds the same panes as a split of a preset, takes that split's sizes: several new panels side by side (Staged Files and Commit) would
    otherwise each take a share of the last one's space, and end up tiny."""
    if not isinstance(node, SplitNode):
        return node
    node = dataclasses.replace(node, children=tuple(_default_sizes(c, new) for c in node.children))
    if any(isinstance(c, PanelNode) and c.panel_id in new for c in node.children):
        for split in (s for preset in PRESETS for t in preset.state.areas.values()
                      for s in _splits(t)):
            if _shape(split) == _shape(node):
                return dataclasses.replace(node, sizes=split.sizes)
    return node


def _splits(node: LayoutNode | None) -> list[SplitNode]:
    if not isinstance(node, SplitNode):
        return []
    return [node, *(s for c in node.children for s in _splits(c))]


def _shape(node: LayoutNode | LayoutState | None) -> object:
    """``node`` without its sizes or current tab; a state's arrangement and the
    shape of each area."""
    match node:
        case LayoutState():
            return node.arrangement, tuple(_shape(node.tree(a)) for a in Area)
        case SplitNode():
            return node.orientation, tuple(_shape(c) for c in node.children)
        case TabsNode():
            return "tabs", tuple(c.panel_id for c in node.children)
    return node
