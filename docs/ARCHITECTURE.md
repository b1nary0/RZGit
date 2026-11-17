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
src/rzgitclient/
  app.py                        composition root
  git/
    git_models.py               Commit, Stash, Branch, FileStatus, FileDiff, Hunk, DiffLine, Worktree, WorktreeState
    repo.py                     Repo: runs git, returns models; canonical_path; worktree_state
  core/
    repo_session.py             RepoSession: the open repo, its data, git commands
    diff_request.py             DiffRequest, the size limit, load_diff (the worker side of diffs)
    diff_patch.py               build_patch: a patch of chosen lines of a FileDiff (plain Python)
    git_commands.py             GitCommand, run_command (the worker side of commands: stage, commit, …)
    commit_drafts.py            CommitDrafts: the unfinished commit message of each repository (a file each)
    column_prefs.py             ColumnPrefs: each panel's column choices (hidden columns, widths)
    selection.py                Selection: what the user has selected (commit, file)
    repo_watcher.py             RepoWatcher: refreshes the session when the repository changes on disk
    inotify_watcher.py          InotifyWatcher: Linux inotify on a QSocketNotifier (folders → named events)
    worktree_reader.py          WorktreeReader: a repository's worktrees and their state, kept up to date
    app_settings.py             AppSettings: typed wrapper over QSettings (last_repo, layout, …)
  ui/
    main_window.py              MainWindow: thin shell (menus, status bar, LayoutHost)
    app_windows.py              AppWindows: the open main windows; open a window on a path, or raise it
    app_actions.py              AppActions: every QAction, defined once; BranchOp, StashOp, WorktreeOp
    branch_dialogs.py           BranchDialogs: the confirmations and name boxes of branch operations
    stash_dialogs.py            StashDialogs: Stash Changes… and the Drop confirmation
    worktree_dialogs.py         WorktreeDialogs, NewWorktreeDialog: New Worktree…, Remove, Prune; folder, terminal
    app_icon.py                 app_icon(): the app's QIcon, built from assets/icon (app.py sets it)
    assets/                     image files shipped as package data (no modules)
      icon/                     the app icon: its SVG source and PNGs at 16–512 px
      logo/                     mark + "RZGit"; _dark (for dark backgrounds) and _light files
      splash/                   splash images, solid and transparent; _dark and _light
    theme/
      app_theme.py              AppTheme, ThemeColors, StatusColors, ThemeFonts, ThemeMetrics, LIGHT_THEME
      theme_stylesheet.py       build_stylesheet(theme) -> str
      style_roles.py            Surface and Role enums (semantic style hooks)
      theme_manager.py          ThemeManager: current theme + theme_changed
    widgets/
      section_header.py         SectionHeader: shared widget used by frames and panels
      panel_segment.py          PanelSegment, PanelSegments: a panel's own internal sections
      column_header.py          ColumnHeader: the column labels of a list (painted from the theme)
    panels/
      panel.py                  Panel, PanelContext
      panel_registry.py         PANEL_TYPES, panel_type(id)
      controls_panel.py         ControlsPanel: Open, Refresh, current branch (replaces the toolbar)
      branches_panel.py         BranchesPanel
      stash_panel.py            StashPanel: the stashes; apply, pop and drop them
      worktrees_panel.py        WorktreesPanel: the repository's worktrees and their state; open one
      history_panel.py          HistoryPanel
      history_model.py          HistoryModel: the commit table's Qt model, paged
      files_panel.py            FilesPanel: "Unstaged Files"
      staged_panel.py           StagedPanel: "Staged Files"
      file_list.py              FileList: one source's changed files, the body of both
      file_tree.py              FileTree: changed files grouped by folder (plain Python, no Qt)
      commit_files_panel.py     CommitFilesPanel: the files the selected commit changed
      commit_details_panel.py   CommitDetailsPanel: the selected commit's message, people, SHA, parents, refs
      commit_message_text.py    reflow: a hard-wrapped message body as paragraphs (plain Python, no Qt)
      diff_panel.py             DiffPanel
      diff_view.py              DiffView: read-only diff text, row backgrounds, hunk header links
      diff_gutter.py            DiffGutter: line buttons, line numbers and +/− marks beside a DiffView
      line_selection.py         LineSelection: which changed rows of a DiffView are selected
      diff_rows.py              DiffRow, unified_rows, split_rows, *_partners, word_spans (no Qt)
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
packaging/
  linux/
    rzgitclient.desktop         the desktop entry; install-desktop.sh fills in its Exec
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

    def open(self, path) -> bool          # path, or a Repo already read (§8.1)
    def close(self) -> None
    def refresh(self, parts: Refresh = Refresh.ALL) -> None   # Flag: HEAD|BRANCHES|HISTORY|STATUS|STASHES|COUNT
    def load_more_history(self) -> None   # the next page of history, in the background
    def request_diff(self, file: FileSelection | None, full: bool = False) -> None
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

    # Latest values, so a panel created later renders at once (read-only properties):
    repo, head, branches, history, status, stashes, commit_count, history_complete, diff_file, diff,
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
  (panels reset), then `head`, `branches`, `history`, `status`, `stashes`, `commit_count`. `close()` sends
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
- **Stashes** (RZG-36) are a part like the others, `Refresh.STASHES`, in `Refresh.ALL`:
  `Repo.stashes()` reads `git stash list -z` (sha, parents, author, date, reflog subject)
  and returns a tuple of `Stash` (`git_models.py`), newest first. A `Stash` is a `Commit`
  (so `selection.commit` can hold one, §4.2) with `index` (its `stash@{n}`, as `ref`),
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
      With `stash`, it first runs `git stash push -q --include-untracked -m "RZGitClient:
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
    worktrees: WorktreeReader | None = None        # the window's worktree reader (§4.4)

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
RZG-49), later a sort order (RZG-43). The layout shows them (§6.2): a **⋯** button at the right of the section
header in a split, and the menu of the panel's tab when it is right-clicked in a tab
group. A panel with none shows no button and no menu. The panel owns its actions (they
are its children, or come from `ctx.actions`) and keeps their enabled state; the layout
adds none of its own.

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
- **Not clickable:** labels don't sort or select.
- **Showing and hiding columns:** right-clicking the labels gives a checkable list of the
  columns. The first column (the main one: Subject, Message, Name) is always shown, so
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

| Panel | id | Surface | Shows | Writes |
|---|---|---|---|---|
| ControlsPanel | `controls` | chrome | Open, Refresh (`ctx.actions`), current branch | (later: fetch/pull/push, switch branch) |
| BranchesPanel | `branches` | sidebar | branches, current in bold | branch operations, through `actions.request_branch` |
| StashPanel | `stash` | sidebar | "Stashes": message, branch and age of each, newest first | `selection.commit` (a `Stash`); stash operations, through `actions.request_stash` |
| HistoryPanel | `history` | content | Uncommitted changes, then the commit table; no row while a stash is selected | `selection.commit` |
| CommitDetailsPanel | `commit_details` | sidebar | "Commit Details": the selected commit's message, author, committer, SHA, parents and refs; collapsed for Uncommitted changes | `selection.commit` (a parent link) |
| CommitFilesPanel | `commit_files` | sidebar | "Commit Files": the selected commit's changed files, in place of Unstaged Files, Staged Files and Commit; collapsed for Uncommitted changes | `selection.select()` |
| FilesPanel | `files` | sidebar | "Unstaged Files": unstaged and untracked files, status colours | `selection.select()`; stages |
| StagedPanel | `staged` | sidebar | "Staged Files": the index's changes; collapsed when empty | `selection.select()`; unstages |
| DiffPanel | `diff` | content | the diff of `selection.file`, unified or side by side | stages, unstages and discards hunks and lines |
| CommitPanel | `commit` | sidebar | the commit message being written | commits the staged changes |
| WorktreesPanel | `worktrees` | sidebar | "Worktrees": each worktree's name, state and ahead/behind (branch and path on request); collapsed with only one worktree | opens a worktree, through `actions.request_open_worktree` and `request_open_worktree_here`; asks for worktree operations (`request_worktree`) |

**Branches** (BranchesPanel, RZG-35) is where branches are worked with. It never runs
git or shows a dialog: it asks `actions.request_branch(op, name)` (§8), and the list
follows the session.
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
the main worktree first, in labelled columns (§5.2): **Name** (the main column),
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
- The selection is kept by path through refreshes.

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
rule (§6.2) gives it their space. Like Staged Files it builds its list the first time a
commit is selected; Commit keeps its draft while collapsed.

**Commit Details** (CommitDetailsPanel, RZG-26) shows the commit selected in History. It
asks `session.request_commit_details` when `selection.commit` changes, and collapses by
itself while it is None, like Commit Files: it sits directly above Commit Files in every
preset, so the swap gives the two the working tree's space, in equal shares. It builds its
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
  (`src/rzgitclient/ui`). Folders come first, then files, each by name. With it off, the
  list is flat, one full path per row, as before. The tree is built by `file_tree.py`,
  plain Python tested without Qt.
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
  stylesheet's and are drawn over it. Uncommitted changes is never striped.
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
- **Paging:** when the view is scrolled to within a screen of the end, the panel asks the
  session for the next page (§4.1). It watches its scroll bar rather than using Qt's
  `canFetchMore` / `fetchMore`, which Qt also calls while laying rows out, before they have
  a position, so it would load pages nobody scrolled to.
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
  - **Line selection** (`LineSelection`, `line_selection.py`): the diff selects whole changed
    lines (`+` or `−`), not text. A click on a changed line, in the text or the gutter,
    selects just it; dragging selects the changed lines from there to the pointer
    (scrolling when the pointer leaves the view); Shift-click selects from the last click;
    Ctrl-click adds or removes one line. A click elsewhere, or Esc, clears it; Ctrl+A
    selects every changed line. Context lines, hunk headers, filler and notes are never
    selected. A selected line has a stronger background of its own colour
    (`diff_added_selected_bg`, `diff_removed_selected_bg`, in the gutter too) and a 1 px
    outline in its mark colour (`status.added`, `status.deleted`) around the text; a run of
    selected lines of one kind shares one outline. Ctrl+C and Copy copy the selected lines'
    text, joined by newlines (no marks). A new diff of the same file with the same rows
    keeps the selection; any other clears it. Each view (side by side: each side) has its
    own.
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
  selected lines (the clicked line when nothing is selected): Stage/Unstage Hunk,
  Stage/Unstage Selected Lines and, for a tracked file's unstaged diff, Discard Hunk… and
  Discard Selected Lines…, then Copy. Commit diffs have none of these. A staged rename, or a
  diff with unreadable text, has no links or line buttons, and its menu entries are
  disabled, saying why.
  Discards go through `AppActions.request_discard_lines`: the window confirms (§8). The diff
  that comes back keeps the scroll position.
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
  **One `CommitDrafts`** is shared by every window (app.py makes it; §8.1). It keeps
  **one file per repository** in its folder (app.py gives it `drafts_folder()`:
  `commit-drafts/` in `QStandardPaths.AppDataLocation`, e.g.
  `~/.local/share/RZGitClient/RZGitClient/commit-drafts/`). The file is named by the SHA-1
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
History and Diff are tabs of one group, with History current, and Staged Files, Commit,
Commit Details and Commit Files are directly below Unstaged Files (`files`), in that order:
- **Standard** (`DEFAULT_LAYOUT`): Branches, Worktrees, Unstaged Files, Staged Files,
  Commit, Commit Details, Commit Files and Stashes stacked in a left sidebar, History and
  Diff on the right: `Split(V, [controls, Split(H, [Split(V, [branches, worktrees, files,
  staged, commit, commit_details, commit_files, stash], sizes=(180, 140, 220, 120, 160, 0,
  0, 170)), Tabs([history, diff])], sizes=(220, 880))])`. Commit Details' and Commit Files' sizes are 0 in every
  preset: they have no space of their own (§6.2, swaps).
- **Sidebar Tabs:** Branches, Worktrees, Unstaged Files and Stashes as tabs on the left, Staged
  Files and Commit below the tabs; History and Diff on the right.
- **Stacked:** History and Diff across the top; below, Branches with Worktrees and
  Stashes below it,
  beside Unstaged Files, with Staged Files, Commit, Commit Details and Commit Files below
  it.

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
  header plus the panel, with the panel's `surface` set as a style property. The header
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
- **The header menu** (§5.1): in a split, `PanelFrame` puts a **⋯** `QToolButton`
  (`Role.HEADER_MENU`: on the header's background, with the theme's hover) at the right
  of the section header, inside it, so a press on it never starts a drag. It pops up a
  menu of the panel's `header_actions`, follows `header_actions_changed`, and is made
  the first time the panel has actions (most have none), hidden while it has none again. In a tab group, right-clicking a panel's tab shows a menu of
  the same actions (`menu_for(index)`), and nothing when it has none.
- An empty tree (everything dropped by `normalize`) shows an empty area. Falling back to
  `DEFAULT_LAYOUT` is the loader's job (§6.3).

### 6.3 Persistence

`AppSettings` (`core/app_settings.py`) is the only code that touches `QSettings`. It has
typed accessors (`last_repo`, `layout`, `layout_preset`, `window_geometry`,
`side_by_side_diff`, `saved_layouts`, `column_prefs`, …) and takes a file path for tests.
`app.py` creates it and `AppWindows` passes it to each `MainWindow`; panels don't get it.
One `AppSettings` is shared by every window. What panels save goes through a small object
in `ctx` that holds only that: the commit draft through `CommitDrafts` (§5.4), column
choices through `ColumnPrefs`.

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

It is reopened at startup by `MainWindow.open_last_repo()`, quietly:
if it was moved, deleted or is no longer a repository, the window starts with none, the
status bar says "Last repository not found: <path>", and the setting is kept (the drive
may be back next time). `RepoSession.open(path, report=False)` is the quiet open. A path
given on the command line is opened as usual and shows its error.

On close, the window saves the host's `state()` (as JSON, so the format is ours) and its
geometry (with several windows, each saves as it closes, so the last one closed wins): size and maximized state, plus position where the platform allows it (not on
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
class ThemeColors:   menubar_bg: str; toolbar_bg: str; tab_bg: str; header_bg: str; sidebar_bg: str; content_bg: str; border: str; tree_guide: str;
                     column_header_bg, column_header_text, column_header_text_hover,
                     column_header_separator, column_header_border: str   # ColumnHeader (§5.2)
                     row_alt_bg: str   # every second row of a striped list (History, RZG-63); at least ΔE 1.5 from content_bg and content_hover
                     text: str; text_muted: str; hover: str; selection: str; content_selection: str;
                     accent: str; button_*: str; scroll_*: str; diff_*: str (incl. diff_hunk_bg, diff_hunk_text, diff_line_button*, diff_*_selected_bg, diff_*_word); …
@dataclass(frozen=True)
class StatusColors:  modified, added, deleted, renamed, copied, type_changed, conflict, untracked: str
                     def for_code(self, code: str) -> str | None   # porcelain letter → colour; None if unknown
@dataclass(frozen=True)
class ThemeFonts:    default_point_size: float = 10; mono_family: str = "monospace"
                     ui_point_size, menu_point_size, status_bar_point_size, list_point_size,
                     column_header_point_size, tab_point_size, tooltip_point_size,
                     section_header_point_size, segment_header_point_size,
                     mono_point_size: float | None = None
                     def point_size(self, kind: FontKind) -> float   # its own size, else the default
@dataclass(frozen=True)
class ThemeMetrics:  splitter_width: int = 1; scrollbar_width: int = 10; row_padding: int = 2; row_min_height: int = 18

@dataclass(frozen=True)
class AppTheme:
    name: str; colors: ThemeColors; status: StatusColors; fonts: ThemeFonts; metrics: ThemeMetrics
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
- **Colour-blind variants** (RZG-33): `LIGHT_CB_THEME` and `DARK_CB_THEME` derive from
  their base with `dataclasses.replace`, so they share every token except those that tell
  kinds apart: `status.*`, `diff_added*` / `diff_removed*`, `diff_line_button*` and
  `warning`. Those use one palette built on blue and orange instead of green and red
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

`style_roles.py` defines `Surface` (SIDEBAR, CONTENT, CHROME) and `Role` (SECTION_HEADER,
SEGMENT_HEADER for the header strip of a segment inside a panel (§5.2),
PLACEHOLDER for a muted "nothing to show" message, DIFF_VIEW for the borderless diff text,
HINT and HINT_WARNING for a small muted note and the same past a limit, HEADER_MENU for
the ⋯ button of a section header's menu, §6.2).
Widgets get them as Qt dynamic properties through `set_surface()` / `set_role()`, and the
stylesheet targets those (`QWidget[surface="sidebar"]`). The theme decides what "sidebar"
looks like; panels only say what they are.

A surface or role is added when a widget first needs it (`Surface.CHROME` came with the
Controls panel: the toolbar's background), not ahead of time. A test checks that every
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
  with the layout, so panels never touch settings. **Show Files as Tree** works the same
  way (`AppSettings.file_tree`, on by default): every `FileList` follows its `toggled`.
  Both are in the **View** menu.
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
- **`MainWindow`** is a thin shell: menus built from `AppActions`, the status
  bar summary (from session signals: path · branch · commits · changed files), error dialogs (from `error_occurred`), and a
  `LayoutHost` as the central widget. The Open, Quit and Reset Layout handlers live here
  because they need dialogs or the window. Target: around 100 lines.
- **Title:** `<worktree name> — RZGitClient` (`Repo.worktree_name`: git's name for a linked
  worktree, the folder's name for the main one), or `RZGitClient` with none open.
- **File → Open Repository…** opens the folder in this window, unless another window
  already shows that worktree: then that window is raised and this one is unchanged
  (§8.1). **File → Quit** closes every window (`QApplication.closeAllWindows`).
- **Commit count text** (`commit_summary`): the exact `commit_count` with separators
  ("2,421 commits", "1 commit"). Until it arrives, or if it failed, the loaded count, with a
  plus while more history is unloaded ("500+ commits").
- **No toolbar:** its buttons are the **Controls panel** (§5.4), placed by the layout across
  the top and styled like the toolbar was. Remote controls (fetch/pull/push) join it when
  those commands exist; switching branches is in the Branches panel.

### 8.1 Windows

The app can have several main windows open, each on its own repository or worktree
(RZG-47). **`AppWindows`** (`ui/app_windows.py`), made once by app.py, holds them.

- **Shared by every window:** `AppSettings`, `ThemeManager`, `CommitDrafts` (§5.4) and
  `ColumnPrefs` (§6.3): one of each, made by app.py and passed to `AppWindows`.
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
  the height of the screen's available area (a quarter of a maximized window), and its
  top-left is the other window's, moved down and right by a title bar's height
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
  `rzgitclient <path>` started while the app runs is a second process, as before.
- **A removed worktree's window closes** (RZG-49): before removing a worktree,
  `WorktreeDialogs` closes the window showing it (`window_for`), which saves and shuts
  down as on any close. A window never removes its own worktree.
- **Each window's worktree reader** (`WorktreeReader`, §4.4) is one of its own objects,
  like its watcher: made with it, told when the app is active, and shut down when it
  closes.
- Not yet: a Window menu, and a fuller rule for which main window owns child windows
  (RZG-51).

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

## 11. Conventions

- Type hints everywhere, with `from __future__ import annotations`.
- Value types are `@dataclass(frozen=True)`. State with signals is a `QObject`.
- Signals are named `<thing>_changed` / `<thing>_occurred` and carry the new value.
- No module-level mutable state. Constants and presets are immutable.
- Private members are `_prefixed`. Modules stay focused; around 300 lines is a cue to split.
- Every user-visible string lives in the panel or action that shows it (ready for i18n later).
