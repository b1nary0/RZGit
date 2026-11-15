"""Built-in layouts (spec §6.1)."""

from __future__ import annotations

from rzgitclient.ui.layout.layout_model import Orientation, PanelNode, SplitNode

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
