"""The rows a diff is shown as, unified or side by side (spec §5.4).

Plain Python, no Qt: DiffView paints rows by their kind, and these functions
decide which rows there are, which changed rows pair up, and which of a pair's
words changed. The whole-file view (RZG-60) also places hunk headers its own
way and folds long unchanged stretches.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
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
    FOLD = "fold"  # whole file: "⋯ 24 unchanged lines" in place of them


FoldKey = tuple[int | None, int | None]  # a stretch's first folded line: its old and new numbers


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
    fold: FoldKey | None = field(default=None, compare=False)  # a fold row's stretch
    # The line's own text when ``text`` shows it otherwise (a CR as "␍"), for copying.
    raw: str | None = field(default=None, compare=False)
    hidden: tuple[DiffRow, ...] = field(default=(), compare=False)  # a fold row's rows


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


# ---- the whole file (RZG-60) -------------------------------------------------

FOLD_MIN_LINES = 20  # an unchanged stretch longer than this can be folded
FOLD_KEEP = 3  # unchanged lines kept in view next to a change when it is
_CHANGES = (RowKind.ADDED, RowKind.REMOVED, RowKind.FILLER)


def whole_rows(diff: FileDiff, headers: str) -> tuple[DiffRow, ...]:
    """``unified_rows`` with the hunk headers ``headers`` asks for (``HunkHeaders``,
    in ``app_actions``): "full", "slim" or "off"."""
    return _place_headers(headers, unified_rows(diff))[0]


def whole_split_rows(diff: FileDiff, headers: str
                     ) -> tuple[tuple[DiffRow, ...], tuple[DiffRow, ...]]:
    """``split_rows`` with the hunk headers ``headers`` asks for, on both sides."""
    left, right = _place_headers(headers, *split_rows(diff))
    return left, right


def _place_headers(headers: str,
                   *sides: Sequence[DiffRow]) -> list[tuple[DiffRow, ...]]:
    """Rows of one or two sides that line up: full headers stay; slim ones go
    before each hunk's first changed row (on either side); off, none."""
    if headers == "full":
        return [tuple(rows) for rows in sides]
    out: list[list[DiffRow]] = [[] for _ in sides]
    seen: set[int] = set()
    for n in range(len(sides[0])):
        if sides[0][n].kind is RowKind.HUNK:
            continue
        hunk = next((rows[n].hunk for rows in sides
                     if rows[n].kind in _CHANGES and rows[n].hunk is not None), None)
        if headers == "slim" and hunk is not None and hunk not in seen:
            seen.add(hunk)
            for rows in out:
                rows.append(DiffRow(RowKind.HUNK, "", hunk=hunk))
        for rows, side in zip(out, sides):
            rows.append(side[n])
    return [tuple(rows) for rows in out]


@dataclass(frozen=True)
class Stretch:
    """The foldable part of a long unchanged stretch: rows ``start`` to ``end``
    (not included), holding ``lines`` unchanged lines (and maybe a header)."""

    key: FoldKey
    start: int
    end: int
    lines: int


def stretches(rows: Sequence[DiffRow]) -> list[Stretch]:
    """The stretches of ``rows`` that can be folded: unchanged runs (hunk headers
    don't break one) of more than ``FOLD_MIN_LINES`` lines, less ``FOLD_KEEP``
    lines next to a change on either side. Side by side, either side's rows
    give the same stretches."""
    found: list[Stretch] = []
    context: list[int] = []
    seen_change = False

    def end_run(at_end: bool) -> None:
        if len(context) > FOLD_MIN_LINES:
            keep = context[FOLD_KEEP if seen_change else 0:
                           len(context) - (0 if at_end else FOLD_KEEP)]
            first = rows[keep[0]]
            found.append(Stretch((first.old_no, first.new_no), keep[0], keep[-1] + 1, len(keep)))
        context.clear()

    for n, row in enumerate(rows):
        if row.kind is RowKind.CONTEXT:
            context.append(n)
        elif row.kind is not RowKind.HUNK:
            end_run(at_end=row.kind is RowKind.NOTE and _only_notes(rows, n))
            seen_change = seen_change or row.kind in _CHANGES
    end_run(at_end=True)
    return found


def _only_notes(rows: Sequence[DiffRow], n: int) -> bool:
    return all(row.kind is RowKind.NOTE for row in rows[n:])


def fold(rows: Sequence[DiffRow], found: Sequence[Stretch], folded: Collection[FoldKey]
         ) -> tuple[tuple[DiffRow, ...], dict[int, FoldKey]]:
    """``rows`` with each stretch whose key is in ``folded`` as one fold row,
    and where the others start (the rows that show a fold mark), by key."""
    out: list[DiffRow] = []
    marks: dict[int, FoldKey] = {}
    starts = {s.start: s for s in found}
    n = 0
    while n < len(rows):
        stretch = starts.get(n)
        if stretch is not None and stretch.key in folded:
            out.append(DiffRow(RowKind.FOLD, f"⋯ {stretch.lines:,} unchanged lines",
                               fold=stretch.key, hidden=tuple(rows[n:stretch.end])))
            n = stretch.end
            continue
        if stretch is not None:
            marks[len(out)] = stretch.key
        out.append(rows[n])
        n += 1
    return tuple(out), marks


def _rows(line: DiffLine, hunk: int, index: int) -> list[DiffRow]:
    text = line.text.translate(_VISIBLE)
    row = DiffRow(_KINDS[line.kind], text, line.old_no, line.new_no, hunk, index,
                  raw=None if text == line.text else line.text)
    return [row, _NOTE] if line.no_newline else [row]


# ---- copying text (RZG-73) ---------------------------------------------------

_NOT_COPIED = (RowKind.HUNK, RowKind.FILLER, RowKind.NOTE)


@dataclass(frozen=True, order=True)
class TextPos:
    """A place in a view's text: a row, and a column in Python characters."""

    row: int
    col: int


def touched_rows(rows: Sequence[DiffRow], start: TextPos, end: TextPos) -> range:
    """The rows a text selection from ``start`` to ``end`` covers: not the row it
    ends on at column 0, nor the row it starts on at the end, when it goes on."""
    start, end = min(start, end), max(start, end)
    first, last = start.row, end.row
    if last > first and end.col == 0:
        last -= 1
    if last > first and start.col >= len(rows[first].text):
        first += 1
    return range(first, last + 1)


def copy_text(rows: Sequence[DiffRow], start: TextPos, end: TextPos) -> str:
    """The code a text selection from ``start`` to ``end`` copies: each row's own
    text, without hunk headers, filler and notes, and a fold row's hidden lines in
    its place. Within one row, what it shows (a hunk header too)."""
    start, end = min(start, end), max(start, end)
    if start.row == end.row:
        row = rows[start.row]
        if row.kind is RowKind.FOLD:
            return _hidden_text(row) if start.col < end.col else ""
        return _raw(row)[start.col:end.col]
    touched = touched_rows(rows, start, end)
    parts: list[str] = []
    for n in range(start.row, end.row + 1):
        row = rows[n]
        if row.kind in _NOT_COPIED:
            continue
        if row.kind is RowKind.FOLD:
            parts.append(_hidden_text(row) if n in touched else "")
            continue
        text = _raw(row)
        parts.append(text[start.col if n == start.row else 0:
                          end.col if n == end.row else len(text)])
    return "\n".join(parts)


def _raw(row: DiffRow) -> str:
    return row.raw if row.raw is not None else row.text


def _hidden_text(row: DiffRow) -> str:
    return "\n".join(_raw(r) for r in row.hidden if r.kind not in _NOT_COPIED)


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
