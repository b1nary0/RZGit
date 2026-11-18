"""A patch of chosen lines of a diff (spec §4.1, "Lines and hunks").

Plain Python. The worker applies the patch with ``git apply``: forwards to
stage, in reverse to unstage or discard. Which unselected lines become context
depends on the direction, so the patch always describes what to change and
nothing else.
"""

from __future__ import annotations

from collections.abc import Collection

from rzgit.git.git_models import FileDiff, Hunk, LineKind
from rzgit.git.repo import GitError

Line = tuple[int, int]  # (index of the hunk, index of the line in it)

NO_NEWLINE = "\\ No newline at end of file"
UNREADABLE = "�"  # what bytes that aren't UTF-8 became when the diff was read


def hunk_lines(diff: FileDiff, hunk: int) -> frozenset[Line]:
    """Every changed line of one hunk."""
    return frozenset((hunk, i) for i, line in enumerate(diff.hunks[hunk].lines)
                     if line.kind is not LineKind.CONTEXT)


def can_patch(diff: FileDiff) -> str | None:
    """Why parts of ``diff`` can't be staged, or None if they can."""
    if diff.old_path and diff.old_path != diff.path:
        return "A renamed file is staged and unstaged whole."
    if any(UNREADABLE in line.text for hunk in diff.hunks for line in hunk.lines):
        return "This file has text that isn't UTF-8, so its lines can't be staged one by one."
    return None


def build_patch(diff: FileDiff, lines: Collection[Line], *, reverse: bool = False,
                new_file: bool = False, mode: str = "100644") -> str | None:
    """A patch that changes only ``lines`` of ``diff``; None if there's nothing to change.

    ``reverse``: it will be applied in reverse (unstage, discard), so an
    unselected added line stays as context and an unselected removed line is
    left out, instead of the other way round. ``new_file``: ``diff`` is of an
    untracked file, and the patch creates it with mode ``mode``.
    """
    if (reason := can_patch(diff)) is not None:
        raise GitError(reason)
    chosen = set(lines)
    hunks: list[str] = []
    delta = 0  # how far the side being changed has moved, from the hunks before
    for h, hunk in enumerate(diff.hunks):
        built = _hunk(hunk, {i for hh, i in chosen if hh == h}, reverse, delta)
        if built is not None:
            text, hunk_delta = built
            hunks.append(text)
            delta += hunk_delta
    if not hunks:
        return None
    name = _quote(diff.path, "a/"), _quote(diff.path, "b/")
    head = [f"diff --git {name[0]} {name[1]}"]
    if new_file:
        head += [f"new file mode {mode}", "--- /dev/null"]
    else:
        head.append(f"--- {name[0]}")
    head.append(f"+++ {name[1]}")
    return "\n".join(head) + "\n" + "".join(hunks)


def _hunk(hunk: Hunk, chosen: set[int], reverse: bool, delta: int) -> tuple[str, int] | None:
    if not chosen:
        return None
    body: list[str] = []
    old_count = new_count = 0

    def emit(mark: str, i: int) -> None:
        nonlocal old_count, new_count
        line = hunk.lines[i]
        body.append(f"{mark}{line.text}\n")
        if line.no_newline:
            body.append(NO_NEWLINE + "\n")
        old_count += mark != "+"
        new_count += mark != "-"

    for block in _blocks(hunk):
        if isinstance(block, int):
            emit(" ", block)
            continue
        removed, added = block
        # Pair the k-th removed line with the k-th added one, so a chosen
        # replacement lands where its old line was ("b, c" → "B, C" with only
        # b → B chosen gives "B, c").
        for k in range(max(len(removed), len(added))):
            if k < len(removed):
                i = removed[k]
                if i in chosen:
                    emit("-", i)
                elif not reverse:
                    emit(" ", i)  # it stays in the file the patch is applied to
            if k < len(added):
                i = added[k]
                if i in chosen:
                    emit("+", i)
                elif reverse:
                    emit(" ", i)
    if all(not line.startswith(("+", "-")) for line in body):
        return None
    # The side the patch is applied to is the file as it is: its numbers are
    # git's. The other side's first line is ``delta`` lines away. An empty side
    # is numbered by the line before it, as git does.
    if reverse:
        new_first = hunk.new_start + (new_count == 0)
        old_first = new_first - delta
    else:
        old_first = hunk.old_start + (old_count == 0)
        new_first = old_first + delta
    old_start = old_first - (old_count == 0)
    new_start = new_first - (new_count == 0)
    header = f"@@ -{old_start},{old_count} +{new_start},{new_count} @@\n"
    return header + "".join(body), new_count - old_count


def _blocks(hunk: Hunk) -> list[int | tuple[list[int], list[int]]]:
    """The hunk's lines in order: a context line's index, or a change block's
    removed and added line indices (git writes a block's removals first)."""
    blocks: list[int | tuple[list[int], list[int]]] = []
    removed: list[int] = []
    added: list[int] = []
    for i, line in enumerate(hunk.lines):
        if line.kind is LineKind.CONTEXT:
            if removed or added:
                blocks.append((removed, added))
                removed, added = [], []
            blocks.append(i)
        elif line.kind is LineKind.REMOVED and not added:
            removed.append(i)
        elif line.kind is LineKind.REMOVED:  # a removal after additions: a new block
            blocks.append((removed, added))
            removed, added = [i], []
        else:
            added.append(i)
    if removed or added:
        blocks.append((removed, added))
    return blocks


def _quote(path: str, prefix: str) -> str:
    """``prefix + path``, quoted as git does when it holds a quote, backslash or
    control character."""
    name = prefix + path
    if not any(c in '"\\' or ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        return name
    escapes = {'"': '\\"', "\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}
    out = "".join(escapes.get(c) or (f"\\{ord(c):03o}" if ord(c) < 0x20 or ord(c) == 0x7F
                                      else c) for c in name)
    return f'"{out}"'
