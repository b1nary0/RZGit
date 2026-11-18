# Workflow settings: RZGitClient

The shared workflow skills (`lanes`, `work-graph`, `linear`, `sync-workflow`) read this file for everything that
differs between repositories. It's per repository and never synced.

| Setting            | Value                                                        |
|--------------------|--------------------------------------------------------------|
| Repository         | RZGitClient (a PySide6 git client)                           |
| Main checkout      | `/home/JPOD/dev/RZGitClient`                                 |
| Linear team        | `RZG` (workspace `rze`)                                      |
| Archive sweep      | on: `python3 <main>/.claude/skills/linear/archive_closed.py --team RZG` |
| Lane name          | `RZG-N` (the issue ID)                                       |
| Lane worktree      | `/home/JPOD/dev/RZGitClient-RZG-N`, branch `RZG-N`          |
| Small-items lane   | worktree `/home/JPOD/dev/RZGitClient-small`, tab `RZG-small`, hook name `small` |
| Lanes by default   | yes: lanes is the default way to work an RZG issue          |
| Peer repository    | `/home/JPOD/dev/RZE` (for `sync-workflow`)                   |

`<main>` in the shared skills means the main checkout above.

## Landing

A lane lands on the branch the main checkout is on when the lane starts (`{{BASE}}` in the lane prompt). Here that's
always `main`.

## Small issues

Small means a few files, no new module, no change to the architecture, and about one short sitting (for example:
always open on the History tab). The full suite runs **2×** instead of 5×.

## Overlap hints for planning

Issues that edit these overlap and must rebase carefully:
- the same panel, `repo_session.py`, the theme (`ui/theme`), `main_window.py`, the layout model
- `docs/ARCHITECTURE.md` is shared by all lanes; each lane touches only its own sections

## Rules every lane carries (they're in `lane-prompt.md`; keep them in sync)

- Python from the shared venv, with the lane's own src first: `PYTHONPATH=src /home/JPOD/dev/RZGitClient/.venv/bin/python …`. The editable install points at the main checkout.
- Anything that writes to a repository uses a scratch clone of RZE, never `/home/JPOD/dev/RZE` itself. Timings are noisy while other lanes run.
- The user's standing rules: short replies; no `rm -rf` on variable paths; the Linear section is "Notes"; an epic goes In Progress with its child; no images in Linear; never print the Linear API key; no hard-coded colours outside `ui/theme`; layering git/ → core/ → ui/.
