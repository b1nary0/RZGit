---
name: linear
description: How RZGitClient's work is tracked in Linear (workspace "rze", team RZG): writing issues, the per-issue workflow and its states, epics, views and the API key, and archiving closed issues. Use whenever reading, creating, updating, closing or organising Linear issues, projects or views, and before working on an RZG issue.
---

# Linear for RZGitClient

**Workspace and teams**
- The workspace is `rze`. It has two teams:
  - **RZG**: RZGitClient, this repository.
  - **RZE**: the user's engine. Don't work its issues. Only the archive sweep below touches them.
- RZG's projects:
  - **Core Workflow**: the everyday features. Its description holds the agreed per-issue workflow (its "Notes" section) and is the source of truth for it.
  - **App Architecture**: architecture work.
  - **UI**: look and feel.
- Use the Linear MCP tools first. Use the API key only for what they can't do (archiving, views).

## Archive closed issues (every time)

The rule, agreed with the user:

1. An issue that is **Done or Canceled** (any completed or canceled state) is archived:
   - if it has **no parent**, or
   - if its **parent is closed** too.
2. A closed issue whose parent is still open stays unarchived until the parent closes.
3. Archiving a parent **takes all its children with it**, whatever their state.

When to run it:
- **Right after you set an issue Done or Canceled.** Finish its edits first (the Result note, the commit hash).
- **At the start of any Linear work.** That catches issues the user closed in Linear itself.

How: `python3 .claude/skills/linear/archive_closed.py`, from the repository root.
- It applies the rule to the whole workspace (RZG and RZE), archives children before their parents, and prints what it archived.
- `--dry-run` lists what it would archive without changing anything.
- The user has approved these archive writes in advance. Run it without asking, and say in one line what was archived.
- Archived issues stay searchable and can be restored. The MCP has no archive tool, which is why this uses the API.

## Writing an issue

- **Check for a duplicate first** (`list_issues` with a `query`).
- **Title:** `[Feature]`, `[Bug]`, `[Architecture]` or `[Epic]`, then a plain description: `[Feature] Stash panel: list, inspect, apply, pop and drop stashes`.
- **Labels:** `Claude`, plus `Feature`, `Bug` or `Improvement`. Put the issue in the right project.
- **Body**, for people to read: short sentences, and the same word for the same thing throughout.
  1. What changes and why: a short paragraph.
  2. `**Spec:**` the `docs/ARCHITECTURE.md` sections (§) it changes.
  3. `**Done when**`: checkable bullets.
  4. `**Questions to review**`: numbered, each ending with `*Recommended:* …`.
  5. Related issues.
  6. Detail for agents (plans, file lists, logs) goes last, in a collapsible section named **Notes**: `+++ Notes` … `+++`. Never "Agent notes".
- **A placeholder** ("we'll discuss it later") says so, and lists what is to be discussed instead of "Done when".
- **Images** the user shares are never attached to issues. Record what you derive from them as text.
- **Editing:** use `patch` rather than resending the description.
  - Linear turns `RZG-N` in a description into a link. A `patch` whose `old_string` contains an issue ID won't match. Anchor on text next to it instead.
  - `replace_range`'s `to` is exclusive.

## Working an issue

Follow the Core Workflow description; in short:

1. **Review:** go through the questions with the user, with recommendations. Record the answers under `**Decisions** (date)` in the issue. A change made while building is noted in the Result.
2. **Plan:** a short plan. Wait for "start".
3. **Build:** set the issue **In Progress**, and its **parent epic In Progress** too. Then `docs/ARCHITECTURE.md` first, then the code and tests.
4. **Verify:** the suite 5×, an offscreen render compared with main, and a Wayland launch on RZE with timings.
5. **Report:** set it **In Review**. Add `**Result (date, uncommitted):**` before the Notes: what was done, any changes from the decisions, and the test, mutation-check and timing results.
6. **Commit** only on the user's yes:
   - one commit `RZG-N: <title without the prefix>`, ending with the Co-Authored-By line
   - set the issue **Done**, and replace "uncommitted" with the hash
   - then **archive** (above)
   - push only when the user asks

For parallel work, see the `lanes` skill.

**Epics**
- An epic (`[Epic]`) has its parts as child issues (`parentId`).
- When a child goes In Progress, the epic does too. When its last child closes, ask the user before closing the epic.
- Closing the epic archives it with all its children.

**Limits**
- The workspace is on Linear's free plan, which caps how many issues it can have. If creating an issue fails with "exceeded the free issue limit", tell the user. Run the archive sweep, then suggest archiving or deleting more. Don't work around it silently.

## The API key and views

- The key is in `~/.config/linear-api-key`. Read it only inside the command (`-H "Authorization: $(tr -d '[:space:]' < ~/.config/linear-api-key)"`, or the way `archive_closed.py` reads it). Never print it, log it or copy it anywhere.
- Endpoint: `https://api.linear.app/graphql`.
- **Show the user every write before you run it**, apart from the archive sweep (pre-approved above).
- **Views:**
  1. `customViewCreate` with `filterData`.
  2. Then `viewPreferencesCreate` (`viewType: customView`) twice: `type: organization` and `type: user`. The user's own preferences win.
  3. Set `showCompletedIssues: "all"` when the view shows closed issues.
  4. For collapsible parents, use `issueSubGrouping: "rootIssue"` with `issueNesting: "none"`.
  5. Check `viewPreferencesValues` afterwards.
- A view's link is `https://linear.app/rze/view/<slugId>`.
