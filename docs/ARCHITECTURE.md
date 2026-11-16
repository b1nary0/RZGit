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
 ui/    widgets. theme/ → widgets/ → app_actions → panels/ → layout/ → main_window
  ↑
 app.py composition root. The only place concrete objects are created and wired together.
```

Rules (enforced by `tests/test_architecture.py`, which reads imports with `ast`):
- `git/` never imports PySide6.
- `core/` never imports `PySide6.QtWidgets` or `PySide6.QtGui`.
- A panel never imports another panel, `layout/` or `main_window`.
- A panel never imports `git/repo.py`: it never runs git (contract rule 4). It may import
  `git/git_models.py` for types.
- Imports point down the diagram: `git/` never imports `core/` or `ui/`, and `core/` never
  imports `ui/`. Inside `ui/`, a module imports only from its own or a lower layer, in the
  order above (`app.py` may import anything).
- `ui/layout/layout_model.py` imports no Qt at all (it's plain data).

The rule checker is a function, and its tests feed it small sources that break each rule,
so every rule is shown to fire.

**Why:** this lets the git layer be tested with no Qt, state be tested with no widgets, and
panels be tested with no window. It also stops the usual drift where widgets start reaching
into each other.

## 3. Package layout

```
src/rzgitclient/
  app.py                        composition root
  git/
    git_models.py               Commit, Branch, FileStatus, FileDiff, Hunk, DiffLine
    repo.py                     Repo: runs git, returns models
  core/
    repo_session.py             RepoSession: the open repo, its data, git commands
    diff_request.py             DiffRequest, the size limit, load_diff (the worker side of diffs)
    git_commands.py             GitCommand, run_command (the worker side of commands: stage, commit, …)
    commit_drafts.py            CommitDrafts: the unfinished commit message of each repository
    selection.py                Selection: what the user has selected (commit, file)
    app_settings.py             AppSettings: typed wrapper over QSettings (last_repo, layout, …)
  ui/
    main_window.py              MainWindow: thin shell (menus, status bar, LayoutHost)
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
      controls_panel.py         ControlsPanel: Open, Refresh, current branch (replaces the toolbar)
      branches_panel.py         BranchesPanel
      history_panel.py          HistoryPanel
      history_model.py          HistoryModel: the commit table's Qt model, paged
      files_panel.py            FilesPanel: "Unstaged Files"
      staged_panel.py           StagedPanel: "Staged Files"
      file_list.py              FileList: one source's changed files, the body of both
      commit_files_panel.py     CommitFilesPanel: the files the selected commit changed
      diff_panel.py             DiffPanel
      diff_view.py              DiffView: read-only text with a line-number gutter
      diff_rows.py              DiffRow, unified_rows, split_rows (plain Python, no Qt)
      commit_panel.py           CommitPanel: the message box and Commit button
    layout/
      layout_model.py           PanelNode / SplitNode / TabsNode, (de)serialisation, normalize
      layout_presets.py         PRESETS, DEFAULT_LAYOUT, restore_layout
      layout_host.py            LayoutHost
      panel_frame.py            PanelFrame: section header + panel
      drop_indicator.py         DropIndicator: where a dragged panel will land
      layout_menu.py            LayoutMenu: View → Layout (presets, saved layouts)
      manage_layouts_dialog.py  ManageLayoutsDialog: rename and delete saved layouts
      panels_menu.py            PanelsMenu: View → Panels (show and hide)
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
    commit_count_changed = Signal(object)  # int | None  (commits reachable from HEAD)
    diff_changed     = Signal(object, object)  # FileSelection | None, FileDiff | None
    commit_files_changed = Signal(object, object)  # sha | None, tuple[FileStatus, ...] | None
    command_finished = Signal(object, bool)    # GitCommand, whether it ran without error
    error_occurred   = Signal(str)
    busy_changed     = Signal(bool)  # True while git work is queued or running

    def open(self, path) -> bool
    def close(self) -> None
    def refresh(self, parts: Refresh = Refresh.ALL) -> None   # Flag: HEAD|BRANCHES|HISTORY|STATUS|COUNT
    def load_more_history(self) -> None   # the next page of history, in the background
    def request_diff(self, file: FileSelection | None, full: bool = False) -> None
    def request_commit_files(self, sha: str | None) -> None   # the files a commit changed

    # Commands: they change the repository (§4.1, "Commands").
    def stage(self, paths) -> None        # git add
    def unstage(self, paths) -> None      # git restore --staged (git rm --cached before the first commit)
    def stage_all(self) -> None
    def unstage_all(self) -> None
    def discard(self, paths) -> None      # unstaged changes; untracked files go to the trash
    def commit(self, message) -> GitCommand | None   # the staged changes; None with no repo

    # Latest values, so a panel created later renders at once (read-only properties):
    repo, head, branches, history, status, commit_count, history_complete, diff_file, diff,
    commit_files_sha, commit_files
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
  (panels reset), then `head`, `branches`, `history`, `status`, `commit_count`. `close()` sends
  `repo_changed(None)` and then empty data.
- **Errors go through `error_occurred`.** MainWindow shows them. Panels never pop up dialogs
  for git failures, so errors look the same wherever they come from. `refresh()` stops at the
  first part that fails and sends one error. Parts already loaded keep their new values;
  the rest keep their old ones.
- **Git runs on one worker thread.** `open()` checks the folder synchronously (one fast,
  local git call) and returns True/False as before; loading the data and every `refresh()`
  run on a single background thread, one job at a time, in order. Signals are emitted on
  the main thread. **Why one thread, not a pool:** git commands on the same repo can clash
  on `index.lock`, and in-order jobs can't deliver results out of order.
  - A `refresh()` requested while another is waiting merges into the waiting one.
  - Results from a repo that has since been closed or replaced are dropped (a generation
    counter, bumped by `open()` and `close()`).
  - A job loads its parts, then emits them in the fixed order; the error rule above holds
    per job.
  - On quit, the session waits for the running git command (it can't be interrupted).
  - `busy_changed(True)` when the first job is queued, `False` when the queue is empty. The
    window shows it in the status bar at once. Tests wait with `wait_idle()`.
- **History loads in pages** of `HISTORY_PAGE` (500) commits, from `HEAD`.
  - `history` holds every commit loaded so far; `history_complete` is True once the oldest
    commit is in it (and with no repo, so nothing asks for more).
  - `load_more_history()` queues the next page on the worker. It does nothing when history
    is complete, or a page is already queued or loading. A page job runs
    `git log --skip=<loaded>` and emits `history_changed` with the longer tuple, whose first
    part is the very same objects as before, so a listener can tell it's an append. Every
    read asks git for one commit more than it keeps, to know whether it reached the end.
  - **Paging relies on HEAD.** History from the same HEAD commit can't change, so a job
    first compares `HEAD` with the newest loaded commit:
    - **Unchanged:** a refresh runs no `git log` at all, however much is loaded, and a page
      continues where the last load ended.
    - **Moved** (a commit, a checkout, or anything outside the app): the job reloads, so
      `--skip` never repeats or misses commits.
  - **A reload keeps what was loaded.** It reads a page more than was loaded and keeps
    everything down to the oldest commit loaded before, so the user's scroll position and
    selection survive new commits on top. If that commit is gone (another branch), it keeps
    as many whole pages as were loaded. A page requested at the same time is added on.
- **The commit count** (`git rev-list --count HEAD`, 0 before the first commit) is exact
  however little history is loaded. It never runs in the same job as `HISTORY`: when both
  are due, the count waits for the next job, so the first page isn't delayed by it (0.1 s
  for 200k commits). `commit_count` is None until it arrives; parts still queued aren't
  reported as empty after a job.
- **Diffs are asked for.** Unlike the parts above, a diff is for one file, the one the
  Diff panel shows. `request_diff(file)` makes `file` the session's **diff target** and
  queues it on the same worker, so a diff is never read between two parts of a refresh.
  - The result arrives as `diff_changed(file, diff)`, after the parts of its job, and only
    when it differs from the last one. `diff` is None when nothing is selected or the
    diff couldn't be read (the error goes through `error_occurred` as usual).
  - A newer request makes older ones stale: a diff counter, bumped by every request and by
    `open()` and `close()`, drops their results. `request_diff(None)` clears the target.
  - **The diff follows refreshes.** Every job that loads `STATUS` reads the target's diff
    again, after the status, when the target is a worktree or index file. Equal results
    aren't emitted, so the panel only redraws when the file changed. If an earlier part of
    the job fails, the diff is queued again by itself.
  - The worker side is `core/diff_request.py`. It picks the git source from the latest
    status: a worktree file that status calls untracked is diffed against nothing, and a
    staged rename passes its old path too, so git shows it as a rename.
  - **Size limit:** a diff of more than `DIFF_MAX_BYTES` (1 MB) or `DIFF_MAX_LINES`
    (20,000 lines) comes back as `FileDiff(too_large=True)` with no hunks, and isn't parsed.
    `request_diff(file, full=True)` reads it whole; the target keeps `full` across refreshes.
- **A commit's files are asked for** too, like a diff: `request_commit_files(sha)` makes
  `sha` the **commit files target**, and the result arrives as `commit_files_changed(sha,
  files)`, after the parts of its job. A newer request, `open()` and `close()` make older
  results stale (their own counter); `None` clears the target. A commit never changes,
  so refreshes don't read them again.
  - `Repo.commit_files(sha)` runs `git diff-tree -r -M --root --first-parent
    --no-commit-id --name-status -z`: a merge is compared with its first parent, the
    first commit with nothing. Each change is a `FileStatus` whose `index` is its code
    (`M`, `A`, `D`, `R`…) and whose `worktree` is `" "`: like a staged change, which is
    what a commit is to its parent; `orig_path` is a rename's source.
  - A commit file's diff (`DiffSource.COMMIT`, `git show --diff-merges=first-parent`)
    passes a rename's old path, found in the latest commit files, so git shows it as a
    rename.
- **Commands** change the repository. A command is a `GitCommand` (`core/git_commands.py`)
  queued on the same worker as refreshes, so commands and loads never overlap and run in the
  order they were asked for.
  - A command adds the parts it changes to its job (staging changes only `STATUS`), and the
    job loads them after running its commands, so every command is followed by a refresh
    of what it changed, and the diff target's diff with it. Commands asked for while a job
    runs go into the next job, still in order. A command asked for with no repo does nothing.
  - A failing command sends its error through `error_occurred`, and the job skips the
    commands after it but still loads its parts, so the window shows what really happened.
  - `open()`, `close()` and `shutdown()` drop commands that haven't run: they were meant for
    the repository that was open.
  - `busy` covers commands like any other job.
  - The worker side, `run_command(repo, command)`, reads the status itself where a command
    depends on it (a staged rename unstages its old path too; discard tells untracked files
    from tracked ones), so a stale status in the window can't make it act on the wrong file.
  - **Discard** restores the worktree from the index (`git restore`), so staged work is
    kept. Untracked files are moved to the trash (`QFile.moveToTrash`), never deleted; if the
    trash can't be used, that is an error. The confirmation is the window's job (§8).
  - Paths are passed with `--literal-pathspecs`, so a file named `:x` or `*.c` means itself.
  - **Results:** after a job, `command_finished(command, ok)` is sent for each of its
    commands, in order, after the parts and the error: `ok` is False for the one that
    failed and for those skipped after it. The command object is the one the method
    queued (`commit()` returns it), so a panel can tell its own command by identity.
    Commands of a repository closed meanwhile report nothing, like the rest of the job.
  - **Commit** (`git commit -q --cleanup=whitespace -F -`) passes the message on stdin and
    reloads `HEAD | BRANCHES | HISTORY | STATUS | COUNT`. Hooks run as in a terminal; a
    failing hook is a failed command, and its output is the error. `--cleanup=whitespace`,
    not `strip`: the message box has no template, so a line starting with `#` is the
    user's text, not a comment.
  - **No editor or prompt, ever:** git runs with `GIT_EDITOR=false` and
    `GIT_TERMINAL_PROMPT=0`, and with stdin closed unless a command passes input. A
    command that would open an editor fails instead of waiting for it.

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
    file_changed   = Signal(object)  # FileSelection | None: the current row, what Diff shows
    files_changed  = Signal(object)  # tuple[FileSelection, ...]: every selected row
```

History sets `selection.commit`. The Unstaged Files and Staged Files panels call
`selection.select(files, current)`, which sets `selection.files` (every selected row, which
the stage actions act on) and `selection.file` (the current row); the Diff panel listens to
`file_changed`. Setters emit only when the value changes. This is the mediator pattern:
panels share state, not references.

**Selected files follow the status.** Selection owns what happens to selected files when
the status changes, so the two file panels never need to know each other. It listens to
`status_changed` before any panel does (it is created first), and each selected row:
- stays, if the new status still has it (`file_rows(status)`: a worktree row for each
  unstaged or untracked file, an index row for each staged one);
- or moves to its **follow target**: `follow(paths, source)` says those paths are about
  to move to `source` (Stage and Unstage call it, from the menu, the keyboard or a
  double-click). The target waits until a status has the row (an older refresh may arrive
  first), and `select()` cancels it: the user chose something else;
- or moves to the same path in the other source (staged or unstaged outside the app);
- or is dropped, and the Diff panel shows its empty state if it was the current one.

Selection is cleared when the repo changes.

**The selected commit.** `selection.commit` is the commit selected in History, or None for
the **Uncommitted changes** row (the working tree), which is where it starts. A Commit
Files row is `FileSelection(path, COMMIT, sha)`.
- Selecting another commit (or Uncommitted changes) keeps only the selected rows that
  belong to it: a commit's files for a commit, worktree and index rows for Uncommitted
  changes. The panels that show the others are collapsed then (§5.4), so neither the
  Diff panel nor the stage actions act on rows the user can't see.
- **A selected commit that disappears** (amend, reset, rebase): Selection listens to
  `history_changed` (before any panel) and sets `commit` to None when the new history
  doesn't have it, which is back to Uncommitted changes. Loaded history always reaches
  the selected commit (a reload keeps every commit down to the oldest loaded, §4.1), so
  one missing from it is gone.

## 5. Panels

### 5.1 Panel and PanelContext

```python
@dataclass(frozen=True)
class PanelContext:
    session: RepoSession
    selection: Selection
    theme: ThemeManager
    actions: AppActions
    drafts: CommitDrafts                           # the Commit panel's unfinished messages

class Panel(QWidget):
    panel_id: ClassVar[str]                        # stable, saved in layouts: "history"
    title: ClassVar[str]                           # "History"
    surface: ClassVar[Surface] = Surface.CONTENT   # SIDEBAR for branches/files
    show_header: ClassVar[bool] = True             # False: the frame draws no section header
    fixed_height: ClassVar[bool] = False           # True: its pane in a vertical split can't be resized
    always_shown: ClassVar[bool] = False           # True: the layout never leaves it out (§6.6)
    movable: ClassVar[bool] = True                 # False: can't be dragged, isn't a drop target (§6.2)
    header_changed = Signal(str)                   # emitted by set_header()
    activate_requested = Signal()                  # emitted by request_activation()
    collapsed_changed = Signal(bool)               # emitted by set_collapsed()

    def __init__(self, ctx: PanelContext, parent=None): ...   # final: panels don't override it
    def _setup(self) -> None: ...                         # the panel builds its widgets here
    def apply_theme(self, theme: AppTheme) -> None: ...   # default no-op; the base wires theme_changed
    header: str                                           # read-only; `title` until set_header()
    def set_header(self, text: str) -> None: ...          # e.g. "Modified files (19)"; emits on change
    def request_activation(self) -> None: ...             # "bring me to the front" (a tab)
    collapsed: bool                                       # read-only; False until set_collapsed()
    def set_collapsed(self, collapsed: bool) -> None: ... # "nothing to show here now"; emits on change
```

`set_collapsed(True)` asks the layout to give the panel's space to its neighbours for now,
without hiding it (§6.2): the panel stays in the tree, View → Panels still shows it
checked, and saved layouts keep its size. The Commit panel collapses while nothing is
staged. Hiding, by contrast, is the user's choice (§6.6), and a hidden panel stays hidden
whatever it would ask.

`request_activation()` asks the layout to make the panel visible; the panel doesn't know
whether it sits in a tab group. The host raises its tab (§6.2); in a split it is visible
already, so nothing happens. A panel asks for this only in answer to something the user
did (the Diff panel, when the user selects another file), never on a refresh.

The panel keeps its current header text, so a frame built later (when `LayoutHost` reuses
a panel, §6.2) shows the latest one instead of falling back to `title`. Header text is
normal case; `SectionHeader` displays it in upper case, and tab labels use it as is.

`Panel.__init_subclass__` enforces the rules it can check when the class is defined: a
panel that overrides `__init__`, or doesn't set `panel_id` and `title`, raises `TypeError`.

**Construction order.** `Panel.__init__` is a template method. In order, it:

1. calls `QWidget.__init__` and stores `ctx`,
2. sets the `surface` style property,
3. calls `self._setup()`. The panel builds its widgets, connects its signals and renders
   the current state (contract rule 3),
4. calls `self.apply_theme(ctx.theme.current)`,
5. connects `ctx.theme.theme_changed` to `apply_theme`.

Panels don't define `__init__` and take no constructor arguments besides `ctx` and `parent`.
Everything they need comes through `ctx`. Widget attributes created in `_setup()` are
declared in the class body (`_list: QListWidget`), so readers and type checkers see them.

**Why a hook instead of each panel's own `__init__`:** the base has work to do *after* the
panel's widgets exist (the first `apply_theme`). With a hook that order can't be got wrong
or forgotten, and a step for every panel can be added in one place. "Call `super().__init__`
last" isn't an option: PySide6 refuses to parent child widgets to a `QWidget` whose
`__init__` hasn't run. The cost is a less familiar pattern and attributes created outside
`__init__`. Revisit if a panel ever needs its own constructor arguments or the hook gets in
the way.

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

`panels/panel_registry.py` has an explicit `PANEL_TYPES = (ControlsPanel, BranchesPanel,
HistoryPanel, FilesPanel, StagedPanel, DiffPanel, CommitPanel)` and a `panel_type(panel_id)` lookup that raises `KeyError` for an unknown id.
**Why explicit:** no import-time side effects or decorator magic, and one place to see
every panel. The lookup table is built at import by `index_panel_types()`, which raises
`ValueError` on a duplicate id, so a clash fails at startup and in every test run.

### 5.4 Panels

The first three are extracted from the original `main_window.py` with the same behaviour
(History no longer stops at 500 commits):

| Panel | id | Surface | Shows | Writes |
|---|---|---|---|---|
| ControlsPanel | `controls` | chrome | Open, Refresh (`ctx.actions`), current branch | (later: fetch/pull/push, switch branch) |
| BranchesPanel | `branches` | sidebar | branches, current in bold | (later: checkout) |
| HistoryPanel | `history` | content | Uncommitted changes, then the commit table | `selection.commit` |
| CommitFilesPanel | `commit_files` | sidebar | "Commit Files": the selected commit's changed files, in place of Unstaged Files, Staged Files and Commit; collapsed for Uncommitted changes | `selection.select()` |
| FilesPanel | `files` | sidebar | "Unstaged Files": unstaged and untracked files, status colours | `selection.select()`; stages |
| StagedPanel | `staged` | sidebar | "Staged Files": the index's changes; collapsed when empty | `selection.select()`; unstages |
| DiffPanel | `diff` | content | the diff of `selection.file`, unified or side by side | (later: stage hunks) |
| CommitPanel | `commit` | sidebar | the commit message being written | commits the staged changes |

**Unstaged Files** (FilesPanel) and **Staged Files** (StagedPanel, `staged_panel.py`)
are one list each, as in GitKraken. Both are a thin panel around a `FileList`
(`file_list.py`, a widget for one source), since a panel never imports another. A file
with both kinds of change is in both, and each row is one `FileSelection` (worktree or
index). Headers show the count, e.g. "Unstaged Files (29)". Staged Files sits between
Unstaged Files and Commit and collapses (§5.1) when nothing is staged. Like Commit, it
builds its list the first time something is staged (6 ms of startup otherwise).
**Commit Files** (CommitFilesPanel) is the third `FileList`, in commit mode: it lists
`session.commit_files` with one status column, its header names the commit ("Files in
a1b2c3d4 (12)"), and it has no staging menu or double-click. It asks
`session.request_commit_files` when `selection.commit` changes. **It takes the place of
the working tree's panels**, as in GitKraken: while a commit is selected, Unstaged
Files, Staged Files and Commit collapse and Commit Files is shown; on Uncommitted changes
it's the other way round. Each panel collapses by itself from `selection.commit` (none
knows another), and it sits directly below Commit in every preset, so the host's swap
rule (§6.2) gives it their space. Like Staged Files it builds its list the first time a
commit is selected; Commit keeps its draft while collapsed.
- **Selecting:** extended selection (Ctrl and Shift) within a panel. One panel holds the
  selection at a time: selecting in one clears the other, since each shows only the rows
  of `selection.files` that are its own.
- **Staging:** the context menu shows that panel's actions (§8): Stage, Discard and Stage
  All in Unstaged Files; Unstage and Unstage All in Staged Files. Double-click, or Enter,
  moves the selected rows to the other panel.
- **The selection follows the action:** after Stage the files' rows in Staged Files are
  selected, after Unstage their rows in Unstaged Files, through `selection.follow()`
  (§4.2). The rest of "what stays selected after a refresh" is Selection's too.

HistoryPanel is a `QTableView` over `HistoryModel` (`QAbstractTableModel`), because item
widgets for 100k commits are too slow to build. The model creates nothing per row: `data()`
answers text, the muted colour and the monospace hash font from the current theme when Qt
asks, so a theme change is one `dataChanged`.

- **A table, styled as a tree.** A `QTreeView` keeps a layout entry per row and redoes all
  of them on every insert or reset: a page cost 63 ms with 20k rows loaded and 232 ms with
  100k. A table with fixed row heights costs the same at any depth (13–24 ms). The view
  hides the grid and row numbers, selects and hovers whole rows (a small delegate), and
  its header is left-aligned and not clickable; the stylesheet's tree rules cover
  `QTableView` too. It renders pixel-identical to the tree it replaced.

- **Uncommitted changes:** while a repo is open, row 0 is "Uncommitted changes (N)", N
  the changed files in the status, in italics, with the other columns empty. It's
  selected by default, and selecting it sets `selection.commit` to None. Commits start at
  row 1: `commit_at(row)` and `row_of(sha)` account for it, and an appended page
  inserts after it. A reset that can't find the selected commit selects it.
- **Paging:** when the view is scrolled to within a screen of the end, the panel asks the
  session for the next page (§4.1). It watches its scroll bar rather than using Qt's
  `canFetchMore` / `fetchMore`, which Qt also calls while laying rows out, before they have
  a position, so it would load pages nobody scrolled to.
- **Updates:** an appended page inserts rows. Any other change resets the model; the panel
  then re-selects the same commit and keeps the same commit at the top of the view (or
  stays at the top, so new commits show).
- **Column widths:** the author, date and hash columns fit their widest text in the first
  page, as `resizeColumnToContents` would. That asks the model for every cell (45 ms for
  500 commits), so the panel finds the widest text with font metrics and has the view
  measure only that row. Every row is as tall as the first wants.

**Selection survives a refresh.** A list panel rebuilds with its signals blocked, then
re-selects the same item (a commit by sha; a file as `selection.files` now says, §4.2).
History writes `Selection` only when the selected commit no longer exists (it then
selects Uncommitted changes, as Selection did already, §4.2); the file panels
never write it on a refresh. So a refresh never clears what
another panel (a future Diff) is showing. `apply_theme` recolours existing rows rather
than rebuilding them.

**DiffPanel** shows the diff of the selected file (`selection.file`), read through
`session.request_diff` (§4.1).
- **Views:** unified (one column, old and new line numbers in the gutter) or side by side
  (old on the left, new on the right, with filler rows so the sides line up). The
  checkable **Side by Side Diff** action (§8) switches between them, from the View menu or
  the panel's own button.
- **Rows** come from `diff_rows.py`, plain Python tested without Qt: `unified_rows(diff)`
  and `split_rows(diff)` turn a `FileDiff` into `DiffRow`s (hunk header, context, added,
  removed, filler, note) with their line numbers. In side by side, a run of removed lines
  is paired with the run of added lines after it, and the shorter side is padded with
  filler. "No newline at end of file" is a note row after its line.
- **DiffView** (`diff_view.py`) is a read-only, non-wrapping `QPlainTextEdit`, so large
  text costs little: its layout is lazy. The text goes in with one `setPlainText`. Row
  backgrounds are painted for the visible rows only, under the text, from the row kinds;
  hunk headers also get the hunk text colour. The gutter is drawn like Qt's code-editor
  example. Colours are `diff_*` tokens, the font is `theme.mono_font()`, and a trailing
  carriage return shows as `␍`.
- **States:** "Select a file to see its diff", "No changes to show", "Binary file", "Large
  diff (N lines) [Show anyway]" and "Couldn't read this diff" are a muted message instead
  of the view (`Role.PLACEHOLDER`). If a diff takes more than 150 ms, "Loading…" is shown.
- **Updates:** a new diff of the same file keeps the scroll position; another file starts
  at the top. Selecting a file at another path calls `request_activation()`, so the Diff
  tab comes to the front; a refresh never does.

**CommitPanel** writes a commit. It sits directly below Files in every preset (§6.1), as
in GitKraken: a one-line **summary** (`QLineEdit`) with its length counter inside it on the
right, a **description** (`QPlainTextEdit`), and a full-width button for the Commit
action (§8). Viewing a commit selected in History is a separate panel (Commit Details,
RZG-26).
- **The message** is `summary`, or `summary + "\n\n" + description` when there is a
  description. Enter in the summary moves to the description; Ctrl+Enter commits from
  either.
- **Shown only when something is staged:** otherwise it is collapsed (§5.1) and Files
  takes its space. While either field has focus it stays, so unstaging everything
  mid-sentence doesn't snatch it away; it collapses once focus has left both (checked
  after the event loop turns, so moving from one field to the other doesn't count).
- **The button says what's needed:** "Stage Files to Commit", "Type a Message to
  Commit" (the summary is blank), "Committing…", or "Commit 3 Files" (the staged files).
  Only the last is enabled.
- **Committing:** the panel tells `AppActions` whether its message is ready
  (`set_commit_ready`: a summary, and no commit of its own still running), and the Commit
  action, Ctrl+Enter or the button calls `session.commit(message)`. When
  `command_finished` reports that command ok, both fields are cleared, unless the user has
  changed them since. On failure they are kept; the error is shown like any other (§4.1).
- **Drafts:** the message is the open repository's draft, kept in `ctx.drafts`
  (`CommitDrafts`, `core/commit_drafts.py`) as it is typed, so it comes back after
  switching repositories or restarting; it is split back into summary (the first line)
  and description (the rest, without the blank line). A cleared message removes the
  draft.
  `CommitDrafts` writes through `AppSettings` (`commit/drafts`, JSON keyed by the
  repository root); tests give it none, and it keeps drafts in memory.
- **Summary hint:** "n/50", muted (`Role.HINT`), and in the warning colour past 72
  (`Role.HINT_WARNING`). Nothing is enforced.
- **Built when first shown:** the panel starts collapsed (the repository opens after the
  window), so its widgets are created the first time something is staged. Until then
  its message is the stored draft. This keeps it out of the startup time (3.5 ms).

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

@dataclass(frozen=True)
class Anchor:      neighbors: tuple[str, ...]; orientation: Orientation | None; before: bool; share: float | None = None
@dataclass(frozen=True)
class LayoutState: root: LayoutNode; hidden: Mapping[str, Anchor] = {}   # what's saved
```

- `to_dict` / `from_dict` turn a `LayoutState` into `{"version": 2, "root": …, "hidden":
  {…}}` and back, so the saved format can be migrated. Version 1 (no `hidden`) still loads.
- `normalize(node, known_ids)` drops unknown or duplicate panel ids, removes empty
  containers and collapses single-child splits. A saved layout from an older version can
  never crash the app. It just loses what no longer exists.
- Immutable: an edit returns a new tree. That makes it simple and safe, and undo and
  presets come almost for free.
- **Moving a panel** (drag and drop, §6.2): `drop_zone(x, y)` maps a point inside the
  target pane, as fractions of its width and height, to a `DropZone`: the nearest edge
  (`LEFT`, `RIGHT`, `TOP`, `BOTTOM`) if it's within the outer quarter (`EDGE = 0.25`),
  else `CENTER`. `move_panel(root, id, target, zone)` is `remove_panel` (its old
  neighbours take back its space) and then `insert_panel` at `zone_anchor(target, zone)`:
  beside the target with `share=0.5`, so the two split the target's space evenly, or for
  `CENTER` a tab after it (current). A target in a tab group with an edge zone puts the
  panel beside the whole group. Dropping a panel on itself, or on the centre of the tab
  group it's already in, returns `root` itself (`is`), which callers treat as "not a target".

- `from_dict` raises `LayoutError(ValueError)` on structural errors: wrong version, unknown
  node type, missing or mistyped fields. `sizes` that don't fit (wrong length, negative)
  are dropped instead, so the splitter shares space evenly and the rest of the layout is
  kept. `normalize` returns `None` when nothing is left; callers then use the default.
- A split with one child collapses into it. A tab group with one tab stays: its tab label
  still means something. When tabs are dropped, `current` stays on the same panel; it's
  clamped only if that panel itself is gone.
- JSON nodes: `{"type": "panel", "panel_id": …}`, `{"type": "split", "orientation":
  "horizontal" | "vertical", "sizes": [...], "children": [...]}` (`sizes` optional) and
  `{"type": "tabs", "current": n, "children": [...]}`. A hidden panel's anchor:
  `{"neighbors": [...], "container": "horizontal" | "vertical" | "tabs", "before": bool,
  "share": 0.3}` (`share` optional, dropped if it isn't between 0 and 1).

**Presets** (`layout_presets.py`, `PRESETS`) are named trees that show every panel. Each
has the Controls panel across the top, above the arrangement described. In every preset,
History and Diff are tabs of one group, with History current, and Staged Files, Commit
and Commit Files are directly below Unstaged Files (`files`), in that order:
- **Standard** (`DEFAULT_LAYOUT`): Branches, Unstaged Files, Staged Files, Commit and
  Commit Files stacked in a left sidebar, History and Diff on the right: `Split(V,
  [controls, Split(H, [Split(V, [branches, files, staged, commit, commit_files],
  sizes=(200, 220, 120, 160, 0)), Tabs([history, diff])], sizes=(220, 880))])`. Commit
  Files' size is 0 in every preset: it has no space of its own (§6.2, swaps).
- **Sidebar Tabs:** Branches and Unstaged Files as tabs on the left, Staged Files and
  Commit below the tabs; History and Diff on the right.
- **Stacked:** History and Diff across the top; below, Branches beside Unstaged Files,
  with Staged Files, Commit and Commit Files below it.

### 6.2 LayoutHost

`LayoutHost(QWidget)` (`layout/layout_host.py`) turns a tree into widgets:
- `apply(node)` builds `QSplitter`s and `QTabWidget`s. It **reuses existing panel
  instances** by id: they're re-parented, so lists, scroll positions and selection survive.
  It creates missing panels through the registry and deletes panels no longer in the tree.
- `current_layout() -> LayoutNode` reads splitter sizes and the current tab back into a tree.
- It also holds the **hidden panels** and their anchors (§6.6): `apply_state(state)`,
  `state() -> LayoutState`, `hide_panel(id)`, `show_panel(id)`, and `layout_changed` after
  every change, which the menus follow.
- In a split, each panel is wrapped in a `PanelFrame` (`layout/panel_frame.py`): a section
  header plus the panel, with the panel's `surface` set as a style property. In tabs, the
  tab label is the panel's `header` instead, and follows `header_changed`. A tab group
  also follows each panel's `activate_requested` and makes that tab current.
- The host owns the panels (a read-only `panels` mapping by id); frames and containers are
  rebuilt on every `apply`. Panes can't be collapsed to nothing by dragging a divider
  (`setChildrenCollapsible(False)`): with 1px dividers a collapsed pane is hard to find.
- **A collapsed panel** (§5.1) has its frame hidden in a split, or its tab hidden in a tab
  group. In a split, its space goes to the pane before it (the one it sits below or
  beside; the one after, if it is first), and comes back from that pane when it is shown
  again, at the size it had: so Staged Files or Commit appearing takes its height from
  Unstaged Files (the nearest shown pane above), never from
  Branches. (If that pane can't spare it, Qt shares it out as usual.) `current_layout()`
  reports the pane at that size, taken from the same pane, so a layout saved meanwhile
  brings it back at that size.
- **Collapsing is batched:** the host applies the collapse changes of one moment together
  (a zero-time timer), per splitter. When panes collapse and others are shown in the same
  splitter at once, a **swap**, the space the collapsing panes free goes to the panes
  being shown, shared in proportion to their sizes when they collapsed, and the other
  panes keep theirs: so selecting a commit puts Commit Files exactly where Unstaged
  Files, Staged Files and Commit were (§5.4), and back. Otherwise each change lends or
  takes back space one pane at a time, as above. Tab visibility isn't batched.
  - The host remembers which shown panes **stand in** for which collapsed ones. A stand-in
    has no size of its own (it collapses at 0), and `current_layout()` reads a swap as if
    swapped back, so a layout saved while a commit is selected is the working tree's.
  - Building a split applies the same rule: shown panes of size 0 next to collapsed panes
    with sizes take their space (a layout applied while a commit is selected); otherwise
    collapsed panes lend theirs from the start, as above.
- **Drag and drop** moves a panel. A drag starts from a panel's section header (in a
  split) or its tab (in a tab group) once the mouse moves `startDragDistance`, and
  carries the panel id under its own MIME type. The host accepts the drop: the pane
  under the cursor (a frame, or a whole tab group, where the tab bar means `CENTER`) and
  `drop_zone` give the target, and `move_panel` the new tree, which `apply` shows (panels
  keep their state). A `DropIndicator` overlay (`drop_indicator.py`), transparent to the mouse, fills the half
  of the pane the panel will take (all of it for tabs) with the theme's `accent` at low
  opacity and an accent border; it reads `ctx.theme.current` when painting, never a
  stylesheet. Panels with `movable = False` (Controls) can't be dragged and aren't
  targets, and a drop that changes nothing isn't a target either (no indicator). Escape
  or a drop elsewhere cancels (Qt's `QDrag`). The drop is applied after `QDrag.exec`
  returns, never inside it, because `apply` deletes the header or tab bar the drag
  started from.
- An empty tree (everything dropped by `normalize`) shows an empty area. Falling back to
  `DEFAULT_LAYOUT` is the loader's job (§6.3).

### 6.3 Persistence

`AppSettings` (`core/app_settings.py`) is the only code that touches `QSettings`. It has
typed accessors (`last_repo`, `layout`, `layout_preset`, `window_geometry`,
`side_by_side_diff`, `commit_drafts`, `saved_layouts`) and takes a file path for tests. `app.py` creates it and passes it to
`MainWindow`; panels don't get it. The one thing a panel saves, the commit draft, goes
through `CommitDrafts` (§5.4), which holds only that.

On close, the window saves the host's `state()` (as JSON, so the format is ours) and its
geometry: size and maximized state, plus position where the platform allows it (not on
Wayland). With the layout it saves `layout_preset`: the title of the preset the layout
is, if it is one (`matching_preset(state)`: the preset whose shape, without sizes or
current tabs and with the saved hidden panels taken out, is the layout's), else None.

**Built-in layouts follow the code; custom ones stay.** On start,
`restore_layout(saved, preset)` in `layout_presets.py` (plain Python, no Qt) returns the
`LayoutState` to apply. When `preset` names a current preset whose shape (hidden panels
taken out) is no longer the saved layout's, the preset changed in code since: that
preset is applied instead, with the same panels hidden (anchored in the new tree) and the
preset's sizes. Otherwise, and for a custom layout (`preset` None: rearranged, or a saved
named layout), the saved layout is used:
- nothing saved, malformed data, or `normalize` leaves nothing → `DEFAULT_LAYOUT`, with a
  logged warning for malformed data (no dialog);
- a panel the user hid stays hidden (anchors of panels this version no longer has are
  dropped);
- a panel that is neither shown nor hidden is new in this version: it's inserted at its
  place in `DEFAULT_LAYOUT` (§6.6), and the rest of the user's layout is kept. When it
  joins a tab group, the tab the user had current stays current (a Diff tab added beside
  History doesn't hide History on the first start).
  - "Its place" is taken among the panels on screen (`default_anchor(id, shown)`), so two
    panels new in one version land together: Staged Files below Files, then Commit below
    Staged Files. (`show_panel` uses the same rule for a panel never hidden.)
  - A split that gained new panels, and now holds the same panes as a split of a preset,
    takes that split's sizes; otherwise each new pane takes its share of its neighbour's
    space, and a second new pane would take a share of the first's.

**View → Reset Layout** applies `DEFAULT_LAYOUT` at once, showing every panel.

**Saved layouts** (named user layouts) are app-wide, not per repository:
`AppSettings.saved_layouts` is a list of `(name, layout)` pairs, stored as JSON, where each
layout is in the same format as the current one (`to_dict` of a `LayoutState`, so hidden
panels are kept). Entries that aren't a name and an object are dropped with a logged
warning. Opening a saved layout goes through `restore_layout`, exactly like a start: panels
this version no longer has are dropped, new ones go to their default place, and the
panels it hid stay hidden. Each change is written at once, not on close.
- **Names** are trimmed. Empty names and the names of the built-in presets are refused,
  ignoring case (`layout_name_problem` in `layout_presets.py`); presets can't be
  overwritten, renamed or deleted.
- **View → Layout** lists the presets, a separator, the saved layouts in the order they
  were saved, a separator, then **Save Layout As…** and **Manage Layouts…**.
- **Save Layout As…** asks for a name and saves `LayoutHost.state()`. A name already used
  by a saved layout (ignoring case) asks before replacing it; the replaced layout keeps
  its place in the list.
- **Manage Layouts…** opens `ManageLayoutsDialog`: the saved layouts in a list, with
  **Rename…** (the same name rules; a name another saved layout has is refused, not
  replaced) and **Delete** (asks first). The menu is rebuilt when the dialog closes.

### 6.4 Customisation levels

The data model supports all of these now; the UI comes in stages:
1. Code-defined default (first phase)
2. Sizes and tabs remembered between runs (first phase)
3. Switching between preset layouts: **View → Layout** (§6.1)
4. Editing: hide and show panels from **View → Panels** (§6.6), and save the current
   layout under a name (§6.3, saved layouts), and move panels by drag and drop (§6.1,
   §6.2).

### 6.5 Why not QDockWidget

Docks bring floating windows, dock title bars and a fixed "dock areas around a central
widget" shape. That fights the flat, borderless look and limits nesting. Docks also save
state as an opaque byte blob, which we can't validate or migrate. A small tree of splitters
and tabs gives full control over the look, is plain data we can test, and is about 150 lines.

### 6.6 Hiding and showing panels

**View → Panels** has a checkable entry per registered panel (`panels_menu.py`). The
last visible panel can't be hidden. A hidden panel is deleted like any panel that leaves
the tree, and a new one is created when it's shown again (it renders the current state, §5.2).

Edits are pure functions in `layout_model.py`, tested without Qt:
- `remove_panel(root, id) -> (root, Anchor)`: the panel's share of space goes to its
  neighbours, and a split left with one child collapses into it. The **anchor** records
  where it sat: the panels of the node beside it (the next one, or the previous one if it
  was last), whether they were side by side, stacked or tabs, which side it was on, and
  its share of the space the two had.
- `insert_panel(root, id, anchor, fallback, make_current=True)`: finds the smallest node holding the anchor's
  neighbours that are still shown, and puts the panel on the recorded side of it, taking
  its share of that node's space. If that node sits in a split of the same orientation,
  the panel joins that split; otherwise the node is wrapped in a new split (or tabs). If
  none of the neighbours is shown, it uses `fallback` (the panel's anchor in
  `DEFAULT_LAYOUT`), and failing that the right-hand edge. A panel added to tabs becomes
  the current tab, unless `make_current` is False (migration, §6.3).

**Panels that are always shown** (`always_shown`, the Controls panel): `LayoutHost.apply`
inserts one at its `DEFAULT_LAYOUT` place whenever a tree lacks it, so no preset, saved
layout or edit can lose it; `hide_panel` refuses it, and its View → Panels entry stays
checked but disabled. "The last visible panel" counts only panels that can be hidden.

**Fixed-height panes** (`fixed_height`): in a vertical split, the host fixes the pane to the
height the panel asks for, and disables the dividers beside it (no resize cursor). Used
by the Controls panel, so the bar can't be dragged taller.

**Why anchors, not "the default place":** a panel hidden from a rearranged layout comes
back where the user had it. The default place is only the fallback.

## 7. AppTheme

### 7.1 Theme model

`ui/theme/app_theme.py`:

```python
@dataclass(frozen=True)
class ThemeColors:   menubar_bg: str; toolbar_bg: str; sidebar_bg: str; content_bg: str; border: str;
                     text: str; text_muted: str; hover: str; selection: str; content_selection: str;
                     accent: str; button_*: str; scroll_*: str; diff_*: str (incl. diff_hunk_bg, diff_hunk_text); …
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

`style_roles.py` defines `Surface` (SIDEBAR, CONTENT, CHROME) and `Role` (SECTION_HEADER,
PLACEHOLDER for a muted "nothing to show" message, DIFF_VIEW for the borderless diff text,
HINT and HINT_WARNING for a small muted note and the same past a limit).
Widgets get them as Qt dynamic properties through `set_surface()` / `set_role()`, and the
stylesheet targets those (`QWidget[surface="sidebar"]`). The theme decides what "sidebar"
looks like; panels only say what they are.

A surface or role is added when a widget first needs it (`Surface.CHROME` came with the
Controls panel: the toolbar's background), not ahead of time. A test checks that every
value is used by the stylesheet, so a role can't be defined and forgotten.

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
  The menu bar, context menus and the Controls panel all show the same objects.
  **Side by Side Diff** is checkable: the Diff panel follows its `toggled`. MainWindow sets
  it from `AppSettings.side_by_side_diff` before the panels are built, and saves it on close
  with the layout, so panels never touch settings.
- **Staging actions:** Stage (Ctrl+S) and Unstage (Ctrl+U) act on the selected rows of
  that kind (`selection.files`), Discard Changes… (Ctrl+Backspace) on the selected unstaged
  rows, Stage All (Ctrl+Shift+S) and Unstage All (Ctrl+Shift+U) on the whole status. Each is
  enabled only when it has something to act on. They're in the **Repository** menu and the
  file panels' context menus. Stage and Unstage call `selection.follow()` first, so the
  moved rows are selected in the other panel (§4.2). Discard is connected by MainWindow, which asks for
  confirmation first (listing the files, and that untracked ones go to the trash).
- **Commit** (Ctrl+Enter, in the Repository menu) commits the staged changes with the
  Commit panel's message. It is enabled when a repository is open, something is staged
  and the panel reports its message ready (`set_commit_ready`); the panel connects
  `triggered`, since it holds the message. A panel that is hidden (deleted) reports not
  ready.
- **`MainWindow`** is a thin shell: menus built from `AppActions`, the status
  bar summary (from session signals: path · branch · commits · changed files), error dialogs (from `error_occurred`), and a
  `LayoutHost` as the central widget. The Open, Quit and Reset Layout handlers live here
  because they need dialogs or the window. Target: around 100 lines.
- **Commit count text** (`commit_summary`): the exact `commit_count` with separators
  ("2,421 commits", "1 commit"). Until it arrives, or if it failed, the loaded count, with a
  plus while more history is unloaded ("500+ commits").
- **No toolbar:** its buttons are the **Controls panel** (§5.4), placed by the layout across
  the top and styled like the toolbar was. Remote controls (fetch/pull/push) and switching
  branches join it when those commands exist.

## 9. Performance stance

Measure first; optimise what's measured. The design keeps these known future hot spots
cheap to fix **later**, without paying for them now:

| Hot spot | When it matters | Planned fix | Kept open by |
|---|---|---|---|
| git on the UI thread | large repos, network ops | **done:** one worker thread inside RepoSession (§4.1) | push-based signals (§4.1); panels never run git (§5.2) |
| History rebuilds as item widgets | 10k+ commits | **done:** `QAbstractTableModel` + paging (§4.1, §5.4) | HistoryPanel is the only place that changes |
| Full rebuild on every refresh | frequent refreshes | granular signals, emitted only on change | §4.1 (done now: cheap, and the right semantics anyway) |
| Layout changes recreating panels | layout switching | panel reuse in `LayoutHost.apply` | §6.2 (done now: needed to keep panel state anyway) |
| Restyling | theme switch | one app-wide stylesheet | §7.3 |

## 10. Testing

- `tests/support.py`: `make_repo()` / `commit_file()` build throwaway repos, `qt_app()`
  returns one shared offscreen `QApplication` (`QT_QPA_PLATFORM=offscreen` is set before
  import), `SignalRecorder` records emissions in order, `make_panel_context()` gives a
  `PanelContext` with no repo open, and `wait_idle(session)` waits for the git worker. A test
  that opens a repo waits before it ends, so no late result reaches the next test. Qt is
  imported only inside these functions, so git-only tests never load it.
- One test file per module: `test_repo` (git, no Qt), `test_repo_session` (signals and
  latest values on temp repos), `test_app_theme` / `test_theme_manager` (the stylesheet
  builds, and every role it uses exists), `test_layout_model` (round-trip and `normalize`
  edge cases), `test_layout_host` (build, panel reuse, reading sizes back),
  `test_<name>_panel` (each panel renders a temp repo and the empty state),
  `test_diff_request` and `test_diff_rows` (the diff's worker side and its rows),
  `test_git_commands` (commands on temp repos), `test_commit_drafts`, and
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
