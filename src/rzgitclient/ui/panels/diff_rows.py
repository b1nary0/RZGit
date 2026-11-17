"""The rows a diff is shown as, unified or side by side (spec §5.4).

Plain Python, no Qt: DiffView paints rows by their kind, and these functions
decide which rows there are, which changed rows pair up, and which of a pair's
words changed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum

from rzgitclient.git.git_models import DiffLine, FileDiff, LineKind

NO_NEWLINE = "\\ No newline at end of file"


class RowKind(StrEnum):
    HUNK = "hunk"  # the "@@ -1,4 +1,5 @@" header
    CONTEXT = "context"
    ADDED = "added"
    REMOVED = "removed"
    FILLER = "filler"  # side by side: where the other side has a line
    NOTE = "note"  # "\ No newline at end of file"


@dataclass(frozen=True)
class DiffRow:
    kind: RowKind
    text: str = ""
    old_no: int | None = None
    new_no: int | None = None
    # Where it is in the FileDiff: a hunk header's hunk; a line's hunk and line.
    # Position, not content, so rows still compare by what they show.
    hunk: int | None = field(default=None, compare=False)
    line: int | None = field(default=None, compare=False)


_FILLER = DiffRow(RowKind.FILLER)
_NOTE = DiffRow(RowKind.NOTE, NO_NEWLINE)
_KINDS = {LineKind.CONTEXT: RowKind.CONTEXT, LineKind.ADDED: RowKind.ADDED,
          LineKind.REMOVED: RowKind.REMOVED}
# A text view starts a new row at these; show them as symbols instead, so rows
# and lines stay one to one (a CRLF line end shows as "␍").
_VISIBLE = str.maketrans({"\r": "␍", " ": "␤", " ": "¶"})


def unified_rows(diff: FileDiff) -> tuple[DiffRow, ...]:
    """One column: each line once, with its old and new numbers."""
    rows: list[DiffRow] = []
    for h, hunk in enumerate(diff.hunks):
        rows.append(DiffRow(RowKind.HUNK, hunk.header, hunk=h))
        for i, line in enumerate(hunk.lines):
            rows.extend(_rows(line, h, i))
    return tuple(rows)


def whole_file(diff: FileDiff) -> str | None:
    """"added" or "deleted" when ``diff`` adds or deletes the whole file, else None.

    Told from the hunks alone, so it holds for any source: every hunk has only
    added lines and an old start of 0, or only removed lines and a new start of 0.
    """
    if not diff.hunks:
        return None
    for kind, start, name in ((LineKind.ADDED, "old_start", "added"),
                              (LineKind.REMOVED, "new_start", "deleted")):
        if all(getattr(h, start) == 0 and h.lines and all(line.kind is kind for line in h.lines)
               for h in diff.hunks):
            return name
    return None


def split_rows(diff: FileDiff) -> tuple[tuple[DiffRow, ...], tuple[DiffRow, ...]]:
    """Old on the left, new on the right; both sides have the same number of rows.

    A run of removed lines is paired with the run of added lines after it,
    and the shorter side is padded with filler.
    """
    left: list[DiffRow] = []
    right: list[DiffRow] = []
    removed: list[DiffRow] = []
    added: list[DiffRow] = []

    def flush() -> None:
        for i in range(max(len(removed), len(added))):
            left.append(removed[i] if i < len(removed) else _FILLER)
            right.append(added[i] if i < len(added) else _FILLER)
        removed.clear()
        added.clear()

    for h, hunk in enumerate(diff.hunks):
        header = DiffRow(RowKind.HUNK, hunk.header, hunk=h)
        left.append(header)
        right.append(header)
        for i, line in enumerate(hunk.lines):
            if line.kind is LineKind.REMOVED:
                if added:  # removed after added: a new pair of runs
                    flush()
                removed.extend(_rows(line, h, i))
            elif line.kind is LineKind.ADDED:
                added.extend(_rows(line, h, i))
            else:
                flush()
                rows = _rows(line, h, i)
                left.extend(rows)
                right.extend(rows)
        flush()
    return tuple(left), tuple(right)


def _rows(line: DiffLine, hunk: int, index: int) -> list[DiffRow]:
    row = DiffRow(_KINDS[line.kind], line.text.translate(_VISIBLE), line.old_no, line.new_no,
                  hunk, index)
    return [row, _NOTE] if line.no_newline else [row]


# ---- word highlights (RZG-22) ------------------------------------------------

WORD_MIN_RATIO = 0.5  # below this, a pair has too little in common to compare by words
WORD_MAX_LENGTH = 1000  # longer lines aren't compared by words
_TOKEN = re.compile(r"\w+|\s+|[^\w\s]+")

Spans = tuple[tuple[int, int], ...]  # (start, end) character ranges, in order


def unified_partners(rows: Sequence[DiffRow]) -> dict[int, str]:
    """Each paired changed row's number → its partner's text, in unified rows.

    As in side by side: the nth removed line of a run pairs with the nth added
    line of the run after it.
    """
    partners: dict[int, str] = {}
    removed: list[int] = []
    added: list[int] = []

    def flush() -> None:
        for r, a in zip(removed, added):
            partners[r] = rows[a].text
            partners[a] = rows[r].text
        removed.clear()
        added.clear()

    for n, row in enumerate(rows):
        if row.kind is RowKind.REMOVED:
            if added:
                flush()
            removed.append(n)
        elif row.kind is RowKind.ADDED:
            added.append(n)
        elif row.kind is not RowKind.NOTE:  # a note sits inside a run
            flush()
    flush()
    return partners


def split_partners(left: Sequence[DiffRow],
                   right: Sequence[DiffRow]) -> tuple[dict[int, str], dict[int, str]]:
    """For side by side: each side's paired rows → the other side's text."""
    old: dict[int, str] = {}
    new: dict[int, str] = {}
    for n, (a, b) in enumerate(zip(left, right)):
        if a.kind is RowKind.REMOVED and b.kind is RowKind.ADDED:
            old[n], new[n] = b.text, a.text
    return old, new


def word_spans(old: str, new: str) -> tuple[Spans, Spans] | None:
    """The changed character ranges of ``old`` and of ``new``, or None when the
    two have too little in common (or are too long) to compare by words."""
    if len(old) > WORD_MAX_LENGTH or len(new) > WORD_MAX_LENGTH:
        return None
    a, b = _tokens(old), _tokens(new)
    matcher = SequenceMatcher(None, [t for t, _ in a], [t for t, _ in b], autojunk=False)
    if matcher.ratio() < WORD_MIN_RATIO:
        return None
    old_spans: list[tuple[int, int]] = []
    new_spans: list[tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        _add(old_spans, a, i1, i2)
        _add(new_spans, b, j1, j2)
    return tuple(old_spans), tuple(new_spans)


def _tokens(text: str) -> list[tuple[str, int]]:
    """``(token, start)``: runs of word characters, of whitespace, of anything else."""
    return [(m.group(), m.start()) for m in _TOKEN.finditer(text)]


def _add(spans: list[tuple[int, int]], tokens: list[tuple[str, int]], i1: int, i2: int) -> None:
    """Add tokens ``i1:i2`` as one range, joined to the last one if they touch."""
    if i1 == i2:
        return
    start = tokens[i1][1]
    end = tokens[i2 - 1][1] + len(tokens[i2 - 1][0])
    if spans and spans[-1][1] == start:
        spans[-1] = (spans[-1][0], end)
    else:
        spans.append((start, end))
