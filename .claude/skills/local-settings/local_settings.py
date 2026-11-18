#!/usr/bin/env python3
"""This machine's local settings for the repository, in a file beside its main checkout.

    python3 local_settings.py show                    # every value, and the file's path
    python3 local_settings.py get test-repo           # print one value; exit 2 if it isn't set
    python3 local_settings.py set test-repo <path>    # check and store a value

The file is <parent>/<folder>.local.json, where <folder> is the main checkout (for example
~/dev/RZGit.local.json). It's never committed. A lane worktree finds the same file, because
the main checkout comes from git's shared .git folder, not from the folder the script is in.
--repo PATH works on another repository (the tests use it).

Keys:
    test-repo   a large real git repository, not this one, that lanes launch the app against
                and clone for anything that writes to a repository
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

KEYS = {"test-repo": "test_repo"}


def common_dir(path: Path) -> Path | None:
    """The repository's shared .git folder (the same for a main checkout and its worktrees)."""
    out = subprocess.run(["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                         capture_output=True, text=True).stdout.strip()
    return Path(out).resolve() if out else None


def main_checkout(start: Path) -> Path:
    git = common_dir(start)
    if git is None or git.name != ".git":
        raise SystemExit(f"not in a git repository with a main checkout: {start}")
    return git.parent


def settings_file(start: Path) -> Path:
    main = main_checkout(start)
    return main.parent / f"{main.name}.local.json"


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError:
        raise SystemExit(f"{path} isn't valid JSON: fix or remove it, then set the values again")


def save(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def check_test_repo(value: str, start: Path) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise SystemExit(f"not a folder: {path}")
    theirs = common_dir(path)
    if theirs is None:
        raise SystemExit(f"not a git repository: {path}")
    if theirs == common_dir(start):
        raise SystemExit(f"{path} is this repository: the test repository must be another one")
    return str(path)


def key_name(key: str) -> str:
    if key not in KEYS:
        raise SystemExit(f"unknown key '{key}'; keys: {', '.join(KEYS)}")
    return KEYS[key]


def main() -> None:
    args = sys.argv[1:]
    start = Path(__file__).resolve().parent
    if "--repo" in args:
        i = args.index("--repo")
        start = Path(args[i + 1]).resolve()
        del args[i:i + 2]
    command = args[0] if args else "show"
    path = settings_file(start)
    me = Path(__file__).resolve()

    if command == "show" and len(args) <= 1:
        data = load(path)
        print(f"Local settings: {path}" + ("" if path.exists() else " (not created yet)"))
        for key, name in KEYS.items():
            print(f"  {key:<10} {data.get(name, '(not set)')}")
    elif command == "get" and len(args) == 2:
        name = key_name(args[1])
        value = load(path).get(name)
        if not value:
            print(f"{args[1]} is not set in {path}. Ask the user for it, then run:\n"
                  f"  python3 {me} set {args[1]} <value>", file=sys.stderr)
            sys.exit(2)
        if name == "test_repo" and not Path(value).is_dir():
            print(f"test-repo {value} (from {path}) no longer exists. Ask the user, then run:\n"
                  f"  python3 {me} set test-repo <path>", file=sys.stderr)
            sys.exit(2)
        print(value)
    elif command == "set" and len(args) == 3:
        name = key_name(args[1])
        value = check_test_repo(args[2], start) if name == "test_repo" else args[2]
        data = load(path)
        data[name] = value
        save(path, data)
        print(f"{args[1]} = {value}  ({path})")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
