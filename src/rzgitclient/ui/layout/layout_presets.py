"""Built-in layouts, and choosing the layout to start with (spec §6.1, §6.3)."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Collection
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
    remove_panel,
)

log = logging.getLogger(__name__)

H, V = Orientation.HORIZONTAL, Orientation.VERTICAL
BRANCHES, FILES, HISTORY = PanelNode("branches"), PanelNode("files"), PanelNode("history")
CONTROLS, DIFF, COMMIT = PanelNode("controls"), PanelNode("diff"), PanelNode("commit")
STAGED, COMMIT_FILES = PanelNode("staged"), PanelNode("commit_files")
# History and the diff of the selected file share one tab group in every preset.
CONTENT = TabsNode((HISTORY, DIFF))


@dataclass(frozen=True)
class Preset:
    title: str  # shown in View → Layout
    root: LayoutNode


def _below_controls(root: LayoutNode) -> SplitNode:
    """The Controls bar across the top (its height is fixed), ``root`` below it."""
    return SplitNode(V, (CONTROLS, root))


# In every preset Staged Files, Commit and Commit Files are directly below
# (Unstaged) Files, as in GitKraken: Commit Files takes the place of the other
# three while a commit is selected (spec §5.4). Its size is 0: it always takes
# theirs (§6.2). Standard: Branches, Unstaged Files, Staged Files and Commit
# stacked in a left sidebar; History and Diff on the right.
DEFAULT_LAYOUT = _below_controls(SplitNode(
    H, (SplitNode(V, (BRANCHES, FILES, STAGED, COMMIT, COMMIT_FILES),
                  sizes=(200, 220, 120, 160, 0)),
        CONTENT),
    sizes=(220, 880)))

PRESETS = (
    Preset("Standard", DEFAULT_LAYOUT),
    # Branches and Unstaged Files as tabs, above Staged Files and Commit.
    Preset("Sidebar Tabs", _below_controls(SplitNode(H, (
        SplitNode(V, (TabsNode((BRANCHES, FILES)), STAGED, COMMIT, COMMIT_FILES),
                  sizes=(330, 120, 160, 0)),
        CONTENT), sizes=(220, 880)))),
    # History and Diff across the top; below, Branches beside Unstaged Files,
    # with Staged Files, Commit and Commit Files below it.
    Preset("Stacked", _below_controls(SplitNode(V, (
        CONTENT,
        SplitNode(H, (BRANCHES, SplitNode(V, (FILES, STAGED, COMMIT, COMMIT_FILES),
                                          sizes=(120, 90, 160, 0))),
                  sizes=(400, 700))),
        sizes=(240, 370)))),
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


def default_anchor(panel_id: str, shown: Collection[str] | None = None) -> Anchor | None:
    """Where ``panel_id`` sits in ``DEFAULT_LAYOUT``: the fallback place to show it.

    With ``shown``, the panels on screen, its place among those only: so Files
    shown while Commit is hidden goes below Branches, not next to the missing
    Commit (and two panels new in a version, such as Files and Commit, still
    land together).
    """
    reference: LayoutNode | None = DEFAULT_LAYOUT
    if shown is not None:
        for other in panel_ids(DEFAULT_LAYOUT):
            if other != panel_id and other not in shown and reference is not None:
                reference = remove_panel(reference, other)[0]
    return anchor_of(reference, panel_id) if reference is not None else None


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
    new = {pid for pid in known if pid not in shown and pid not in hidden}
    for pid in known:
        if pid in new:  # added in a newer version
            anchor = default_anchor(pid, panel_ids(root))
            # The tab the user had current stays current.
            root = insert_panel(root, pid, anchor, default_anchor(pid), make_current=False)
    return LayoutState(_default_sizes(root, new) if new else root, hidden)


def _default_sizes(node: LayoutNode, new: set[str]) -> LayoutNode:
    """``node`` where each split that gained one of the ``new`` panels, and now
    holds the same panes as a split of a preset, takes that split's sizes: several new panels side by side (Staged Files and Commit) would
    otherwise each take a share of the last one's space, and end up tiny."""
    if not isinstance(node, SplitNode):
        return node
    node = dataclasses.replace(node, children=tuple(_default_sizes(c, new) for c in node.children))
    if any(isinstance(c, PanelNode) and c.panel_id in new for c in node.children):
        for split in (s for preset in PRESETS for s in _splits(preset.root)):
            if _shape(split) == _shape(node):
                return dataclasses.replace(node, sizes=split.sizes)
    return node


def _splits(node: LayoutNode) -> list[SplitNode]:
    if not isinstance(node, SplitNode):
        return []
    return [node, *(s for c in node.children for s in _splits(c))]


def _shape(node: LayoutNode) -> object:
    """``node`` without its sizes or current tab."""
    match node:
        case SplitNode():
            return node.orientation, tuple(_shape(c) for c in node.children)
        case TabsNode():
            return "tabs", tuple(c.panel_id for c in node.children)
    return node
