# RZGitClient Architecture

How the app is put together: **Panels** that each provide one feature, arranged by a
**Layout** described as data, all styled by one shared **AppTheme**.

This is the rulebook. New code follows it, and changes to it are made here first. Work
items that implement it are in Linear (team `RZG`, project *App Architecture*), and each
one names the sections below that it covers.

> **Status:** target design. As of 2026-11-15 the code is still the original single
> `ui/main_window.py` + `ui/theme.py`. The Linear epics move it to this structure step by
> step, keeping the app working after every step.

---

## 1. Goals and non-goals

**Goals**
- Each feature area is a self-contained **Panel** that can be built, tested and moved alone.
- Arranging panels is **data** (a layout tree), so it can be saved, restored, swapped and
  later edited by the user without code changes.
- One **AppTheme** object is the only source of colours, fonts and metrics.
- Dependencies point one way (§2), so each layer can be tested without the ones above it.
- Keep the door open for performance work (async git, virtualised lists) **without doing it
  now**. The APIs below are shaped so that work later doesn't change panel code.

**Non-goals (for the first phase)**
- Changing behaviour. The refactor ends with the same window as before it.
- A UI for editing layouts by drag and drop. The data model supports it; the UI comes later.
- A dark theme or loading themes from files. The theme model makes both easy later.

## 2. Layering

```
 git/   pure Python. Runs the git CLI and parses its output. No Qt imports.
  ↑
 core/  application state. QtCore only (QObject, Signal). No widgets.
  ↑
 ui/    widgets. theme/ → widgets/ → panels/ → layout/ → main_window
  ↑
 app.py composition root. The only place concrete objects are created and wired together.
```

Rules (enforced by `tests/test_architecture.py`, which reads imports with `ast`):
- `git/` never imports PySide6.
- `core/` never imports `PySide6.QtWidgets` or `PySide6.QtGui`.
- A panel never imports another panel, `layout/` or `main_window`.
- `ui/layout/layout_model.py` imports no Qt at all (it's plain data).

**Why:** this lets the git layer be tested with no Qt, state be tested with no widgets, and
panels be tested with no window. It also stops the usual drift where widgets start reaching
into each other.

## 3. Package layout

```
src/rzgitclient/
  app.py                        composition root
  git/
    git_models.py               Commit, Branch, FileStatus
    repo.py                     Repo: runs git, returns models
  core/
    repo_session.py             RepoSession: the open repo, its data, git commands
    selection.py                Selection: what the user has selected (commit, file)
    app_settings.py             AppSettings: typed wrapper over QSettings (last_repo, layout)
  ui/
    main_window.py              MainWindow: thin shell (menus, toolbar, status bar, LayoutHost)
    app_actions.py              AppActions: every QAction, defined once
    theme/
      app_theme.py              AppTheme, ThemeColors, StatusColors, ThemeFonts, ThemeMetrics, LIGHT_THEME
      theme_stylesheet.py       build_stylesheet(theme) -> str
      style_roles.py            Surface and Role enums (semantic style hooks)
      theme_manager.py          ThemeManager: current theme + theme_changed
    widgets/
      section_header.py         SectionHeader: shared widget used by frames and panels
    panels/
      panel.py                  Panel, PanelContext
      panel_registry.py         PANEL_TYPES, panel_type(id)
      branches_panel.py         BranchesPanel
      history_panel.py          HistoryPanel
      files_panel.py            FilesPanel
    layout/
      layout_model.py           PanelNode / SplitNode / TabsNode, (de)serialisation, normalize
      layout_presets.py         DEFAULT_LAYOUT
      layout_host.py            LayoutHost
      panel_frame.py            PanelFrame: section header + panel
tests/
  support.py                    temp-repo builder, shared offscreen QApplication
  test_<module>.py              one file per module, named after it (test_layout_model.py, …)
```

**Naming rule:** a module is named after the main class it holds, in snake_case, so its
type is in the file name (`BranchesPanel` → `branches_panel.py`, `ThemeManager` →
`theme_manager.py`). A module of several small related types or functions is named for
the group and its domain (`git_models.py`, `theme_stylesheet.py`). Classes are
PascalCase. `__init__.py` files stay empty: no registries or re-exports hidden in them.

**When to add a module or folder:** when it is either (a) a separate concept that other
code depends on, or (b) code that would otherwise be duplicated. Not for its own sake.

## 4. State and data flow: RepoSession and Selection

### 4.1 RepoSession

`RepoSession(QObject)` (`core/repo_session.py`) is the single owner of "the open repository".

```python
class RepoSession(QObject):
    repo_changed     = Signal(object)  # Repo | None
    head_changed     = Signal(object)  # str | None  (branch name, None = detached)
    branches_changed = Signal(object)  # tuple[Branch, ...]
    history_changed  = Signal(object)  # tuple[Commit, ...]
    status_changed   = Signal(object)  # tuple[FileStatus, ...]
    error_occurred   = Signal(str)

    def open(self, path) -> bool
    def close(self) -> None
    def refresh(self, parts: Refresh = Refresh.ALL) -> None   # Flag: BRANCHES|HISTORY|STATUS|HEAD

    # Latest values, so a panel created later renders at once (read-only properties):
    repo, head, branches, history, status

    # Future git commands (stage, commit, checkout…) are methods here. Each one ends with
    # refresh(<only the parts it changes>).
```

- **Push, not pull.** Panels never ask for data and wait for a return value. They read the
  latest value once when they're built, then listen for signals. **Why:** today git runs
  synchronously. Moving it to a worker thread later only changes `RepoSession`. Panels
  already react to signals, so they don't change.
- **Granular signals.** One signal per kind of data, emitted only when the value actually
  changed (the models are frozen dataclasses, so `==` is cheap). **Why:** after staging a
  file only `status_changed` fires, and History doesn't rebuild 500 rows.
- **Data is immutable.** Values are tuples of frozen dataclasses. Every panel shares the
  session's copy, so none of them can change it behind the others' backs.
- **Opening and closing.** A failed `open()` leaves the current repo open and returns False.
  A successful one forgets the stored values first, so every signal fires for the new repo
  even when its data equals the old repo's. Signals go out in a fixed order: `repo_changed`
  (panels reset), then `head`, `branches`, `history`, `status`. `close()` sends
  `repo_changed(None)` and then empty data.
- **Errors go through `error_occurred`.** MainWindow shows them. Panels never pop up dialogs
  for git failures, so errors look the same wherever they come from. `refresh()` stops at the
  first part that fails and sends one error. Parts already loaded keep their new values;
  the rest keep their old ones.
- **Growth:** when the session gains many commands (stage, commit, checkout, fetch…), they
  can move to a separate `core/git_commands.py` that the session delegates to. Not before.

### 4.2 Selection

`Selection(QObject)` (`core/selection.py`) is how panels talk to each other without
knowing each other:

```python
class FileSource(StrEnum):
    WORKTREE = "worktree"
    INDEX = "index"
    COMMIT = "commit"

@dataclass(frozen=True)
class FileSelection:
    path: str
    source: FileSource
    commit: str | None = None        # set only when source is COMMIT

class Selection(QObject):
    commit_changed = Signal(object)  # Commit | None
    file_changed   = Signal(object)  # FileSelection | None
```

History sets `selection.commit`, and a future Diff panel listens to it. Setters emit only
when the value changes. This is the mediator pattern: panels share state, not references.

Selection is cleared when the repo changes. What happens when a selected commit or file
disappears (after an amend, or staging) is decided with the Diff and Files panels that
care about it.

## 5. Panels

### 5.1 Panel and PanelContext

```python
@dataclass(frozen=True)
class PanelContext:
    session: RepoSession
    selection: Selection
    theme: ThemeManager
    actions: AppActions

class Panel(QWidget):
    panel_id: ClassVar[str]                        # stable, saved in layouts: "history"
    title: ClassVar[str]                           # "History"
    surface: ClassVar[Surface] = Surface.CONTENT   # SIDEBAR for branches/files
    header_changed = Signal(str)                   # optional dynamic header, e.g. "MODIFIED FILES (19)"

    def __init__(self, ctx: PanelContext, parent=None): ...
    def apply_theme(self, theme: AppTheme) -> None: ...   # default no-op; the base wires theme_changed
```

**Why panels subclass QWidget instead of wrapping one:** a widget is what the layout places,
it's idiomatic Qt, and Qt disconnects signals automatically when the panel is destroyed.
A wrapper object would add a layer with no benefit.

### 5.2 The Panel contract

Every panel follows these rules:

1. **One feature area.** A panel owns its widgets and behaviour completely.
2. **No references to other panels, the layout or the window.** It talks only through
   `ctx` (session, selection, actions, theme).
3. **Renders the current state on construction**, then updates from signals. It must work
   when no repo is open (empty state).
4. **Never runs git itself.** It calls `ctx.session` methods. That keeps the async move
   possible (§4.1).
5. **Doesn't draw its own title.** The layout's frame draws the header or tab label from
   `title` and `header_changed`. Panel = content; layout = chrome.
6. **No hard-coded colours, fonts or `setStyleSheet` calls.** It tags widgets with semantic
   roles (§7) and reads `ctx.theme.current` for anything painted in code (status colours,
   monospace hashes). It re-applies those in `apply_theme`.
7. **One instance per `panel_id`.** A panel lives in exactly one place in the layout.

### 5.3 Registry

`panels/panel_registry.py` has an explicit `PANEL_TYPES = (BranchesPanel, HistoryPanel,
FilesPanel)` and a `panel_type(panel_id)` lookup. **Why explicit:** no import-time side
effects or decorator magic, and one place to see every panel. A test checks that ids are
unique.

### 5.4 Panels

The first three are extracted from the original `main_window.py` with the same behaviour:

| Panel | id | Surface | Shows | Writes |
|---|---|---|---|---|
| BranchesPanel | `branches` | sidebar | branches, current in bold | (later: checkout) |
| HistoryPanel | `history` | content | commit table | `selection.commit` |
| FilesPanel | `files` | sidebar | working tree changes, staged/unstaged, status colours | `selection.file` |

FilesPanel is the original "Working Tree Changes" list. Planned feature panels (commit
message, diff, controls) plug in the same way, with no architecture changes.

## 6. Layout

### 6.1 Model

`layout/layout_model.py` is plain immutable data:

```python
@dataclass(frozen=True)
class PanelNode:  panel_id: str
@dataclass(frozen=True)
class SplitNode:  orientation: Orientation; children: tuple[LayoutNode, ...]; sizes: tuple[int, ...] | None = None
@dataclass(frozen=True)
class TabsNode:   children: tuple[PanelNode, ...]; current: int = 0
LayoutNode = PanelNode | SplitNode | TabsNode
```

- `to_dict` / `from_dict` use `{"version": 1, "root": …}`, so the saved format can be migrated.
- `normalize(node, known_ids)` drops unknown or duplicate panel ids, removes empty
  containers and collapses single-child splits. A saved layout from an older version can
  never crash the app. It just loses what no longer exists.
- Immutable: an edit returns a new tree. That makes it simple and safe, and undo and
  presets come almost for free.

The default preset (`layout_presets.py`) reproduces the original window:
`Split(H, [branches, Split(V, [history, files], sizes=(450, 250))], sizes=(220, 880))`.

### 6.2 LayoutHost

`LayoutHost(QWidget)` (`layout/layout_host.py`) turns a tree into widgets:
- `apply(node)` builds `QSplitter`s and `QTabWidget`s. It **reuses existing panel
  instances** by id: they're re-parented, so lists, scroll positions and selection survive.
  It creates missing panels through the registry and deletes panels no longer in the tree.
- `current_layout() -> LayoutNode` reads splitter sizes and the current tab back into a tree.
- In a split, each panel is wrapped in a `PanelFrame` (`layout/panel_frame.py`): a section
  header plus the panel, with the panel's `surface` set as a style property. In tabs, the
  tab label is the title instead.

### 6.3 Persistence

On close, the window saves `current_layout()` through `AppSettings`
(`core/app_settings.py`). On start it loads and `normalize`s that layout, and falls back to
`DEFAULT_LAYOUT` if that fails. A **View → Reset Layout** action restores the default.

### 6.4 Customisation levels

The data model supports all of these now; the UI comes in stages:
1. Code-defined default (first phase)
2. Sizes and tabs remembered between runs (first phase)
3. Switching between preset layouts (later)
4. Editing: move, add and remove panels from a menu or by drag and drop (later)

### 6.5 Why not QDockWidget

Docks bring floating windows, dock title bars and a fixed "dock areas around a central
widget" shape. That fights the flat, borderless look and limits nesting. Docks also save
state as an opaque byte blob, which we can't validate or migrate. A small tree of splitters
and tabs gives full control over the look, is plain data we can test, and is about 150 lines.

## 7. AppTheme

### 7.1 Theme model

`ui/theme/app_theme.py`:

```python
@dataclass(frozen=True)
class ThemeColors:   menubar_bg: str; toolbar_bg: str; sidebar_bg: str; content_bg: str; border: str;
                     text: str; text_muted: str; hover: str; selection: str; content_selection: str;
                     accent: str; button_*: str; scroll_*: str; diff_*: str; …
@dataclass(frozen=True)
class StatusColors:  modified, added, deleted, renamed, copied, type_changed, conflict, untracked: str
                     def for_code(self, code: str) -> str | None   # porcelain letter → colour; None if unknown
@dataclass(frozen=True)
class ThemeFonts:    ui_point_size: float = 9; mono_family: str = "monospace"; header_point_size: float = 8
@dataclass(frozen=True)
class ThemeMetrics:  splitter_width: int = 1; scrollbar_width: int = 10; row_padding: int = 2; row_min_height: int = 18

@dataclass(frozen=True)
class AppTheme:
    name: str; colors: ThemeColors; status: StatusColors; fonts: ThemeFonts; metrics: ThemeMetrics
    def stylesheet(self) -> str     # generated from the tokens by theme_stylesheet.py
    def palette(self) -> QPalette
    def mono_font(self) -> QFont
    def status_color(self, code: str) -> str   # status.for_code(code), falling back to colors.text

LIGHT_THEME = AppTheme(...)
```

- **Typed fields instead of a dict.** A typo becomes an error at import time, and editors
  can autocomplete. Frozen, so a theme can't be half-changed.
- **Each git status has its own colour field**, even where `LIGHT_THEME` gives two the same
  colour (copied = renamed, type changed = modified), so a theme can tell them apart.
- **Which values are tokens:** a colour is always a token. A size or spacing becomes a
  `ThemeMetrics` field only when it's used in more than one place, or a different theme
  would plausibly change it. One-off paddings stay as plain numbers in the stylesheet.
- **Generating the stylesheet:** `theme_stylesheet.py` fills a `string.Template` from the
  colour, font and metric fields combined, so each `$name` is a field name. A test checks
  that no field name appears in two groups.

### 7.2 Semantic roles

`style_roles.py` defines `Surface` (SIDEBAR, CONTENT) and `Role` (SECTION_HEADER).
Widgets get them as Qt dynamic properties through `set_surface()` / `set_role()`, and the
stylesheet targets those (`QWidget[surface="sidebar"]`). The theme decides what "sidebar"
looks like; panels only say what they are.

A surface or role is added when a widget first needs it (e.g. `Surface.CHROME` with the
Controls panel), not ahead of time. A test checks that every value is used by the
stylesheet, so a role can't be defined and forgotten.

Roles style **widgets**. Things inside a list (a muted author column, a monospace hash) are
per-row data, which Qt's stylesheet can't target, so panels set them from the theme's tokens
(`theme.colors.text_muted`, `theme.mono_font()`).

### 7.3 ThemeManager

`ThemeManager(QObject)` (`theme_manager.py`) holds `current` and is the **only** caller of
`QApplication.setStyle`, `setPalette` and `setStyleSheet`. It applies its first theme when
it's created. `set_theme(t)` applies a new theme and emits `theme_changed(t)`, but does
nothing if `t` equals the current theme. The stylesheet repaints itself; panels refresh any colours
they set in code in `apply_theme`.

**Performance rule:** one stylesheet for the whole app, never per-widget `setStyleSheet`.
Per-widget sheets are slow to apply and scatter the rules.

## 8. AppActions and MainWindow

- **`AppActions`** (`ui/app_actions.py`) defines every `QAction` once: text, icon, shortcut
  and enabled state (e.g. Refresh is disabled with no repo open, driven by `repo_changed`).
  The menu bar, toolbar, context menus and a future Controls panel all show the same objects.
- **`MainWindow`** is a thin shell: menus and toolbar built from `AppActions`, the status
  bar summary (from session signals), error dialogs (from `error_occurred`), and a
  `LayoutHost` as the central widget. The Open, Quit and Reset Layout handlers live here
  because they need dialogs or the window. Target: around 100 lines.
- The toolbar stays a `QToolBar` for now. It becomes a **Controls panel** when there are real
  controls to show (fetch/pull/push, the branch box).

## 9. Performance stance

Measure first; optimise what's measured. The design keeps these known future hot spots
cheap to fix **later**, without paying for them now:

| Hot spot | When it matters | Planned fix | Kept open by |
|---|---|---|---|
| git on the UI thread | large repos, network ops | worker thread inside RepoSession | push-based signals (§4.1); panels never run git (§5.2) |
| History rebuilds as item widgets | 10k+ commits | `QAbstractTableModel` + paging | HistoryPanel is the only place that changes |
| Full rebuild on every refresh | frequent refreshes | granular signals, emitted only on change | §4.1 (done now: cheap, and the right semantics anyway) |
| Layout changes recreating panels | layout switching | panel reuse in `LayoutHost.apply` | §6.2 (done now: needed to keep panel state anyway) |
| Restyling | theme switch | one app-wide stylesheet | §7.3 |

## 10. Testing

- `tests/support.py`: `make_repo()` builds throwaway repos, and `qt_app()` returns one shared
  offscreen `QApplication` (`QT_QPA_PLATFORM=offscreen` is set before import).
- One test file per module: `test_repo` (git, no Qt), `test_repo_session` (signals and
  latest values on temp repos), `test_app_theme` / `test_theme_manager` (the stylesheet
  builds, and every role it uses exists), `test_layout_model` (round-trip and `normalize`
  edge cases), `test_layout_host` (build, panel reuse, reading sizes back),
  `test_<name>_panel` (each panel renders a temp repo and the empty state), and
  `test_architecture` (§2 import rules, unique panel ids).
- Stays on stdlib `unittest`: there are no extra dependencies, and VS Code's test panel is
  already set up for it.

## 11. Conventions

- Type hints everywhere, with `from __future__ import annotations`.
- Value types are `@dataclass(frozen=True)`. State with signals is a `QObject`.
- Signals are named `<thing>_changed` / `<thing>_occurred` and carry the new value.
- No module-level mutable state. Constants and presets are immutable.
- Private members are `_prefixed`. Modules stay focused; around 300 lines is a cue to split.
- Every user-visible string lives in the panel or action that shows it (ready for i18n later).
