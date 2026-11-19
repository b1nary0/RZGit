# Workflow settings: RZGit

The shared workflow skills (`lanes`, `work-graph`, `linear`, `sync-workflow`) read this file for everything that
differs between repositories. It's per repository and never synced.

| Setting            | Value                                                        |
|--------------------|--------------------------------------------------------------|
| Repository         | RZGit (a PySide6 git client)                                 |
| Main checkout      | `/home/JPOD/dev/RZGit`                                       |
| Linear team        | `RZG` (workspace `rze`)                                      |
| Archive sweep      | on: `python3 <main>/.claude/skills/linear/archive_closed.py --team RZG` |
| Lane name          | `RZG-N` (the issue ID)                                       |
| Lane worktree      | `/home/JPOD/dev/RZGit-RZG-N`, branch `RZG-N`                 |
| Small-items lane   | worktree `/home/JPOD/dev/RZGit-small`, tab `RZG-small`, hook name `small` |
| Lanes by default   | yes: lanes is the default way to work an RZG issue          |
| Test runs          | `-m unittest\b|run_tests\.py` (a regular expression over a lane's process command lines; see the lanes skill's "Test runs"). `.claude/workflow/run_tests.py --runs N` runs the suite N times and writes the progress file |
| Local settings     | `~/dev/RZGit.local.json`, this machine's only (the `local-settings` skill: `test-repo`) |

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

- Python from the shared venv, with the lane's own src first: `PYTHONPATH=src /home/JPOD/dev/RZGit/.venv/bin/python …`. The editable install points at the main checkout.
- The test repository comes from the local settings (`local_settings.py get test-repo`); if it isn't set, ask the user. Anything that writes to a repository uses a scratch clone of it, never the test repository itself. Timings are noisy while other lanes run.
- The user's standing rules: short replies; no `rm -rf` on variable paths; the Linear section is "Notes"; an epic goes In Progress with its child; no images in Linear; never print the Linear API key; no hard-coded colours outside `ui/theme`; layering git/ → core/ → ui/.
