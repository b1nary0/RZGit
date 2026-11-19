"""A patch of chosen lines of a diff (spec §4.1, "Lines and hunks").

Plain Python. The worker applies the patch with ``git apply``: forwards to
stage, in reverse to unstage or discard. Which unselected lines become context
depends on the direction, so the patch always describes what to change and
nothing else. Copy as Patch (``copy_patch``) puts a forwards patch on the
clipboard, with git's usual context.
"""

from __future__ import annotations

from collections.abc import Collection

from rzgit.git.git_models import FileDiff, Hunk, LineKind
from rzgit.git.repo import GitError

Line = tuple[int, int]  # (index of the hunk, index of the line in it)

COPY_CONTEXT = 3  # lines of context around a change in a copied patch, as git's default
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


def all_lines(diff: FileDiff) -> frozenset[Line]:
    """Every changed line of the diff."""
    return frozenset(line for h in range(len(diff.hunks)) for line in hunk_lines(diff, h))


def copy_patch(diff: FileDiff, lines: Collection[Line]) -> str | None:
    """The patch Copy as Patch copies: ``lines`` of ``diff``, forwards, so that
    ``git apply`` applies it to the diff's base. Context is trimmed to
    ``COPY_CONTEXT`` lines; an added file's patch creates it, and one that
    removes every line of a deleted file deletes it."""
    deleting = diff.deleted_mode is not None and set(lines) >= all_lines(diff)
    mode = diff.new_mode or (diff.deleted_mode if deleting else None) or "100644"
    return build_patch(diff, lines, new_file=diff.new_mode is not None,
                       deleted_file=deleting, mode=mode, context=COPY_CONTEXT)


def build_patch(diff: FileDiff, lines: Collection[Line], *, reverse: bool = False,
                new_file: bool = False, deleted_file: bool = False, mode: str = "100644",
                context: int | None = None) -> str | None:
    """A patch that changes only ``lines`` of ``diff``; None if there's nothing to change.

    ``reverse``: it will be applied in reverse (unstage, discard), so an
    unselected added line stays as context and an unselected removed line is
    left out, instead of the other way round. ``new_file``: ``diff`` is of an
    added file, and the patch creates it with mode ``mode``; ``deleted_file``:
    the patch deletes the file (mode ``mode``), so it must remove every line.
    ``context``: at most this many context lines around each change (a hunk is
    split where there are more); None keeps every one.
    """
    if (reason := can_patch(diff)) is not None:
        raise GitError(reason)
    chosen = set(lines)
    hunks: list[str] = []
    delta = 0  # how far the side being changed has moved, from the hunks before
    for h, hunk in enumerate(diff.hunks):
        built = _hunk(hunk, {i for hh, i in chosen if hh == h}, reverse, delta, context)
        if built is not None:
            texts, hunk_delta = built
            hunks += texts
            delta += hunk_delta
    if not hunks:
        return None
    name = _quote(diff.path, "a/"), _quote(diff.path, "b/")
    head = [f"diff --git {name[0]} {name[1]}"]
    if new_file:
        head += [f"new file mode {mode}", "--- /dev/null", f"+++ {name[1]}"]
    elif deleted_file:
        head += [f"deleted file mode {mode}", f"--- {name[0]}", "+++ /dev/null"]
    else:
        head += [f"--- {name[0]}", f"+++ {name[1]}"]
    return "\n".join(head) + "\n" + "".join(hunks)


def _hunk(hunk: Hunk, chosen: set[int], reverse: bool, delta: int,
          context: int | None) -> tuple[list[str], int] | None:
    """The patch's hunks for one of the diff's (several if ``context`` splits
    it), and how far they move the side being changed."""
    if not chosen:
        return None
    entries: list[tuple[str, int]] = []  # (mark, index of the line in the hunk)
    for block in _blocks(hunk):
        if isinstance(block, int):
            entries.append((" ", block))
            continue
        removed, added = block
        # Pair the k-th removed line with the k-th added one, so a chosen
        # replacement lands where its old line was ("b, c" → "B, C" with only
        # b → B chosen gives "B, c").
        for k in range(max(len(removed), len(added))):
            if k < len(removed):
                i = removed[k]
                if i in chosen:
                    entries.append(("-", i))
                elif not reverse:
                    entries.append((" ", i))  # it stays in the file the patch is applied to
            if k < len(added):
                i = added[k]
                if i in chosen:
                    entries.append(("+", i))
                elif reverse:
                    entries.append((" ", i))
    if all(mark == " " for mark, _ in entries):
        return None
    # The side the patch is applied to (the base) is the file as it is: its
    # numbers are git's, and the entries hold every one of the hunk's lines on
    # it. The other side's first line is ``delta`` lines away, plus what this
    # hunk's entries before a piece moved it. An empty side is numbered by the
    # line before it, as git does.
    base_mark = "-" if reverse else "+"  # the mark of lines that aren't on the base
    base_total = sum(mark != base_mark for mark, _ in entries)
    base_first = (hunk.new_start if reverse else hunk.old_start) + (base_total == 0)
    texts: list[str] = []
    moved = 0
    for first, end in _pieces(entries, context):
        before = entries[:first]
        old_before = sum(mark != "+" for mark, _ in before)
        new_before = sum(mark != "-" for mark, _ in before)
        on_base = base_first + (new_before if reverse else old_before)
        shift = delta + new_before - old_before
        old_first, new_first = (on_base - shift, on_base) if reverse else (on_base, on_base + shift)
        body: list[str] = []
        old_count = new_count = 0
        for mark, i in entries[first:end]:
            line = hunk.lines[i]
            body.append(f"{mark}{line.text}\n")
            if line.no_newline:
                body.append(NO_NEWLINE + "\n")
            old_count += mark != "+"
            new_count += mark != "-"
        old_start = old_first - (old_count == 0)
        new_start = new_first - (new_count == 0)
        texts.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@\n" + "".join(body))
        moved += new_count - old_count
    return texts, moved


def _pieces(entries: list[tuple[str, int]], context: int | None) -> list[tuple[int, int]]:
    """``(first, end)`` ranges of ``entries`` that keep at most ``context``
    context lines around each change: one range for all when it's None."""
    if context is None:
        return [(0, len(entries))]
    changed = [j for j, (mark, _) in enumerate(entries) if mark != " "]
    pieces: list[tuple[int, int]] = []
    first = max(0, changed[0] - context)
    for prev, nxt in zip(changed, changed[1:]):
        if nxt - prev - 1 > 2 * context:
            pieces.append((first, prev + context + 1))
            first = nxt - context
    pieces.append((first, min(len(entries), changed[-1] + context + 1)))
    return pieces


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
