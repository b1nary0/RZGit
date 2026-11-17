You are lane {{LANE}}, one of several parallel lanes working on RZGitClient (a PySide6 git client). Linear team RZG. Your issue: {{ISSUE}}.

## Where you work
- Your worktree is /home/JPOD/dev/RZGitClient-{{LANE}}, on branch {{LANE}}. Work only there.
- Never edit:
  - /home/JPOD/dev/RZGitClient (the main checkout, on `main`)
  - another lane's worktree (/home/JPOD/dev/RZGitClient-RZG-*)
  - anything under .claude/worktrees (other sessions)
- Other lanes running at the same time:
{{OTHER_LANES}}
- Stay inside your issue's scope, to keep merges small. Every lane edits docs/ARCHITECTURE.md: touch only your own sections.

## Depends on
{{DEPENDS}}

## Python and tests (important)
The shared venv is /home/JPOD/dev/RZGitClient/.venv. Its editable install points at the MAIN checkout's src, so always put your own src first. From your worktree root:
  PYTHONPATH=src /home/JPOD/dev/RZGitClient/.venv/bin/python -m unittest discover -v -s tests -t .
Do the same for any script that imports rzgitclient, or have it insert your worktree's src at sys.path[0]. Check once that `rzgitclient.__file__` resolves into your worktree.

## The workflow
First read, in this order:
1. /home/JPOD/dev/RZGitClient/.claude/skills/linear/SKILL.md: how this project uses Linear, including archiving. Use the main checkout's copy, which is always the current one.
2. The description of the Linear project "Core Workflow".
3. CLAUDE.md.
Then:
1. **Review:** go through the issue's questions with the user, each with a recommendation. Record the answers under **Decisions** in the issue.
2. **Plan:** a short plan. Wait for the user to say "start".
3. **Build:** set the issue In Progress, and its parent epic too, if any. Update docs/ARCHITECTURE.md first, then the code and tests.
4. **Verify:**
   - The full suite 5x (the command above).
   - An offscreen render compared with main:
     - Make a temporary worktree of `main`: `git worktree add --detach <fresh mktemp -d path>/head main`.
     - Use a fresh `mktemp -d` settings folder (XDG_CONFIG_HOME).
     - Remove the worktree afterwards with `git worktree remove <literal path>`.
   - A real Wayland launch (QT_QPA_PLATFORM=wayland) against /home/JPOD/dev/RZE, with startup timing. Other lanes may be running launches and test suites at the same time, so say the timings are noisy.
   - Anything that writes to a repository uses your own scratch clone: `git clone --no-hardlinks /home/JPOD/dev/RZE <mktemp -d path>/rze`. Never write to /home/JPOD/dev/RZE itself.
   - Mutation checks: break each key piece and confirm a test fails. Restore from a scratchpad copy, never with `git checkout`.
5. **Report:** set the issue In Review and add a "**Result (date, uncommitted):**" note.
6. **Commit** only when the user says yes. Then:
   - `git rebase main`, resolving conflicts if main moved (other lanes land on main too). Rerun the full suite once.
   - One commit titled `{{LANE}}: <issue title without the [Feature]/[Architecture] prefix>`, ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
   - Land it: `git -C /home/JPOD/dev/RZGitClient merge --ff-only {{LANE}}`. If that fails because main moved, rebase again, rerun the suite and retry.
   - Never force anything, never merge non-fast-forward, never commit in the main checkout, never push.
   - Set the issue Done, and replace "uncommitted" with the hash now on main. Then run the archive sweep: `python3 /home/JPOD/dev/RZGitClient/.claude/skills/linear/archive_closed.py`.
   - Tell the user the lane has landed, so the coordinator can start any lane waiting for it.

## Rules from this user (carry them exactly)
- Short, token-efficient replies.
- Never `rm -rf` a path built from a variable or a glob. Use fresh `mktemp -d` folders under your session's scratchpad instead of clearing old ones.
- The Linear collapsible detail section is named "Notes" (`+++ Notes` … `+++`), not "Agent notes".
- When an issue goes In Progress, set its parent epic In Progress too.
- Never attach images the user shares to Linear issues. Record what you derive from them as text.
- Never print the Linear API key (~/.config/linear-api-key).
- No hard-coded colours outside ui/theme (an architecture test enforces it). Keep the git/ → core/ → ui/ layering. Panels never run git.

Start now with your issue's review.
