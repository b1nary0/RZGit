"""Builds the window's areas and panes from a layout state (spec §6.2)."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType

from PySide6.QtCore import (
    QEvent, QMimeData, QObject, QPoint, QPointF, QRect, Qt, QTimer, Signal, Slot,
)
from PySide6.QtGui import QAction, QDrag, QDropEvent, QKeySequence, QMouseEvent
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
    Area,
    DropZone,
    LayoutNode,
    LayoutState,
    Orientation,
    PanelNode,
    SplitNode,
    TabsNode,
    area_order,
    area_orientation,
    drop_zone,
)
from rzgitclient.ui.layout.drop_indicator import DropIndicator, zone_rect
from rzgitclient.ui.layout.layout_presets import default_anchor
from rzgitclient.ui.layout.panel_frame import PanelFrame
from rzgitclient.ui.panels.panel import Panel, PanelContext
from rzgitclient.ui.panels.panel_registry import PANEL_TYPES, panel_type
from rzgitclient.ui.theme.style_roles import Surface, set_surface

_KNOWN_IDS = frozenset(t.panel_id for t in PANEL_TYPES)
_ALWAYS_SHOWN = tuple(t.panel_id for t in PANEL_TYPES if t.always_shown)
_QT_ORIENTATION = {
    Orientation.HORIZONTAL: Qt.Orientation.Horizontal,
    Orientation.VERTICAL: Qt.Orientation.Vertical,
}
PANEL_MIME = "application/x-rzgitclient-panel"  # a dragged panel's id (§6.2)
_DRAG_EVENTS = frozenset({QEvent.Type.DragEnter, QEvent.Type.DragMove,
                          QEvent.Type.DragLeave, QEvent.Type.Drop})
# An empty area never shrinks below this, so it stays a drop target (spec §6.2).
EMPTY_AREA_MIN = 48
# Each area's background (spec §6.1, §7.2): its panels take it, wherever they came from.
AREA_SURFACES = {Area.CONTROLS: Surface.CHROME, Area.SIDEBAR: Surface.SIDEBAR,
                 Area.MAIN: Surface.CONTENT}


class AreaPane(QWidget):
    """One area (spec §6.2): its tree's widget on the area's surface, or, when
    the area is empty, the surface alone."""

    def __init__(self, area: Area, content: QWidget | None):
        super().__init__()
        self.area = area
        self.content = content
        set_surface(self, AREA_SURFACES[area])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if content is not None:
            layout.addWidget(content)
        else:
            self.setMinimumSize(EMPTY_AREA_MIN, EMPTY_AREA_MIN)


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
        self._state = LayoutState()  # as applied; state() reads the sizes back
        # Each area's splitters and tabs, in pre-order of its tree.
        self._containers: dict[Area, list[QSplitter | QTabWidget]] = {}
        self._body: QSplitter | None = None  # the Sidebar and the Main pane
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
        # Drag and drop (§6.2): the press a drag may start from (where, the panel, and
        # its tab's index, -1 for a header), whether QDrag.exec is running, and the
        # tree to apply once it returns.
        self._press: tuple[QPoint, str, int] | None = None
        self._dragging = False
        self._pending: LayoutState | None = None
        self._indicator = DropIndicator(ctx.theme, self)
        self.setAcceptDrops(True)

    @property
    def panels(self) -> Mapping[str, Panel]:
        return MappingProxyType(self._panels)

    @property
    def hidden(self) -> Mapping[str, Anchor]:
        """Hidden panels, and where each sat."""
        return self._state.hidden

    def area_of(self, panel_id: str) -> Area | None:
        """The area ``panel_id`` is shown in; None if it isn't shown (spec §6.1)."""
        return self._state.area_of(panel_id)

    def can_hide(self, panel_id: str) -> bool:
        """Shown, not ``always_shown``, and not the last panel that could be hidden."""
        hideable = [pid for pid in self._panels if pid not in _ALWAYS_SHOWN]
        return panel_id in hideable and len(hideable) > 1

    def hide_panel(self, panel_id: str) -> None:
        """Hide a shown panel, remembering where it sat (if ``can_hide``)."""
        if self.can_hide(panel_id):
            self.apply(self.state().hide(panel_id))

    def show_panel(self, panel_id: str) -> None:
        """Show a panel where it sat when hidden, else at its default place."""
        if panel_id in self._panels or panel_id not in _KNOWN_IDS:
            return
        self.apply(self.state().show(panel_id, default_anchor(panel_id, self._panels)))

    def apply(self, state: LayoutState) -> None:
        """Show ``state``: its areas, and its hidden panels kept for later.

        A panel that is ``always_shown`` is added at its default place if
        ``state`` lacks it (§6.6).
        """
        state = state.normalized(_KNOWN_IDS)
        for panel_id in _ALWAYS_SHOWN:
            state = state.insert(panel_id, None, default_anchor(panel_id, state.panel_ids()))
        wanted = set(state.panel_ids())

        # Take every panel out of the old containers before they are deleted.
        for panel_id, panel in list(self._panels.items()):
            panel.setParent(None)
            if panel_id not in wanted:
                del self._panels[panel_id]
                panel.deleteLater()
        if self._root is not None:
            self._layout.removeWidget(self._root)
            self._root.deleteLater()

        self._state, self._containers = state, {}
        self._stand_ins, self._swapped_out = set(), set()
        self._root = self._build_areas(state)
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
        state, self._pending = self._pending, None
        if state is not None:
            self.apply(state)

    def drop_target(self, panel_id: str, pos: QPoint) -> tuple[LayoutState, QRect] | None:
        """Dropping ``panel_id`` at ``pos`` (host coordinates): the new state, and the
        part of the window the panel would take. None if ``pos`` isn't a target or
        nothing changes."""
        dragged = self._panels.get(panel_id)
        if dragged is None or not dragged.movable:
            return None
        pane = self._pane_at(pos, panel_id)
        if pane is None:
            return None
        widget, target, on_tab_bar = pane
        rect = QRect(widget.mapTo(self, QPoint(0, 0)), widget.size())
        state = self.state()
        if isinstance(widget, AreaPane):  # an empty area: the panel fills it
            moved = state.move_to_area(panel_id, widget.area)
            return None if moved is state else (moved, rect)
        zone = DropZone.CENTER if on_tab_bar else drop_zone(
            (pos.x() - rect.x()) / max(rect.width(), 1),
            (pos.y() - rect.y()) / max(rect.height(), 1))
        moved = state.move(panel_id, target, zone)
        return None if moved is state else (moved, zone_rect(rect, zone))

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
            index = watched.tabAt(point) if isinstance(watched, QTabBar) else -1
            self._press = (point, panel_id, index) if panel_id is not None else None
        elif kind == QEvent.Type.MouseMove and self._press is not None \
                and event.buttons() & Qt.MouseButton.LeftButton:
            start, panel_id, index = self._press
            pos, distance = event.position().toPoint(), QApplication.startDragDistance()
            if isinstance(watched, QTabBar):
                if watched.rect().adjusted(-distance, -distance, distance, distance) \
                        .contains(pos):
                    return False  # along the bar: Qt moves the tab (§6.2)
                self._press = None
                self._end_tab_move(watched, pos, panel_id, index)
                self.drag_panel(panel_id)
                return True
            if (pos - start).manhattanLength() >= distance:
                self._press = None
                self.drag_panel(panel_id)
                return True
        elif kind == QEvent.Type.MouseButtonRelease:
            self._press = None
        return super().eventFilter(watched, event)

    def _end_tab_move(self, bar: QTabBar, pos: QPoint, panel_id: str, index: int) -> None:
        """End Qt's move of ``panel_id``'s tab (a release it reads as the end of the
        gesture) and put the tab back at ``index``, where the press found it."""
        QApplication.sendEvent(bar, QMouseEvent(
            QEvent.Type.MouseButtonRelease, QPointF(pos), QPointF(bar.mapToGlobal(pos)),
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        now = bar.parentWidget().indexOf(self._panels[panel_id])
        if index >= 0 and now >= 0 and now != index:
            bar.moveTab(now, index)

    @Slot(int, int)
    def _on_tab_moved(self, _from: int, _to: int) -> None:
        self.layout_changed.emit()

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
        ``pos`` is on a tab bar; or an empty Sidebar or Main pane (an ``AreaPane``,
        no panel). None over dividers or an unmovable panel (the Controls bar: an
        empty Controls area is never built)."""
        widget, on_tab_bar = self.childAt(pos), False
        while widget is not None and widget is not self:
            if isinstance(widget, AreaPane):
                return (widget, "", False) if widget.content is None else None
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

    def state(self) -> LayoutState:
        """What to save: the applied state with the splitter sizes, current tabs and
        the split between the Sidebar and the Main pane as they are now."""
        areas = {a: self._current_tree(a) for a in Area}
        sizes = self._state.sizes
        if self._body is not None and sum(now := self._body.sizes()) > 0:
            first, second = now
            sizes = (first, second) if area_order(self._state.arrangement)[0] is Area.SIDEBAR \
                else (second, first)
        return dataclasses.replace(self._state, areas=areas, sizes=sizes)

    def _current_tree(self, area: Area) -> LayoutNode | None:
        """``area``'s applied tree with its splitter sizes and current tabs as they are now."""
        tree = self._state.tree(area)
        if tree is None:
            return None
        containers = iter(self._containers[area])

        def read(node: LayoutNode) -> LayoutNode:
            match node:
                case SplitNode():
                    splitter = next(containers)
                    children = tuple(read(c) for c in node.children)
                    return dataclasses.replace(
                        node, children=children, sizes=self._sizes(splitter)
                    )
                case TabsNode():
                    tabs = next(containers)
                    order = [tabs.widget(i).panel_id for i in range(tabs.count())]
                    by_id = {c.panel_id: c for c in node.children}
                    # In the order the user left the tabs (§6.2).
                    children = tuple(by_id[i] for i in order) \
                        if sorted(order) == sorted(by_id) else node.children
                    return dataclasses.replace(node, children=children,
                                               current=tabs.currentIndex())
            return node

        return read(tree)

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
        for tabs in (c for area in self._containers.values() for c in area):
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

    def _panel(self, panel_id: str, area: Area) -> Panel:
        """The panel, made if it's new, on ``area``'s surface."""
        panel = self._panels.get(panel_id)
        if panel is None:
            panel = self._panels[panel_id] = panel_type(panel_id)(self._ctx)
            panel.collapsed_changed.connect(self._on_collapsed)
        set_surface(panel, AREA_SURFACES[area])  # repolished if it changed area
        return panel

    def _build_areas(self, state: LayoutState) -> QWidget:
        """Controls above a split of the Sidebar and the Main pane, in the
        arrangement's order; an empty Controls area is left out."""
        def pane(area: Area) -> AreaPane:
            tree = state.tree(area)
            self._containers[area] = []
            return AreaPane(area, self._build(tree, area) if tree is not None else None)

        outer = QSplitter(Qt.Orientation.Vertical)
        outer.setChildrenCollapsible(False)
        if state.tree(Area.CONTROLS) is not None:
            outer.addWidget(pane(Area.CONTROLS))
        else:
            self._containers[Area.CONTROLS] = []
        body = self._body = QSplitter(_QT_ORIENTATION[area_orientation(state.arrangement)])
        body.setChildrenCollapsible(False)
        order = area_order(state.arrangement)
        for area in order:
            body.addWidget(pane(area))
        # No sizes: an even split, so an empty area keeps its space too.
        sizes = state.sizes or (1, 1)
        body.setSizes(list(sizes if order[0] is Area.SIDEBAR else sizes[::-1]))
        outer.addWidget(body)
        _fix_heights(outer)
        return outer

    def _build(self, node: LayoutNode, area: Area) -> QWidget:
        match node:
            case PanelNode():
                frame = PanelFrame(self._panel(node.panel_id, area), AREA_SURFACES[area])
                if frame.panel.movable:
                    frame.header.installEventFilter(self)
                return frame
            case SplitNode():
                splitter = QSplitter(_QT_ORIENTATION[node.orientation])
                splitter.setChildrenCollapsible(False)
                self._containers[area].append(splitter)
                for child in node.children:
                    splitter.addWidget(self._build(child, area))
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
                tabs.tabBar().tabMoved.connect(self._on_tab_moved)
                self._containers[area].append(tabs)
                for child in node.children:
                    panel = self._panel(child.panel_id, area)
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


def _fixed_height(widget: QWidget) -> bool:
    """A pane of a ``fixed_height`` panel, or an area holding just one (Controls)."""
    if isinstance(widget, AreaPane):
        widget = widget.content
    return isinstance(widget, PanelFrame) and widget.panel.fixed_height


def _fix_heights(splitter: QSplitter) -> None:
    """Fix the panes of ``fixed_height`` panels, and lock the dividers beside them."""
    for i in range(splitter.count()):
        frame = splitter.widget(i)
        if not _fixed_height(frame):
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
    """Tabs whose labels follow each panel's header text. The tabs can be
    reordered: dragged along the bar, from their right-click menu (after the
    panel's header actions) or with Ctrl+Shift+PgUp/PgDn (spec §6.2)."""

    def __init__(self):
        super().__init__()
        self.setDocumentMode(True)
        self.tabBar().setMovable(True)
        self.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabBar().customContextMenuRequested.connect(self._show_menu)
        for key, slot in (("Ctrl+Shift+PgUp", self._move_current_left),
                          ("Ctrl+Shift+PgDown", self._move_current_right)):
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.triggered.connect(slot)
            self.addAction(action)

    def menu_for(self, index: int) -> QMenu | None:
        """The menu of the tab at ``index``: its panel's header actions, then Move
        Left and Move Right; None if there is no tab there."""
        panel = self.widget(index)
        if not isinstance(panel, Panel):
            return None
        menu = QMenu(self)
        if panel.header_actions:
            menu.addActions(list(panel.header_actions))
            menu.addSeparator()
        for text, step in (("Move &Left", -1), ("Move &Right", 1)):
            action = menu.addAction(text)
            action.setEnabled(self.neighbour(index, step) is not None)
            action.triggered.connect(lambda _=False, s=step: self.move_tab(self.indexOf(panel), s))
        return menu

    def neighbour(self, index: int, step: int) -> int | None:
        """The nearest shown tab from ``index`` in the direction of ``step`` (-1
        left, 1 right), or None. Collapsed panels' tabs are skipped."""
        i = index + step
        while 0 <= i < self.count():
            if self.isTabVisible(i):
                return i
            i += step
        return None

    def move_tab(self, index: int, step: int) -> None:
        """Move the tab at ``index`` past its shown neighbour on one side; the
        current tab stays on the same panel."""
        target = self.neighbour(index, step) if 0 <= index < self.count() else None
        if target is not None:
            self.tabBar().moveTab(index, target)

    @Slot()
    def _move_current_left(self) -> None:
        self.move_tab(self.currentIndex(), -1)

    @Slot()
    def _move_current_right(self) -> None:
        self.move_tab(self.currentIndex(), 1)

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

