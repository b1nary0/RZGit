---
name: lanes
description: Run several RZG Linear issues in parallel as "lanes" - one git worktree, one Konsole tab and one Claude session per issue, each named after its issue ID (RZG-N). This is the default way to work any RZG issue (even a single one): use it whenever the user asks to work on, start or do an issue, unless they explicitly ask to do it in the current session. Also for planning which issues can run side by side.
---

# Lanes: parallel issues in Konsole tabs

One lane works on one Linear issue. A lane is named after its issue's ID, **RZG-N**. That name is used for:

- its git worktree: `/home/JPOD/dev/RZGitClient-RZG-N`
- its branch: `RZG-N`
- its Konsole tab title: `RZG-N`

Each lane is a Claude session in its own tab. It runs the issue's normal per-issue workflow with the user (review, plan, build, verify, report, commit) and lands its commit on `main` by fast-forward.

This session is the **coordinator**. It plans the lanes, starts them, starts lanes that were waiting, and cleans up. It doesn't do lane work itself. While lanes run, it never commits in the main checkout or edits it: lanes fast-forward `main` there.

## 1. Plan (wait for the user)

1. Gather the issues the user named (or the open RZG issues, if they asked for "the remaining ones"). Read each one with `get_issue` and its comments, including parents and children.
2. Check what is already running:
   - `git worktree list`: existing lanes, and other sessions' worktrees (for example `.claude/worktrees/*`). Never touch those.
   - `git status`: the main checkout must be clean, apart from untracked `.claude/`.
   - `git fetch -q && git status -sb`: `main` is in sync with `origin/main`, or note that it is ahead.
3. Work out dependencies and overlap:
   - which issue needs another's code
   - which issues edit the same modules (the same panel, `repo_session.py`, the theme, `main_window.py`)
   - `docs/ARCHITECTURE.md` is shared by all lanes; each lane touches only its own sections
4. Propose the lanes in a short table: lane (RZG-N), issue title, what it may touch, and what it waits for. A lane that depends on another either:
   - **starts later**: the coordinator starts it once the other has landed; or
   - **starts now, lands after**: it builds the parts that don't depend on the other, and lands only after the other's commit is on `main`. Its prompt says so.

   Keep it to about 3 lanes at a time, unless the user asks for more.
5. **Wait for the user's go.** Don't create worktrees or tabs before it.

## 2. Start the lanes

For each lane RZG-N that starts now:

```bash
cd /home/JPOD/dev/RZGitClient
git worktree add -q -b RZG-N /home/JPOD/dev/RZGitClient-RZG-N main
```

Write its prompt from `lane-prompt.md` (next to this file) into the session's scratchpad as `RZG-N.md`. Fill in:

- `{{LANE}}`: `RZG-N`
- `{{ISSUE}}`: the issue's ID and title
- `{{OTHER_LANES}}`: one line per other lane: ID, title, and the areas it touches
- `{{DEPENDS}}`: for "starts now, lands after", say which issue must be on `main` first, and which parts may be built before it lands; otherwise "None."

Then open the tab and start Claude in it. This is Konsole's D-Bus API on this window (see the memory note "Konsole tabs via D-Bus"). `runCommand` needs Konsole's "Enable the security sensitive parts of the DBus API" setting, which the user has turned on.

```bash
P=<scratchpad>/RZG-N.md
ID=$(qdbus-qt6 "$KONSOLE_DBUS_SERVICE" "$KONSOLE_DBUS_WINDOW" newSession "" /home/JPOD/dev/RZGitClient-RZG-N)
qdbus-qt6 "$KONSOLE_DBUS_SERVICE" /Sessions/$ID setTabTitleFormat 0 "RZG-N"
qdbus-qt6 "$KONSOLE_DBUS_SERVICE" /Sessions/$ID setTabTitleFormat 1 "RZG-N"
qdbus-qt6 "$KONSOLE_DBUS_SERVICE" /Sessions/$ID setTitle 1 "RZG-N"
qdbus-qt6 "$KONSOLE_DBUS_SERVICE" /Sessions/$ID runCommand "claude \"\$(cat $P)\""
```

The tab title format is set to the literal lane name, so Claude's own terminal title doesn't replace it.

**Check that Claude started.** `runCommand` sent before the new tab's shell is ready is lost. This happened to one of two tabs. Check for a `claude` child of the tab's shell, and send the command again if there is none:

```bash
pgrep -a -P "$(qdbus-qt6 "$KONSOLE_DBUS_SERVICE" /Sessions/$ID processId)" | grep -q claude \
  || qdbus-qt6 "$KONSOLE_DBUS_SERVICE" /Sessions/$ID runCommand "claude \"\$(cat $P)\""
```

- If `KONSOLE_DBUS_SERVICE` is unset (not running in Konsole), or `runCommand` is refused, give the user the exact command to run in a new tab: `cd /home/JPOD/dev/RZGitClient-RZG-N && claude "$(cat <P>)"`.
- Don't retry temporary Konsole profiles: a running Konsole ignores them.

Tell the user which lanes started (the tab and the session ID). They review and approve each lane in its own tab.

## 3. While lanes run

- When the user says a lane has landed, check `git log --oneline main`. Then start any lane that was waiting for it (step 2).
- If the user asks about a lane, read its issue in Linear (state, Result note) and `git -C <worktree> log`/`status`. Don't guess.
- Don't push unless the user asks. When they do, push `main` from the main checkout (`git push origin main`), after checking it is a fast-forward of `origin/main`.

## 4. Clean up (when the user says the lanes are done)

For each finished lane:

```bash
cd /home/JPOD/dev/RZGitClient
git worktree remove /home/JPOD/dev/RZGitClient-RZG-N   # literal path, never a variable or glob
git branch -d RZG-N                                     # -d: refuses if not merged; then ask
```

- If `remove` refuses (uncommitted changes), show the user what is left. Don't force it.
- Close the lane's tab only if the user asks, and only once its Claude session has exited: `qdbus-qt6 "$KONSOLE_DBUS_SERVICE" /Sessions/<ID> runCommand exit`. A Konsole session has no `close` method over D-Bus.
- Never `rm -rf` a worktree, or any path built from a variable or a glob.

## Rules for every lane (they're in `lane-prompt.md`; keep them in sync)

- Lanes follow the `linear` skill. The prompt points at the main checkout's copy by absolute path, so a lane always reads the current one.

- One issue per lane, and the per-issue workflow from the Linear project "Core Workflow" (review, Decisions, plan, wait for "start", build, verify, report, commit only on the user's yes).
- Python from the shared venv, with the lane's own src first: `PYTHONPATH=src /home/JPOD/dev/RZGitClient/.venv/bin/python …`. The editable install points at the main checkout.
- Landing: `git rebase main`, the suite once more, one commit `RZG-N: <title>` with the Co-Authored-By line, then `git -C /home/JPOD/dev/RZGitClient merge --ff-only RZG-N`. Never force anything, never merge non-fast-forward, never commit in the main checkout.
- Anything that writes to a repository uses a scratch clone of RZE, never `/home/JPOD/dev/RZE` itself. Timings are noisy while other lanes run.
- The user's standing rules: short replies; no `rm -rf` on variable paths; the Linear section is "Notes"; an epic goes In Progress with its child; no images in Linear; never print the Linear API key; no hard-coded colours outside `ui/theme`; layering git/ → core/ → ui/.
