"""Builds the window's panes from a layout tree (spec §6.2)."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType

from PySide6.QtCore import QEvent, QMimeData, QObject, QPoint, QRect, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QDrag, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QSplitter,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rzgitclient.ui.layout.layout_model import (
    Anchor,
    DropZone,
    LayoutNode,
    LayoutState,
    Orientation,
    PanelNode,
    SplitNode,
    TabsNode,
    drop_zone,
    insert_panel,
    move_panel,
    normalize,
    panel_ids,
    remove_panel,
)
from rzgitclient.ui.layout.drop_indicator import DropIndicator, zone_rect
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
PANEL_MIME = "application/x-rzgitclient-panel"  # a dragged panel's id (§6.2)
_DRAG_EVENTS = frozenset({QEvent.Type.DragEnter, QEvent.Type.DragMove,
                          QEvent.Type.DragLeave, QEvent.Type.Drop})


class LayoutHost(QWidget):
    """Owns the panels and the containers that arrange them.

    ``apply`` rebuilds the containers (splitters, tabs, frames) but keeps the
    panel instances, so their lists, scroll positions and selection survive.
    It also remembers where hidden panels sat, so they come back there (§6.6),
    and hides the panes of collapsed panels (§5.1) without changing the tree.
    """

    layout_changed = Signal()  # after every apply

    def __init__(self, ctx: PanelContext, parent: QWidget | None = None):
        super().__init__(parent)
        self._ctx = ctx
        self._panels: dict[str, Panel] = {}
        self._tree: LayoutNode | None = None
        self._hidden: dict[str, Anchor] = {}
        self._containers: list[QSplitter | QTabWidget] = []  # in pre-order of _tree
        # A collapsed panel's size in its split when it collapsed: what to save.
        self._collapsed_sizes: dict[str, int] = {}
        # Collapse changes in splits, applied together (§6.2): panel id → collapsed.
        self._collapse_queue: dict[str, bool] = {}
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(0)
        self._collapse_timer.timeout.connect(self._apply_collapses)
        # Panes shown in place of others by a swap, and the panes they replace.
        self._stand_ins: set[str] = set()
        self._swapped_out: set[str] = set()
        self._root: QWidget | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        # Drag and drop (§6.2): the press a drag may start from, whether QDrag.exec
        # is running, and the tree to apply once it returns.
        self._press: tuple[QPoint, str] | None = None
        self._dragging = False
        self._pending: LayoutNode | None = None
        self._indicator = DropIndicator(ctx.theme, self)
        self.setAcceptDrops(True)

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
                                default_anchor(panel_id, self._panels)), hidden)

    def apply(self, node: LayoutNode, hidden: Mapping[str, Anchor] | None = None) -> None:
        """Show ``node``. ``hidden`` (default: none) is kept for panels not in it.

        A panel that is ``always_shown`` is added at its default place if
        ``node`` lacks it (§6.6).
        """
        tree = normalize(node, _KNOWN_IDS)
        if tree is not None:
            for panel_id in _ALWAYS_SHOWN:
                tree = insert_panel(tree, panel_id, None,
                                    default_anchor(panel_id, panel_ids(tree)))
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
        self._stand_ins, self._swapped_out = set(), set()
        self._root = self._build(tree) if tree is not None else None
        if self._root is not None:
            self._layout.addWidget(self._root)
        self.layout_changed.emit()

    # ---- drag and drop (§6.2) ---------------------------------------------

    def drag_panel(self, panel_id: str) -> None:
        """Run a drag of ``panel_id``; a drop on a target moves it once the drag ends.

        While it runs, the host filters the application's drag events: Qt sends
        them only to the deepest widget that accepts drops (a text box), never on
        to its parents, so the host sees them all this way.
        """
        mime = QMimeData()
        mime.setData(PANEL_MIME, panel_id.encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        self._dragging, self._pending = True, None
        app = QApplication.instance()
        app.installEventFilter(self)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            app.removeEventFilter(self)
            self._dragging = False
            self._indicator.hide()
        # Only now: apply deletes the header or tab bar the drag started from.
        tree, self._pending = self._pending, None
        if tree is not None:
            self.apply(tree, self._hidden)

    def drop_target(self, panel_id: str, pos: QPoint) -> tuple[LayoutNode, QRect] | None:
        """Dropping ``panel_id`` at ``pos`` (host coordinates): the new tree, and the
        area the panel would take. None if ``pos`` isn't a target or nothing changes."""
        dragged = self._panels.get(panel_id)
        root = self.current_layout()
        if dragged is None or not dragged.movable or root is None:
            return None
        pane = self._pane_at(pos, panel_id)
        if pane is None:
            return None
        widget, target, on_tab_bar = pane
        rect = QRect(widget.mapTo(self, QPoint(0, 0)), widget.size())
        zone = DropZone.CENTER if on_tab_bar else drop_zone(
            (pos.x() - rect.x()) / max(rect.width(), 1),
            (pos.y() - rect.y()) / max(rect.height(), 1))
        tree = move_panel(root, panel_id, target, zone)
        return None if tree is root else (tree, zone_rect(rect, zone))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Start a drag from a section header or a tab (installed in ``_build``),
        and, while one runs, handle the drag events of every widget in the host."""
        kind = event.type()
        if kind in _DRAG_EVENTS:
            if self._dragging and isinstance(watched, QWidget) \
                    and (watched is self or self.isAncestorOf(watched)):
                self._on_drag_event(watched, event)
                return True
            return False
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            panel_id = _drag_source(watched, point)
            self._press = (point, panel_id) if panel_id is not None else None
        elif kind == QEvent.Type.MouseMove and self._press is not None \
                and event.buttons() & Qt.MouseButton.LeftButton:
            start, panel_id = self._press
            if (event.position().toPoint() - start).manhattanLength() \
                    >= QApplication.startDragDistance():
                self._press = None
                self.drag_panel(panel_id)
                return True
        elif kind == QEvent.Type.MouseButtonRelease:
            self._press = None
        return super().eventFilter(watched, event)

    def _on_drag_event(self, receiver: QWidget, event: QEvent) -> None:
        if event.type() == QEvent.Type.DragLeave:
            self._indicator.hide()
            return
        assert isinstance(event, QDropEvent)  # also QDragEnterEvent and QDragMoveEvent
        mime = event.mimeData()
        if not mime.hasFormat(PANEL_MIME):
            event.ignore()
            return
        panel_id = bytes(mime.data(PANEL_MIME).data()).decode()
        pos = receiver.mapTo(self, event.position().toPoint())
        move = self.drop_target(panel_id, pos)
        is_drop = event.type() == QEvent.Type.Drop
        if is_drop or move is None:
            self._indicator.hide()
        else:
            self._indicator.setGeometry(move[1])
            self._indicator.show()
            self._indicator.raise_()
        if move is None and event.type() != QEvent.Type.DragEnter:
            event.ignore()  # not a target; an enter is still accepted so moves keep coming
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        if is_drop and move is not None:
            self._pending = move[0]  # applied when drag_panel's QDrag.exec returns

    def _pane_at(self, pos: QPoint, dragged: str) -> tuple[QWidget, str, bool] | None:
        """The pane under ``pos``: its widget, the panel to drop beside, and whether
        ``pos`` is on a tab bar. None over dividers, empty space or an unmovable panel."""
        widget, on_tab_bar = self.childAt(pos), False
        while widget is not None and widget is not self:
            if isinstance(widget, QTabBar) and isinstance(widget.parentWidget(), _PanelTabs):
                on_tab_bar = True
            if isinstance(widget, PanelFrame):
                panel = widget.panel
                return (widget, panel.panel_id, False) if panel.movable else None
            if isinstance(widget, _PanelTabs):
                ids = [widget.widget(i).panel_id for i in range(widget.count())]
                current = ids[widget.currentIndex()] if widget.currentIndex() >= 0 else dragged
                others = [i for i in ids if i != dragged]
                target = current if current != dragged else (others[0] if others else dragged)
                return widget, target, on_tab_bar
            widget = widget.parentWidget()
        return None

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
                        node, children=children, sizes=self._sizes(splitter)
                    )
                case TabsNode():
                    return dataclasses.replace(node, current=next(containers).currentIndex())
            return node

        return read(self._tree)

    def _sizes(self, splitter: QSplitter) -> tuple[int, ...]:
        """The splitter's sizes, with each collapsed pane at its size when it
        collapsed, as if it were shown (see ``_lend``). A swap is read as if
        swapped back: the stand-ins' space goes to the panes they replace."""
        sizes = splitter.sizes()
        frames = [(i, w) for i in range(splitter.count())
                  if isinstance(w := splitter.widget(i), PanelFrame)]
        outs = [(i, f.panel.panel_id) for i, f in frames
                if f.panel.panel_id in self._swapped_out and f.isHidden()]
        ins = [i for i, f in frames if f.panel.panel_id in self._stand_ins and not f.isHidden()]
        if outs and ins:
            freed = sum(sizes[i] for i in ins)
            for i in ins:
                sizes[i] = 0
            wants = [max(self._collapsed_sizes.get(pid, 0), 1) for _, pid in outs]
            for (i, _), share in zip(outs, _shares(freed, wants)):
                sizes[i] = share
        for i in range(splitter.count()):
            frame = splitter.widget(i)
            if isinstance(frame, PanelFrame) and frame.panel.collapsed and sizes[i] == 0:
                if (size := self._collapsed_sizes.get(frame.panel.panel_id)) is not None:
                    sizes = _lend(sizes, i, size) or sizes
        return tuple(sizes)

    @Slot(bool)
    def _on_collapsed(self, collapsed: bool) -> None:
        panel = self.sender()
        if not isinstance(panel, Panel):
            return
        frame = panel.parentWidget()
        if isinstance(frame, PanelFrame) and isinstance(frame.parentWidget(), QSplitter):
            self._collapse_queue[panel.panel_id] = collapsed
            self._collapse_timer.start()
            return
        for tabs in self._containers:
            if isinstance(tabs, _PanelTabs) and (index := tabs.indexOf(panel)) >= 0:
                tabs.setTabVisible(index, not collapsed)
                return

    def _apply_collapses(self) -> None:
        """Apply the queued collapse changes, per splitter (§6.2)."""
        queue, self._collapse_queue = self._collapse_queue, {}
        changes: dict[QSplitter, list[tuple[Panel, bool]]] = {}
        for panel_id, collapsed in queue.items():
            panel = self._panels.get(panel_id)
            frame = panel.parentWidget() if panel is not None else None
            splitter = frame.parentWidget() if isinstance(frame, PanelFrame) else None
            # Gone, moved into tabs, or rebuilt as it is now by an apply since.
            if not isinstance(splitter, QSplitter) or frame.isHidden() == collapsed:
                continue
            changes.setdefault(splitter, []).append((panel, collapsed))
        for splitter, panels in changes.items():
            hiding = [p for p, c in panels if c]
            showing = [p for p, c in panels if not c]
            if not (hiding and showing and self._swap(splitter, hiding, showing)):
                for panel, collapsed in panels:
                    self._collapse_one(splitter, panel, collapsed)

    def _swap(self, splitter: QSplitter, hiding: list[Panel], showing: list[Panel]) -> bool:
        """Give the space of ``hiding`` to ``showing``, in proportion to their sizes
        when they collapsed; the other panes keep theirs. False if nothing is freed."""
        sizes = splitter.sizes()
        index = {p.panel_id: splitter.indexOf(p.parentWidget()) for p in hiding + showing}
        freed = sum(sizes[index[p.panel_id]] for p in hiding)
        if freed <= 0:
            return False
        hid, shown = {p.panel_id for p in hiding}, {p.panel_id for p in showing}
        back = bool(hid & self._stand_ins)  # the stand-ins go: swapped back
        for panel in hiding:
            pid = panel.panel_id
            # A stand-in has no size of its own: it always takes the others' space.
            self._collapsed_sizes[pid] = 0 if pid in self._stand_ins else sizes[index[pid]]
            panel.parentWidget().hide()
        for panel in showing:
            panel.parentWidget().show()
        if back:
            self._stand_ins -= hid
            self._swapped_out -= shown
        else:
            self._stand_ins |= shown
            self._swapped_out |= hid
        wants = [max(self._collapsed_sizes.get(p.panel_id, 0), 1) for p in showing]
        new = list(sizes)
        for panel in hiding:
            new[index[panel.panel_id]] = 0
        for panel, share in zip(showing, _shares(freed, wants)):
            new[index[panel.panel_id]] = share
        splitter.setSizes(new)
        return True

    def _collapse_one(self, splitter: QSplitter, panel: Panel, collapsed: bool) -> None:
        """Lend one pane's space to its neighbour, or take it back."""
        self._stand_ins.discard(panel.panel_id)
        self._swapped_out.discard(panel.panel_id)
        frame = panel.parentWidget()
        index, sizes = splitter.indexOf(frame), splitter.sizes()
        if collapsed:
            if sizes[index] > 0:
                self._collapsed_sizes[panel.panel_id] = sizes[index]
            frame.hide()
            new = _lend(sizes, index, 0)
        else:
            frame.show()
            size = self._collapsed_sizes.get(panel.panel_id)
            new = _lend(sizes, index, size) if size else None
        if new is not None and sum(sizes) > 0:
            splitter.setSizes(new)

    # ---- building --------------------------------------------------------

    def _place_collapsed(self, splitter: QSplitter, sizes: list[int] | None) -> None:
        """Hide the panes of collapsed panels in a new splitter, and share out
        their ``sizes`` (changed in place) as if they had just collapsed. Shown
        panes of size 0 stand in for them (a swap: a commit is selected, §5.4)."""
        frames = [(i, w) for i in range(splitter.count())
                  if isinstance(w := splitter.widget(i), PanelFrame)]
        collapsed = [(i, f) for i, f in frames if f.panel.collapsed]
        for _, frame in collapsed:
            frame.hide()
        if sizes is None:
            return
        for i, frame in collapsed:
            self._collapsed_sizes[frame.panel.panel_id] = sizes[i]
        ins = [(i, f) for i, f in frames if not f.panel.collapsed and sizes[i] == 0]
        freed = sum(sizes[i] for i, _ in collapsed)
        if ins and freed > 0:
            for i, frame in collapsed:
                sizes[i] = 0
                self._swapped_out.add(frame.panel.panel_id)
            for (i, frame), share in zip(ins, _shares(freed, [1] * len(ins))):
                sizes[i] = share
                self._stand_ins.add(frame.panel.panel_id)
            return
        for i, _ in collapsed:  # lent out from the start, as if it had collapsed
            sizes[:] = _lend(sizes, i, 0) or sizes

    def _panel(self, panel_id: str) -> Panel:
        panel = self._panels.get(panel_id)
        if panel is None:
            panel = self._panels[panel_id] = panel_type(panel_id)(self._ctx)
            panel.collapsed_changed.connect(self._on_collapsed)
        return panel

    def _build(self, node: LayoutNode) -> QWidget:
        match node:
            case PanelNode():
                frame = PanelFrame(self._panel(node.panel_id))
                if frame.panel.movable:
                    frame.header.installEventFilter(self)
                return frame
            case SplitNode():
                splitter = QSplitter(_QT_ORIENTATION[node.orientation])
                splitter.setChildrenCollapsible(False)
                self._containers.append(splitter)
                for child in node.children:
                    splitter.addWidget(self._build(child))
                sizes = list(node.sizes) if node.sizes is not None else None
                self._place_collapsed(splitter, sizes)
                if sizes is not None:
                    splitter.setSizes(sizes)
                if node.orientation is Orientation.VERTICAL:
                    _fix_heights(splitter)
                return splitter
            case TabsNode():
                tabs = _PanelTabs()
                tabs.tabBar().installEventFilter(self)
                self._containers.append(tabs)
                for child in node.children:
                    panel = self._panel(child.panel_id)
                    tabs.add_panel(panel)
                    if panel.collapsed:
                        tabs.setTabVisible(tabs.count() - 1, False)
                tabs.setCurrentIndex(node.current)
                return tabs
        raise TypeError(f"not a layout node: {node!r}")


def _drag_source(watched: QObject, pos: QPoint) -> str | None:
    """The movable panel whose header or tab ``watched`` is (the tab at ``pos``)."""
    parent = watched.parentWidget() if isinstance(watched, QWidget) else None
    panel: Panel | None = None
    if isinstance(parent, PanelFrame) and watched is parent.header:
        panel = parent.panel
    elif isinstance(watched, QTabBar) and isinstance(parent, _PanelTabs):
        index = watched.tabAt(pos)
        panel = parent.widget(index) if index >= 0 else None
    return panel.panel_id if isinstance(panel, Panel) and panel.movable else None


def _shares(total: int, weights: list[int]) -> list[int]:
    """``total`` split in proportion to ``weights``; the last takes the remainder."""
    shares = [total * w // sum(weights) for w in weights[:-1]]
    return [*shares, total - sum(shares)]


def _lend(sizes: list[int], index: int, size: int) -> list[int] | None:
    """``sizes`` with pane ``index`` at ``size``, the difference taken from (or
    given to) the shown pane before it: the one it sits below or beside (the
    one after, if it is first).

    So Commit's space goes to Files when it collapses, and comes back from it,
    never from Branches. None when that pane can't make up the difference:
    Qt then shares it out as usual.
    """
    before = [j for j in range(index - 1, -1, -1) if sizes[j] > 0]
    after = [j for j in range(index + 1, len(sizes)) if sizes[j] > 0]
    neighbours = before or after
    if not neighbours:
        return None
    j = neighbours[0]
    delta = size - sizes[index]
    if delta >= sizes[j]:
        return None
    new = list(sizes)
    new[index], new[j] = size, sizes[j] - delta
    return new


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
    """Tabs whose labels follow each panel's header text. Right-clicking a
    tab shows its panel's header actions (spec §6.2)."""

    def __init__(self):
        super().__init__()
        self.setDocumentMode(True)
        self.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabBar().customContextMenuRequested.connect(self._show_menu)

    def menu_for(self, index: int) -> QMenu | None:
        """The menu of the tab at ``index``: its panel's header actions, or None."""
        panel = self.widget(index)
        if not isinstance(panel, Panel) or not panel.header_actions:
            return None
        menu = QMenu(self)
        menu.addActions(list(panel.header_actions))
        return menu

    def _show_menu(self, pos: QPoint) -> None:
        menu = self.menu_for(self.tabBar().tabAt(pos))
        if menu is not None:
            menu.exec(self.tabBar().mapToGlobal(pos))
            menu.deleteLater()

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

