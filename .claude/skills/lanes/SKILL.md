---
name: lanes
description: Run several RZG Linear issues in parallel as "lanes" - one git worktree, one Konsole tab and one Claude session per issue, each named after its issue ID (RZG-N). This is the default way to work an RZG issue: use it whenever the user asks to work on, start or do one, except a small issue (done in the coordinator session, or on the small-items lane) or when they ask to do it in the current session. Also for planning which issues can run side by side.
argument-hint: "[-N] [graph | RZG-n ...]"
---

# Lanes: parallel issues in Konsole tabs

One lane works on one Linear issue. A lane is named after its issue's ID, **RZG-N**. That name is used for:

- its git worktree: `/home/JPOD/dev/RZGitClient-RZG-N`
- its branch: `RZG-N`
- its Konsole tab title: `RZG-N`

Each lane is a Claude session in its own tab. It runs the issue's normal per-issue workflow with the user (review, plan, build, verify, report, commit) and lands its commit on `main` by fast-forward.

This session is the **coordinator**. It plans the lanes, starts them, starts lanes that were waiting, and cleans up. It doesn't do lane work itself, apart from small issues (step 0). While lanes run, it never commits in the main checkout or edits it: lanes fast-forward `main` there.

## Arguments

`/lanes -N …` sets **N, the most lanes running at once** for this invocation; for example `/lanes -4` allows four. It counts every lane tab this coordinator has open, including the small-items lane, but not lanes from other sessions. Without `-N`, the limit is 3. The rest of the arguments name the issues to run (`/lanes -2 RZG-12 RZG-14`); with none, use the issues the user names in the request.

While lanes run, the limit holds: when a lane lands and frees a slot, start the next waiting issue; when every slot is full, an unblocked issue waits for one. Tell the user in one line when an unblocked issue is waiting for a slot. The user can change N at any time ("allow 5 lanes").

## Running a work graph (`/lanes -N graph`)

`graph` runs the team's work graph, built with the work-graph skill: `~/.claude/work-graphs/RZG.md`. If the file is missing, or its Status isn't `active`, say so and stop.

- **Linear holds the edges.** Never trust a copy: each time you decide what to start, read each open node's blocked-by relations from Linear. A blocker counts as cleared when it's Done or Canceled, or archived. Pick up any change the user or a lane made there.
- **What can start:** a node whose blockers are all cleared, that isn't `held` or an `epic`, and that has no lane yet. Start them in wave order, up to N lanes (Arguments). A `small` node goes to the small-items lane, in order, or to the coordinator if the user prefers. A `discussion first` node's prompt says its lane starts with the review. The last-alone node starts only when every other node is done and no lane is running.
- **Catching landings:** run a background Bash that exits when `main` moves (`X=$(git rev-parse main); until [ "$(git rev-parse main)" != "$X" ]; do sleep 5; done; git log --format=%s "$X"..main`). Each time it exits, work out what to start, start it, and re-arm the watch. Say so in one line when you start something; stay silent when nothing was unblocked.
- **Only graph nodes get lanes.** A new task joins the graph only if it directly relates to graph work: a split of a node into an epic's children, or an issue that blocks a node. Add it to the file's Nodes with a Log line. Everything else goes to the backlog, with no lane.
- **Epics:** when all of an epic's children are done, ask the user before closing it.
- **Keep the file current:** log each landing, each lane started, and each change the user makes (a hold, a reorder, a dropped node). When every node is done, set Status to `done` and tell the user.

## 0. Lane or coordinator?

**Small issues are done in the coordinator session, not as a lane.** Small means:
- a few files
- no new module, and no change to the architecture
- about one short sitting (for example: always open on the History tab)

Everything else is a lane. When unsure, tell the user which you'd pick and why.

A small issue follows the same per-issue workflow, except that the full suite runs **2×** instead of 5×. While lanes are running, the coordinator does it in its own worktree (`/home/JPOD/dev/RZGitClient-RZG-N`, branch `RZG-N`, with no tab), and lands it with `merge --ff-only` like a lane. Its changes must never sit in the main checkout, because a dirty main checkout makes the lanes' fast-forward landing fail.

**The small-items lane.** The user may instead put the small issues on one lane of their own, worked strictly in the order given:
- worktree `/home/JPOD/dev/RZGitClient-small`, created with `git worktree add -q --detach /home/JPOD/dev/RZGitClient-small main`, then `git -C /home/JPOD/dev/RZGitClient-small switch -q -c RZG-<first>`
- tab title `RZG-small`; the lane's name in the hooks (step 2) is `small`
- one branch `RZG-N` per issue, each started from the current `main`; each issue lands, and the user agrees to move on, before the next one starts
- its prompt is `lane-prompt.md` with `{{SMALL_LANE}}` filled in (step 2)

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

   Keep it to N lanes at a time (see Arguments; 3 if not given).
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
- `{{SMALL_LANE}}`: empty for a normal lane. For the small-items lane, use `{{LANE}}` = `small`, `{{ISSUE}}` = the issues in order, and this text, with any per-issue notes (a colour to pick, overlap with another lane):

  ```
  ## Small-items lane
  This lane runs several small issues one after another, in this order: RZG-a, RZG-b, …
  - Your worktree is /home/JPOD/dev/RZGitClient-small. Wherever this prompt says branch `small`, or a commit title or merge of `small`, use the current issue's own `RZG-N` instead.
  - Small issues: the same workflow, but the full suite runs 2x, not 5x.
  - For each issue: `git switch -c RZG-N main`, do the whole workflow, land it, then `git switch --detach main` and `git branch -d RZG-N`. Start the next issue only after the previous one has landed and the user agrees to move on.
  ```

**The attention hooks.** Each lane's Claude session runs with a settings file that tells the coordinator when the lane needs the user. `<dir>` is `<scratchpad>/lanes`, shared by all of this coordinator's lanes. Write `<dir>/settings-<lane>.json` (`<lane>` is `RZG-N`, or `small`):

```json
{"hooks": {
  "Stop": [{"hooks": [{"type": "command", "command": "python3 /home/JPOD/dev/RZGitClient/.claude/skills/lanes/lane_hook.py <dir> <lane> stop"}]}],
  "Notification": [{"hooks": [{"type": "command", "command": "python3 /home/JPOD/dev/RZGitClient/.claude/skills/lanes/lane_hook.py <dir> <lane> notify"}]}]
}}
```

Then open the tab and start Claude in it. This is Konsole's D-Bus API on this window (see the memory note "Konsole tabs via D-Bus"). `runCommand` needs Konsole's "Enable the security sensitive parts of the DBus API" setting, which the user has turned on.

```bash
P=<scratchpad>/RZG-N.md
SET=<scratchpad>/lanes/settings-RZG-N.json
HERE=$(qdbus-qt6 "$KONSOLE_DBUS_SERVICE" "$KONSOLE_DBUS_WINDOW" currentSession)
ID=$(qdbus-qt6 "$KONSOLE_DBUS_SERVICE" "$KONSOLE_DBUS_WINDOW" newSession "" /home/JPOD/dev/RZGitClient-RZG-N)
qdbus-qt6 "$KONSOLE_DBUS_SERVICE" "$KONSOLE_DBUS_WINDOW" setCurrentSession "$HERE"   # the new tab doesn't take focus
qdbus-qt6 "$KONSOLE_DBUS_SERVICE" /Sessions/$ID setTabTitleFormat 0 "RZG-N"
qdbus-qt6 "$KONSOLE_DBUS_SERVICE" /Sessions/$ID setTabTitleFormat 1 "RZG-N"
qdbus-qt6 "$KONSOLE_DBUS_SERVICE" /Sessions/$ID setTitle 1 "RZG-N"
qdbus-qt6 "$KONSOLE_DBUS_SERVICE" /Sessions/$ID runCommand "claude --settings $SET \"\$(cat $P)\""
```

The tab title format is set to the literal lane name, so Claude's own terminal title doesn't replace it.

**Check that Claude started.** `runCommand` sent before the new tab's shell is ready is lost. This happened to one of two tabs. Check for a `claude` child of the tab's shell, and send the command again if there is none:

```bash
pgrep -a -P "$(qdbus-qt6 "$KONSOLE_DBUS_SERVICE" /Sessions/$ID processId)" | grep -q claude \
  || qdbus-qt6 "$KONSOLE_DBUS_SERVICE" /Sessions/$ID runCommand "claude --settings $SET \"\$(cat $P)\""
```

- If `KONSOLE_DBUS_SERVICE` is unset (not running in Konsole), or `runCommand` is refused, give the user the exact command to run in a new tab: `cd /home/JPOD/dev/RZGitClient-RZG-N && claude --settings <SET> "$(cat <P>)"`.
- Don't retry temporary Konsole profiles: a running Konsole ignores them.

Tell the user which lanes started (the tab and the session ID). They review and approve each lane in its own tab.

**Start the watch**, if it isn't running yet: a Monitor running `python3 -u /home/JPOD/dev/RZGitClient/.claude/skills/lanes/watch.py <scratchpad>/lanes 2>&1`, with the maximum timeout (30 minutes). Re-arm it each time it expires, for as long as lanes run.

## 3. While lanes run

- **When a lane needs the user, tell them.** Each watch event is a lane that finished its turn (questions, a plan waiting for "start", a report) or is stuck on a permission prompt. Reply with just one line naming the lane and what it needs ("RZG-58 needs you: six review questions"). Don't mention the monitor or the event.
- **Tab colours:** the watch colours each lane's tab red (`#c0392b`) while it needs the user, green (`#3b8f5a`) once they have answered it, and blue (`#2f6fd0`) while it has stopped but is waiting on one of its own shell commands (a background task), not the user. An answer shows as a new `user` record in the lane's transcript. A lane started without the hooks gets neither colours nor events: say so.

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
- Remove the lane's `<scratchpad>/lanes/state/<lane>.json`, so the watch stops colouring its tab. It is a literal path, so a plain `rm` is fine.
- The small-items lane's worktree has no branch of its own: remove it once its last issue has landed, and delete each `RZG-N` branch as it lands.
- Close the lane's tab only if the user asks, and only once its Claude session has exited: `qdbus-qt6 "$KONSOLE_DBUS_SERVICE" /Sessions/<ID> runCommand exit`. A Konsole session has no `close` method over D-Bus.
- Never `rm -rf` a worktree, or any path built from a variable or a glob.

## Rules for every lane (they're in `lane-prompt.md`; keep them in sync)

- Lanes follow the `linear` skill. The prompt points at the main checkout's copy by absolute path, so a lane always reads the current one.

- One issue per lane (the small-items lane: its issues in order, one at a time), and the per-issue workflow from the Linear project "Core Workflow" (review, Decisions, plan, wait for "start", build, verify, report, commit only on the user's yes).
- Python from the shared venv, with the lane's own src first: `PYTHONPATH=src /home/JPOD/dev/RZGitClient/.venv/bin/python …`. The editable install points at the main checkout.
- Landing: `git rebase main`, the suite once more, one commit `RZG-N: <title>` with the Co-Authored-By line, then `git -C /home/JPOD/dev/RZGitClient merge --ff-only RZG-N`. Never force anything, never merge non-fast-forward, never commit in the main checkout.
- Anything that writes to a repository uses a scratch clone of RZE, never `/home/JPOD/dev/RZE` itself. Timings are noisy while other lanes run.
- The user's standing rules: short replies; no `rm -rf` on variable paths; the Linear section is "Notes"; an epic goes In Progress with its child; no images in Linear; never print the Linear API key; no hard-coded colours outside `ui/theme`; layering git/ → core/ → ui/.
