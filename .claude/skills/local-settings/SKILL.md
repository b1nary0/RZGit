---
name: local-settings
description: Show or set this repository's local settings on this machine (a file beside the main checkout, e.g. ~/dev/RZGit.local.json, never committed), such as the test repository that lanes launch the app against and clone. Use when the user runs /local-settings, asks to set or change a local setting, or when a lane or command reports a local setting isn't set.
argument-hint: "[show | set test-repo <path>]"
---

# Local settings

Some values belong to this machine, not to the repository: which other repository the lanes test against, for one. They live in `<parent>/<folder>.local.json` beside the main checkout (here `~/dev/RZGit.local.json`), which is never committed. The repository's files never name those values; they read them through this skill's script.

The script is `local_settings.py` in this folder (below: `<script>`). From a lane worktree, the main checkout's copy finds the same file.

Arguments: `$ARGUMENTS`

## No argument, or `show`

Run `python3 <script> show` and give the user the values in a few lines, with the file's path.

## `set <key> <value>`

Run `python3 <script> set <key> <value>`. It checks the value first; for `test-repo`, that the path is a git repository and isn't this one. Tell the user in one line what was set. If it refuses, show why and ask.

## When a value is missing

`python3 <script> get <key>` prints the value, or exits with code 2 and the command to set it. When that happens (in a lane or anywhere else), **stop and ask the user for the value**. Don't guess a path or fall back to one. Then set it with `set` and carry on.

## Keys

| Key         | What it is |
|-------------|------------|
| `test-repo` | A large real git repository, not this one. Lanes launch the app against it (Wayland launch, offscreen render) and `git clone --no-hardlinks` it for anything that writes to a repository. Never written to itself. |

A new key goes in `KEYS` in the script, in this table, and in whatever reads it.
