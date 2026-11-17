"""The Panel base class and the context every panel receives (spec §5.1)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QWidget

from rzgitclient.core.column_prefs import ColumnPrefs
from rzgitclient.core.commit_drafts import CommitDrafts
from rzgitclient.core.repo_session import RepoSession
from rzgitclient.core.selection import Selection
from rzgitclient.core.worktree_reader import WorktreeReader
from rzgitclient.ui.app_actions import AppActions
from rzgitclient.ui.theme.app_theme import AppTheme
from rzgitclient.ui.theme.style_roles import Surface, set_surface
from rzgitclient.ui.theme.theme_manager import ThemeManager


@dataclass(frozen=True)
class PanelContext:
    """Everything a panel may use. Panels talk to the rest of the app only
    through this (contract rule 2)."""

    session: RepoSession
    selection: Selection
    theme: ThemeManager
    actions: AppActions
    drafts: CommitDrafts  # the Commit panel's unfinished messages
    columns: ColumnPrefs  # each panel's column choices (ColumnHeader, spec §5.2)
    worktrees: WorktreeReader | None = None  # the Worktrees window's (spec §4.4)


class Panel(QWidget):
    """One feature area of the window. Follow the Panel contract (spec §5.2).

    ``__init__`` is final and runs, in order:

    1. ``QWidget.__init__`` and stores ``ctx``,
    2. sets the content ``surface``, until the layout sets its area's (§6.1),
    3. ``self._setup()``: the panel builds its widgets, connects its signals
       and renders the current state,
    4. ``self.apply_theme(ctx.theme.current)``,
    5. connects ``ctx.theme.theme_changed`` to ``apply_theme``.

    Subclasses set ``panel_id`` and ``title``, override ``_setup()`` and, if
    they paint anything from the theme in code, ``apply_theme()``. Widget
    attributes created in ``_setup()`` are declared in the class body.
    """

    panel_id: ClassVar[str]  # stable, saved in layouts: "history"
    title: ClassVar[str]  # "History"
    show_header: ClassVar[bool] = True  # False: the frame draws no section header
    fixed_height: ClassVar[bool] = False  # True: its pane in a vertical split can't be resized
    always_shown: ClassVar[bool] = False  # True: the layout never leaves it out (§6.6)
    movable: ClassVar[bool] = True  # False: can't be dragged, isn't a drop target (§6.2)

    header_changed = Signal(str)
    activate_requested = Signal()  # "bring me to the front": the layout raises my tab
    collapsed_changed = Signal(bool)  # "nothing to show now": the layout lends my space out
    header_actions_changed = Signal()  # the header menu's actions changed (spec §5.1)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "__init__" in cls.__dict__:
            raise TypeError(
                f"{cls.__name__} overrides Panel.__init__; build widgets in _setup()"
            )
        for name in ("panel_id", "title"):
            if not isinstance(getattr(cls, name, None), str):
                raise TypeError(f"{cls.__name__} must set the class attribute {name!r}")

    def __init__(self, ctx: PanelContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self._header = self.title
        self._collapsed = False
        self._header_actions: tuple[QAction, ...] = ()
        set_surface(self, Surface.CONTENT)  # until the layout places it (§6.1)
        self._setup()
        self.apply_theme(ctx.theme.current)
        ctx.theme.theme_changed.connect(self.apply_theme)

    def _setup(self) -> None:
        """Build widgets, connect signals and render the current state."""

    def apply_theme(self, theme: AppTheme) -> None:
        """Re-apply anything painted from the theme in code. Default: nothing."""

    # ---- header ----------------------------------------------------------

    @property
    def header(self) -> str:
        """The header text the layout shows: ``title`` unless the panel set one."""
        return self._header

    def set_header(self, text: str) -> None:
        """Change the header text, e.g. "Modified files (19)". Emits on change."""
        if text == self._header:
            return
        self._header = text
        self.header_changed.emit(text)

    def request_activation(self) -> None:
        """Ask the layout to show this panel: in tabs, its tab becomes current.

        Only in answer to something the user did, never on a refresh (§5.1).
        """
        self.activate_requested.emit()

    # ---- collapsing ------------------------------------------------------

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        """Ask the layout to give this panel's space to its neighbours for now
        (True), or to take it back. The panel stays in the layout (§5.1)."""
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self.collapsed_changed.emit(collapsed)

    # ---- the header menu (spec §5.1) ----------------------------------------

    @property
    def header_actions(self) -> tuple[QAction, ...]:
        """The panel's commands for the panel as a whole, shown by the layout
        in its header menu (a split) or its tab's menu (tabs)."""
        return self._header_actions

    def set_header_actions(self, actions: Iterable[QAction]) -> None:
        """Replace the header menu's actions. Emits on change."""
        actions = tuple(actions)
        if actions == self._header_actions:
            return
        self._header_actions = actions
        self.header_actions_changed.emit()
