"""Builds the window's panes from a layout tree (spec §6.2)."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QSplitter, QTabWidget, QVBoxLayout, QWidget

from rzgitclient.ui.layout.layout_model import (
    Anchor,
    LayoutNode,
    LayoutState,
    Orientation,
    PanelNode,
    SplitNode,
    TabsNode,
    insert_panel,
    normalize,
    panel_ids,
    remove_panel,
)
from rzgitclient.ui.layout.layout_presets import default_anchor
from rzgitclient.ui.layout.panel_frame import PanelFrame
from rzgitclient.ui.panels.panel import Panel, PanelContext
from rzgitclient.ui.panels.panel_registry import PANEL_TYPES, panel_type

_KNOWN_IDS = frozenset(t.panel_id for t in PANEL_TYPES)
_ALWAYS_SHOWN = tuple(t.panel_id for t in PANEL_TYPES if t.always_shown)
_QT_ORIENTATION = {
    Orientation.HORIZONTAL: Qt.Orientation.Horizontal,
    Orientation.VERTICAL: Qt.Orientation.Vertical,
}


class LayoutHost(QWidget):
    """Owns the panels and the containers that arrange them.

    ``apply`` rebuilds the containers (splitters, tabs, frames) but keeps the
    panel instances, so their lists, scroll positions and selection survive.
    It also remembers where hidden panels sat, so they come back there (§6.6).
    """

    layout_changed = Signal()  # after every apply

    def __init__(self, ctx: PanelContext, parent: QWidget | None = None):
        super().__init__(parent)
        self._ctx = ctx
        self._panels: dict[str, Panel] = {}
        self._tree: LayoutNode | None = None
        self._hidden: dict[str, Anchor] = {}
        self._containers: list[QSplitter | QTabWidget] = []  # in pre-order of _tree
        self._root: QWidget | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    @property
    def panels(self) -> Mapping[str, Panel]:
        return MappingProxyType(self._panels)

    @property
    def hidden(self) -> Mapping[str, Anchor]:
        """Hidden panels, and where each sat."""
        return MappingProxyType(self._hidden)

    def apply_state(self, state: LayoutState) -> None:
        self.apply(state.root, state.hidden)

    def state(self) -> LayoutState | None:
        """What to save: ``current_layout()`` plus the hidden panels."""
        root = self.current_layout()
        return LayoutState(root, self._hidden) if root is not None else None

    def can_hide(self, panel_id: str) -> bool:
        """Shown, not ``always_shown``, and not the last panel that could be hidden."""
        hideable = [pid for pid in self._panels if pid not in _ALWAYS_SHOWN]
        return panel_id in hideable and len(hideable) > 1

    def hide_panel(self, panel_id: str) -> None:
        """Hide a shown panel, remembering where it sat (if ``can_hide``)."""
        root = self.current_layout()
        if root is None or not self.can_hide(panel_id):
            return
        root, anchor = remove_panel(root, panel_id)
        assert root is not None and anchor is not None  # it wasn't the only panel
        self.apply(root, {**self._hidden, panel_id: anchor})

    def show_panel(self, panel_id: str) -> None:
        """Show a panel where it sat when hidden, else at its default place."""
        if panel_id in self._panels or panel_id not in _KNOWN_IDS:
            return
        hidden = dict(self._hidden)
        anchor = hidden.pop(panel_id, None)
        self.apply(insert_panel(self.current_layout(), panel_id, anchor,
                                default_anchor(panel_id)), hidden)

    def apply(self, node: LayoutNode, hidden: Mapping[str, Anchor] | None = None) -> None:
        """Show ``node``. ``hidden`` (default: none) is kept for panels not in it.

        A panel that is ``always_shown`` is added at its default place if
        ``node`` lacks it (§6.6).
        """
        tree = normalize(node, _KNOWN_IDS)
        if tree is not None:
            for panel_id in _ALWAYS_SHOWN:
                tree = insert_panel(tree, panel_id, None, default_anchor(panel_id))
        wanted = set(panel_ids(tree))
        self._hidden = {
            pid: a for pid, a in (hidden or {}).items() if pid in _KNOWN_IDS and pid not in wanted
        }

        # Take every panel out of the old containers before they are deleted.
        for panel_id, panel in list(self._panels.items()):
            panel.setParent(None)
            if panel_id not in wanted:
                del self._panels[panel_id]
                panel.deleteLater()
        if self._root is not None:
            self._layout.removeWidget(self._root)
            self._root.deleteLater()

        self._tree, self._containers = tree, []
        self._root = self._build(tree) if tree is not None else None
        if self._root is not None:
            self._layout.addWidget(self._root)
        self.layout_changed.emit()

    def current_layout(self) -> LayoutNode | None:
        """The applied tree with the splitter sizes and current tabs as they are now."""
        if self._tree is None:
            return None
        containers = iter(self._containers)

        def read(node: LayoutNode) -> LayoutNode:
            match node:
                case SplitNode():
                    splitter = next(containers)
                    children = tuple(read(c) for c in node.children)
                    return dataclasses.replace(
                        node, children=children, sizes=tuple(splitter.sizes())
                    )
                case TabsNode():
                    return dataclasses.replace(node, current=next(containers).currentIndex())
            return node

        return read(self._tree)

    # ---- building --------------------------------------------------------

    def _panel(self, panel_id: str) -> Panel:
        panel = self._panels.get(panel_id)
        if panel is None:
            panel = self._panels[panel_id] = panel_type(panel_id)(self._ctx)
        return panel

    def _build(self, node: LayoutNode) -> QWidget:
        match node:
            case PanelNode():
                return PanelFrame(self._panel(node.panel_id))
            case SplitNode():
                splitter = QSplitter(_QT_ORIENTATION[node.orientation])
                splitter.setChildrenCollapsible(False)
                self._containers.append(splitter)
                for child in node.children:
                    splitter.addWidget(self._build(child))
                if node.sizes is not None:
                    splitter.setSizes(list(node.sizes))
                if node.orientation is Orientation.VERTICAL:
                    _fix_heights(splitter)
                return splitter
            case TabsNode():
                tabs = _PanelTabs()
                self._containers.append(tabs)
                for child in node.children:
                    tabs.add_panel(self._panel(child.panel_id))
                tabs.setCurrentIndex(node.current)
                return tabs
        raise TypeError(f"not a layout node: {node!r}")


def _fix_heights(splitter: QSplitter) -> None:
    """Fix the panes of ``fixed_height`` panels, and lock the dividers beside them."""
    for i in range(splitter.count()):
        frame = splitter.widget(i)
        if not (isinstance(frame, PanelFrame) and frame.panel.fixed_height):
            continue
        frame.ensurePolished()
        frame.setFixedHeight(frame.sizeHint().height())
        splitter.setStretchFactor(i, 0)
        for h in (i, i + 1):  # handle(i) is above widget i; handle(0) is never shown
            if 0 < h < splitter.count():
                handle = splitter.handle(h)
                handle.setEnabled(False)
                handle.setCursor(Qt.CursorShape.ArrowCursor)


class _PanelTabs(QTabWidget):
    """Tabs whose labels follow each panel's header text."""

    def __init__(self):
        super().__init__()
        self.setDocumentMode(True)

    def add_panel(self, panel: Panel) -> None:
        self.addTab(panel, panel.header)
        # Bound slots: Qt drops the connections when these tabs are deleted.
        panel.header_changed.connect(self._retitle)
        panel.activate_requested.connect(self._activate)

    @Slot(str)
    def _retitle(self, text: str) -> None:
        index = self.indexOf(self.sender())
        if index >= 0:  # the panel may have moved to another container
            self.setTabText(index, text)

    @Slot()
    def _activate(self) -> None:
        index = self.indexOf(self.sender())
        if index >= 0:
            self.setCurrentIndex(index)

