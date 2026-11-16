"""The rows a diff is shown as, unified or side by side (spec §5.4).

Plain Python, no Qt: DiffView paints rows by their kind, and these functions
decide which rows there are.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    for hunk in diff.hunks:
        rows.append(DiffRow(RowKind.HUNK, hunk.header))
        for line in hunk.lines:
            rows.extend(_rows(line))
    return tuple(rows)


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

    for hunk in diff.hunks:
        header = DiffRow(RowKind.HUNK, hunk.header)
        left.append(header)
        right.append(header)
        for line in hunk.lines:
            if line.kind is LineKind.REMOVED:
                if added:  # removed after added: a new pair of runs
                    flush()
                removed.extend(_rows(line))
            elif line.kind is LineKind.ADDED:
                added.extend(_rows(line))
            else:
                flush()
                rows = _rows(line)
                left.extend(rows)
                right.extend(rows)
        flush()
    return tuple(left), tuple(right)


def _rows(line: DiffLine) -> list[DiffRow]:
    row = DiffRow(_KINDS[line.kind], line.text.translate(_VISIBLE), line.old_no, line.new_no)
    return [row, _NOTE] if line.no_newline else [row]
