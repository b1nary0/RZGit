# RZGit Architecture

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
- Loading themes from files. The theme model makes it easy later. (A dark theme came
  with RZG-32: §7.1, §7.3.)

## 2. Layering

```
 git/   pure Python. Runs the git CLI and parses its output. No Qt imports.
  ↑
 core/  application state. QtCore only (QObject, Signal). No widgets.
  ↑
 ui/    widgets. theme/ → app_icon → widgets/ → app_actions → panels/ → layout/ → branch_dialogs → main_window → app_windows
  ↑
 app.py composition root. The only place concrete objects are created and wired together,
        with AppWindows (§8.1), which app.py makes to build each main window's own objects.
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
src/rzgit/
  app.py                        composition root
  git/
    git_models.py               Commit, HistoryOrder, Stash, Branch, Ref, Remotes, FileStatus, FileDiff, Hunk, DiffLine, Worktree, WorktreeState
    repo.py                     Repo: runs git, returns models; canonical_path; worktree_state
  core/
    repo_session.py             RepoSession: the open repo, its data, git commands
    diff_request.py             DiffRequest, the size limit, load_diff (the worker side of diffs)
    diff_patch.py               build_patch: a patch of chosen lines of a FileDiff (plain Python)
    git_commands.py             GitCommand, run_command (the worker side of commands: stage, commit, …)
    network_worker.py           NetworkWorker, NetworkCommand: remote commands (fetch, pull's fetch, push) on a thread of their own
    commit_drafts.py            CommitDrafts: the unfinished commit message of each repository (a file each)
    column_prefs.py             ColumnPrefs: each panel's column choices (hidden columns, widths)
    sort_options.py             SortOption, GroupOption, SortOptions, SortOrder, Group: a panel's sort orders and groupings (plain Python)
    sort_prefs.py               SortPrefs: each panel's chosen sort order and grouping
    preferences.py              Setting, Preferences: the settings the Settings window shows (§8.2)
    commit_graph.py             CommitGraph, GraphRow: History's branch graph lanes (plain Python)
    ref_labels.py               RefLabel, labels_by_commit: History's ref labels (plain Python)
    selection.py                Selection: what the user has selected (commit, file)
    repo_watcher.py             RepoWatcher: refreshes the session when the repository changes on disk
    inotify_watcher.py          InotifyWatcher: Linux inotify on a QSocketNotifier (folders → named events)
    worktree_reader.py          WorktreeReader: a repository's worktrees and their state, kept up to date
    vscode_target.py            VSCodeTarget: which VS Code window a worktree's files open in, and the command line (plain Python)
    recent_repos.py             RecentRepos: the repositories and worktrees opened recently (§6.3)
    app_settings.py             AppSettings: typed wrapper over QSettings (last_repo, layout, …)
    old_name_migration.py       migrate_old_name: copies the settings and drafts of the old name (RZGitClient) once (§6.3)
    gc_on_main_thread.py        GcOnMainThread: Python's garbage collection, on the main thread only (§4.1)
  ui/
    main_window.py              MainWindow: thin shell (menus, status bar, LayoutHost)
    app_windows.py              AppWindows: the open main windows; open a window on a path, or raise it
    settings_window.py          SettingsWindow: the app's one Settings window (§8.2)
    recent_repos_menu.py        RecentReposMenu: File → Recent Repositories (§8)
    app_actions.py              AppActions: every QAction, defined once; BranchOp, StashOp, WorktreeOp
    branch_dialogs.py           BranchDialogs: the confirmations and name boxes of branch operations
    stash_dialogs.py            StashDialogs: Stash Changes… and the Drop confirmation
    pull_dialogs.py             PullDialogs: Pull's boxes (changes, diverged)
    push_dialogs.py             PushDialogs: Push's box (push and track)
    worktree_dialogs.py         WorktreeDialogs, NewWorktreeDialog: New Worktree…, Remove, Prune; folder, terminal
    vscode_launcher.py          VSCodeLauncher: Open with VS Code (the command setting, starting it)
    app_icon.py                 app_icon(): the app's QIcon, built from assets/icon (app.py sets it)
    assets/                     image files shipped as package data (no modules)
      icon/                     the app icon: its SVG source and PNGs at 16–512 px
      logo/                     mark + "RZGit"; _dark (for dark backgrounds) and _light files
      splash/                   splash images, solid and transparent; _dark and _light
    theme/
      app_theme.py              AppTheme, ThemeColors, StatusColors, GraphColors, ThemeFonts, ThemeMetrics, LIGHT_THEME
      theme_stylesheet.py       build_stylesheet(theme) -> str
      style_roles.py            Surface and Role enums (semantic style hooks)
      theme_manager.py          ThemeManager: current theme + theme_changed
    widgets/
      section_header.py         SectionHeader: shared widget used by frames and panels
      panel_segment.py          PanelSegment, PanelSegments: a panel's own internal sections
      column_header.py          ColumnHeader: the column labels of a list (painted from the theme)
      sort_menu.py              SortMenu: a panel's Sort By and Group By menus and its sort order
      grouped_tree.py           GroupedTree: a tree whose top-level rows can be group headings
    panels/
      panel.py                  Panel, PanelContext
      panel_registry.py         PANEL_TYPES, panel_type(id)
      controls_panel.py         ControlsPanel: Open, Refresh, the branch button (replaces the toolbar)
      branches_panel.py         BranchesPanel
      stash_panel.py            StashPanel: the stashes; apply, pop and drop them
      worktrees_panel.py        WorktreesPanel: the repository's worktrees and their state; open one
      history_panel.py          HistoryPanel
      history_model.py          HistoryModel: the commit table's Qt model, paged
      graph_painter.py          GraphPainter: draws a row of History's branch graph
      ref_label_painter.py      RefLabelPainter: draws a row of History's ref labels
      back_to_top_button.py     BackToTopButton: History's floating "↑ Back to top" control
      files_panel.py            FilesPanel: "Unstaged Files"
      staged_panel.py           StagedPanel: "Staged Files"
      file_list.py              FileList: one source's changed files, the body of both
      file_tree.py              FileTree: changed files grouped by folder (plain Python, no Qt)
      commit_files_panel.py     CommitFilesPanel: the files the selected commit changed
      commit_details_panel.py   CommitDetailsPanel: the selected commit's message, people, SHA, parents, refs
      commit_message_text.py    reflow: a hard-wrapped message body as paragraphs (plain Python, no Qt)
      diff_panel.py             DiffPanel
      diff_view.py              DiffView: read-only diff text, text selection, row backgrounds, hunk header links
      diff_gutter.py            DiffGutter: line buttons, line numbers and +/− marks beside a DiffView
      line_selection.py         LineSelection: which changed rows of a DiffView are selected (from the gutter)
      diff_rows.py              DiffRow, unified_rows, split_rows, *_partners, word_spans, copy_text (no Qt)
      find_query.py             FindQuery, FindMatch, find_matches: Find in a diff (no Qt)
      find_bar.py               FindBar: the Diff panel's find bar
      commit_panel.py           CommitPanel: the message box and Commit button
    layout/
      layout_model.py           PanelNode / SplitNode / TabsNode, (de)serialisation, normalize
      layout_presets.py         PRESETS, DEFAULT_LAYOUT, restore_layout
      layout_host.py            LayoutHost, AreaPane (one per area)
      panel_frame.py            PanelFrame: section header + panel
      drop_indicator.py         DropIndicator: where a dragged panel will land
      layout_menu.py            LayoutMenu: View → Layout (presets, saved layouts)
      manage_layouts_dialog.py  ManageLayoutsDialog: rename and delete saved layouts
      panels_menu.py            PanelsMenu: View → Panels (show and hide)
tests/
  support.py                    temp-repo builder, shared offscreen QApplication
  test_<module>.py              one file per module, named after it (test_layout_model.py, …)
packaging/
  linux/
    rzgit.desktop               the desktop entry; install-desktop.sh fills in its Exec
    install-desktop.sh          installs the entry and the icon PNGs for the current user
                                (re-running is a clean reinstall); --uninstall removes them
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
There is one per main window (§8.1), with its own `Selection`, `AppActions` and
`RepoWatcher`: what one window does never reaches another's session.

```python
class RepoSession(QObject):
    repo_changed     = Signal(object)  # Repo | None
    head_changed     = Signal(object)  # str | None  (branch name, None = detached)
    head_sha_changed = Signal(object)  # str | None  (HEAD's commit, None before the first)
    branches_changed = Signal(object)  # tuple[Branch, ...]
    history_changed  = Signal(object)  # tuple[Commit, ...]
    status_changed   = Signal(object)  # tuple[FileStatus, ...]
    stashes_changed  = Signal(object)  # tuple[Stash, ...], newest first
    commit_count_changed = Signal(object)  # int | None  (commits reachable from HEAD)
    diff_changed     = Signal(object, object)  # FileSelection | None, FileDiff | None
    commit_files_changed = Signal(object, object)  # sha | None, tuple[FileStatus, ...] | None
    last_commit_changed = Signal(object)   # LastCommit | None  (asked for: Refresh.LAST_COMMIT)
    commit_details_changed = Signal(object, object)  # sha | None, CommitDetails | None
    command_finished = Signal(object, bool)    # GitCommand, whether it ran without error
    error_occurred   = Signal(str)
    notice_occurred  = Signal(str)   # a command worked, but not quite as asked
    busy_changed     = Signal(bool)  # True while git work is queued or running
    remotes_changed  = Signal(object)  # tuple[str, ...]  (the remotes' names, RZG-62; also on a new push_default)
    workspace_files_changed = Signal(object)  # tuple[str, ...]  (tracked *.code-workspace files, RZG-96)
    refs_changed     = Signal(object)  # tuple[Ref, ...]  (branches and tags, RZG-77)
    network_changed  = Signal(object)  # NetworkCommand | None  (the one running)
    network_progress = Signal(str)     # its latest progress line
    network_finished = Signal(object, bool)  # NetworkCommand, whether it ran without error
    pull_diverged    = Signal(object, object)  # GitCommand (the PULL), PullDiverged (RZG-71)

    def open(self, path) -> bool          # path, or a Repo already read (§8.1)
    def close(self) -> None
    def refresh(self, parts: Refresh = Refresh.ALL) -> None   # Flag: HEAD|BRANCHES|REFS|HISTORY|STATUS|STASHES|COUNT|REMOTES|WORKSPACE_FILES
    def load_more_history(self) -> None   # the next page of history, in the background
    def set_history_order(self, order: HistoryOrder) -> None   # DATE or TOPO; reloads history
    def request_diff(self, file: FileSelection | None, full: bool = False,
                     whole: bool = False) -> None
    def request_commit_files(self, sha: str | None) -> None   # the files a commit changed
    def request_commit_details(self, sha: str | None) -> None # its message, people, parents, refs

    # Commands: they change the repository (§4.1, "Commands").
    def stage(self, paths) -> None        # git add
    def unstage(self, paths) -> None      # git restore --staged (git rm --cached before the first commit)
    def stage_all(self) -> None
    def unstage_all(self) -> None
    def discard(self, paths) -> None      # unstaged changes; untracked files go to the trash
    def stage_lines(self, diff, lines) -> GitCommand | None    # chosen lines of the shown diff
    def unstage_lines(self, diff, lines) -> GitCommand | None
    def discard_lines(self, diff, lines) -> GitCommand | None  # from the working tree; no undo
    def commit(self, message) -> GitCommand | None   # the staged changes; None with no repo
    def amend(self, message) -> GitCommand | None    # replace HEAD's commit (RZG-27)
    def switch_branch(self, name, stash=False) -> GitCommand | None   # git switch
    def create_branch(self, name, start, switch=False) -> GitCommand | None
    def rename_branch(self, old, new) -> GitCommand | None
    def delete_branch(self, name, force=False) -> GitCommand | None   # -d, or -D with force
    def merge(self, name) -> GitCommand | None       # name into the current branch
    def rebase(self, onto) -> GitCommand | None      # the current branch onto `onto`
    def stash_changes(self, message, untracked=True) -> GitCommand | None   # git stash push
    def apply_stash(self, sha, pop=False) -> GitCommand | None   # rolled back on a conflict
    def drop_stash(self, sha) -> GitCommand | None
    def add_worktree(self, path, branch, start=None) -> GitCommand | None  # git worktree add (RZG-49)
    def remove_worktree(self, path, force=False) -> GitCommand | None    # --force: even with changes
    def prune_worktrees(self) -> GitCommand | None    # the records of missing worktrees
    commands_in_flight: tuple[GitCommand, ...]   # queued or running, until command_finished

    # Network commands: on a worker of their own (§4.1, "Network commands").
    def fetch(self) -> NetworkCommand | None   # git fetch --all --prune; None: no repo, or one running
    def pull(self, stash=False) -> NetworkCommand | GitCommand | None   # fetch, then integrate (RZG-71)
    def pull_integrate(self, mode, stash=False) -> GitCommand | None   # after pull_diverged
    def push(self, remote=None) -> NetworkCommand | None   # the current branch; remote: track it there (RZG-72)
    def cancel_network(self) -> None           # kill the running one, if any
    network: NetworkCommand | None             # the one running

    # Latest values, so a panel created later renders at once (read-only properties):
    repo, head, head_sha, branches, history, history_order, status, stashes, commit_count, remotes,
    push_default, workspace_files,
    history_complete, diff_file, diff, diff_whole, diff_context,
    commit_files_sha, commit_files, commit_details_sha, commit_details, last_commit
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
  (panels reset), then `head`, `head_sha`, `branches`, `history`, `status`, `stashes`, `commit_count`,
  `remotes`. `close()` sends
  `repo_changed(None)` and then empty data.
- **HEAD is two values** (RZG-56): `Refresh.HEAD` reads the branch (`symbolic-ref`) and
  the commit (`rev-parse`) in the same job. `head_changed` and `head_sha_changed` each
  fire only when their own value changed, so a commit on a branch fires only
  `head_sha_changed`, and a switch between two branches at one commit only `head_changed`.
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
  - **Garbage is collected on the main thread only** (RZG-101). Python's cyclic collector
    runs on whichever thread is allocating when a threshold is crossed, the worker too. A
    cycle that holds main-thread widgets would then be destroyed on the worker: a widget's
    `QBasicTimer` (item views, text edits) is stopped through the current thread's event
    dispatcher, which the worker doesn't have, so the timer stays registered and later
    fires at freed memory (a segfault, with no warning). `GcOnMainThread`
    (`core/gc_on_main_thread.py`) turns automatic collection off (`gc.disable()`, for the
    whole process) and collects from a main-thread `QTimer` every 500 ms, only the
    generations over their thresholds (`gc.get_count()` against `gc.get_threshold()`),
    so it costs what automatic collection did. `app.main()` installs it right after the
    `QApplication`, and the tests' `qt_app()` too. `install()` is idempotent.
- **History loads in pages** of `HISTORY_PAGE` (500) commits, from `HEAD`.
  - **Order and parents** (RZG-58): `Repo.log()` reads each commit's parents (`%P`, into
    `Commit.parents`) for the branch graph (§5.4), and passes `--date-order` (the default)
    or `--topo-order`, the session's `history_order` (`HistoryOrder.DATE` / `TOPO`,
    `git_models.py`). Both keep every commit above its parents, which the graph needs; git's
    default order doesn't when commit clocks are skewed. `--topo-order` keeps a branch's
    commits together, so the graph has fewer lanes side by side; it is as fast as
    `--date-order` with a commit-graph file, and reads the whole history first without
    one. `set_history_order()` reloads the history from the top (same order: nothing).
    The **Topological Order** action (§8) sets it; `AppSettings.topo_order` keeps it.
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
- **Stashes** (RZG-36) are a part like the others, `Refresh.STASHES`, in `Refresh.ALL`:
  `Repo.stashes()` reads `git stash list -z` (sha, parents, author, date, reflog subject)
  and returns a tuple of `Stash` (`git_models.py`), newest first. A `Stash` is a `Commit`
  (so `selection.commit` can hold one, §4.2; its `parents` are its base commit first,
  which History's labels use, §5.4) with `index` (its `stash@{n}`, as `ref`),
  `branch` (the one it was made on, from "On *X*:" or "WIP on *X*:"; "(no branch)" when
  detached), `message` (what follows) and `untracked` (whether it has an untracked part,
  a third parent). Its `subject` is its message, its `date` when it was made. A `Stash`
  never equals a `Commit` with the same sha (dataclass equality compares the class).
  - **A stash's files** come from the commit files target like a commit's: when the
    target is the sha of a stash in `stashes`, the job reads `Repo.stash_files(stash)`:
    its tracked changes against its base commit (its first parent, as for a commit), then
    the files of its untracked part (`git ls-tree -r <sha>^3`), each a `FileStatus` with
    `index` `"?"`. A stash's untracked file is diffed from that part (`<sha>^3`, a commit
    with no parent, so the file shows as new).
- **The commit count** (`git rev-list --count HEAD`, 0 before the first commit) is exact
  however little history is loaded. It never runs in the same job as `HISTORY`: when both
  are due, the count waits for the next job, so the first page isn't delayed by it (0.1 s
  for 200k commits). `commit_count` is None until it arrives; parts still queued aren't
  reported as empty after a job.
- **The last commit is asked for** (RZG-27): `Refresh.LAST_COMMIT` is a part like the
  others, loaded after them, but not in `Refresh.ALL`, so only a `refresh(LAST_COMMIT)`
  reads it and the watcher never does. Its value is a `LastCommit(sha, message,
  pushed)` (`git_models.py`) or None before the first commit: the full message
  (`git log -1 --format=%B`, trailing newlines dropped) and whether HEAD is on any
  remote-tracking branch (`for-each-ref --count=1 --contains HEAD refs/remotes`). It
  emits `last_commit_changed` only when it changed. `open()` and `close()` reset it to
  None like the other parts.
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
  - **Context lines** (RZG-97): `request_diff(file, context=N)` (3 to 1000, a setting, §8.2;
    3 by default, git's own) reads the hunks view with `--unified=N`, then cuts it with
    `split_hunks` like the whole file (below). The hunks stay git's at 3 lines: changes up
    to 6 lines apart share one, so Stage Hunk acts on the same changes at any N. Where two
    hunks' context would overlap, git merges them and the cut parts them again in the
    middle of the run between them (the larger half above), so each unchanged line shows
    once and no hunk takes another's changed lines. At 3 the cut changes nothing. The
    target keeps `context` across refreshes, and `diff_context` is the count the latest
    `diff_changed` was read with (None for the whole file); like `diff_whole` it is part of
    what makes a result differ.
  - **The whole file** (RZG-60): `request_diff(file, whole=True)` reads the diff with
    `--unified=WHOLE_CONTEXT` (10⁹ lines), so git returns every line of the file in one hunk.
    `split_hunks(diff)` (`diff_request.py`, plain Python) then cuts each hunk in the middle
    of every unchanged run longer than 6 lines (twice git's 3 lines of context). The pieces
    cover the file without gaps, each with its own `@@ -a,b +c,d @@` header, so a hunk is
    still one changed block: Stage Hunk and the patches of `diff_patch.py` work as in the
    hunks view. On a `-U3` diff it changes nothing. The size limit applies to the
    whole-file output as to any other. The target keeps `whole` across refreshes, and
    `diff_whole` says whether the latest `diff_changed` was read whole; it is part of what
    makes a result differ, so toggling always emits.
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
- **A commit's details are asked for** the same way (RZG-26):
  `request_commit_details(sha)` makes `sha` the **commit details target**, read in the
  same job after the commit's files, and the result arrives as
  `commit_details_changed(sha, details)`, only when it differs. Its own counter makes
  older results stale; `None` clears the target. The message and people never change,
  but the refs do, so **a job that loads `BRANCHES` or `HISTORY` reads the target again**.
  If an earlier part of the job fails, it is queued again by itself.
  - `Repo.commit_details(sha)` runs `git show -s --no-color` with a NUL-separated format
    (`%H %P %an %ae %aI %cn %ce %cI %D %B`) and returns a `CommitDetails` (`git_models.py`):
    `sha`, `parents` (none for the first commit, two or more for a merge), `author`,
    `author_email`, `author_date`, `committer`, `committer_email`, `committer_date`,
    `refs` (as `%D` gives them: `"HEAD -> main"`, `"origin/main"`, `"tag: v1.0"`) and
    `message` (the full message, trailing newlines stripped), with `summary` (its first
    line) and `body` (the rest, without the blank line) properties.
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
  - **Lines and hunks** (`stage_lines`, `unstage_lines`, `discard_lines`): a command carries
    the `FileDiff` the Diff panel shows and a set of `(hunk, line)` indices into it. A hunk
    is all its changed lines. The worker builds a patch (`core/diff_patch.py`) and applies
    it with `git apply` on stdin: `--cached` to stage, `--cached --reverse` to unstage,
    `--reverse` on the working tree to discard. Each reloads `STATUS`, and the diff with it.
    - **Which lines go in.** Staging applies the patch forwards, so an unselected `−` line
      becomes context and an unselected `+` line is left out. Unstaging and discarding apply
      it in reverse, so it's the other way round: an unselected `+` line becomes context,
      an unselected `−` line is left out. Hunk headers are recounted; CRs and "No newline at
      end of file" are kept exactly.
    - **Where a chosen line lands:** in a changed block (git writes its removed lines, then
      its added ones), the k-th removed line pairs with the k-th added one, and the patch
      writes them together. So choosing `b → B` in `b, c → B, C` gives `B, c`, and choosing
      only an added line puts it right after its partner, which stays.
    - **New files:** for an untracked file (the worker reads the status, like Discard), the
      patch is a new-file patch with the chosen lines, with the file's executable bit. Its
      lines can't be discarded (whole-file Discard moves it to the trash). Unstaging lines
      that leaves a new file empty in the index takes it out of the index: it is untracked
      again, as it was, not staged as an empty file.
    - **Safe by refusal:** git checks the patch's context against the file. If the file
      changed since the diff was read, the patch doesn't apply, nothing changes, the error
      shows, and the reload brings the new diff. A diff with text git couldn't read as UTF-8
      (shown as `�`) is refused before git runs: writing it back would change the file.
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
  - **Branch commands** (RZG-35). A command carries the branch it acts on (`branch`) and,
    for New Branch and Rename, the new name (`new_name`). Git checks names; a bad or taken
    one is git's error. Branch names never start with `-` (git refuses them), so they are
    passed as they are.
    - **Switch** (`git switch -q`) reloads `HEAD | BRANCHES | HISTORY | STATUS | COUNT`.
      With `stash`, it first runs `git stash push -q --include-untracked -m "RZGit:
      before switching to <branch>"`: staged, unstaged and untracked changes, not ignored
      files. If the switch then fails, the stash is popped back (`stash pop -q --index`), so
      the repository is as it was; if even that fails, the error says the changes are in
      the stash. Whether a stash was made is read from `refs/stash` before and after, since
      "nothing to stash" isn't an error. Without `stash`, git carries the changes over or
      refuses.
    - **New Branch** (`git branch <name> <start>`, or `git switch -q -c <name> <start>` with
      `switch`) reloads `BRANCHES`, or everything Switch does.
    - **Rename** (`git branch -m <old> <new>`, the current branch too) reloads
      `HEAD | BRANCHES`.
    - **Delete** (`git branch -d`, `-D` with `force`) reloads `BRANCHES`. Git refuses `-d`
      on an unmerged branch, so a stale `merged` can't lose work.
    - **Merge** (`git merge --no-edit <branch>`) and **Rebase** (`git rebase <onto>`)
      reload everything. **Never left mid-way:** if either fails and left a merge or rebase
      in progress (`MERGE_HEAD`, or the `rebase-merge` / `rebase-apply` folders, found with
      `git rev-parse --git-path`), the worker runs `merge --abort` / `rebase --abort` and
      the error says so (git's `hint:` lines, which say to resolve and continue, are left
      out). A failure before anything started (a dirty working tree) needs no
      abort. The app has no conflict view yet.
    - `Branch.merged`: whether the branch's tip is in HEAD (`git for-each-ref --merged
      HEAD`, a second read in `BRANCHES`; all False if HEAD has no commit). The Delete
      confirmation uses it (§8).
    - `Branch.upstream`, `ahead`, `behind` (RZG-62): the branch's upstream
      (`%(upstream:short)`, e.g. `origin/main`, None without one) and how many commits
      it is ahead of and behind it (`%(upstream:track,nobracket)`: "ahead 2, behind 1",
      both 0 when even). Read in the same `for-each-ref` as the names, at no measurable
      cost. An upstream that is gone ("gone": its remote branch was deleted) keeps its
      name, with `ahead` and `behind` None, as without one.
    - `Branch.remote` (RZG-71): the upstream's remote (`%(upstream:remotename)`, `.` for
      a local branch), what Pull fetches; None without an upstream.
  - **Pull** (RZG-71) brings the upstream's commits into the current branch in two
    parts: a fetch on the network worker (below), then a `PULL` command here, which
    carries the branch (`branch`), how to integrate (`mode`, a `PullMode`) and `stash`.
    - **Its checks change nothing:** a current branch that isn't `branch` any more (a
      switch while it fetched) is an error; behind its upstream by 0 (`git rev-list
      --left-right --count HEAD...@{u}`) is done, with no stash and no notice.
    - **`PullMode.FF_ONLY`** (the first try): `git merge --ff-only @{u}`. If the branch
      is also ahead (diverged), the command changes nothing and returns `PullDiverged`
      (ahead, behind, and the rebase default) instead of a notice. The session sends it
      as `pull_diverged(command, diverged)`, before `command_finished(command, True)`.
    - **`MERGE`** (`git merge --no-edit @{u}`) and **`REBASE`** (`git rebase @{u}`) are
      Merge and Rebase onto the upstream: never left mid-way (above). Git refuses to
      rebase with changes; that is the error.
    - **Rebase default:** `branch.<name>.rebase`, else `pull.rebase`: any value but
      `false` (`true`, `merges`, `interactive`) means Rebase; unset or `false`, Merge.
      `pull.ff` isn't read: a diverged branch always asks.
    - **With `stash`**, the changes are stashed first, as Switch does ("RZGit:
      before pulling <branch>"), only once the checks pass. If the integration fails, the
      stash is popped back. If it worked, the stash is put back as Pop does (with
      `--index`, then without, rolled back if both fail). If that fails, the pull stays,
      the working tree is clean at the pulled commit, the stash is kept, and a notice
      says so and names it.
    - It reloads everything.
  - **Amend** (`git commit -q --amend --cleanup=whitespace -F -`, the message on stdin)
    replaces HEAD's commit with the staged changes, if any, and the message, and reloads
    what Commit does. Git keeps the author and author date, and a merge's parents.
  - **Stash commands** (RZG-36). Apply, Pop and Drop carry the stash's sha (`sha`), not
    its `stash@{n}`, which moves when a stash is made: the worker finds the stash's
    current ref in `git stash list` itself, and a stash that is gone is an error.
    - **Stash Changes** (`git stash push -q -m <message>`, with `--include-untracked`
      when `untracked`) reloads `STATUS | STASHES`. An empty message gives git's own
      ("WIP on *X*: …"). Nothing to stash (only untracked files, without `untracked`) is
      a notice, not an error.
    - **Apply** and **Pop** reload `STATUS | STASHES`. They need a clean working tree
      (no staged, unstaged or untracked changes; the worker checks the status itself),
      so there is always a state to go back to. Then `git stash apply --index <sha>`,
      which restores staged changes as staged and untracked files too (not `-q`: that
      hides the `CONFLICT` lines; the error keeps those and git's `error:` lines, not the
      status git prints after them). **Never left
      half-applied:** if it fails, the worker rolls back (`git reset --hard -q`, and
      `git clean -f -q` of only those files of the stash's untracked part that are
      untracked now) and tries again without `--index`. If that works, the staged changes
      came back unstaged, and a notice says so; if it fails too, it rolls back again and
      the error is git's, saying the stash is kept. Pop then drops the stash (by its
      ref, found again), only when it applied.
    - **Drop** (`git stash drop -q stash@{n}`) reloads `STASHES`. No undo: the
      confirmation is the window's (§8).
    - **Notices:** `run_command` returns a message when a command worked, but not quite
      as asked. The session sends each through `notice_occurred`, after the parts and
      before `command_finished`, and MainWindow shows it in an information box.
  - **Worktree commands** (RZG-49) change the repository's list of worktrees, which
    the session doesn't hold (§4.4). They run in this window's worktree (any worktree
    of the repository can) and reload `BRANCHES`. A command carries the folder
    (`path`, passed after `--`).
    - **Add** (`git worktree add --quiet [-b <branch>] -- <path> <start or branch>`):
      with `start`, a new branch `branch` from `start`; without it, the existing branch
      `branch` checked out. Git refuses a branch already checked out in another worktree,
      a taken branch name, and a folder that exists and isn't empty.
    - **Remove** (`git worktree remove [--force] -- <path>`) deletes the folder (with its
      ignored files) and git's record of it; the branch is kept. Without `force`, git
      refuses a worktree with changes or untracked files, so a stale state can't lose
      work. It refuses a locked one either way. On a missing worktree it drops only the
      record.
    - **Prune** (`git worktree prune`) drops the records of worktrees whose folders are
      gone, except locked ones.
  - **No editor or prompt, ever:** git runs with `GIT_EDITOR=false` and
    `GIT_TERMINAL_PROMPT=0`, and with stdin closed unless a command passes input. A
    command that would open an editor fails instead of waiting for it.
  - **In flight:** `commands_in_flight` holds the commands queued or running, a job's
    until its `command_finished` signals have gone out. The watcher (§4.3) uses it to tell
    the app's own changes from others.
- **Reads write nothing.** Git runs with `GIT_OPTIONAL_LOCKS=0`, so `git status` doesn't
  refresh `.git/index` behind the app's back. **Why:** the watcher (§4.3) would see that
  write and refresh again, for ever.
- **Worktrees** aren't a session part: each window's `WorktreeReader` reads them (§4.4).
- **Remotes** (RZG-62) are a part, `Refresh.REMOTES`, in `Refresh.ALL`, after the count:
  `Repo.remotes()` (`git remote`) gives their names, in git's order, as
  `remotes_changed(tuple[str, ...])`. Fetch is enabled by them (§8). The same load reads
  `remote.pushDefault` (RZG-72; `Repo.push_default()`, None if unset): the value is a
  `Remotes(names, push_default)`, and `remotes_changed` sends the names when either
  changes. `session.remotes` is the names, `session.push_default` the other.
- **Workspace files** (RZG-96) are a part, `Refresh.WORKSPACE_FILES`, in `Refresh.ALL`,
  after the remotes: `Repo.workspace_files()` (`git ls-files -z -- '*.code-workspace'`)
  gives the tracked VS Code workspace files, relative to the root, as
  `workspace_files_changed(tuple[str, ...])`. Open with VS Code picks its window with them
  (§8). The watcher doesn't reload them: a new one counts after Refresh or reopening.
- **Refs** (RZG-77) are a part, `Refresh.REFS`, in `Refresh.ALL`, after the branches:
  `Repo.refs()` (`git for-each-ref refs/heads refs/remotes refs/tags`) gives each
  branch and tag as a `Ref(name, sha)` (`git_models.py`): its full name
  (`refs/heads/main`, `refs/remotes/origin/main`, `refs/tags/v1.0`) and the commit it
  points at, an annotated tag peeled to its commit. They are History's labels (§5.4).
  **A job that loads `BRANCHES` or `HISTORY` loads `REFS` too**, so whatever moves a ref
  (a command, the watcher, §4.3) reads them again. They aren't read with `git log`'s `%D`,
  since History doesn't run `git log` while HEAD hasn't moved: a new tag or branch would
  never show.
- **Network commands** (RZG-62) talk to a remote, so they can take minutes. They run on a
  second worker, **`NetworkWorker`** (`core/network_worker.py`), owned by the session,
  so status, diffs and history keep loading and refreshing on the first one meanwhile.
  - **One at a time.** A `NetworkCommand` (frozen, compared by identity, like
    `GitCommand`) has a `kind` (`NetworkKind.FETCH`, `PULL`, `PUSH`). `fetch()` returns None while one runs. `network` is the running command;
    `network_changed` sends it when it starts and None when it ends.
  - **No clash with the first worker:** a network command writes only remote-tracking
    refs, `FETCH_HEAD`, objects and (Push's `-u`) the branch's upstream config, never the
    working tree or the index, and git retries
    a locked `packed-refs` itself (`core.packedRefsTimeout`).
  - **Fetch:** `git fetch --all --prune --progress`: every remote, dropping
    remote-tracking branches that are gone. When it ends, ok or not (some remotes may have
    been fetched), the session refreshes `BRANCHES`, for the upstream counts (the watcher
    sees the refs change too, §4.3).
  - **Pull's fetch** (RZG-71): `git fetch --prune --progress -- <remote>`, the upstream's
    remote (`Branch.remote`). The `NetworkCommand` carries the remote, the branch and
    `stash`. When it ends ok, the session queues the `PULL` command (`FF_ONLY`, above);
    if it failed or was cancelled, nothing is integrated or stashed. An upstream on `.`
    (a local branch) skips the fetch: `pull()` queues the command at once and returns it.
    `pull()` returns None with no repository, on a detached HEAD, without an upstream
    (or one that is gone), or while a network command runs.
  - **Push** (RZG-72) pushes the current branch *X*; never forced, and no tags. The
    `NetworkCommand` carries the remote, the branch and `track`.
    - **To its upstream:** `push()` with no remote, when *X* has an upstream on a remote
      that isn't gone (`Branch.remote` not `.`, `ahead` not None). The worker first reads
      `branch.X.merge` (`Repo.config_value`, on its thread), then runs `git push
      --progress -- <remote> refs/heads/X:<merge>`: an explicit refspec, so `push.default`
      and `branch.X.pushRemote` don't change where it goes.
    - **To track it:** `push(remote)`: `git push -u --progress -- <remote>
      refs/heads/X:refs/heads/X`, which sets (or replaces) *X*'s upstream to
      *remote*/*X*. That is how a branch with no upstream, a gone one or one on `.` is
      pushed.
    - **Rejected** (`[rejected]` with `(fetch first)` or `(non-fast-forward)` in git's
      output): the error starts "*remote* has commits that *X* doesn't. Pull first, then
      push.", then git's output. Any other failure (a hook, a protected branch) is git's
      output, as for Fetch.
    - When it ends, ok or not, the session refreshes `BRANCHES` (the ↑n), as for Fetch.
    - `push()` returns None with no repository, while a network command runs, with no
      remote, on a detached HEAD or before the first commit (*X* isn't in `branches`),
      and with no remote given when *X* has no upstream to push to.
  - **Git runs through `Popen`**, not `run_git`, so it can be read while it runs and
    killed: `Repo.start_network_git(*args)`, with `network_env()`, stdin closed, stderr
    into stdout, and `start_new_session=True`.
  - **Progress:** the worker reads the output as it comes. A segment ending in `\r` is
    git's progress line, overwritten in place; `network_progress` sends the latest
    one (or any line), at most every 100 ms, stripped of `remote: ` and spaces.
  - **Cancel:** `cancel_network()` sends SIGTERM to the command's process group (git and
    the ssh or https helper it started): git's own cleanup removes its lock files, and a
    fetch cut short leaves the refs it hadn't updated as they were. A cancelled command
    reports no error. `open()`, `close()` and `shutdown()` cancel it too (quitting doesn't
    wait minutes); `shutdown()` then waits for it to end.
  - **Errors:** a failed command's error is its output without the progress lines,
    through `error_occurred`. **Results:** `network_finished(command, ok)`, after the
    error; nothing for a cancelled command, or one of a repository closed meanwhile.
  - **Authentication** works as in a terminal for an SSH agent and HTTPS credential
    helpers. **No prompt can hang:** the new session has no terminal, so ssh can't open
    `/dev/tty`, and `GIT_TERMINAL_PROMPT=0` stops git's own prompt. A desktop askpass
    program answers them if one is set (`SSH_ASKPASS`, e.g. ksshaskpass; then
    `SSH_ASKPASS_REQUIRE=prefer`, unless set, so ssh uses it without `DISPLAY`). Without
    one, the prompt fails, and an error that shows one ("terminal prompts disabled",
    "could not read Username", `ssh_askpass`, "Host key verification failed",
    "Permission denied (publickey") starts with "RZGit can't ask for credentials:
    use an SSH agent, a credential helper or an askpass program."

### 4.3 RepoWatcher

`RepoWatcher(QObject)` (`core/repo_watcher.py`) keeps the session up to date with changes
made outside the app: an editor saving a file, a commit or checkout in a terminal, a
fetch. It only ever calls `session.refresh(parts)`, so panels don't know it exists.

```python
class RepoWatcher(QObject):
    def __init__(self, session, parent=None, *, delay_ms=300, max_delay_ms=2000,
                 max_folders=10_000, poll_ms=3000)
    def set_active(self, active: bool) -> None   # the app is active (AppWindows)
    def shutdown(self) -> None
    busy: bool          # a change waits, or the watch set is being built (tests wait on it)
    polling: bool       # over the cap: the working tree is polled, not watched
    folder_count: int   # working tree folders watched
```

- **Linux inotify** (`core/inotify_watcher.py`), through `ctypes`, read on a
  `QSocketNotifier`. One watch per folder. **Why not `QFileSystemWatcher`:** on a folder
  it misses files changed in place (VS Code saves that way), and it doesn't say which
  file changed, so ignored files can't be told apart. Where inotify isn't available,
  nothing is watched and only the focus refresh below works.
- **What is watched.** On `repo_changed`, a background job lists the folders:
  - the git dir and the common dir (`git rev-parse --git-dir --git-common-dir`, so a
    linked worktree works) and every folder under `refs/`;
  - the working tree's root and every folder holding a file `git ls-files --cached
    --others --exclude-standard` lists, plus untracked folders (`--directory`), so an
    empty new folder is seen too. Ignored folders (`.venv`, `build/`) aren't watched.
- **What a change reloads:**

  | Changed | Parts |
  |---|---|
  | `index` (git dir) | `STATUS` |
  | `HEAD`, `packed-refs`, anything under `refs/` | `HEAD \| BRANCHES \| HISTORY \| STASHES \| COUNT` |
  | `config` (remotes and upstreams, RZG-62) | `BRANCHES \| REMOTES` |
  | a working tree file or folder git doesn't ignore | `STATUS` |
  | `*.lock`, `objects/`, logs, anything else in `.git` | nothing |

  A working tree name the last listing didn't have is checked with `git check-ignore`
  (`Repo.ignored`) in the background first, so an ignored file causes no refresh. A new
  folder that isn't ignored is walked (pruning ignored subfolders) and watched. A folder
  moved or deleted stops being watched. A changed `.gitignore` rebuilds the watch set.
  `HISTORY` after a ref change is cheap: with HEAD unchanged it runs no `git log` (§4.1).
  Every stash change rewrites `refs/stash`, dropping an older stash too (git rewrites the
  ref with its reflog), so the reflog in `logs/` needn't be watched.
- **Merged:** changes are collected until 300 ms pass without one, at most 2 s after the
  first, then sent as one `refresh(parts)`. A burst (a checkout, a build) is one refresh,
  or one every 2 s while it lasts. The session merges it into a waiting job too.
- **The app's own commands** already reload what they change (§4.1). While
  `session.commands_in_flight` isn't empty, changes inside `.git` are counted as theirs
  and dropped, and so are working tree changes to the paths of an in-flight Discard or
  Discard Lines. On
  `command_finished` the watcher reads the events already waiting, so a command's own
  changes are dropped even if they arrive after it. Any other working tree change still
  counts. A git command run outside the app in that same moment is missed; the focus
  refresh catches it.
- **The cap:** past `max_folders` working tree folders (or when inotify runs out of
  watches), the working tree isn't watched, only `.git`. Instead `STATUS` is refreshed
  every 3 s while the window is active (`polling`).
- **Focus:** `set_active(True)` after the window was inactive refreshes everything
  (`Refresh.ALL`): it catches whatever the watcher missed. `AppWindows` (§8.1) calls it
  on every window's watcher from `QGuiApplication.applicationStateChanged`, and on a new
  window's watcher when it's made while the app is active. The first activation at startup doesn't
  refresh, since `open()` just loaded everything.
- **Overflow:** if the kernel's event queue overflows, the watch set is rebuilt and
  everything refreshed.
- Its own git (listing folders, `check-ignore`) runs on a background thread of its own:
  it only reads, so it can't clash with the session's worker. Results from a repository
  since closed are dropped (a generation counter).

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
- **A stash is selected the same way** (RZG-36): the Stash panel sets `selection.commit`
  to a `Stash`, so Commit Details, Commit Files and Diff show it as they show a commit,
  and the working tree's panels collapse. History shows no row selected meanwhile. A
  selected stash isn't in the history, so `history_changed` leaves it alone; instead
  Selection listens to `stashes_changed` (before any panel): the selected stash is
  replaced by the new list's stash with the same sha (its `stash@{n}` may have moved), or,
  if there is none (popped, dropped), `commit` is set to None.
- **A selected commit that disappears** (amend, reset, rebase): Selection listens to
  `history_changed` (before any panel) and sets `commit` to None when the new history
  doesn't have it, which is back to Uncommitted changes. Loaded history always reaches
  the selected commit (a reload keeps every commit down to the oldest loaded, §4.1), so
  one missing from it is gone.

### 4.4 WorktreeReader

`WorktreeReader(QObject)` (`core/worktree_reader.py`, RZG-48) keeps the list of the
worktrees of a window's repository and each one's state, for the Worktrees panel (§5.4).
Each main window has one, made by `AppWindows` (§8.1) and passed in `PanelContext`. It
follows the window's session, but it only reads, so it has a worker thread of its own,
like the watcher's (§4.3): it can't clash with any window's session.

```python
class WorktreeReader(QObject):
    worktrees_changed = Signal(object)      # tuple[Worktree, ...], the main worktree first
    state_changed     = Signal(str, object) # path, WorktreeState | None
    error_occurred    = Signal(str)         # listing failed (once per new error); the list is empty

    def __init__(self, session: RepoSession, parent=None, *, delay_ms=300, max_delay_ms=2000)
    def attach(self) -> None                # a panel shows the list: read it, and watch
    def detach(self) -> None                # that panel is gone: stop
    def refresh(self) -> None               # the list, then every worktree's state
    def set_active(self, active: bool) -> None   # the app is active (app.py, like the watcher)
    def shutdown(self) -> None
    attached: bool
    worktrees: tuple[Worktree, ...]
    error: str | None                       # the last listing's error, if it failed
    def state(self, path) -> WorktreeState | None   # None: not read yet, or missing
    busy: bool                              # work queued or running, or a change waits
```

- **Only while it is used:** the Worktrees panel calls `attach()` when it is built and
  `detach()` when it is deleted (hidden, §6.6). Detached, the reader reads nothing and
  watches nothing, and forgets its list, so a hidden panel costs nothing. `refresh()`
  does nothing while it is detached, or with no repository open.
- **It follows the session:** on `repo_changed` it forgets the list, the states and the
  watches (a generation counter drops results still on their way) and, if attached,
  reads the new repository's. A worktree of the same repository is the same list, read
  again.
- **Listing:** `Repo.worktrees()` runs `git worktree list --porcelain -z` in the common
  dir (`list_worktrees`), so it works even when this window's worktree is gone, and returns a
  `Worktree` (`git_models.py`) for each, the main worktree first, as git lists them; a
  bare main repository's entry is left out. A `Worktree` has `path` (canonical), `head`
  (its commit; None before the first), `branch` (the short name; None when detached),
  `main`, `locked` and `lock_reason`, and `missing`: git calls it prunable, or its folder
  isn't there. `name` is the folder's name.
- **State:** `worktree_state(path)` (`git/repo.py`) runs one `git status --porcelain=v2
  --branch -z` in the worktree and returns a `WorktreeState`: `changed` (the files with
  staged, unstaged or untracked changes, each once), `upstream` (its name, or None), and
  `ahead` and `behind` (None without an upstream, or when it's gone). A missing
  worktree's state isn't read: it stays None.
- **When:** on `attach()`, when a repository opens, again when the app becomes active
  after it wasn't (`set_active`), and after a change the watch sees. A job lists the worktrees,
  emits `worktrees_changed` if the list changed, then reads the states one worktree at a
  time and emits each one that changed. So a slow worktree doesn't hold the others back,
  and the list fills in as they come. A refresh asked for while a job waits joins it;
  one asked for while a job runs makes one more job after it. A worktree no longer
  listed loses its state.
- **What it watches** (an `InotifyWatcher` of its own), rebuilt after each listing:
  - the common dir: its `HEAD`, `index` and `packed-refs`, and `worktrees/` appearing;
  - `worktrees/`, for worktrees added or removed, and each `worktrees/<name>/`, a linked
    worktree's own git dir (its `HEAD`, `index`, `locked`); the worktree it belongs to
    is read from its `gitdir` file;
  - every folder under `refs/`.
- **What a change reads again:** a linked worktree's git dir → the list and that
  worktree's state; the common dir's `HEAD` or `index` → the list and the main
  worktree's; `refs/`, `packed-refs` or `worktrees/` itself → the list and every state.
  `*.lock` files and anything else change nothing. Changes are merged as the watcher's
  are: sent 300 ms after the last one, at most 2 s after the first.
- **Not live:** unstaged edits change no git dir, and a worktree's folder deleted outside
  git changes none either. Both show on the next activation (RZG-42, decision 8).
  Without inotify, only opening and activation read anything.
- **Shutdown** (the window closing) drops queued work and waits for the running git
  command; results that arrive after it are dropped.
- **After a worktree command** (§4.1, RZG-49) `WorktreeDialogs` calls `refresh()`, so
  this window's list follows even without inotify. Other windows' readers follow
  through their watches.

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
    columns: ColumnPrefs                           # each panel's column choices (ColumnHeader)
    sorts: SortPrefs                               # each panel's sort order (SortMenu)
    worktrees: WorktreeReader | None = None        # the window's worktree reader (§4.4)
    prefs: Preferences = Preferences()             # the app's settings (§8.2); in memory by default

class Panel(QWidget):
    panel_id: ClassVar[str]                        # stable, saved in layouts: "history"
    title: ClassVar[str]                           # "History"
    show_header: ClassVar[bool] = True             # False: the frame draws no section header
    fixed_height: ClassVar[bool] = False           # True: its pane in a vertical split can't be resized
    always_shown: ClassVar[bool] = False           # True: the layout never leaves it out (§6.6)
    movable: ClassVar[bool] = True                 # False: can't be dragged, isn't a drop target (§6.2)
    header_changed = Signal(str)                   # emitted by set_header()
    activate_requested = Signal()                  # emitted by request_activation()
    collapsed_changed = Signal(bool)               # emitted by set_collapsed()
    header_actions_changed = Signal()              # emitted by set_header_actions()

    def __init__(self, ctx: PanelContext, parent=None): ...   # final: panels don't override it
    def _setup(self) -> None: ...                         # the panel builds its widgets here
    def apply_theme(self, theme: AppTheme) -> None: ...   # default no-op; the base wires theme_changed
    header: str                                           # read-only; `title` until set_header()
    def set_header(self, text: str) -> None: ...          # e.g. "Modified files (19)"; emits on change
    def request_activation(self) -> None: ...             # "bring me to the front" (a tab)
    collapsed: bool                                       # read-only; False until set_collapsed()
    def set_collapsed(self, collapsed: bool) -> None: ... # "nothing to show here now"; emits on change
    header_actions: tuple[QAction, ...]                   # read-only; () until set_header_actions()
    def set_header_actions(self, actions) -> None: ...    # the header menu's; emits on change
```

**The header menu** (RZG-48): `header_actions` are the panel's commands for the panel as a
whole: Open, New Worktree…, Prune Missing Worktrees… and Refresh in Worktrees (RZG-48,
RZG-49), and **Sort By** (RZG-43, below). The layout shows them (§6.2): a **⋯** button at the right of the section
header in a split, and the menu of the panel's tab when it is right-clicked in a tab
group. A panel with none shows no button and no menu. The panel owns its actions (they
are its children, or come from `ctx.actions`) and keeps their enabled state; the layout
adds none of its own.

**Sorting** (RZG-43). A panel whose list can be sorted declares its orders as a
`SortOptions` (`core/sort_options.py`, plain Python): a tuple of `SortOption`s, each with
a saved key, a label, a key function on the panel's own items, a default direction
(newest first is descending) and optionally the column label that picks it. The first
option is the **natural order**, git's or the app's own, with no key function: the way
back, and the default unless `default_key` names another option (RZG-50: Worktrees' Recently
Used). An unknown key reads as the default. `apply(items, order)` is a stable sort, so ties
keep the natural order. `first` is a tuple of tests for rows **pinned** at the top in every
order, reversed or not (RZG-99): the rows passing the first test, then the second, then the
rest in the chosen order; a row passing two is shown once, at the first. Branches pins the
current branch; Worktrees this window's worktree, then the main one.
- **`SortMenu`** (`ui/widgets/sort_menu.py`) is a panel's **Sort By ▸** submenu: the options
  as radio items, a separator, then **Reversed** (relative to the option's default
  direction). The panel puts its `menuAction()` last in its header actions. It holds the
  panel's `SortOrder` (an option's key and `reversed`), read from `ctx.sorts` when it is
  built and written there on each change (the default as no entry), and emits
  `order_changed`; the panel renders again with `sort.apply(items)`.
- **Column labels:** `attach(column_header)` makes the labels of its options' columns
  clickable (§5.2). Clicking one picks that option; clicking the current one flips
  Reversed. The sorted column's label shows an arrow.
- **Through a re-sort** the selection stays on the same items (by identity: the file,
  the stash's sha, the worktree's path, the branch's name), and the current row is
  scrolled into view. A refresh keeps the order.
- A panel that builds its list later (Staged Files, Commit Files) makes its `SortMenu`
  in `_setup`, so the menu is there while it is collapsed, and its list uses the saved
  order.
- **History** isn't sortable: its paging and graph need commit order, and its header
  menu keeps Topological Order (RZG-58), and doesn't group (below).

**Grouping** (RZG-53). A panel whose rows can be put under headings declares
`GroupOption`s in its `SortOptions` (`groups`): each with a saved key, a label, a
function giving an item's group name, and the group order: names listed in `order` come
first, in that order, then the others by name (ignoring case), then those in `last`
("Other"). `SortOrder.group` is the chosen option's key, `None` (the default) for no
grouping. `grouped(items, order)` sorts, then splits the rows into `Group`s (a name and
its items, in group order, empty groups left out): **groups come first, and the sort
applies inside each group**. Reversed reverses only inside the groups. Pinned rows
(`first`) are in no group: `top(items, order)` gives them, and the panel shows them as
top-level rows above the headings, counted in no heading (RZG-99).
- **`SortMenu.group_menu`** is the **Group By ▸** submenu: **None**, then the options,
  as radio items. A panel with groupings puts it just above Sort By
  (`SortMenu.header_actions()`); one `order_changed` covers both. Choosing a sort keeps
  the grouping, and the other way round.
- **Headings** (`GroupedTree`, `ui/widgets/grouped_tree.py`, a `QTreeWidget`): a top-level row
  per group, its rows under it, spanning the columns: the group's name and its count
  ("Modified (3)"), bold, in `group_header_text` on `group_header_bg` (§7.1). While
  grouped the tree shows the arrows (rows indented 10 px, as folders), so a heading folds
  by its arrow or a double-click. The stylesheet ignores an item's own background, so
  `drawRow` paints the heading's under the whole row.
  A folded heading stays folded by its group (the grouping's key and the name), per
  panel, while the app runs (not saved); opening another repository forgets them.
- **Selectable or not:** in the file panels a heading is selected like a folder (§5.4).
  In Branches and Stashes it isn't: a click on one leaves the selection as it is, the
  arrow keys skip it, and it has no menu.
- **Which panels:** the file panels (Status), Branches (Prefix, Merged) and Stashes
  (Branch), §5.4. Worktrees and History don't group.

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
2. sets the content `surface` style property, until the layout places it,
3. calls `self._setup()`. The panel builds its widgets, connects its signals and renders
   the current state (contract rule 3),
4. calls `self.apply_theme(ctx.theme.current)`,
5. connects `ctx.theme.theme_changed` to `apply_theme`.

A panel doesn't choose its background: the layout sets its `surface` from the area it is
in (§6.1, §7.2), so a panel dragged into the Main pane looks like it belongs there.
Outside any layout (a panel's own tests) it stays on the content surface.

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

**Segments inside a panel** (RZG-38): a panel that shows distinct parts can stack them as
segments (`ui/widgets/panel_segment.py`) instead of laying out its own headers and scroll
areas.
- `PanelSegment(content, header=None, fill=False)`: an optional header strip above the
  content. The header is text (a wrapping, selectable label) or a widget, styled by
  `Role.SEGMENT_HEADER`: the header background, bold, normal case, at
  `FontKind.SEGMENT_HEADER`'s size. That's unlike the frame's own title bar (small
  capitals), so a segment never looks like another panel. `header_label` returns the
  label of a text header.
- A **pinned** segment (`fill=False`) is always its content's natural height. A **fill**
  segment puts its content in a frameless scroll area on the panel's own surface: never
  taller than the content, so a short one leaves no gap, and it scrolls when it's longer
  than the room left. It shrinks to `min_lines` lines (1) at the least.
- `PanelSegments(segments)` stacks them top to bottom; spare space goes after the last
  one. It is itself in a scroll area that scrolls only when the panel is shorter than the
  pinned segments plus the fill segments' minimums, so nothing is ever cut off.
- Contents never scroll sideways: they get the segment's width.

**Column labels** (RZG-41): a list with columns labels them with a `ColumnHeader`
(`ui/widgets/column_header.py`), never Qt's plain header. History, Stashes and the three
file lists have them. `ColumnHeader(panel_id, prefs)` is a `QHeaderView` (so columns stay
aligned with the list and keep Qt's drag-resizing) that paints its own sections from the
theme's `column_header_*` tokens (§7.1); the owning panel passes the theme on in
`apply_theme`. There is no stylesheet rule for it.
- **Look:** a row of chrome: its own background, labels at full text strength and normal
  weight (the section header above stays the stronger level), a 1 px line under the row,
  and an inset separator (60% of the row's height) between neighbouring labels, none after
  the last. The hovered label's text is `column_header_text_hover`. The same on sidebar
  and content surfaces. Its font is `FontKind.COLUMN_HEADER` (the `QHeaderView` class
  font, §7.3).
- **Short labels:** a label too wide for its column shows its first letter and ".."
  ("Status" → "S.."), cut off if even that doesn't fit; its tooltip then gives it whole.
  A column's size hint is the whole label's, so fitting a column to its contents never
  shortens its label: only a column made narrower than that does.
- **Sorting** (RZG-43): a label is clickable only when the panel's `SortMenu` has an
  option for its column (§5.1); others don't sort or select. A sortable column's size
  hint makes room for the arrow, so sorting never shortens its label. The sorted
  column's label shows a small triangle at its right edge, in the label's text colour:
  pointing up for ascending (A to Z; in Age, newest first), down for descending.
- **Showing and hiding columns:** right-clicking the labels gives a checkable list of the
  columns. The main column (Subject, Message, Name; the first, except in History,
  whose first is the Graph, `ColumnHeader(main_column=...)`) is always shown, so
  its entry is disabled. The choice is saved per panel in `ctx.columns` (`ColumnPrefs`,
  §6.3) and applied whenever the list's columns or labels are (re)created.
- **Hidden at first** (RZG-48): a panel can name columns that start hidden
  (`ColumnHeader(..., hidden_by_default=...)`: Worktrees' Branch and Path). They are
  hidden until the user shows them; once the user changes any column of that panel, its
  own choice is kept, even "none hidden".

**Column widths** (RZG-40), the same in every panel with labels, kept by `ColumnHeader`:
- **Always the full width.** The columns fill the list's width exactly: the first
  column's left edge and the last column's right edge are pinned, and the list never
  scrolls sideways. The main column (Subject, Message, Name) takes what the other columns
  leave. When even its minimum doesn't fit, the others shrink for now, in proportion;
  their widths come back when there is room.
- **Fitted until the user sizes it.** On each render the panel fits its other columns to
  their contents: `fit(column)` (the view's size hint and the whole label) or, where
  that is slow, `set_fitted(column, width)` (History, §5.4). A column the user has
  resized keeps the user's width: fitting no longer changes it, through refreshes,
  reloads and switching repository.
- **Dragging a separator** moves only the two columns beside it: the left one grows as
  the right one shrinks, so no other separator moves and the separator follows the
  pointer. No column can be narrower than its short label (`minimumSectionSize`). The
  pinned outer edges can't be dragged. Qt's own resizing is off (every column is
  `Fixed`): `ColumnHeader` does the dragging, the split cursor and the layout.
- **Saved:** the dragged widths of the other columns are saved per panel and column label
  in `ctx.columns` when the button is released, and given back when the columns are
  created (between runs). The main column has no width of its own to save.
- **Getting back:** double-clicking a separator fits the column beside it again (the
  left one, or the right one after the main column); its other neighbour gives or takes
  the difference, so again no other separator moves. **Reset Column Widths**, after the
  columns in the labels' right-click menu, fits every column and forgets the saved
  widths.
- A cut-off name is still readable from its row's tooltip: file rows show their path,
  folder rows theirs, a stash its whole message.

### 5.3 Registry

`panels/panel_registry.py` has an explicit `PANEL_TYPES = (ControlsPanel, BranchesPanel,
HistoryPanel, FilesPanel, StagedPanel, DiffPanel, CommitPanel, CommitDetailsPanel,
CommitFilesPanel, StashPanel, WorktreesPanel)` and a `panel_type(panel_id)` lookup that raises `KeyError` for an unknown id.
**Why explicit:** no import-time side effects or decorator magic, and one place to see
every panel. The lookup table is built at import by `index_panel_types()`, which raises
`ValueError` on a duplicate id, so a clash fails at startup and in every test run.
`WorktreesPanel` is the last of them.

### 5.4 Panels

The first three are extracted from the original `main_window.py` with the same behaviour
(History no longer stops at 500 commits):

| Panel | id | Default area | Shows | Writes |
|---|---|---|---|---|
| ControlsPanel | `controls` | Controls | Open, Refresh, Fetch, Pull, Push (`ctx.actions`), the branch button | switches and makes branches, through `actions.request_branch`; fetches, pulls and pushes, through `actions.fetch`, `actions.pull` and `actions.push` |
| BranchesPanel | `branches` | Sidebar | branches, current in bold; by name or last commit | branch operations, through `actions.request_branch` |
| StashPanel | `stash` | Sidebar | "Stashes": message, branch and age of each, newest first | `selection.commit` (a `Stash`); stash operations, through `actions.request_stash` |
| HistoryPanel | `history` | Main pane | Uncommitted changes, then the commit table; no row while a stash is selected | `selection.commit` |
| CommitDetailsPanel | `commit_details` | Main pane | "Commit Details", below History and Diff: the selected commit's message, author, committer, SHA, parents and refs; collapsed for Uncommitted changes | `selection.commit` (a parent link) |
| CommitFilesPanel | `commit_files` | Sidebar | "Commit Files": the selected commit's changed files, in place of Unstaged Files, Staged Files and Commit; collapsed for Uncommitted changes | `selection.select()` |
| FilesPanel | `files` | Sidebar | "Unstaged Files": unstaged and untracked files, status colours | `selection.select()`; stages |
| StagedPanel | `staged` | Sidebar | "Staged Files": the index's changes; collapsed when empty | `selection.select()`; unstages |
| DiffPanel | `diff` | Main pane | the diff of `selection.file`, unified or side by side | stages, unstages and discards hunks and lines |
| CommitPanel | `commit` | Sidebar | the commit message being written | commits the staged changes |
| WorktreesPanel | `worktrees` | Sidebar | "Worktrees": each worktree's name, state and ahead/behind (branch and path on request); collapsed with only one worktree | opens a worktree, through `actions.request_open_worktree` and `request_open_worktree_here`; asks for worktree operations (`request_worktree`) |

**The counts on Pull and Push** (ControlsPanel, RZG-94): the `↓n` on Pull and the `↑n` on
Push are drawn in colours of their own (`upstream_behind`, `upstream_ahead`, §7.1), while
the label keeps the button's text colour. A tool button can't colour part of its text, so
each of the two is a `_CountButton`:
- It shows its action's icon text ("Pull"), as any tool button does, and its action's
  count (`upstream_count(action)`, §8) in a `QLabel` of its own on the right, with the
  `upstream-behind` or `upstream-ahead` style role. The label ignores the mouse, so a click
  or hover on it is the button's.
- With a count, the button's size hint is wider by the label and a 4 px gap, and it draws
  its icon and text in the part left of the label; its background (hover, pressed) still
  fills the whole button. Without one (0, no upstream, or while it runs) there's no label,
  and it is a plain tool button.
- A disabled button's label is disabled with it, so the stylesheet mutes it
  (`text_muted`), like the label's text.

**The branch button** (ControlsPanel, RZG-56) names the current branch and switches to
another. It sits at the right end of the Controls panel, beside the Branches panel, which
keeps the other operations. Like Branches, it never runs git or shows a dialog.
- **Label:** the branch's name; "detached at *sha*" (8 characters of `session.head_sha`)
  when HEAD is detached; "*main* (no commits)" before the first commit. It follows
  `head_changed` and `head_sha_changed`. A name longer than 360 px is cut
  short in the middle; the tooltip gives the full label. In a narrow window the button
  gives up width first (RZG-71): its label is cut shorter to fit, down to 60 px, so the
  Controls panel's buttons don't set the window's minimum width. With no repository it is
  disabled and says "No repository".
- **The arrow** is the style's menu indicator, drawn from the theme palette (the
  `branch-button` style role sets only its padding).
- **The menu** is built each time it opens, from `session.branches`, so a branch made,
  renamed or deleted anywhere is in it:
  - the local branches, checkable, the current one checked and disabled (a fresh
    repository's branch is its one, checked, entry); choosing one asks
    `request_branch(SWITCH, name)`, so it gets Branches' confirmations (§8);
  - with more than 15 branches, a **filter box** on top: typing hides the branches whose
    names don't contain the text (ignoring case), and Enter switches to the only one left;
  - a separator, then **New Branch…**: `request_branch(NEW, start)` from the current
    branch, or from HEAD's sha when detached. Disabled before the first commit.

**Branches** (BranchesPanel, RZG-35) is where branches are worked with. It never runs
git or shows a dialog: it asks `actions.request_branch(op, name)` (§8), and the list
follows the session.
- **Sort By** (RZG-43): **Name** (git's order, the default)
  or **Last Commit** (the tip's committer date, `Branch.date`, newest first).
- **Group By** (RZG-53), above Sort By in the header menu: **Prefix** (the part before
  the first `/`, shown with it: "feature/"; branches without one under "Other", last) or
  **Merged** ("Not merged", then "Merged": `Branch.merged`). The list is a `GroupedTree`
  with one column and no labels; the current branch stays bold, and the selection is kept
  by name through re-sorts and refreshes. The "(no commits)" row of a fresh repository
  is never grouped.
- **The current branch is the first row** (RZG-99) in every sort order, reversed or not,
  and above the headings when grouped (`first`, §5.1), shown once. On a detached HEAD the
  first row is instead "HEAD (detached at *sha*)" (8 characters, `session.head_sha`), in
  bold; like "(no commits)" it has no menu and does nothing on a double-click. The panel
  renders on `head_changed` and, while detached, on `head_sha_changed`.
- **Double-click** a branch to switch to it (MainWindow confirms first). The current
  branch, and the "(no commits)" row of a fresh repository, do nothing.
- **Right-click** a branch for its menu, built by `menu_for(name)`:
  Switch to *X*, New Branch from *X*…, New Worktree from *X*… (RZG-49), Rename *X*…,
  Delete *X*…; a **Merge / Rebase**
  submenu with Merge *X* into *current*… and Rebase *current* onto *X*…; then, after a
  separator, Copy Name (to the clipboard, the panel's own job). *current* is the
  current branch, or "HEAD" when detached. On the current branch, Switch, Delete, Merge
  and Rebase are disabled and say why: "(current branch)". The "(no commits)" row and
  the empty space below the list have no menu.

**Worktrees** (WorktreesPanel, RZG-48), below Branches, lists `ctx.worktrees.worktrees`,
this window's worktree first, then the main one, in labelled columns (§5.2): **Name** (the main column),
**State** and **↑↓**, and **Branch** and **Path**, which start hidden (right-click the labels
to show them). Branch, ↑↓ and path are muted (`text_muted`). The other columns fit their
text, the path up to about 48 characters (shown with `~` for the home folder, elided in
the middle), so the name keeps room. A row's tooltip gives its path and branch whatever
is shown. The header counts them ("Worktrees (3)"). With no reader, or none listed, the
list says "No worktrees"; when listing failed, it says why instead. Like Stashes, it never
runs git or shows a dialog.
- **Its reader:** the panel attaches to `ctx.worktrees` when it is built and detaches
  when it is deleted (§4.4).
- **Collapsed** (§5.1) while the repository has only its main worktree (or none is open),
  so it takes no space until there is a second one. A failed listing isn't collapsed: it
  shows why. Like Staged Files, it builds its list the first time it isn't collapsed
  (about 10 ms of startup otherwise).
- **Branch:** its name, "detached at *sha*" (8 characters), or "(no commits)".
- **State:** "…" until read, "clean", "1 changed file", "3 changed files", or "missing
  folder"; a locked worktree adds " · locked", and its tooltip gives the reason.
- **↑↓:** `↑2 ↓1`, leaving out a count of 0, so a branch even with its upstream, or with
  none, shows nothing. The tooltip names the upstream ("2 ahead of, 1 behind
  origin/main", "up to date with origin/main", "no upstream").
- **This window's worktree** (the one `ctx.session` shows) is in bold, and its tooltip
  says "(this window)".
- **Open:** double-clicking a row, Enter, or **Open** in the header menu asks
  `ctx.actions.request_open_worktree(path)` (§8): a main window of its own. Open acts on
  the selected row: it is disabled with none selected and for a missing worktree, and
  double-clicking a missing one does nothing. **Refresh** in the header menu calls
  `ctx.worktrees.refresh()`.
- **The header menu** (RZG-49): Open, **New Worktree…** (`ctx.actions.new_worktree`),
  **Prune Missing Worktrees…** and Refresh. Prune asks
  `ctx.actions.request_worktree(WorktreeOp.PRUNE)`, and is enabled when a listed
  worktree is missing and not locked (git prunes no locked one). The panel is collapsed
  with one worktree, but its ⋯ button still shows, so New Worktree… is there too.
- **Right-click** a worktree for **Open in New Window** (as Open) and **Open in This
  Window** (`ctx.actions.request_open_worktree_here(path)`). Open in This Window is
  disabled on this window's own worktree; both are disabled on a missing one.
  Then (RZG-49) **Open Folder** and **Open Terminal**, disabled on a missing one, and
  **Remove…**, disabled with the reason: "(main worktree)", "(this window)" or
  "(locked)". Each asks `ctx.actions.request_worktree(op, path)`.
- The selection is kept by path through refreshes and re-sorts.
- **Sort By** (RZG-43), last in the header menu: **Git Order**, **Recently Used** (the
  default, RZG-50), **Name**, **Branch** and **Path**, the last three also from their
  column labels (ignoring case). State and ↑↓ don't sort. **This window's worktree is
  first** in every order, reversed or not, then the main worktree (RZG-99; one row when
  this window shows the main one), then the rest in the chosen order (`first`, §5.1).
- **Recently Used** (RZG-50): most recent first, by `ctx.recent.last_opened(root)` (§6.3). This
  window's own worktree counts as the most recent (it is pinned first anyway, RZG-99),
  so a window's list doesn't move when the window is activated (no row jumps under the
  click). Worktrees never used come last, in git's order. Reversed is a plain reverse:
  oldest first, the never-used ones at the top. The panel renders again when a time
  changes (another window was activated), keeping the selection.

**Stashes** (StashPanel, RZG-36) lists `session.stashes`, newest first, in three
labelled columns: **Message**, **Branch** and **Age** ("now", "5 min", "3 h", "2 days", "3 weeks",
"4 months", "2 years"; redrawn every minute). branch and age are muted (`text_muted`), as in History, under the column labels
(§5.2). A row's tooltip has the whole message, the
date and its `stash@{n}`. The header counts them ("Stashes (2)"); with none, the list
says "No stashes". Like Branches, it never runs git or shows a dialog.
- **Selecting** a stash sets `selection.commit` to it (§4.2): Commit Details and Commit
  Files show it in place of the working tree's panels, and its files' diffs show in Diff.
  When `selection.commit` becomes anything else (History, Uncommitted changes), the
  panel's selection is cleared.
- **Right-click** a stash for **Apply**, **Pop** and **Drop…**, asked of
  `actions.request_stash(op, stash)` (§8). Apply and Pop need a clean working tree
  (§4.1): while `session.status` isn't empty they are disabled and say why: "(commit or
  stash your changes first)".
- **Stash Changes…** (`actions.stash_changes`, §8) is a button above the list.
- **Sort By** (RZG-43), the header menu: **Age** (git's order, newest first, the
  default), **Message** and **Branch** (ignoring case, then by age), each from its column
  label too.
- **Group By** (RZG-53), above Sort By: **Branch** (the stash's branch; "(no branch)"
  is a group too), groups by name.

**Unstaged Files** (FilesPanel) and **Staged Files** (StagedPanel, `staged_panel.py`)
are one list each, as in GitKraken. Both are a thin panel around a `FileList`
(`file_list.py`, a widget for one source), since a panel never imports another. A file
with both kinds of change is in both, and each row is one `FileSelection` (worktree or
index). Headers show the count, e.g. "Unstaged Files (29)". Staged Files sits between
Unstaged Files and Commit and collapses (§5.1) when nothing is staged. Like Commit, it
builds its list the first time something is staged (6 ms of startup otherwise).
**Commit Files** (CommitFilesPanel) is the third `FileList`, in commit mode: it lists
`session.commit_files` with one status column, its header names the commit ("Files in
a1b2c3d4 (12)", or "Files in stash@{0} (3)" for a stash; its untracked files have
status "?"), and it has no staging menu or double-click. It asks
`session.request_commit_files` when `selection.commit` changes. **It takes the place of
the working tree's panels**, as in GitKraken: while a commit is selected, Unstaged
Files, Staged Files and Commit collapse and Commit Files is shown; on Uncommitted changes
it's the other way round. Each panel collapses by itself from `selection.commit` (none
knows another), and it sits directly below Commit in every preset, so the host's swap
rule (§6.2) gives it all their space: it is their only stand-in (RZG-65). Like Staged Files it builds its list the first time a
commit is selected; Commit keeps its draft while collapsed.

**Commit Details** (CommitDetailsPanel, RZG-26) shows the commit selected in History. It
asks `session.request_commit_details` when `selection.commit` changes, and collapses by
itself while it is None, like Commit Files. **It sits at the bottom of the main pane**
(RZG-65), below the History and Diff tabs in a vertical split of their own in every
preset, so on the content surface: the message reads at the main pane's width, right under
the commit. Collapsed, its space goes to the tabs above, and it comes back at the height
it had (§6.2); it is nobody's stand-in. It builds its
widgets the first time a commit is selected; uncollapsed with no commit (a layout where it
can't collapse), it shows the placeholder "No commit selected".
- **Content**, in three segments (RZG-38): the **summary** as the header of a pinned
  segment at the top (wrapping, selectable); the **body**, a fill segment, so it scrolls
  by itself and the rest stays in view; and **Details**, a pinned segment at the bottom
  with its own header strip. A commit with no body has no body segment. Details is a grid
  of muted labels (`Role.HINT`) and values: **SHA** (the full sha, monospace, with a small
  **Copy** button), **Parents** (short shas, monospace), **Refs** (one muted line, as git
  writes them; the row is left out when there are none), **Author** (name, email and
  date) and **Committer** (only when name, email or date differ from the author's).
  Dates are local time as `2026-11-16 21:50`; the tooltip has the full time and offset.
  Until the details arrive, the summary and sha of the `Commit` are shown. **A stash**
  (RZG-36) is shown the same way, except that instead of Parents (its base commit, its
  index and its untracked part) it has **Based on**: the first parent only. The content
  never scrolls sideways: a value too long for the panel (the SHA in a narrow sidebar)
  is cut off, and the SHA's tooltip and Copy still give it whole.
- **Wide and narrow** (RZG-65): the message (summary and body) and Details are two
  stacks side by side when the panel is wider than `WIDE` (700 px): `PanelSegments` of
  the message on the left, and of Details, `DETAILS_WIDTH` (320 px) wide, on the right,
  each scrolling by itself. Narrower, the three are one stack as in a sidebar, Details
  pinned below the message. A resize across `WIDE` moves the segments (kept, with their
  text and selection) into new stacks. Built while collapsed, the panel's own width is
  stale, so it is first arranged for the width of its nearest shown container (the main
  pane's split): it isn't arranged narrow and then again wide.
- **Line length** (RZG-65): the summary and body labels are at most `MESSAGE_CHARS`
  (100) average characters of their own font wide, so a wide pane never stretches them
  into lines too long to read; the rest of the width stays empty. The cap is worked out
  again in `apply_theme` (the font may change). A text segment header puts the space a
  capped label leaves after it, so the label stays on the left.
- **The body is reflowed** (RZG-37): commit messages are hard-wrapped at about 72
  characters, which a narrow sidebar would wrap again into ragged lines, so
  `reflow(body)` (`commit_message_text.py`, plain Python) joins the lines of each
  paragraph and the label wraps them to the panel. Each line is blank, a list item
  (`-`, `*`, `+`, `•`, `1.`, `1)`), indented (two spaces or a tab), a trailer
  (`Token: value`, the token without spaces: `Co-Authored-By:`, `Fixes:`, but only in a
  paragraph of nothing else, as git reads trailers: a wrapped prose line may start
  "thread: positions only") or prose. A
  prose line joins the line before only when that is prose too, and not when that line is
  under 50 characters and this one starts with a capital (a short headline). A list
  item's wrapped continuation, indented to the item's text, joins its item. Everything
  else keeps its line break: when in doubt, the break stays. Joined lines are joined with
  one space; blank lines and indented lines are kept exactly. Only the display changes:
  `CommitDetails.message` and `body` are git's.
- **Copying:** every text is selectable with the mouse and copied with Ctrl+C, as shown
  (the body reflowed). Right-clicking the summary or the body offers **Copy Selection**
  (Ctrl+C; disabled with nothing selected), **Copy Message (Git Format)** (the whole
  message as git has it, its line breaks too) and **Select All**. Copy puts the full
  sha on the clipboard. Right-clicking a parent offers *Copy SHA* (its full sha).
- **Parents** are links when that commit is loaded in `session.history`: a click sets
  `selection.commit` to it, and History selects and scrolls to it. Others are plain text.
  The links are worked out again on `history_changed`.
- **Column labels** (RZG-41): **Name** and **Status** (§5.2); in tree mode **Name**
  heads the folder tree.
- **Selecting:** extended selection (Ctrl and Shift) within a panel. One panel holds the
  selection at a time: selecting in one clears the other, since each shows only the rows
  of `selection.files` that are its own.
- **Folder tree** (RZG-25): with **Show Files as Tree** on (§8, the default), each
  `FileList` shows its files under their folders, as in VS Code: a file row is the file's
  name and status letter, a rename shows both names (`old.py → new.py`, or the old path when the
  folder changed), and a chain of folders that each hold one folder is one row
  (`src/rzgit/ui`). Folders come first, then files, each by name. With it off, the
  list is flat, one full path per row, as before. The tree is built by `file_tree.py`,
  plain Python tested without Qt.
  - **Sort By** (RZG-43), in each of the three panels' header menu, saved per panel:
    **Name** (the default: git's path order when flat; by name inside each folder in the
    tree) and **Status** (the status letter as shown, then that order), from the Name and
    Status labels too. In the tree, folders stay first and the order applies inside each
    folder (`sort_tree`); Name reversed reverses the folders too.
  - **Group By** (RZG-53), above Sort By, saved per panel: **Status**, in this order:
    Conflicted, Modified, Type changed, Added, Deleted, Renamed, Copied, Untracked. In
    the tree each status group holds a folder tree of its own, so a folder may show under
    two headings; a folder folded under one stays open under the other. "By folder" is the
    tree itself, not a grouping. A heading is selected like a folder: it selects its
    files, so Stage, Unstage, Discard, Enter and the menu act on them; double-click
    folds it. The folder guides start below the headings.
  - **Expanded by default.** A folder the user collapses stays collapsed, by its path,
    through refreshes, staging and toggling, while the app runs (not saved). Opening
    another repository forgets them.
  - **Folder rows can be selected.** Selecting a folder selects every file under it, so
    Stage, Unstage, Discard, Enter and the context menu act on all of them. Double-click
    on a folder folds it, as usual. Diff shows the current row only if it is a file.
  - Headers count files, not folders.
  - **Easy to read, as in VS Code:** a file's name is in the normal text colour, and its
    status is one letter in the status colour at the right edge (M, A, D, R, C, T; U for
    untracked, so a conflict is `!`). The full word is in the row's tooltip, after the
    path. Folder names are muted, so the files stand out. Each folder level has a faint
    indent guide (`tree_guide`), drawn under the folder's arrow, and the indent is small
    (10 px) so deep trees keep their names. Flat mode colours and letters the same way.
- **Staging:** the context menu shows that panel's actions (§8): Stage, Discard and Stage
  All in Unstaged Files; Unstage and Unstage All in Staged Files. Double-click, or Enter,
  moves the selected rows to the other panel.
- **Open with VS Code** (RZG-96) ends the context menu of all three lists, after a
  separator; in Commit Files it is the whole menu. It opens the selected **file** rows
  (`actions.open_in_vscode(paths, menu)`, §8): a selected folder or heading adds nothing,
  so with only folders selected the entry is disabled ("Select a file to open, not a
  folder."). It opens the working-tree file, also from Commit Files.
- **The selection follows the action:** after Stage the files' rows in Staged Files are
  selected, after Unstage their rows in Unstaged Files, through `selection.follow()`
  (§4.2). The rest of "what stays selected after a refresh" is Selection's too.

HistoryPanel is a `QTableView` over `HistoryModel` (`QAbstractTableModel`), because item
widgets for 100k commits are too slow to build. The model creates nothing per row: `data()`
answers text, the muted colour, the monospace hash font and the stripe from the current
theme when Qt asks, so a theme change is one `dataChanged`.

- **A table, styled as a tree.** A `QTreeView` keeps a layout entry per row and redoes all
  of them on every insert or reset: a page cost 63 ms with 20k rows loaded and 232 ms with
  100k. A table with fixed row heights costs the same at any depth (13–24 ms). The view
  hides the grid and row numbers, selects and hovers whole rows (a small delegate), and
  its header is a `ColumnHeader` (§5.2); the stylesheet's tree rules cover
  `QTableView` too. It renders pixel-identical to the tree it replaced.

- **Uncommitted changes:** while a repo is open, row 0 is "Uncommitted changes (N)", N
  the changed files in the status, in italics, with the other columns empty. It's
  selected by default, and selecting it sets `selection.commit` to None. Commits start at
  row 1: `commit_at(row)` and `row_of(sha)` account for it, and an appended page
  inserts after it. A reset that can't find the selected commit selects it.
- **Stripes** (RZG-63): commit rows alternate between the content background and
  `row_alt_bg` (§7.1), counted from the first commit. The model answers the stripe as
  the cell's `BackgroundRole` (every second commit), not through Qt's alternating rows,
  which would count the Uncommitted changes row. Hover and selection are the
  stylesheet's and are drawn over it. Uncommitted changes is never striped. The stripe is
  clearly visible, about ΔE 4 from the background, and hover stays clear of both row
  backgrounds (RZG-66; the minimums are in §7.1).
- **Uncommitted changes is pinned** (RZG-63). While the table is scrolled, a strip at the
  top of the rows (`_PinnedRow`, a child of the view's viewport) draws row 0 with the
  view's own delegate, hover and selection, with a `border_light` line under it. The
  commits pass under it. At the top it is hidden, and row 0 is simply the first row, so
  the two look alike. Clicking it selects Uncommitted changes. Up from the first commit
  reaches it and Down from it reaches the first commit, as before. `scrollTo` never
  scrolls for row 0, since it is always in view, and keeps any other row clear of the
  strip. The commit "at the top" that a reset keeps (§ Updates) is the first one below it.
- **Follows the selection:** when `selection.commit` is changed elsewhere (a parent link
  in Commit Details), History selects that commit's row, or Uncommitted changes for None,
  and scrolls to it if it's off screen. A commit that isn't loaded leaves History as it is.
- **Back to top** (RZG-64): `BackToTopButton` (`back_to_top_button.py`), a child of the
  view's viewport like `_PinnedRow`, floats over the commits: centred, 6 px below the
  pinned row. The rows pass under it, and it never covers the pinned row, the column labels
  or the scroll bar (it stays inside the viewport). Elsewhere, scrolling, hovering and
  clicking rows work as before; over it, no row is hovered.
  - **When:** it shows once the first commit is more than a screen above the view
    (`value > pageStep`; the view scrolls per row), or while there are new commits and the view isn't at
    the top. At the top it hides.
  - **New commits:** a reset while the view is scrolled down keeps its top commit
    (§ Updates). The panel counts the commits that arrived above the newest commit shown
    before, and adds them up until the view is back at the top, where the count goes back
    to 0. If that commit is gone (a checkout), the panel adds nothing. The label reads
    "↑ N new commit(s)" while there are new commits, otherwise "↑ Back to top". The tooltip reads
    "Back to top (Ctrl+Home)".
  - **Click:** `scrollToTop()`. It's a jump, not a smooth scroll, and the selection
    stays.
  - **Look:** it paints itself from the `floating_button_*` tokens (§7.1): a pill with
    slightly rounded corners (3 px), a 1 px border and a soft shadow under it. The hovered
    pill uses `floating_button_hover`. It fades in and out over 150 ms, and appears at once
    when the desktop turns effects off (`QApplication.isEffectEnabled(UI_General)`). It
    takes no keyboard focus, since Ctrl+Home already goes to the top.
- **Paging:** when the view is scrolled to within a screen of the end, the panel asks the
  session for the next page (§4.1). It watches its scroll bar rather than using Qt's
  `canFetchMore` / `fetchMore`, which Qt also calls while laying rows out, before they have
  a position, so it would load pages nobody scrolled to.
- **The branch graph** (RZG-58) is the first column, **Graph**; Subject stays the
  main column (`ColumnHeader(main_column=SUBJECT_COLUMN)`, §5.2). A dot marks each commit
  in its lane, and lines join it to its parents.
  - **The layout is plain Python:** `CommitGraph` (`core/commit_graph.py`) turns the
    commits, in order, into a `GraphRow` per commit: its lane, its colour, and the lines
    through the row's top half (straight down: lanes into its dot, or passing by) and
    bottom half (from its dot to its parents' lanes, or passing by). Each lane holds the
    commit it waits for, and no two lanes wait for the same one. A commit takes the lane
    waiting for it; a branch tip opens the leftmost free lane. Its first parent continues
    in its lane, unless another lane already waits for that parent (a fork, seen from
    below): then the two join at once, into the left one, as `git log --graph` does.
    Another parent (a merge) joins the lane waiting for it, or opens the leftmost free
    one. Lanes don't move sideways, and a free lane is reused. A new lane takes the next
    of the theme's lane colours that no open lane has, and keeps it down to where it
    ends. On RZE (2,422 commits) the graph is at most 6 lanes wide in date order and 33 in
    topological order (a run of merges from one branch), as wide as git's own.
  - **Built a page at a time.** Rows are worked out when first asked for, and kept. Fitting
    the column (below) asks for every loaded row, so a page's rows are built when it
    loads: about 4 ms for 500 commits on RZE, 10 ms for all 2,422. An appended page adds
    commits to the same `CommitGraph`, which carries on with its open lanes: rows already
    shown don't change. A reset builds a new one.
  - **Drawing:** the delegate draws the graph over the cell's stripe, hover and selection,
    antialiased: straight lines down a lane, a short curve where a line changes lane,
    and a dot at the commit. Uncommitted changes has a hollow dot above HEAD's, in its
    lane, joined to it (in the pinned row too). The sizes are `graph_*` metrics and the
    colours `graph.lanes` (§7.1).
  - **Width (RZG-92):** the column fits every loaded commit, not only the rows in view
    (`set_fitted`, unless the user sized it): the most lanes any row has, up to
    `GRAPH_MAX_LANES` (8), after the inset for the ref labels, or the widest label and its
    `+N` (RZG-86) where they reach further. So the width stays put while scrolling, and a
    label is never cut off by the column's edge. It is fitted again when a page loads,
    the labels change and the theme or font changes, and it shrinks as well as grows: a
    deleted branch's label gives its room back. A width the user drags is kept (saved in
    `ctx.columns`, §5.2) and fitting stops; double-clicking its separator or **Reset Column
    Widths** fits it again. A row with more lanes is cut off there and its
    last lane fades out (a gradient to transparent, so nothing past it shows). The graph
    is clipped to its cell, for a column made narrower.
  - **Cost:** a graph cell that isn't selected, hovered or current skips the style's item
    painting (it has no text, and the style draws no background for it), which costs more
    than the graph; pens and brushes are made once per theme. A full repaint of History
    on RZE costs about 19 ms against 16 ms without the graph (offscreen).
  - History keeps commit order: it isn't sortable (§5.1, RZG-43).
- **Ref labels** (RZG-77, moved onto the graph by RZG-86): each commit's branches, tags
  and stashes. **One label** shows, centred on the commit's dot; a `+N` pill after it
  counts the others, and the tooltip names them all.
  - **What gets one:** local branches, remote-tracking branches and tags, from
    `session.refs` (§4.1), and stashes, from `session.stashes`. A stash's label is on the
    commit it was made on (its first parent), so you can see where it's built from; the
    stash itself isn't in History. `<remote>/HEAD` gets none. A detached HEAD has a
    **HEAD** label on its commit. Uncommitted changes has none.
  - **Plain Python:** `labels_by_commit(refs, head, head_sha, stashes)`
    (`core/ref_labels.py`) gives each commit's `RefLabel`s, by sha, in order: the first
    is the one shown. A `RefLabel` has its `kind` (`HEAD`, `BRANCH`, `REMOTE`, `TAG`,
    `STASH`), its `text`, the `names` it stands for, `current` (HEAD's branch) and a
    stash's `message`.
  - **The order** (RZG-86) is in three tiers:
    1. The **main** label: the local branch `main`, or `origin/main` (alone or in
       `main ⇄ origin`). A repository with neither has `master` in its place. Matched by
       name; `origin/HEAD` isn't read.
    2. A label with any other `origin/…` ref (a remote branch, or a local branch with it).
    3. The rest.

    Within a tier: the current branch (or HEAD), the other local branches, remote
    branches, tags, then stashes (newest first); within a kind, by name.
  - **A branch and its remote:** a local branch and the remote-tracking branches of the
    same name on the same commit are one label, `main ⇄ origin` (`main ⇄ origin, fork`
    for two). That's a branch and its upstream, nearly always; it's matched by name, so
    History needs no upstream data.
  - **Tooltip:** on the Graph cell of a commit with labels: every ref, one a line, in
    full, in the order above: "Branch main (checked out)", "Remote branch origin/main",
    "Tag v1.0", "Stash stash@{0}: *message*", "HEAD (detached)".
  - **Drawing:** the delegate draws the graph, then `RefLabelPainter`
    (`ui/panels/ref_label_painter.py`) draws the label over it. A pill is a rounded rect
    (`ref_label_radius`) with 1 px border and the list font in `text`; the current branch
    and HEAD are bold.
    - **Opaque:** each pill is first filled with the row's background (`content_bg`, or
      `row_alt_bg` on a striped row), so it hides the dot and the lane lines under it in
      that row. Its look is drawn over that fill.
    - A local branch and HEAD: a tint of the commit's lane colour (`graph.lanes`, at
      `graph.label_fill_alpha`) with a border in the lane colour, so the label belongs to
      its line. A remote-only branch: the border alone.
    - A tag: `graph.tag_fill` and `graph.tag_border`. A stash: `graph.stash_fill` and a
      dashed `graph.stash_border`. Neutral, so they don't read as a lane.
    - **Where:** centred on the dot (a row past the lane cap: on the last lane drawn),
      vertically centred, always. Where a label's left half wouldn't fit left of its dot
      (with `ref_label_gap`), the lanes move right: the graph's **inset**, the most any
      loaded commit needs, so every row's lanes still line up. It is worked out with the
      column's width (RZG-92), so it doesn't change while scrolling. The `+N` pill
      (its border and text `text_muted`) follows `ref_label_gap` to its right.
  - **Width:** a pill is at most `ref_label_max_width` (160 px); a longer name is
    shortened in the middle. The Graph column fits every loaded commit, inset and labels
    included (RZG-92). In a column the user made narrower, the label and `+N` move left to
    fit (no further than the left edge), and then the label is shortened.
  - **Turning them off:** hide the Graph column; the labels go with it.
  - **Updates:** the panel rebuilds the labels when `refs_changed`, `stashes_changed`,
    `head_changed` or `head_sha_changed` fires, the model repaints only the Graph column
    (`dataChanged`), and the column is fitted again. Each label's text width is measured
    once per theme. A full repaint of History on RZE costs about 20 ms, as with the Refs
    column (offscreen, noisy).
  - **Later:** a line's own label while its tip is scrolled away (RZG-82), and a context
    menu on a label (RZG-83).
- **Updates:** an appended page inserts rows. Any other change resets the model; the panel
  then re-selects the same commit and keeps the same commit at the top of the view (or
  stays at the top, so new commits show).
- **Column widths:** the author, date and hash columns fit their widest text in the first
  page (`set_fitted`, §5.2: unless the user sized them), as `resizeColumnToContents`
  would. That asks the model for every cell (45 ms for 500 commits), so the panel finds
  the widest text with font metrics and has the view measure only that row. Every row is
  as tall as the first wants.

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
  - **A whole added or deleted file shows unified**, even with Side by Side Diff on: side
    by side, one half would be empty. `whole_file(diff)` (`diff_rows.py`) tells it from the
    diff itself: every hunk has only added lines and an old start of 0 (`"added"`), or only
    removed lines and a new start of 0 (`"deleted"`). That covers untracked, staged,
    commit and stash files alike. A partial add (lines appended) still shows side by side.
    While this applies, a muted hint (`Role.HINT`) beside the toggle says "Unified: whole
    file added" or "… deleted". The action stays checked; the next file with both sides
    shows side by side again.
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
  - **The gutter** (`DiffGutter`, `diff_gutter.py`) sits in the view's left margin and
    paints the visible rows only (the view's `visible_rows`). From the left: line buttons
    (when on, on the row under the pointer and on selected rows), the number columns, then
    the marks.
  - **Text selection** (RZG-73): the text is selectable like a read-only editor, with a
    caret. A drag selects characters, a double click a word, a triple click a line, and
    Ctrl-drag (or Ctrl-click) word by word; Shift-click extends. Arrows, PgUp/PgDn and
    Home/End move the caret, and Shift extends. Ctrl+A selects all the text of the view
    (side by side: of the focused side, so a selection never crosses sides). Hunk links
    and fold rows keep their single click. A right-click inside the selection keeps it;
    with nothing selected, it moves the caret. The selection is in `text_selection`
    (the stylesheet's), over the row backgrounds and word highlights; a fold row inside
    it is filled in that colour across the whole row, since its hidden lines are part of
    it. A new diff of the same file with the same rows keeps it.
    - **Copy** (Ctrl+C, the menu's Copy, and the primary selection on Linux) is code only:
      `copy_text(rows, start, end)` in `diff_rows.py` (no Qt) takes each row's original
      text (`DiffRow.raw`, so `␍` is a carriage return again), skips hunk headers,
      filler and notes, and puts a fold row's hidden lines (`DiffRow.hidden`) in its place.
      Unified copies removed and added lines in the order shown. A selection within one
      row copies what it shows, a hunk header too. With no text selected, Copy copies the
      selected lines.
    - **The API** for find and go to definition: `TextPos(row, col)` (`diff_rows.py`;
      `col` counts Python characters, not UTF-16 units), `text_cursor()`,
      `text_range()` (start before end, or None), `select_text(start, end)` (the caret at
      `end`, scrolled into view), `word_at_cursor()` and `copy()` on `DiffView`.
      `touched_rows(rows, start, end)` gives the rows a selection covers (not the next
      row's start, where a line selected whole ends).
  - **Line selection** (`LineSelection`, `line_selection.py`): the gutter selects whole
    changed lines (`+` or `−`), for staging. A click on a changed line in the gutter
    selects just it; dragging selects the changed lines from there to the pointer
    (scrolling when the pointer leaves the view); Shift-click selects from the last click;
    Ctrl-click adds or removes one line. A click elsewhere in the gutter clears it.
    Context lines, hunk headers, filler and notes are never selected. A selected line has
    a stronger background of its own colour (`diff_added_selected_bg`,
    `diff_removed_selected_bg`, in the gutter too) and a 1 px outline in its mark colour
    (`status.added`, `status.deleted`) around the text; a run of selected lines of one
    kind shares one outline. A new diff of the same file with the same rows keeps the
    selection; any other clears it. Each view (side by side: each side) has its own.
  - **One selection at a time:** a click in the text clears the line selection, and a
    click in the gutter clears the text selection. Esc clears both. `selected_lines()` is
    the line selection, or else the changed lines the text selection touches.
  - **Line buttons:** with `set_line_buttons("Stage")` (or `"Unstage"`), every changed line
    (`+` or `−`) has a small button with that label at the left edge of the gutter. It
    shows while its row is under the pointer (in the text or the gutter; the view
    tells the gutter, and works it out again after a scroll), and on every selected row for
    as long as it is selected: square and green
    (`diff_line_button`) with a thin outline (`diff_line_button_border`: white in the dark
    theme, dark green in the light one), and another green with a hand when the pointer is on it.
    Its column is kept while buttons are on, so nothing moves as the pointer does. A click on
    a selected row's button sends `lines_clicked` with all the selected lines; on another
    row's, with that one line. Off, the column takes no room. (RZG-23; to be iterated on.)
  - **Marks:** after the line numbers, the gutter shows `+` on added rows and `−` (U+2212,
    as wide as `+`) on removed rows, in `status.added` and `status.deleted`. They are
    painted, not part of the text, so copied text is code only, and they are drawn for the
    visible rows only, like the numbers. A line's kind never shows by colour alone.
  - **Hunk links:** when the panel gives it a link text (`set_hunk_link`), every hunk header
    shows it at the right edge of the view, in the accent colour, over the header's own
    background. The pointer becomes a hand over it, and a click sends `hunk_clicked(hunk)`.
    With no link text, the panel can give a tooltip for hunk headers instead.
  - **Word highlights** (RZG-22): in a pair of removed and added lines, the changed words
    are filled in `diff_removed_word` / `diff_added_word`, over the line's (or selected
    line's) background and under the text. Lines pair as in side by side: the nth removed
    line of a run with the nth added line of the run after it (`unified_partners(rows)`,
    `split_partners(left, right)`: each paired row's number → its partner's text). `word_spans(old, new)`
    in `diff_rows.py` (no Qt) splits both lines into tokens (runs of word characters, of
    whitespace, of other characters), compares them with `difflib.SequenceMatcher`, and
    returns each side's changed character ranges; it returns None when the token
    similarity ratio is below 0.5 or either line is over 1000 characters, so lines with
    little in common keep only their line colour. The panel gives the view each row's
    partner text with its rows (`set_rows(rows, partners=…)`). Spans are worked out while
    painting, for visible rows only, cached per row until the next `set_rows`, and placed
    with the block's `QTextLayout.cursorToX`, so tabs line up.
  - **Rows know their line:** each `DiffRow` holds the index of its hunk and of its line in
    the `FileDiff`, so `selected_lines()` turns the line selection into `(hunk, line)`
    pairs. Side by side, each view only holds its own side's lines.
- **Hunks and lines (DiffPanel):** for a worktree or index diff, each hunk header links
  **Stage Hunk** or **Unstage Hunk**, and each changed line's gutter button stages or
  unstages that line, or all the selected lines if it is one of them (it never discards). Right-click offers the hunk under the pointer and the
  selected lines (`selected_lines()`: the line selection, or the changed lines the text
  selection touches; the clicked line when neither): Stage/Unstage Hunk,
  Stage/Unstage Selected Lines and, for a tracked file's unstaged diff, Discard Hunk… and
  Discard Selected Lines…, then Copy. Commit diffs have none of these. A staged rename, or a
  diff with unreadable text, has no links or line buttons, and its menu entries are
  disabled, saying why. Last, after a separator, every diff's menu has **Open with VS
  Code** (RZG-96, §8): the shown file, at the line under the pointer
  (`diff_rows.new_line_at(rows, n)`: the row's new line number; for a removed line, a
  hunk header or a fold row, the next new line, else the last one before it; none at all
  opens the file at the top). The number is the shown diff's new side: in a staged or
  commit diff, the working-tree file may have moved on.
  Discards go through `AppActions.request_discard_lines`: the window confirms (§8). The diff
  that comes back keeps the scroll position.
- **Show Whole File** (RZG-60): the checkable **Show Whole File** action (§8), from the View
  menu or the panel's own button beside Side by Side Diff, asks for the diff with
  `whole=True` (§4.1): every line of the file, the changes highlighted where they are, in
  unified and side by side alike (a whole added or deleted file still shows unified, as
  above). Line numbers, marks, word highlights, line buttons and the hunk actions work as
  in the hunks view, on the hunks `split_hunks` made.
  - **Hunk headers** in the whole-file view are one of three (`HunkHeaders`, in
    `app_actions.py`; from a drop-down button in the bar, shown only while the whole file
    is, and from View → Hunk Headers): **Full**, the `@@` rows where the hunks meet; **Slim** (the default), a
    header row with no text directly above each hunk's first changed line, there for its
    Stage Hunk link; **Off**, none (Stage Hunk is in the context menu). `whole_rows(diff,
    headers)` and `whole_split_rows(diff, headers)` in `diff_rows.py` place them. The
    hunks view always has full headers.
  - **Folding:** every unchanged stretch (a run of context rows) longer than
    `FOLD_MIN_LINES` (20) can be folded: all but `FOLD_KEEP` (3) lines next to each change
    (both are settings, §8.2; a stretch with nothing left to fold isn't one)
    collapse into one `FOLD` row, "⋯ N unchanged lines", in the hunk colours with no line
    numbers. The stretch's first foldable row shows a fold mark (`▾`) in the gutter's
    mark column, and a fold row shows `▸`: a click on the mark folds, and a click on a
    fold row unfolds. Right-click adds **Fold All Unchanged** and **Unfold All**. `stretches(rows)` and `fold(rows, folded)`
    (`diff_rows.py`) do it; a stretch is known by the old and new numbers of its first
    line, so side by side folds both sides at the same rows. Folds are for viewing only:
    they aren't saved, they survive switching views and header modes, and a new diff
    resets them (to all folded, with the setting *Fold unchanged stretches when a whole
    file opens*). Fold rows are never line-selected; a text selection over one copies its
    hidden lines.
  - **Keeping the place:** toggling it keeps the selected lines selected (matched by
    their kind and old and new numbers) and in view; with nothing selected, the line at
    the top of the view stays at the top.
- **Find** (RZG-74): **Ctrl+F** opens the find bar (`FindBar`, `find_bar.py`) at the bottom
  of the panel and focuses its field, filled with the selected text when that is within one
  row and isn't the current match. The shortcuts are the panel's own
  (`WidgetWithChildrenShortcut`: focus anywhere in it), with no menu entry. Esc in the bar
  closes it (the highlights go) and puts focus back in the view; Esc in the view still
  clears the selection.
  - **The bar:** the query field, toggles **Aa** (match case, off by default), **W** (whole
    word), **.\*** (regex) and **Keep**, ↑ and ↓, the count and ×. The count is a hint
    (`Role.HINT`): "3 of 12", "12 matches" while none is current, "No results", or
    "Invalid pattern" (`Role.HINT_WARNING`) for a regex that doesn't compile (nothing is
    found). The toggles are
    kept while the app runs (a new bar starts with the last ones), not saved.
  - **Searching** is live: 100 ms after the last keystroke, or at once for a toggle.
    `FindQuery(text, case, word, regex).pattern()` (`find_query.py`, no Qt) compiles it
    (raising `ValueError` if it can't); `find_matches(rows, pattern)` gives each
    `FindMatch(row, start, end, hidden)` in a view's rows: on context, added and removed
    rows and in a fold row's hidden lines (`hidden`: the index among them), never on hunk
    headers, filler or notes, in the text as shown (`DiffRow.text`). Empty matches are
    skipped; a match never spans rows. A query that can't match a line break (not a
    regex: `FindQuery.joinable`) searches all the rows joined as one text, which is faster;
    a regex searches row by row, so `^` and `$` are a row's ends. It runs on the UI thread:
    the diff size limit (§4.1) caps the text, and Find stops at `MAX_MATCHES` (10,000:
    "3 of 10,000+"), since Python's `re` takes about 50 ms per 100,000 matches. A diff
    at the size limit takes under 40 ms, side by side included.
  - **Stepping:** Enter / Shift+Enter in the field, F3 / Shift+F3 anywhere in the panel,
    and ↓ / ↑, go to the next and the previous match, wrapping at the ends. Matches are in
    row order; side by side, a row's left matches before its right ones. With no current
    match, the first is the one at or after the start of the selection (or the caret).
    The current match is the text selection (`select_text`), scrolled into view, on its
    side (the other side's text selection is cleared), and has a 1 px outline in
    `diff_find_current` (`DiffView.set_find_current`), so it doesn't rest on colour alone.
    The outline goes round the whole word the match is part of (`word_around`, in
    `find_query.py`: a match edge inside a run of word characters moves out to the run's
    end), so "Command" found in "CommandQueue" outlines "CommandQueue"; the fill and the
    selection stay on the match. Stepping onto a match in a fold row
    unfolds that stretch first.
  - **Highlights:** every match on a shown row is filled in `diff_find_match`
    (`DiffView.set_find_spans`), over the row and word backgrounds and under the text,
    for the visible rows only, placed like word highlights.
  - **One diff at a time:** a search never spills into another file. A new diff of the same
    file, or new rows (folds, headers, the view), searches again, and the current match
    stays current while the selection is still on it. Switching to another file (a
    different `FileSelection`) closes the bar, unless **Keep** is on: then the search
    applies to the new file, and its first match is selected.
- **States:** "Select a file to see its diff", "No changes to show", "Binary file", "Large
  diff (N lines) [Show anyway]" and "Couldn't read this diff" are a muted message instead
  of the view (`Role.PLACEHOLDER`). If a diff takes more than 150 ms, "Loading…" is shown.
  A whole file over the size limit also offers **Show Changes Only**, which unchecks Show
  Whole File.
- **Updates:** a new diff of the same file keeps the scroll position; another file starts
  at the top. Selecting a file at another path calls `request_activation()`, so the Diff
  tab comes to the front; a refresh never does.
  - The session's latest diff is shown at once (no "Loading…") only when it was read the
    way the panel wants it now: the same file, `diff_whole` and `diff_context` (RZG-97).
  - **A new context count keeps the change in place** (RZG-103): the hunk at the top of
    the view (the first whose `@@` header is on screen, else the one the top line is in)
    keeps its header at the same height, so its new context shows above and below its
    changes. It is found again by its place in the list: the hunks are the same at any
    count (§4.1). `DiffView.place()` records it as `Place.hunk` (its number, and its
    header's row less the top row), and `go_to(place, by_hunk=True)` scrolls to it and
    selects the selected lines again. Show Whole File's toggle keeps the top line instead
    (its hunks are others).

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
  **One `CommitDrafts`** is shared by every window (app.py makes it; §8.1). It keeps
  **one file per repository** in its folder (app.py gives it `drafts_folder()`:
  `commit-drafts/` in `QStandardPaths.AppDataLocation`, e.g.
  `~/.local/share/RZGit/RZGit/commit-drafts/`). The file is named by the SHA-1
  of the key and holds `{"root": key, "text": draft}` as JSON. It holds no copy in memory:
  `get` reads the file, and `set` replaces it atomically (a temporary file in the same
  folder, then a rename) or deletes it for a blank draft, and doesn't write an unchanged
  draft. So a window, or a second app process, only ever writes its own repository's
  file, and can't overwrite another's or be overwritten with an older copy. A file that
  can't be read, isn't valid, or names another root reads as no draft (logged). Tests
  give it no folder, and it keeps drafts in memory.
  The key is the root's `canonical_path` (§6.3), so every path to one repository finds
  the same draft (on macOS, `/var/...` and `/private/var/...` are one folder). A draft
  whose folder is gone is kept (it may be on a drive not mounted now).
  **Drafts saved by older versions** (`commit/drafts` in `AppSettings`, JSON keyed by the
  root) are moved once: `CommitDrafts(folder, legacy=settings)` reads them
  (`AppSettings.old_commit_drafts`), re-keys them canonically (where two meet, the one
  already canonical wins) and writes each one whose repository has no draft file yet (a
  file is newer). Only when every one is written does it remove the old value
  (`drop_old_commit_drafts()`); if a write fails, they stay for the next start.
- **Amend** (RZG-27): an **Amend** checkbox beside the button shows the `amend` action
  (§8), checked both ways. Turning it on puts the last commit's message in the fields
  (`refresh(LAST_COMMIT)`; at once if `session.last_commit` is already HEAD's), and the
  draft isn't touched while it's on. Turning it off, by hand or by the action, puts the
  draft back. The button reads "Amend Last Commit", or "Amend with 3 Files" when
  something is staged; the message is ready once the last commit's message has arrived
  and the summary isn't blank. Committing calls `actions.confirm_amend(last)` (MainWindow
  asks if it's pushed), then `session.amend(message)`. While Amend is on the panel isn't
  collapsed, even with nothing staged, so the message can be changed alone.
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

class Area(StrEnum):        CONTROLS; SIDEBAR; MAIN
class Arrangement(StrEnum): SIDEBAR_LEFT; SIDEBAR_RIGHT; MAIN_TOP

@dataclass(frozen=True)
class Anchor:      neighbors: tuple[str, ...]; orientation: Orientation | None; before: bool; share: float | None = None; area: Area | None = None
@dataclass(frozen=True)
class LayoutState: areas: Mapping[Area, LayoutNode | None]; arrangement: Arrangement = SIDEBAR_LEFT
                   sizes: tuple[int, int] | None = None; hidden: Mapping[str, Anchor] = {}   # what's saved
```

**Areas** (RZG-69). The window has three areas: **Controls** (the bar across the top),
the **Sidebar** and the **Main pane**. Every layout has all three: `LayoutState.areas`
always holds the three keys, and an area whose panels all left it holds `None` (empty),
never disappears. Each area holds a free tree of splits and tab groups, the
`LayoutNode` below, and the tree functions work on one area's tree.
- **Arrangement:** Controls is always across the top. Below it, the Sidebar sits left
  (`SIDEBAR_LEFT`) or right (`SIDEBAR_RIGHT`) of the Main pane, or below it (`MAIN_TOP`).
  The preset chooses the arrangement; the user moves panels, not areas. `sizes` is the
  split between the Sidebar and the Main pane, Sidebar first (None: the preset's).
- **Which area:** `state.area_of(panel_id) -> Area | None` (None: hidden or unknown), and
  `LayoutHost.area_of` for what's on screen. A window opening asks it ("is History on
  the Main pane?", §6.3).
- **Default area:** the area a panel has in `DEFAULT_LAYOUT` (`default_area(panel_id)`,
  `layout_presets.py`). Panels don't declare one.
- **Surfaces follow the area** (§7.2): Controls is on `CHROME`, the Sidebar on `SIDEBAR`,
  the Main pane on `CONTENT` (`AREA_SURFACES`, in `layout_host.py`). A panel moved to
  another area takes that area's surface.
- **Edits of a whole state** are `LayoutState` methods, each returning a new state:
  `normalized(known_ids)`, `hide(id)`, `show(id, fallback)`, `move(id, target, zone)`
  (beside a panel, in the target's area), `move_to_area(id, area)` (into an empty area,
  which the panel then fills), `insert(id, anchor, fallback)` and
  `with_current_tab(id)` (its tab group's current tab is that panel; the state itself
  when it isn't in a tab group, or is already current). They keep every area,
  so the Main pane stays when its last panel leaves.
- **Controls holds only the Controls panel:** it can't be moved (§6.2), and the Controls
  area is never a drop target.

- `to_dict` / `from_dict` turn a `LayoutState` into `{"version": 3, "arrangement": …,
  "sizes": [s, m], "areas": {"controls": …, "sidebar": …, "main": …}, "hidden": {…}}` and
  back, so the saved format can be migrated. An empty area is `null`. Versions 1 and 2 (one
  tree for the whole window) are migrated as they load: `from_dict(data, default_area)`
  passes them to `migrate_tree` (§6.3).
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
- **Tab order** (RZG-79) is the order of `TabsNode.children`, and the user changes it in
  the host (§6.2). `current` follows the panel, not the index: moving a tab never changes
  which panel is current. A panel hidden from a reordered group records its neighbour in
  the new order (`anchor_of`), so it comes back to its place in it.

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
  "share": 0.3, "area": "sidebar"}` (`share` optional, dropped if it isn't between 0 and 1;
  `area` optional, dropped if unknown: the panel's default area). `neighbors` is empty for
  a panel that was alone in its area: it comes back filling it. `normalized` runs
  `normalize` on each area with one set of seen ids, so a panel in two areas keeps its
  first place (Controls, Sidebar, Main pane).

**Presets** (`layout_presets.py`, `PRESETS`) are named `LayoutState`s that show every
panel. Each has the Controls panel alone in the Controls area, and an arrangement. The
Main pane holds `MAIN` in every preset: History and Diff are tabs of one group, with History current, and Commit Details below
the group in a vertical split of their own (`MAIN`: `Split(V, [Tabs([history, diff]),
commit_details], sizes=(500, 220))`, RZG-65). Staged Files, Commit and Commit Files are
directly below Unstaged Files (`files`), in that order, and **Commit is the bottom panel
of its Sidebar column** in every preset (RZG-93): only Commit Files is below it, and it
has no space of its own until a commit is selected.
- **Standard** (`DEFAULT_LAYOUT`, `SIDEBAR_LEFT`, sizes (220, 880)): the Sidebar holds
  Branches, Worktrees, Stashes, Unstaged Files, Staged Files, Commit and Commit Files
  stacked: `Split(V, [branches, worktrees, stash, files, staged, commit, commit_files],
  sizes=(180, 140, 170, 220, 120, 160, 0))`. The repository's lists are at the top and
  staging and committing at the bottom. Commit Files' size is 0 in every preset: it
  has no space of its own (§6.2, swaps).
- **Sidebar Tabs** (`SIDEBAR_LEFT`): the Sidebar holds Branches, Worktrees, Unstaged Files
  and Stashes as tabs, with Staged Files, Commit and Commit Files below the tabs.
- **Stacked** (`MAIN_TOP`, sizes (370, 380)): the Main pane across the top; the Sidebar
  below holds Branches with Worktrees and Stashes below it, beside Unstaged Files, with
  Staged Files, Commit and Commit Files below it.

`flatten(state)` (`layout_model.py`) is a state as one tree, the way versions 1 and 2 saved it:
`Split(V, [controls, Split(H | V, [sidebar, main] in the arrangement's order)])`, without
the empty areas. The host builds that same shape (§6.2), so the presets look as they did
before areas.

### 6.2 LayoutHost

`LayoutHost(QWidget)` (`layout/layout_host.py`) turns a `LayoutState` into widgets:
- `apply(state)` builds the areas, and in each its tree of `QSplitter`s and `QTabWidget`s.
  It **reuses existing panel instances** by id: they're re-parented, so lists, scroll
  positions and selection survive. It creates missing panels through the registry and
  deletes panels no longer in the layout.
- **The areas:** a vertical `QSplitter` holds the Controls area above a splitter of the
  Sidebar and the Main pane, side by side or stacked by the arrangement, at the state's
  `sizes` (an even split when it has none). Each area is an `AreaPane` on its area's
  surface, holding its tree's widget, or nothing when it is empty. An empty Controls area
  is left out (it can only be empty in a layout built by hand); an empty Sidebar or Main
  pane keeps its space, shows its surface only, and never shrinks below
  `EMPTY_AREA_MIN` (48 px), so it stays a drop target.
- `state() -> LayoutState` reads splitter sizes, the current tabs and the split between
  the areas back. `area_of(panel_id)` is the area a shown panel is in.
- It also holds the **hidden panels** and their anchors (§6.6): `hide_panel(id)`,
  `show_panel(id)`, and `layout_changed` after every change, which the menus follow.
- In a split, each panel is wrapped in a `PanelFrame` (`layout/panel_frame.py`): a section
  header plus the panel. The host sets the area's `surface` as a style property on the
  frame and on the panel, in splits and in tabs (`set_surface` repolishes the panel when it
  changes, §7.2). The header
  has its own background (`header_bg`), not the surface's, so it reads as chrome like a
  tab bar; `LIGHT_THEME` gives it the tab bar's colour. In tabs, the
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
  Branches, and Commit Details takes its height from the History and Diff tabs above it. (If that pane can't spare it, Qt shares it out as usual.) `state()`
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
    has no size of its own (it collapses at 0), and `state()` reads a swap as if
    swapped back, so a layout saved while a commit is selected is the working tree's.
  - Building a split applies the same rule: shown panes of size 0 next to collapsed panes
    with sizes take their space (a layout applied while a commit is selected); otherwise
    collapsed panes lend theirs from the start, as above.
- **Drag and drop** moves a panel. A drag starts from a panel's section header (in a
  split) or its tab (in a tab group) once the mouse moves `startDragDistance`, and
  carries the panel id under its own MIME type. The host accepts the drop: the pane
  under the cursor (a frame, or a whole tab group, where the tab bar means `CENTER`) and
  `drop_zone` give the target, and `move_panel` the new tree, which `apply` shows (panels
  keep their state). A drop stays in the area of the pane under the cursor: an edge zone
  puts the panel beside it, in that area. **An empty Sidebar or Main pane** is a target as
  a whole: the panel fills it (`move_to_area`), and the indicator covers the area. The
  Controls area is never a target. A `DropIndicator` overlay (`drop_indicator.py`), transparent to the mouse, fills the half
  of the pane the panel will take (all of it for tabs) with the theme's `accent` at low
  opacity and an accent border; it reads `ctx.theme.current` when painting, never a
  stylesheet. Panels with `movable = False` (Controls) can't be dragged and aren't
  targets, and a drop that changes nothing isn't a target either (no indicator). Escape
  or a drop elsewhere cancels (Qt's `QDrag`). The drop is applied after `QDrag.exec`
  returns, never inside it, because `apply` deletes the header or tab bar the drag
  started from.
- **Reordering tabs** (RZG-79): tab bars are movable (`setMovable`), so dragging a tab
  along its bar moves it, live. Only when the pointer leaves the bar (its rect grown by
  `startDragDistance`) does the panel drag above start: the tab first goes back to where
  the press found it (a synthetic release ends Qt's move), so one gesture either
  reorders or moves the panel, never both, and Escape leaves everything as it was. Every
  tab move (drag, menu or shortcut) emits `layout_changed`, and `state()` reads the order
  from the tab widget. The tab's right-click menu ends with **Move Left** and **Move
  Right** (after a separator when the panel has header actions), disabled when no shown
  tab is on that side; collapsed (hidden) tabs are skipped. **Ctrl+Shift+PgUp** and
  **Ctrl+Shift+PgDn** move the current tab of the tab group that has focus
  (`WidgetWithChildrenShortcut`). These actions belong to each tab group, not to
  `AppActions` (§8): they act on one tab of one group, like the header menu.
- **The header menu** (§5.1): in a split, `PanelFrame` puts a **⋯** `QToolButton`
  (`Role.HEADER_MENU`: on the header's background, with the theme's hover) at the right
  of the section header, inside it, so a press on it never starts a drag. It pops up a
  menu of the panel's `header_actions`, follows `header_actions_changed`, and is made
  the first time the panel has actions (most have none), hidden while it has none again. In a tab group, right-clicking a panel's tab shows a menu of
  the same actions (`menu_for(index)`), then Move Left and Move Right (above).
- A layout with every area empty (everything dropped by `normalize`) shows the empty
  areas. Falling back to `DEFAULT_LAYOUT` is the loader's job (§6.3).

### 6.3 Persistence

`AppSettings` (`core/app_settings.py`) is the only code that touches `QSettings`. It has
typed accessors (`last_repo`, `layout`, `layout_preset`, `window_geometry`,
`side_by_side_diff`, `saved_layouts`, `column_prefs`, …) and takes a file path for tests.
`app.py` creates it and `AppWindows` passes it to each `MainWindow`; panels don't get it.
One `AppSettings` is shared by every window. What panels save goes through a small object
in `ctx` that holds only that: the commit draft through `CommitDrafts` (§5.4), column
choices through `ColumnPrefs`, sort orders through `SortPrefs`.

**Where they are stored** (RZG-59): `app.py` names the app `RZGit` (organisation and
application), so `QSettings` is `~/.config/RZGit/RZGit.conf` and the app's data folder
(`AppDataLocation`) is `~/.local/share/RZGit/RZGit/`. The desktop file name is `rzgit`.
The app used to be called RZGitClient. Before `AppSettings` is created,
`migrate_old_name()` (`core/old_name_migration.py`) copies what that name left behind:
`~/.config/RZGitClient/RZGitClient.conf` and the `commit-drafts/` folder of
`~/.local/share/RZGitClient/RZGitClient/`. Each one is copied only when the new one doesn't
exist yet, so this happens once. The old files are never changed or deleted, and a failed
copy is logged and the app starts without it. The function works on file paths. It
doesn't open `QSettings`, which stays `AppSettings`' alone.

**What goes where** (RZG-47): `AppSettings` holds options, settings and general app state:
preferences, layout and theme customisation (column choices among them), `last_repo`,
window geometry. State that a feature explicitly needs to preserve as its own result goes
in files of its own, owned by that feature: the commit drafts are kept by `CommitDrafts`
(§5.4), one file per repository.

**Column choices** (`ColumnPrefs`, `core/column_prefs.py`, RZG-41, RZG-40) are
app-wide, the same in every layout and repository, keyed by panel id and then by column
label: `column_prefs` is JSON, `{"stash": {"hidden": ["Branch"], "widths": {"Age":
60}}}`: the hidden columns and the widths the user dragged. A panel with columns hidden
at first (§5.2) keeps `"hidden": []` once the user shows them all. Malformed entries are
dropped (logged). Each change is written at once.

**Sort orders** (`SortPrefs`, `core/sort_prefs.py`, RZG-43) are app-wide too, per panel
id: `sort_prefs` is JSON, `{"stash": {"by": "branch", "reversed": true}}`. A panel on its
default order, not reversed, has no entry. Malformed entries are dropped (logged), and an
unknown key reads as the default. Each change is written at once. A panel reads
its order when it is built; a change applies to that panel only, so other open windows
keep theirs until they are built again (the last change wins). The grouping (RZG-53) is
in the same entry: `{"stash": {"by": "age", "reversed": false, "group": "branch"}}`; no
`"group"` is no grouping, and an unknown one reads as none.

**Stored folder paths** (RZG-29) are canonical: `canonical_path(path)` (`git/repo.py`)
expands `~` and resolves symlinks. `Repo.root` is git's `--show-toplevel` made canonical,
and `last_repo` and the draft keys go through the same function, so every caller agrees on
one path per folder. There is no case-folding: git's top level has the case stored on
disk (macOS, Windows), and folding would be wrong on a case-sensitive volume. git's text
output is read as UTF-8 on every platform, so non-ASCII folder names work.

**Worktrees** (RZG-47): `Repo(path)` reads the top level, git dir and common dir in its one
`rev-parse`, so these cost no extra git process. In a linked worktree the git dir
(`.git/worktrees/<name>`) differs from the common dir. `Repo.worktree_name` is then
`<name>`, git's name for it, and `Repo.main_root` is the main worktree's root (the
canonical parent of the common dir, when that is a `.git` folder). Otherwise
`worktree_name` is the root's folder name and `main_root` is `root`.

**The last repository** (`last_repo`) is always a **main repository**: the main
worktree's root (`Repo.main_root`), never a linked worktree, so a restart opens the
repository, and the Worktrees panel is the way back to a worktree. A window sets it
when it opens a repository and when it becomes the active window, so with several
windows it's the repository of the one focused last.

**Recent repositories** (`RecentRepos`, `core/recent_repos.py`, RZG-87) is the one record
of what was opened when: File → Recent Repositories (§8) lists from it, and the Worktrees
panel's recently-used sort (RZG-50) reads it. One for the app, made by app.py from
`AppSettings` (in memory without it, for tests) and given to every window as
`PanelContext.recent`.

```python
class RecentRepos(QObject):
    changed = Signal()
    LIMIT = 10
    def touch(self, repo: Repo) -> None             # opened now: its worktree root and main root
    def repositories(self) -> tuple[Path, ...]      # main roots, newest first, at most LIMIT
    def last_opened(self, root) -> float | None     # a worktree root's time (epoch seconds)
    def remove(self, main_root) -> None             # a repository and its worktrees
    def clear(self) -> None
```

- An entry is a worktree root (canonical), its main root and when it was opened. The
  list is newest first; touching an entry moves it to the front. A main repository's
  time is its newest worktree's, so opening a linked worktree moves its main repository
  to the top.
- Only the worktrees of the `LIMIT` newest main repositories are kept.
- `recent_repos` is JSON, `[{"root": …, "main": …, "opened": 1763400000.0}]`, newest
  first. Malformed entries are dropped (logged). Each change is written at once and
  emits `changed`.
- `MainWindow` touches it on every `repo_changed`, so every route counts: Open, Open in
  New Window, a worktree window, a recent entry, the last repository on start, the
  command line. Becoming the active window does too (RZG-50), so the times are when
  each was last used.
- A folder that no longer exists is kept (its drive may be back); the menu shows it as
  missing (§8).

It is reopened at startup by `MainWindow.open_last_repo()`, quietly:
if it was moved, deleted or is no longer a repository, the window starts with none, the
status bar says "Last repository not found: <path>", and the setting is kept (the drive
may be back next time). `RepoSession.open(path, report=False)` is the quiet open. A path
given on the command line is opened as usual and shows its error.

On close, the window saves the host's `state()` (as JSON, so the format is ours) and its
geometry (with several windows, each saves as it closes, so the last one closed wins): size and maximized state, plus position where the platform allows it (not on
Wayland). With the layout it saves `layout_preset`: the title of the preset the layout
is, if it is one (`matching_preset(state)`: the preset whose arrangement is the layout's,
and whose areas, without sizes or current tabs and with the saved hidden panels taken
out, have the layout's shapes), else None. Tab order is part of the shape (RZG-79): a
preset with reordered tabs is a custom layout, so `restore_layout` keeps its order instead
of reapplying the preset. Reset Layout and View → Layout bring back a preset's order.

**Built-in layouts follow the code; custom ones stay.** On start,
`restore_layout(saved, preset)` in `layout_presets.py` (plain Python, no Qt) returns the
`LayoutState` to apply. When `preset` names a current preset whose shape (hidden panels
taken out) is no longer the saved layout's, the preset changed in code since: that
preset is applied instead, with the same panels hidden (anchored in the new tree) and the
preset's sizes. Otherwise, and for a custom layout (`preset` None: rearranged, or a saved
named layout), the saved layout is used:
- nothing saved, malformed data, or `normalized` leaves every area empty →
  `DEFAULT_LAYOUT`, with a logged warning for malformed data (no dialog);
- a panel the user hid stays hidden (anchors of panels this version no longer has are
  dropped);
- a panel that is neither shown nor hidden is new in this version: it's inserted at its
  place in `DEFAULT_LAYOUT` (§6.6), in its default area, and the rest of the user's
  layout is kept. When it
  joins a tab group, the tab the user had current stays current (a Diff tab added beside
  History doesn't hide History on the first start).
  - "Its place" is taken among the panels on screen (`default_anchor(id, shown)`), so two
    panels new in one version land together: Staged Files below Files, then Commit below
    Staged Files. (`show_panel` uses the same rule for a panel never hidden.)
  - A split that gained new panels, and now holds the same panes as a split of a preset,
    takes that split's sizes; otherwise each new pane takes its share of its neighbour's
    space, and a second new pane would take a share of the first's.

**Migrating to areas** (version 3, RZG-69). A layout saved as one tree (versions 1 and 2:
the window's layout and saved layouts alike) is migrated as it loads
(`migrate_tree(root, hidden, default_area)` in `layout_model.py`, given `default_area`
by `restore_layout`), before the steps above:
- **Each panel goes to its default area**, and each area's tree is the old tree with the
  other areas' panels taken out (`remove_panel`), so it keeps its structure and sizes.
  Controls goes to Controls; History, Diff and Commit Details to the Main pane; the rest
  to the Sidebar.
- **The arrangement** comes from the smallest split holding both Main pane and Sidebar
  panels: side by side with the Main pane's panels after the Sidebar's → `SIDEBAR_LEFT`,
  before → `SIDEBAR_RIGHT`; stacked → `MAIN_TOP`. With no such split (an area left
  empty, or the two mixed in one tab group), `SIDEBAR_LEFT`.
- **The split between the areas** keeps that split's sizes when it has two children, one
  holding each area's panels (every preset); otherwise the default (None).
- So a layout shaped like a preset becomes that preset in areas, with the same sizes and
  the same panels hidden, and `layout_preset` still names it.
- **A hidden panel's anchor** is kept, in the panel's default area, when one of its
  neighbours is in that area; otherwise it's dropped, and showing the panel uses its
  default place.
- The migrated layout is written as version 3 the next time it's saved.

**Windows open on History** (RZG-55). As a window is built, after `restore_layout`,
`MainWindow._build_ui` applies one rule: if History is on the Main pane
(`area_of("history") is Area.MAIN`), its tab group opens on History
(`with_current_tab("history")`), whatever tab was current when the layout was saved.
So a window closed with Diff in front doesn't open on an empty Diff.
- It holds for every window as it's built: the first at startup and every one
  `AppWindows.new_window` spawns (New Window, Open Repository in New Window, a worktree
  opened in a new window). A window that already exists keeps its tabs: opening a
  repository in it, or raising it because it already shows that repository.
- Only History's group, and only on the Main pane. History in the Sidebar, alone, or
  hidden: the saved tabs stand. Other tab groups always keep their saved current tab.
- The current tab is still saved as it is, and applying a layout to an open window
  (View → Layout, Reset Layout) shows it as saved.

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

Edits are pure functions in `layout_model.py`, tested without Qt. `LayoutState.hide(id)`
and `LayoutState.show(id, fallback)` work on a whole state: hiding takes the panel out of
its area's tree and records the area in its anchor (`Anchor.area`); showing puts it back
into that area (the fallback's area, the default area, when the anchor has none). Both
leave every area in place, so the area of the last panel hidden stays, empty. Within an
area's tree:
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
  `DEFAULT_LAYOUT`), and failing that the right-hand edge. An empty area's tree becomes
  the panel alone. A panel added to tabs becomes
  the current tab, unless `make_current` is False (migration, §6.3).

**Panels that are always shown** (`always_shown`, the Controls panel): `LayoutHost.apply`
inserts one at its `DEFAULT_LAYOUT` place, in its default area, whenever a layout lacks it, so no preset, saved
layout or edit can lose it; `hide_panel` refuses it, and its View → Panels entry stays
checked but disabled. "The last visible panel" counts only panels that can be hidden.

**Fixed-height panes** (`fixed_height`): in a vertical split, the host fixes the pane to the
height the panel asks for, and disables the dividers beside it (no resize cursor). Used
by the Controls panel, so the bar can't be dragged taller. The Controls area is fixed the
same way, to the height of its tree, when that tree holds only fixed-height panels.

**Why anchors, not "the default place":** a panel hidden from a rearranged layout comes
back where the user had it. The default place is only the fallback.

## 7. AppTheme

### 7.1 Theme model

`ui/theme/app_theme.py`:

```python
@dataclass(frozen=True)
class ThemeColors:   menubar_bg: str; toolbar_bg: str; tab_bg: str; header_bg: str; sidebar_bg: str; content_bg: str; border: str; tree_guide: str;
                     column_header_bg, column_header_text, column_header_text_hover,
                     column_header_separator, column_header_border: str   # ColumnHeader (§5.2)
                     group_header_bg, group_header_text: str   # a group heading in a list (§5.1, RZG-53)
                     row_alt_bg: str   # every second row of a striped list (History, RZG-63); ΔE ≥ 3.5 from content_bg and ≥ 5 from content_selection (RZG-66)
                     content_hover: str   # hover in content lists: ΔE ≥ 3 from content_bg, row_alt_bg and content_selection (RZG-66)
                     floating_button_bg, floating_button_hover, floating_button_text,
                     floating_button_border, floating_button_shadow: str   # a control floating over a list (History's Back to top, RZG-64)
                     text: str; text_muted: str; hover: str; selection: str; content_selection: str;
                     accent: str; button_*: str; scroll_*: str; diff_*: str (incl. diff_hunk_bg, diff_hunk_text, diff_line_button*, diff_*_selected_bg, diff_*_word); …
                     diff_find_match: str   # every Find match in the diff (RZG-74): text ≥ 4.5:1 on it; ΔE ≥ 6 from the diff backgrounds, content_bg and text_selection, under every simulated vision
                     diff_find_current: str   # the current Find match's outline: ≥ 3:1 on diff_find_match, text_selection and every diff background
                     upstream_behind, upstream_ahead: str   # Pull's ↓n and Push's ↑n on the Controls panel (RZG-94)
@dataclass(frozen=True)
class StatusColors:  modified, added, deleted, renamed, copied, type_changed, conflict, untracked: str
                     def for_code(self, code: str) -> str | None   # porcelain letter → colour; None if unknown
@dataclass(frozen=True)
class GraphColors:   lanes: tuple[str, ...]   # History's branch graph (RZG-58): 8 lane colours, used in turn
                     tag_fill, tag_border, stash_fill, stash_border: str   # History's ref labels (RZG-77)
                     label_fill_alpha: int    # a branch label's tint of its lane colour, 0–255
@dataclass(frozen=True)
class ThemeFonts:    default_point_size: float = 10; mono_family: str = "monospace"
                     ui_point_size, menu_point_size, status_bar_point_size, list_point_size,
                     column_header_point_size, tab_point_size, tooltip_point_size,
                     section_header_point_size, segment_header_point_size,
                     mono_point_size: float | None = None
                     def point_size(self, kind: FontKind) -> float   # its own size, else the default
@dataclass(frozen=True)
class ThemeMetrics:  splitter_width: int = 1; scrollbar_width: int = 10; row_padding: int = 2; row_min_height: int = 18
                     graph_lane_width: int = 14; graph_line_width: float = 1.6; graph_dot_radius: float = 3.5
                     ref_label_max_width: int = 160   # ref labels (RZG-77)
                     ref_label_padding: int = 5; ref_label_gap: int = 3; ref_label_radius: float = 3

@dataclass(frozen=True)
class AppTheme:
    name: str; colors: ThemeColors; status: StatusColors; graph: GraphColors; fonts: ThemeFonts; metrics: ThemeMetrics
    dark: bool = False   # the colour scheme Qt is told (§7.3)
    def stylesheet(self) -> str     # generated from the tokens by theme_stylesheet.py
    def palette(self) -> QPalette
    def mono_font(self) -> QFont
    def status_color(self, code: str) -> str   # status.for_code(code), falling back to colors.text

LIGHT_THEME = AppTheme(...)
DARK_THEME = AppTheme(..., dark=True)
LIGHT_CB_THEME = dataclasses.replace(LIGHT_THEME, ...)    # colour-blind variants
DARK_CB_THEME = dataclasses.replace(DARK_THEME, ...)
def colour_blind_theme(base: AppTheme) -> AppTheme   # LIGHT_THEME → LIGHT_CB_THEME, …
```

- **Font sizes:** each kind of text (`FontKind`: `UI` for buttons, labels, fields and text
  boxes; `MENU`, `STATUS_BAR`, `LIST` for item views, `COLUMN_HEADER`, `TAB`, `TOOLTIP`,
  `SECTION_HEADER`, `SEGMENT_HEADER` and `MONO` for the diff and SHAs) has its own size field. `None` means
  `default_point_size` (10 pt); a theme sets a field only to override that kind. No theme
  overrides any yet, so the whole app is 10 pt (RZG-39). The family is the desktop's,
  except `MONO` (`mono_family`). The stylesheet gets the resolved sizes
  (`$section_header_point_size`), and `mono_font()` uses `MONO`'s.
- **Two base themes:** `LIGHT_THEME` and `DARK_THEME`. Both set every token; neither derives
  from the other. `dark` tells Qt which colour scheme the theme is, so Fusion's own
  drawing (frames, arrows) and native dialogs match it.
- **Column labels** (RZG-41): `column_header_bg` is a step away from both list
  backgrounds (sidebar and content) and from `header_bg`, so the labels read as their own
  row of chrome under a section header. Their text is `text` (at least 7:1 on it), the
  hovered label a step stronger, and the line under the row `border`. The separators are
  at least 1.5:1 on the label background, so a 1 px line shows: `border` in the light
  theme, a step lighter in the dark one.
- **Group headings** (RZG-53): `group_header_bg` is at least ΔE 3 from both list
  backgrounds (sidebar and content), so a heading reads as a row of its own; its text is at
  least 7:1 on it.
- **Graph lanes** (RZG-58): `graph.lanes` are 8 colours a lane takes in turn, so
  neighbouring lanes differ. Each is at least 3:1 on the content background (a line
  shows), and tests require a minimum ΔE between neighbours, under every simulated kind
  of colour vision in the colour-blind variants. The stylesheet doesn't use them.
- **Ref labels** (RZG-77): History's label text is `text`, at least 4.5:1 on every pill
  fill (a lane tint at `label_fill_alpha`, `tag_fill`, `stash_fill`) over both row
  backgrounds (a pill is filled with its row's background first, so it is opaque over the
  graph, RZG-86). A pill's border is at least 1.5:1 on `content_bg`, so it shows. Tags and
  stashes are neutral greys, so they don't read as a lane; a stash's border is dashed, so
  the two differ by shape, not colour. The colour-blind variants share them.
- **Floating button** (RZG-64): `floating_button_bg` is at least ΔE 3 from
  `content_bg`, `row_alt_bg` and `content_hover`, so the pill reads as a control over
  the rows, not as a row. Its text is at least 4.5:1 on both `floating_button_bg` and
  `floating_button_hover`. The shadow is drawn translucent, so it can be a plain dark
  colour.
- **Upstream counts** (RZG-94): `upstream_behind` colours Pull's `↓n` (incoming) and
  `upstream_ahead` Push's `↑n` (outgoing). They aren't the diff's or the statuses' green
  and red, which read as added and deleted: behind is a blue, ahead a violet. Each is at
  least 4.5:1 on the bar (`toolbar_bg`) and on a button's `button_hover` and
  `button_pressed`, and at least ΔE 12 from `text`, so a count stands out from its label.
  A disabled button's count is `text_muted`, like its label.
- **Colour-blind variants** (RZG-33): `LIGHT_CB_THEME` and `DARK_CB_THEME` derive from
  their base with `dataclasses.replace`, so they share every token except those that tell
  kinds apart: `status.*`, `graph.lanes`, `diff_added*` / `diff_removed*`, `diff_line_button*`,
  `upstream_ahead` (orange, beside behind's blue) and `warning`. Those use one palette built on blue and orange instead of green and red
  (Okabe–Ito): added is blue, deleted orange. Tests simulate deuteranopia, protanopia and
  tritanopia (Machado et al. 2009) and require a minimum ΔE between the pairs that must
  differ, and at least the base theme's contrast. Their `name` is the base's plus
  ", colour-blind".
- **Colours live only here.** No module outside `ui/theme/` writes a colour (a `"#rrggbb"`
  string or a Qt named colour); `tests/test_architecture.py` checks it. Panels take theirs
  from the theme's tokens, so a new theme needs no panel changes.
- **The palette** is built from the tokens. A dark theme also sets the bevel roles
  (`Light`, `Midlight`, `Mid`, `Dark`, `Shadow`) from its button colour, since Qt's
  defaults for them are a light theme's.

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

`style_roles.py` defines `Surface` (SIDEBAR, CONTENT, CHROME: one per layout area, which
sets it, §6.1) and `Role` (SECTION_HEADER,
SEGMENT_HEADER for the header strip of a segment inside a panel (§5.2),
PLACEHOLDER for a muted "nothing to show" message, DIFF_VIEW for the borderless diff text,
HINT and HINT_WARNING for a small muted note and the same past a limit, HEADER_MENU for
the ⋯ button of a section header's menu, §6.2, SETTINGS_PAGE for the Settings window's
outlined page, §8.2, UPSTREAM_BEHIND and UPSTREAM_AHEAD for the count on Pull and Push,
§5.4).
Widgets get them as Qt dynamic properties through `set_surface()` / `set_role()`, and the
stylesheet targets those (`QWidget[surface="sidebar"]`). The theme decides what "sidebar"
looks like; panels only say what they are.

A surface or role is added when a widget first needs it (`Surface.CHROME` came with the
Controls panel: the toolbar's background), not ahead of time. Panels don't set their own
surface: the layout sets the surface of the area a panel is in (§6.1, RZG-69), on its
frame and on the panel. A test checks that every
value is used by the stylesheet, so a role can't be defined and forgotten.

Roles style **widgets**. A widget that paints itself (`ColumnHeader`, the diff gutter)
takes its colours from the theme's tokens instead, given by its panel. Things inside a list (a muted author column, a monospace hash) are
per-row data, which Qt's stylesheet can't target, so panels set them from the theme's tokens
(`theme.colors.text_muted`, `theme.mono_font()`).

### 7.3 ThemeManager

`ThemeManager(QObject)` (`theme_manager.py`) holds `current` and is the **only** caller of
`QApplication.setStyle`, `setPalette`, `setFont`, `setStyleSheet` and
`QStyleHints.setColorScheme`. It
applies its first theme when it's created. `set_theme(t)` applies a new theme and emits
`theme_changed(t)`, but does nothing if `t` equals the current theme. The stylesheet
repaints itself; panels refresh any colours they set in code in `apply_theme`.

**Theme mode:** `ThemeMode` is `LIGHT`, `DARK` or `SYSTEM` (a `StrEnum`, so its value is
what settings store). `set_mode(m)` picks the theme for it; `mode` is the current one.
- `LIGHT` and `DARK` set Qt's colour scheme to the theme's (`setColorScheme`).
- `SYSTEM` clears that (`unsetColorScheme`), takes the desktop's scheme, and follows
  `QStyleHints.colorSchemeChanged` while it stays the mode: dark gives `DARK_THEME`,
  anything else `LIGHT_THEME`.
- `ThemeManager(app, mode=m)` starts in a mode, so the first theme applied is already the
  right one: the window never shows light first.

**Colour-blind colours:** `colour_blind` is a flag next to the mode, in every mode. The
mode picks the base theme and the flag picks its variant (`colour_blind_theme(base)`, §7.1).
`set_colour_blind(on)` switches live, and `ThemeManager(app, mode=m, colour_blind=b)` starts
with it.

**Fonts:** the desktop gives some widget types their own font (Qt's per-class fonts: item
views, menus, the status bar, labels, buttons, …). Qt fills those in lazily, the first time
one is asked for, and drops them whenever the application font is set again, so before
RZG-39 the lists were the desktop's size at startup and the theme's after any switch.
`ThemeManager` therefore sets the application font (`UI`) and then an explicit font for
every per-class key Qt's platform themes use, each at its kind's size, on every apply,
after the stylesheet: setting a stylesheet makes Qt fill the desktop's per-class fonts in
again (that left the Controls panel's tool buttons at the desktop's 9 pt).
If the desktop's fonts change later (Qt sends the application an `ApplicationFontChange`
or `ThemeChange`), it applies them again when they no longer match. Widgets that set their
own font (the diff view, SHAs) take theirs from the theme in `apply_theme`.

**Performance rule:** one stylesheet for the whole app, never per-widget `setStyleSheet`.
Per-widget sheets are slow to apply and scatter the rules.

## 8. AppActions and MainWindow

- **`AppActions`** (`ui/app_actions.py`) defines every `QAction` once: text, icon, shortcut
  and enabled state (e.g. Refresh is disabled with no repo open, driven by `repo_changed`).
  **Refresh** (F5, File menu and Controls panel) stays as a fallback: the window follows
  the repository by itself (§4.3).
  The menu bar, context menus and the Controls panel all show the same objects.
  **Side by Side Diff** is checkable: the Diff panel follows its `toggled`. MainWindow sets
  it from `AppSettings.side_by_side_diff` before the panels are built, and saves it on close
  with the layout, so panels never touch settings. The Diff panel may show a whole added or
  deleted file unified while it is on (§5.4); that never unchecks or changes it. **Show Files as Tree** works the same
  way (`AppSettings.file_tree`, on by default): every `FileList` follows its `toggled`.
  **Show Whole File** (`AppSettings.whole_file_diff`, off by default) is a third such
  toggle, and **Hunk Headers** (`AppActions.hunk_headers`, by `HunkHeaders`: Full, Slim,
  Off) is an exclusive group like Theme, saved as `AppSettings.hunk_headers` (`"slim"` by
  default); neither has a shortcut. All are in the **View** menu.
  **Topological Order** (RZG-58, `AppSettings.topo_order`, off by default) too: AppActions
  sets the session's `history_order` (§4.1) on `toggled`. It is also in History's header
  menu.
- **Theme** (View → Theme): **Light**, **Dark** and **System**, three checkable actions in
  one exclusive group (`AppActions.theme_modes`, by `ThemeMode`). MainWindow checks the
  current mode, calls `ctx.theme.set_mode` when one is triggered, and saves it on close
  (`AppSettings.theme_mode`, `"system"` by default). app.py reads it before the window is
  built. Switching is live.
  Below them, **Colour-blind Friendly Colours** (`AppActions.colour_blind`) is checkable:
  MainWindow checks it from `ctx.theme.colour_blind`, calls `set_colour_blind` when it's
  toggled, and saves it on close (`AppSettings.colour_blind`, off by default), read by
  app.py with the mode.
- **Staging actions:** Stage (Ctrl+S) and Unstage (Ctrl+U) act on the selected rows of
  that kind (`selection.files`), Discard Changes… (Ctrl+Backspace) on the selected unstaged
  rows, Stage All (Ctrl+Shift+S) and Unstage All (Ctrl+Shift+U) on the whole status. Each is
  enabled only when it has something to act on. They're in the **Repository** menu and the
  file panels' context menus. Stage and Unstage call `selection.follow()` first, so the
  moved rows are selected in the other panel (§4.2). Discard is connected by MainWindow, which asks for
  confirmation first (listing the files, and that untracked ones go to the trash).
- **Open with VS Code** (RZG-96) opens files in VS Code, in the window of this worktree:
  - **The entry:** context menus make it with `actions.open_in_vscode(paths, parent,
    line=None)`, a new `QAction` per menu, as its files are the menu's. It is enabled
    when `paths` isn't empty and the check MainWindow's `VSCodeLauncher` set
    (`set_vscode_check`) finds no problem. Disabled, its tooltip says why: "Select a file
    to open, not a folder.", "No VS Code command found. Set it in Settings (Tools).", or
    "Not in the working tree." (none of the files is there: deleted, or only in a
    commit). No shortcut. Triggered, it sends `open_in_vscode_requested(paths, line)`.
  - **The window:** `code <file>` alone opens the file in a window whose folder holds it,
    else the last active one: a lane's worktree is outside the main checkout, so its files
    would land in the main checkout's window. So the command names the worktree's own
    target first, which VS Code reuses if a window has it open, or opens a new window
    for: `code <target> <file>…`, or `code <target> --goto <file>:<line>` from the Diff
    panel. `VSCodeTarget(root, workspace_files)` (`core/vscode_target.py`) picks the
    target: when the repository tracks exactly one `*.code-workspace` file
    (`session.workspace_files`, §4.1) and this worktree has it, that file (its folder
    paths are relative, so each worktree's copy opens that worktree); otherwise the
    worktree's folder. A worktree open as a folder while the workspace file is passed
    gets a second window: VS Code can't be asked what it has open (`docs/USAGE.md` says so).
  - **The files:** `present(paths)` keeps those that are files in the working tree, in
    one `code` call, so they open in the same window; the rest are skipped. Paths are
    absolute, so none starts with `-`.
  - **`VSCodeLauncher`** (`ui/vscode_launcher.py`, made by MainWindow) holds the command
    setting (§8.2), sets the check, and starts the command with `QProcess.startDetached`
    in the worktree's folder, as Open Terminal does. If it can't be started, a warning
    says so. It runs no git.
- **Discard lines:** the Diff panel calls `actions.request_discard_lines(diff, lines)`, which
  sends `discard_lines_requested`. MainWindow asks first ("Discard 3 changed lines of
  path?", can't be undone) and then calls `session.discard_lines`.
- **Commit** (Ctrl+Enter, in the Repository menu) commits the staged changes with the
  Commit panel's message. It is enabled when a repository is open, something is staged
  (or Amend Last Commit is on) and the panel reports its message ready (`set_commit_ready`); the panel connects
  `triggered`, since it holds the message. A panel that is hidden (deleted) reports not
  ready.
- **Branch operations** (RZG-35): the Branches panel calls `actions.request_branch(op,
  name)`, with a `BranchOp` (`SWITCH`, `NEW`, `RENAME`, `DELETE`, `MERGE`, `REBASE`),
  which sends `branch_requested`. **`BranchDialogs`** (`ui/branch_dialogs.py`, made by
  MainWindow to keep it thin) asks, then calls the session. Each box's buttons name the
  operation, and Cancel is the default for those that lose work or rewrite history.
  - **Switch:** "Switch to *X*?". With changes in the status (staged, unstaged or
    untracked), it says so and offers **Stash and Switch** (the default), **Bring
    Changes** (git carries them over, or refuses) and **Cancel**, and names the stash. A
    stash isn't reapplied: it is in the Stashes panel, to apply or pop.
  - **New Branch…:** a name box, "New branch from *X*", with **Switch to it** (checked).
  - **Rename…:** a name box filled with the old name. Unchanged or empty does nothing.
  - **Delete…:** "Delete branch *X*?" (Cancel default). If *X* isn't merged into HEAD
    (`Branch.merged`), a second box says its commits that aren't in *current* will be
    lost and asks **Delete Anyway**, which forces it.
  - **Merge:** "Merge *X* into *current*?", saying a conflict aborts it and changes
    nothing. **Rebase:** "Rebase *current* onto *X*?", a warning that it rewrites the
    commits of *current* not in *X* (Cancel default), and that a conflict aborts it.
- **Stash operations** (RZG-36): the Stashes panel calls `actions.request_stash(op,
  stash)`, with a `StashOp` (`APPLY`, `POP`, `DROP`), which sends `stash_requested`.
  **`StashDialogs`** (`ui/stash_dialogs.py`, made by MainWindow) runs Apply and Pop
  straight away (they need a clean tree and roll back on a conflict, so nothing can be
  lost), and asks before Drop: "Drop stash *message*?", saying it can't be undone, with
  Cancel the default.
  - **Stash Changes…** (`actions.stash_changes`, in the Repository menu after Amend, and
    on the Stashes panel) is enabled when a repository is open and the status isn't
    empty. StashDialogs asks for a message (optional) and **Include untracked files**
    (checked), then calls `session.stash_changes`.
  - **Notices** (`notice_occurred`, §4.1) are shown by MainWindow in an information box.
- **Amend Last Commit** (in the Repository menu, after Commit; RZG-27) is checkable. It
  is enabled when the repository has commits (`session.history`), and unchecks itself when
  it is disabled or HEAD moves to another commit (the first commit of `history` isn't the
  one it was checked on): another repository, a commit or checkout anywhere, or the amend
  itself succeeding. A failed amend leaves HEAD, so it stays on. While it's checked, Commit
  is enabled with nothing staged. `actions.confirm_amend(last)` asks MainWindow's
  confirmer (`set_amend_confirmer`; True without one): for a pushed commit, "Amend a
  pushed commit?", saying the remote keeps the old one, with Cancel the default.
- **Show Worktrees** (Ctrl+Shift+W, `actions.worktrees`, at the end of the Repository
  menu; RZG-48) is enabled when a repository is open. MainWindow shows the Worktrees
  panel if it is hidden (`layout_host.show_panel`, §6.6) and brings its tab to the front
  (`request_activation`). While the repository has only one worktree the panel stays
  collapsed (§5.4).
- **Opening a worktree:** the Worktrees panel calls `actions.request_open_worktree(path)`,
  which sends `open_worktree_requested`. MainWindow opens it with `open_in_window` (§8.1):
  the window already showing it is raised, or a new one opens.
  `actions.request_open_worktree_here(path)` (**Open in This Window**) sends
  `open_worktree_here_requested`, and MainWindow opens it with `open_repo`: here, unless
  another window shows it, which is raised instead.
- **Worktree operations** (RZG-49): the Worktrees panel and the Branches panel call
  `actions.request_worktree(op, target)`, with a `WorktreeOp` (`NEW`, `REMOVE`, `PRUNE`,
  `OPEN_FOLDER`, `OPEN_TERMINAL`), which sends `worktree_requested`. `target` is a
  worktree's path, or for `NEW` the branch to start from ("" for none). **New
  Worktree…** (`actions.new_worktree`, after Show Worktrees in the Repository menu, and
  in the Worktrees header menu) asks for `NEW` with no branch; it is enabled when a
  repository is open. **`WorktreeDialogs`** (`ui/worktree_dialogs.py`, made by MainWindow)
  asks, then calls the session, and calls `ctx.worktrees.refresh()` when a worktree
  command finishes (§4.4).
  - **New Worktree…** (`NewWorktreeDialog`): **Existing branch**, a list of the local
    branches, where one checked out in a worktree shows as "*X* (in worktree *Y*)" and
    can't be picked; or **New branch**, a name and a start point (an editable list of
    the branches, the current one, or `HEAD` when detached, by default). **New
    Worktree from *X*…** starts on existing branch *X* when it is free, else on a new
    branch from *X*. The **folder** is `<main repository folder>-<branch>` beside the
    main repository (a `/` in the branch becomes `-`), following the branch until it is
    edited, with **Browse…**. **Open it** is checked. **Create** is enabled when the
    branch can be used and the folder doesn't exist or is empty. With Open it, once
    the add command finishes without error (`command_finished`, by identity), the
    worktree opens with `open_in_window` (§8.1).
  - **Remove…:** "Remove worktree *X*?" (Cancel default): its folder is deleted, with
    any ignored files, and its branch is kept; for a missing one, only git's record is
    removed. If the reader's state (§4.4) has changes, a second box says they will be
    lost and asks **Remove Anyway**, which forces it. The main worktree, a locked one and
    this window's own are refused. A window showing the worktree is closed first
    (`AppWindows.window_for`, §8.1).
  - **Prune Missing Worktrees…:** lists the reader's missing worktrees that aren't
    locked ("Prune 2 missing worktrees?"), naming any missing locked ones as kept,
    then calls `session.prune_worktrees()`. Branches are kept.
  - **Open Folder** opens the folder with `QDesktopServices.openUrl`. **Open
    Terminal** starts a terminal there (`QProcess.startDetached`, the folder as its
    working directory): `$TERMINAL` if set and found (split like a shell would), else the first of
    `konsole`, `gnome-terminal`, `xfce4-terminal`, `x-terminal-emulator` on the `PATH`
    (`find_terminal`). If neither works, a box says so. No git runs.
- **The menu bar** is File, Edit, Repository, View (RZG-89). Edit holds only Settings…
  for now: Copy, Select All and Find stay the panels' own shortcuts, since window-level
  actions on the same keys would clash with them. When Edit gets more entries, Settings…
  goes last, after a separator (the KDE and GNOME place for preferences).
- **`MainWindow`** is a thin shell: menus built from `AppActions`, the status
  bar summary (from session signals: path · branch · commits · changed files), error dialogs (from `error_occurred`), and a
  `LayoutHost` as the central widget. The Open, Quit and Reset Layout handlers live here
  because they need dialogs or the window. Target: around 100 lines.
- **Title:** `<worktree name> — RZGit` (`Repo.worktree_name`: git's name for a linked
  worktree, the folder's name for the main one), or `RZGit` with none open.
- **File → Open Repository…** opens the folder in this window, unless another window
  already shows that worktree: then that window is raised and this one is unchanged
  (§8.1). **File → Open Repository in New Window…** (Ctrl+Shift+O, RZG-52) asks for a folder
  with the same dialog and opens it with `open_in_window`: the window that already shows
  that worktree is raised, else a new window opens on it, placed beside this one (§8.1).
  It always opens a new window, even when this one has no repository. Cancelling does
  nothing; a folder that isn't a repository shows the error and opens no window.
  `last_repo` follows as for any window opening a repository. **File → Quit** closes every
  window (`QApplication.closeAllWindows`).
- **File → Recent Repositories** (RZG-87, `RecentReposMenu`, `ui/recent_repos_menu.py`,
  after the two Open actions) lists `ctx.recent.repositories()` (§6.3), rebuilt each time
  it opens (`aboutToShow`), so every window shows the same list. No shortcuts.
  - **An entry** is the folder name (`&` doubled), with the full path as its tooltip.
    Entries with the same folder name each add their parent folder, home as `~`:
    `RZE — ~/dev`. A folder that doesn't exist (checked as the menu opens) reads
    "*RZE* (missing)".
  - **Its submenu:** **Open in Current Window** (`open_repo`) and **Open in New Window**
    (`open_in_window`), each raising the window that already shows the repository, as
    the Open actions do (§8.1), and disabled for a missing folder; then **Remove from
    List**. A folder that is no longer a repository shows the error.
  - After a separator, **Clear Recent Repositories**. An empty list shows only a disabled
    "No Recent Repositories".
- **Commit count text** (`commit_summary`): the exact `commit_count` with separators
  ("2,421 commits", "1 commit"). Until it arrives, or if it failed, the loaded count, with a
  plus while more history is unloaded ("500+ commits").
- **No toolbar:** its buttons are the **Controls panel** (§5.4), placed by the layout across
  the top and styled like the toolbar was. Remote controls join it as those commands
  come: Fetch, Pull and Push. The branch button switches
  branches, as the Branches panel does.
- **Fetch** (RZG-62, `actions.fetch`, in the Repository menu after Stash Changes…, and
  on the Controls panel after Refresh) calls `session.fetch()`. It is enabled when the
  repository has a remote (`session.remotes`). While a network command runs
  (`network_changed`) it reads **Fetching…**, its tooltip says "Click to cancel", and
  triggering it calls `session.cancel_network()` instead. The status bar shows the
  latest `network_progress` line in a label of its own, beside Loading…, from when the
  command starts until it ends.
- **Pull** (RZG-71, `actions.pull`, in the Repository menu and on the Controls panel after
  Fetch) reads **Pull ↓n** when the current branch is n commits behind its upstream
  (`Branch.behind`), else **Pull**. The menu has that text; the Controls panel's button has
  the icon text **Pull**, and the count `↓n` from `upstream_count(action)`, in a colour of
  its own (RZG-94, §5.4). `upstream_count` reads a property `AppActions` sets on Pull and
  Push before their text, so the button gets the count without taking the text apart;
  it is "" without a count. It is enabled when the current branch has an upstream
  that isn't gone and no network command runs. While its fetch runs it reads **Pulling**
  (the menu **Pulling…**), "Click to cancel", and triggering it cancels; Fetch is
  disabled meanwhile, and Pull while a fetch runs. The integration step is local and
  can't be cancelled. The status bar label reads "Pulling…" until progress comes.
  Triggered otherwise, it sends `pull_requested`, which **`PullDialogs`**
  (`ui/pull_dialogs.py`, made by MainWindow) handles:
  - **Changes first:** with changes in the status (as for Switch), "Pull into *X* with
    uncommitted changes?" offers **Stash and Pull** (the default), **Pull with Changes**
    (git carries them over, or refuses) and **Cancel**, before anything is fetched. The
    stash is put back after the pull.
  - **Diverged** (`pull_diverged`): "*X* and *upstream* have diverged" (ahead and behind
    counts) offers **Rebase**, **Merge** and **Cancel**; the default is the rebase
    default (§4.1), and a conflict aborts either. Then `session.pull_integrate(mode,
    stash)`, with the first answer's `stash`; nothing is fetched again.
  - Already up to date: no box; the ↓n goes when the branches reload.
- **Push** (RZG-72, `actions.push`, in the Repository menu and on the Controls panel after
  Pull) reads **Push ↑n** when the current branch is n commits ahead of an upstream it
  pushes to (§4.1), else **Push**. As for Pull, the button has **Push** and the count
  `↑n` in a colour of its own (RZG-94). It is enabled when the repository has a remote, HEAD
  is on a branch with commits, and no network command runs. While it runs it reads
  **Pushing** (the menu **Pushing…**), "Click to cancel", and triggering it cancels;
  Fetch and Pull are disabled meanwhile, and Push while they run. The status bar label
  reads "Pushing…" until progress comes. Triggered otherwise, it sends
  `push_requested`, which **`PushDialogs`** (`ui/push_dialogs.py`, made by MainWindow)
  handles:
  - **With an upstream to push to:** `session.push()` at once, no box. Ahead 0 pushes
    anyway, with no notice.
  - **Without one:** "Push *X* to *remote* and track it?" with **Push** (the default) and
    **Cancel**, then `session.push(remote)`. The remote: the only one; with several,
    `session.push_default` if it is one of them, else `origin` if there is one, else the
    box offers them in a list, in git's order. With an upstream on `.` the box says
    tracking replaces it.
  - A rejected push is an error (§4.1): it never forces, and offers no Pull.

### 8.1 Windows

The app can have several main windows open, each on its own repository or worktree
(RZG-47). **`AppWindows`** (`ui/app_windows.py`), made once by app.py, holds them.

- **Shared by every window:** `AppSettings`, `ThemeManager`, `CommitDrafts` (§5.4),
  `ColumnPrefs` (§6.3), `RecentRepos` (§6.3) and `Preferences` (§8.2): one of each, made by app.py and passed to
  `AppWindows`, which also holds the one `SettingsWindow`.
  **Each window's own:** `RepoSession`, `RepoWatcher`, `WorktreeReader`, `Selection`,
  `AppActions`, its `PanelContext` and its `MainWindow`. Staging, committing and switching in one window
  never touch another, and each follows its own repository on disk (§4.3).
- **One window per worktree:** a worktree root is shown in at most one window. Opening a
  worktree that a window already shows raises that window instead.
- **API** (for the Worktrees panel, RZG-48, and anything else that opens windows):

  ```python
  class AppWindows(QObject):
      def open(self, path, *, from_window: MainWindow | None = None) -> MainWindow | None
          # the window showing the worktree at `path`, raised; else a new window on it.
          # None if `path` isn't a repository (the error is shown over from_window).
      def new_window(self, *, from_window: MainWindow | None = None) -> MainWindow
          # an empty window (app.py makes the first one this way)
      def window_for(self, root) -> MainWindow | None   # by worktree root (canonical)
      def windows(self) -> tuple[MainWindow, ...]        # in the order opened
      def shutdown(self) -> None                         # after the event loop (app.py)
  ```

  Each `MainWindow` it makes gets `windows` (this object), so a window or its dialogs
  can call `window.open_in_window(path)`: `AppWindows.open(path, from_window=window)`.
  A `MainWindow` made without it (tests) opens everything in itself.
- **Where a new window appears:** the first window restores the saved geometry. A window
  opened from another (`from_window`, else the active window) is half the width and half
  the height of the screen's available area (a quarter of a maximized window), or its
  minimum size if that is larger (RZG-72: the Controls panel's buttons need about 440 px),
  and its top-left is the other window's, moved down and right by a title bar's height
  (`QStyle.PM_TitleBarHeight`), kept on the screen. Where the platform doesn't let the
  app place windows (Wayland), only the size applies.
- **The layout:** every window starts from the one saved layout, view toggles (Side by
  Side Diff, Show Files as Tree) and theme. Each window saves them as it closes, so the
  last one closed wins. The toggles are per window while it's open.
- **The theme is app-wide:** a window checks its Theme and Colour-blind actions from
  `ctx.theme` whenever it becomes active, so a change made in another window shows in
  its menu (menus are only opened in the active window).
- **Closing a window** closes only it. It saves, as above, then its watcher and session
  shut down (the session waits for a running git command, as on quit), and it is deleted
  (`WA_DeleteOnClose`) with its own objects, which are its children: they go after its
  panels, the session last. **File → Quit** closes every window. The app ends when the last window closes
  (`quitOnLastWindowClosed`), and `AppWindows.shutdown()` after the event loop shuts
  down anything still open.
- **A restart** brings back only the last repository (§6.3), not every window. A second
  `rzgit <path>` started while the app runs is a second process, as before.
- **A removed worktree's window closes** (RZG-49): before removing a worktree,
  `WorktreeDialogs` closes the window showing it (`window_for`), which saves and shuts
  down as on any close. A window never removes its own worktree.
- **Each window's worktree reader** (`WorktreeReader`, §4.4) is one of its own objects,
  like its watcher: made with it, told when the app is active, and shut down when it
  closes.
- **Recently used** (RZG-50): a window touches `ctx.recent` (`RecentRepos`, §6.3) when its
  repository changes and whenever it becomes active, so the Worktrees panel's Recently
  Used (§5.4) and File → Recent Repositories put the one you were last in first.
- Not yet: a Window menu, and a fuller rule for which main window owns child windows
  (RZG-51).

### 8.2 Settings

The **Settings window** (RZG-68) shows the settings a user may want to change that have no
menu entry of their own. The View menu's toggles (Side by Side Diff, Show Whole File, Hunk
Headers, Show Files as Tree, Topological Order) stay there and aren't in it: they are per
window while it's open (§8.1). Layouts and columns aren't in it either.

- **Declaring a setting:** the module that uses one declares it, at import:

  ```python
  FOLD_MIN = declare(Setting("diff/fold_min_lines", 20,
                             "Fold unchanged stretches with more lines than",
                             page="Diff", minimum=4, maximum=10_000))
  ```

  `Setting` (`core/preferences.py`, frozen) holds the key, the default (its type is the
  setting's: `bool`, `int`, `float` or `str`), the label, the page, and for the editor
  `minimum`/`maximum`, `step`, `special` (a text field's placeholder, e.g. "Automatic"),
  `choices` (`(value, label)` pairs, for a `str`), `editor` (`"font"`: a monospaced font
  family; `"font_size"`: its size; their defaults, `""` and 0, mean the theme's) and `help`
  (the tooltip). A number box shows only the number, with no unit: the label says what
  it counts.
  `declare(setting)` adds it to the registry; a key declared twice is an error.
  `declared()` lists them in declaration order. A panel adds a setting without touching
  the window.
- **`Preferences(QObject)`** (`core/preferences.py`) holds their values, one for the app,
  shared like `ColumnPrefs` and given to panels as `PanelContext.prefs`.
  - `value(setting)` is the stored value, checked: of the setting's type, clamped to its
    range, one of its choices; anything else reads as the default (logged once).
  - `set(setting, value)` checks it the same way, saves it at once (`AppSettings.set_pref`,
    under `prefs/<key>`, apart from layouts; the default is stored as no value) and emits
    `changed(key, value)` if the value changed. `reset(settings)` sets each to its default.
  - Every window's panels follow `changed`, so a change shows in every window at once.
    Panels still never touch `AppSettings`.
- **The settings now** (page: setting, default, where it's declared):
  - **Appearance:** *Theme* and *Colour-blind friendly colours* (the same values as the
    View → Theme menu: `ThemeManager.set_mode` and `set_colour_blind`, not `Setting`s);
    *Monospace font* and *Monospace font size*: at their defaults (`""`, 0) they follow the
    theme, and the window shows the theme's actual font and size (what "monospace" is on
    this desktop, e.g. 10), never a "Theme's" placeholder. They are used by
    the diff, SHAs and every `mono_font()`: `ThemeManager(prefs=…)` puts them into the
    theme it applies (`ThemeFonts.mono_family`, `mono_point_size`) and applies it again
    when they change (`theme_changed`). `theme_manager.py`.
  - **Diff:** *Fold unchanged stretches with more lines than* (20) and *Lines kept next to
    a change* (3), passed to `stretches(rows, min_lines, keep)`; *Fold unchanged stretches
    when a whole file opens* (off); *Tab width* (4, `DiffView.set_tab_width`); *Ask before
    showing a diff with more lines than* (20 000, passed as `request_diff(…, max_lines=)`);
    *Context lines above and below a change* (3, 3 to 1000, passed as
    `request_diff(…, context=)`, §4.1; the hunks view only).
    A change shows on the diff on screen at once: the folds start again, and a new limit
    or context count reads the diff again. `diff_panel.py`.
  - **History:** *Date format*: ISO (`2026-11-17 14:05`, the default), ISO with seconds, or
    the locale's short format. `history_model.py`.
  - **Tools:** *Terminal command* (`""`): what Open in Terminal starts, split as a shell
    would; when it's empty or its program isn't found, `$TERMINAL` and then the list, as
    before (§5.4). `worktree_dialogs.py`. *VS Code command* (`""`, shown as `code`): what
    Open with VS Code starts (§8), split as a shell would, e.g. `codium` or `flatpak run
    com.visualstudio.code`; empty means `code`. If its program isn't found, the entry is
    disabled. `vscode_launcher.py` (RZG-96; RZG-75 reuses it).
- **The window:** `SettingsWindow` (`ui/settings_window.py`) is a top-level window, not
  modal, one for the app: **Edit → Settings…** (`AppActions.settings`, Ctrl+,, RZG-89) shows it
  and raises it if open (`AppWindows.show_settings`; a `MainWindow` without `AppWindows`
  has its own). It doesn't keep the app running (`WA_QuitOnClose` off), and it closes with
  the last main window.
  - A page list on the left: Appearance, Diff, History, Tools, then any other page in
    declaration order. The page shown is in a subtle outline (`Role.SETTINGS_PAGE`, the
    theme's `border` colour). Each page is a form of its settings: a check box for a
    `bool`, a spin box for an `int` or `float`, a drop-down for `choices` or a font, a text
    field for a `str`.
  - A change applies at once, as it's made (a text field as it's typed). Nothing needs a
    restart. The buttons, on one line, are **Restore Defaults** and **Close**.
  - **Restore Defaults** asks: **Restore All Settings**, **Restore <page> Settings** (the
    page shown) or **Cancel**. The theme rows count as Appearance's. The editors follow
    `changed` and `theme_changed`, so they show a change made elsewhere.

## 9. Performance stance

Measure first; optimise what's measured. The design keeps these known future hot spots
cheap to fix **later**, without paying for them now:

| Hot spot | When it matters | Planned fix | Kept open by |
|---|---|---|---|
| git on the UI thread | large repos, network ops | **done:** one worker thread inside RepoSession (§4.1) | push-based signals (§4.1); panels never run git (§5.2) |
| Network commands blocking loads | a fetch over a slow link | **done:** a second worker for them, cancellable (§4.1, RZG-62) | network commands never touch the working tree or the index |
| History rebuilds as item widgets | 10k+ commits | **done:** `QAbstractTableModel` + paging (§4.1, §5.4) | HistoryPanel is the only place that changes |
| The branch graph's layout | 100k+ commits loaded | **done:** rows worked out as they're drawn, and kept; a page carries on from the last row (§5.4) | `CommitGraph` is plain Python, apart from the model |
| Full rebuild on every refresh | frequent refreshes | granular signals, emitted only on change | §4.1 (done now: cheap, and the right semantics anyway) |
| Layout changes recreating panels | layout switching | panel reuse in `LayoutHost.apply` | §6.2 (done now: needed to keep panel state anyway) |
| Restyling | theme switch | one app-wide stylesheet | §7.3 |
| Refresh storms from the watcher | builds, checkouts, huge trees | **done:** changes merged over 300 ms; ignored files filtered; polling past 10,000 folders | §4.3 |

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
  `test_diff_request`, `test_diff_rows`, `test_diff_view`, `test_diff_gutter` and `test_line_selection` (the diff's
  worker side, its rows, the links, and the marks and line buttons the gutter paints),
  `test_diff_patch` (patches applied by git to temp repos),
  `test_git_commands` (commands on temp repos), `test_commit_drafts`, `test_app_windows`
  (several windows on temp repos),
  `test_inotify_watcher` and `test_repo_watcher` (real file changes in temp repos), and
  `test_architecture` (§2 import rules, unique panel ids).
- Stays on stdlib `unittest`: there are no extra dependencies, and VS Code's test panel is
  already set up for it.

### 10.1 The user guide (RZG-91)

`docs/USAGE.md` is the guide for people using RZGit; this document is for people working
on it. The README links to it at the top.
- **Taken from the code:** every menu entry, button and shortcut it names is spelled as
  the app shows it. Features not on main are left out, not listed as coming.
- **Kept current:** an issue that adds or changes a feature updates `docs/USAGE.md` and
  its screenshots in the same commit (CLAUDE.md and the Core Workflow say so).
- **The shortcut table** at its end lists every shortcut, what it does and where it
  works. `tests/test_usage_guide.py` builds a main window offscreen on a temp repo (with
  something staged and a commit selected, so the panels built on demand exist), collects
  the keys of every `QAction` and `QShortcut` in it, and checks them against the table
  both ways. Keys handled in key events (Esc, Ctrl+A and Ctrl+C in text, Ctrl+Home, Enter
  in the commit summary or the find field) are listed in the test with where they live.
  A standard key the offscreen platform leaves unbound (Quit: Ctrl+Q on a desktop) is
  checked by its `StandardKey` instead, and the keyboard's own Open, Refresh, Find and
  Copy keys, which Qt adds to some standard bindings, aren't listed.
- **Screenshots:** `docs/screenshots/make_screenshots.py` makes the PNGs beside it. It
  clones this repository (main) into a fresh temporary folder, sets up work there (changes,
  one staged, a stash, two branches, a second worktree), and opens it offscreen in the
  Light theme with the Breeze icons, at 1280×860, with settings in that folder. Each
  shot outlines its subject in the theme's `accent`, dims the rest with `text` at low
  alpha, and adds numbered badges (`accent`, `accent_text`) that the guide's text refers
  to. Context menus are grabbed and drawn where they would open. It reads the panels'
  private widgets, so a change to a panel may need a change to its shot.

## 11. Conventions

- **The name** (RZG-59): the app is **RZGit**, its package `rzgit`, its command `rzgit`,
  its desktop file name `rzgit`. The text the app shows says "RZGit".
- Type hints everywhere, with `from __future__ import annotations`.
- Value types are `@dataclass(frozen=True)`. State with signals is a `QObject`.
- Signals are named `<thing>_changed` / `<thing>_occurred` and carry the new value.
- No module-level mutable state. Constants and presets are immutable.
- Private members are `_prefixed`. Modules stay focused; around 300 lines is a cue to split.
- Every user-visible string lives in the panel or action that shows it (ready for i18n later).
