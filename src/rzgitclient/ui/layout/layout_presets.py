"""Built-in layouts, and choosing the layout to start with (spec §6.1, §6.3)."""

from __future__ import annotations

import logging
from typing import Any

from rzgitclient.ui.layout.layout_model import (
    LayoutError,
    LayoutNode,
    Orientation,
    PanelNode,
    SplitNode,
    from_dict,
    normalize,
    panel_ids,
)

log = logging.getLogger(__name__)

# Branches above Files in a left sidebar; History on the right.
DEFAULT_LAYOUT = SplitNode(
    Orientation.HORIZONTAL,
    (
        SplitNode(
            Orientation.VERTICAL,
            (PanelNode("branches"), PanelNode("files")),
            sizes=(300, 400),
        ),
        PanelNode("history"),
    ),
    sizes=(220, 880),
)


def restore_layout(saved: Any) -> LayoutNode:
    """The layout to start with, given what ``AppSettings.layout`` returned.

    Falls back to ``DEFAULT_LAYOUT`` when nothing is saved, the data is
    malformed (logged), or its panels differ from the default's. Users can't
    remove panels yet, so a difference means a newer version added one.
    """
    if saved is None:
        return DEFAULT_LAYOUT
    try:
        tree = from_dict(saved)
    except LayoutError as exc:
        log.warning("Saved layout is malformed (%s); using the default", exc)
        return DEFAULT_LAYOUT
    known = panel_ids(DEFAULT_LAYOUT)
    tree = normalize(tree, known)  # drops panels this version no longer has
    if tree is None or sorted(panel_ids(tree)) != sorted(known):
        log.info("Saved layout doesn't have this version's panels; using the default")
        return DEFAULT_LAYOUT
    return tree
