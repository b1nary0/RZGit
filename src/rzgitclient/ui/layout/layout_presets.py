"""Built-in layouts, and choosing the layout to start with (spec §6.1, §6.3)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from rzgitclient.ui.layout.layout_model import (
    Anchor,
    LayoutError,
    LayoutNode,
    LayoutState,
    Orientation,
    PanelNode,
    SplitNode,
    TabsNode,
    anchor_of,
    from_dict,
    insert_panel,
    normalize,
    panel_ids,
)

log = logging.getLogger(__name__)

H, V = Orientation.HORIZONTAL, Orientation.VERTICAL
BRANCHES, FILES, HISTORY = PanelNode("branches"), PanelNode("files"), PanelNode("history")
CONTROLS, DIFF = PanelNode("controls"), PanelNode("diff")
# History and the diff of the selected file share one tab group in every preset.
CONTENT = TabsNode((HISTORY, DIFF))


@dataclass(frozen=True)
class Preset:
    title: str  # shown in View → Layout
    root: LayoutNode


def _below_controls(root: LayoutNode) -> SplitNode:
    """The Controls bar across the top (its height is fixed), ``root`` below it."""
    return SplitNode(V, (CONTROLS, root))


# Branches above Files in a left sidebar; History and Diff on the right.
DEFAULT_LAYOUT = _below_controls(
    SplitNode(H, (SplitNode(V, (BRANCHES, FILES), sizes=(300, 400)), CONTENT), sizes=(220, 880))
)

PRESETS = (
    Preset("Standard", DEFAULT_LAYOUT),
    # Branches and Files as tabs, each with the sidebar's full height.
    Preset("Sidebar Tabs", _below_controls(
        SplitNode(H, (TabsNode((BRANCHES, FILES)), CONTENT), sizes=(220, 880)))),
    # History and Diff across the top; Branches and Files side by side below.
    Preset("Stacked", _below_controls(
        SplitNode(V, (CONTENT, SplitNode(H, (BRANCHES, FILES), sizes=(400, 700))),
                  sizes=(450, 250)))),
)


def default_anchor(panel_id: str) -> Anchor | None:
    """Where ``panel_id`` sits in ``DEFAULT_LAYOUT``: the fallback place to show it."""
    return anchor_of(DEFAULT_LAYOUT, panel_id)


def restore_layout(saved: Any) -> LayoutState:
    """The layout to start with, given what ``AppSettings.layout`` returned.

    Falls back to ``DEFAULT_LAYOUT`` when nothing is saved, the data is
    malformed (logged) or nothing known is left. Panels the user hid stay
    hidden; a panel neither shown nor hidden is new in this version and goes
    to its place in the default layout.
    """
    if saved is None:
        return LayoutState(DEFAULT_LAYOUT)
    try:
        state = from_dict(saved)
    except LayoutError as exc:
        log.warning("Saved layout is malformed (%s); using the default", exc)
        return LayoutState(DEFAULT_LAYOUT)
    known = panel_ids(DEFAULT_LAYOUT)
    root = normalize(state.root, known)  # drops panels this version no longer has
    if root is None:
        log.info("Saved layout has none of this version's panels; using the default")
        return LayoutState(DEFAULT_LAYOUT)
    shown = set(panel_ids(root))
    hidden = {pid: a for pid, a in state.hidden.items() if pid in known and pid not in shown}
    for pid in known:
        if pid not in shown and pid not in hidden:  # added in a newer version
            # The tab the user had current stays current.
            root = insert_panel(root, pid, default_anchor(pid), make_current=False)
    return LayoutState(root, hidden)
