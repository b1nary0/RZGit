# Verify: RZGit

Read by the `verify` skill (`.claude/skills/verify/SKILL.md`), which holds the general rules and the order of the stages. Per repository, never synced. Commands run from the worktree root; the shared venv's editable install points at the main checkout, so put your own `src` first (`PYTHONPATH=src`).

## 1. Build

none: Python, nothing to compile. The tests import every module.

## 2. Tests

The runner, which runs the suite N times and shows the count on a lane's tab:

```
PYTHONPATH=src /home/JPOD/dev/RZGit/.venv/bin/python /home/JPOD/dev/RZGit/.claude/workflow/run_tests.py --runs N
```

Add `-v` to name each test. N is:
- **5** for a full check;
- **2** for a small issue;
- **1** for the rerun after rebasing, before landing.

Check once that `rzgit.__file__` resolves into your worktree.

## 3. Compare with base

An offscreen render of the window, compared with the base:
- a temporary worktree of the base (the skill's rule), and the same render from both;
- a fresh `mktemp -d` settings folder for each run (`XDG_CONFIG_HOME`);
- report the pixels that differ, and look at any difference.

## 4. Real launch

A real Wayland launch (`QT_QPA_PLATFORM=wayland`) against the test repository, with startup timing:
- the test repository: `TEST_REPO=$(python3 /home/JPOD/dev/RZGit/.claude/skills/local-settings/local_settings.py get test-repo)`. If that fails, stop and ask the user, then set it with `/local-settings set test-repo <path>`;
- anything that writes to a repository uses a scratch clone of it: `git clone --no-hardlinks "$TEST_REPO" <fresh folder>/test-repo`.

## 5. Mutation checks

Break each key piece of the change and confirm a test fails. Run every test run made while a file is mutated with `PYTHONDONTWRITEBYTECODE=1`: a same-size restore within the same second otherwise leaves a stale `.pyc` that still holds the mutation (RZG-66). If one is suspected, delete that one file by its literal path.

## 6. Other platforms

none: RZGit targets Linux only (macOS is RZG-81, outside lanes).

## 7. Report

The Result note, before the Notes: what was done, any changes from the Decisions, and the test runs (passed out of run), mutation-check results, render difference, and launch timings.
