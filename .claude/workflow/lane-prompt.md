<!-- Lane prompt for RZGit. The lanes skill fills in the {{PLACEHOLDERS}}; see its step 2. Per repository, never synced. -->
You are lane {{LANE}}, one of several parallel lanes working on RZGit (a PySide6 git client). Linear team RZG. Your issue: {{ISSUE}}.

## Where you work
- Your worktree is /home/JPOD/dev/RZGit-{{LANE}}, on branch {{LANE}}. Work only there.
- You land on `{{BASE}}`: the branch the main checkout was on when you started.
- Never edit:
  - /home/JPOD/dev/RZGit (the main checkout, on `{{BASE}}`)
  - another lane's worktree (/home/JPOD/dev/RZGit-RZG-*)
  - anything under .claude/worktrees (other sessions)
- Other lanes running at the same time:
{{OTHER_LANES}}
- Stay inside your issue's scope, to keep merges small. Every lane edits docs/ARCHITECTURE.md: touch only your own sections.

## Depends on
{{DEPENDS}}

## Python and tests (important)
The shared venv is /home/JPOD/dev/RZGit/.venv. Its editable install points at the MAIN checkout's src, so always put your own src first. From your worktree root:
  PYTHONPATH=src /home/JPOD/dev/RZGit/.venv/bin/python -m unittest discover -v -s tests -t .
Do the same for any script that imports rzgit, or have it insert your worktree's src at sys.path[0]. Check once that `rzgit.__file__` resolves into your worktree.

## The workflow
First read, in this order:
1. /home/JPOD/dev/RZGit/.claude/skills/linear/SKILL.md and /home/JPOD/dev/RZGit/.claude/workflow/linear.md: how this project uses Linear, including archiving. Use the main checkout's copies, which are always the current ones.
2. The description of the Linear project "Core Workflow".
3. CLAUDE.md.
Then:
1. **Review:** go through the issue's questions with the user, each with a recommendation. Record the answers under **Decisions** in the issue.
2. **Plan:** a short plan. Wait for the user to say "start".
3. **Build:** set the issue In Progress, and its parent epic too, if any. Update docs/ARCHITECTURE.md first, then the code and tests.
4. **Verify:**
   - The full suite 5x (the command above).
   - An offscreen render compared with `{{BASE}}`:
     - Make a temporary worktree of `{{BASE}}`: `git worktree add --detach <fresh mktemp -d path>/head {{BASE}}`.
     - Use a fresh `mktemp -d` settings folder (XDG_CONFIG_HOME).
     - Remove the worktree afterwards with `git worktree remove <literal path>`.
   - A real Wayland launch (QT_QPA_PLATFORM=wayland) against /home/JPOD/dev/RZE, with startup timing. Other lanes may be running launches and test suites at the same time, so say the timings are noisy.
   - Anything that writes to a repository uses your own scratch clone: `git clone --no-hardlinks /home/JPOD/dev/RZE <mktemp -d path>/rze`. Never write to /home/JPOD/dev/RZE itself.
   - Mutation checks: break each key piece and confirm a test fails. Restore from a scratchpad copy, never with `git checkout`.
5. **Report:** set the issue In Review and add a "**Result (date, uncommitted):**" note.
6. **Commit** only when the user says yes. Then:
   - Check the main checkout is still on `{{BASE}}` (`git -C /home/JPOD/dev/RZGit branch --show-current`). If it isn't, stop and ask the user.
   - `git rebase {{BASE}}`, resolving conflicts if it moved (other lanes land on it too). Rerun the full suite once.
   - One commit titled `{{LANE}}: <issue title without the [Feature]/[Architecture] prefix>`, ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
   - Land it: `git -C /home/JPOD/dev/RZGit merge --ff-only {{LANE}}`. If that fails because `{{BASE}}` moved, rebase again, rerun the suite and retry.
   - Never force anything, never merge non-fast-forward, never commit in the main checkout, never push.
   - Set the issue Done, and replace "uncommitted" with the hash now on `{{BASE}}`. Then run the archive sweep: `python3 /home/JPOD/dev/RZGit/.claude/skills/linear/archive_closed.py --team RZG`.
   - Tell the user the lane has landed, so the coordinator can start any lane waiting for it.
   - Last, mark the lane free: `python3 /home/JPOD/dev/RZGit/.claude/skills/lanes/lane_hook.py "$LANE_DIR" "$LANE_NAME" done`. Its tab turns green. (If `LANE_DIR` is unset, you were started without the hooks: skip it.)

## Rules from this user (carry them exactly)
- Short, token-efficient replies.
- Never `rm -rf` a path built from a variable or a glob. Use fresh `mktemp -d` folders under your session's scratchpad instead of clearing old ones.
- The Linear collapsible detail section is named "Notes" (`+++ Notes` … `+++`), not "Agent notes".
- When an issue goes In Progress, set its parent epic In Progress too.
- Never attach images the user shares to Linear issues. Record what you derive from them as text.
- Never print the Linear API key (~/.config/linear-api-key).
- No hard-coded colours outside ui/theme (an architecture test enforces it). Keep the git/ → core/ → ui/ layering. Panels never run git.
{{SMALL_LANE}}

Start now with your issue's review.
