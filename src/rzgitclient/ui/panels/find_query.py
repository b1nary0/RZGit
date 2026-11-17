"""Find in a diff (spec §5.4): what is searched for, and where it is in a
view's rows. Plain Python, no Qt."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterator, Sequence
from itertools import islice
from dataclasses import dataclass
from typing import NamedTuple

from rzgitclient.ui.panels.diff_rows import DiffRow, RowKind

# The rows that hold code: hunk headers, filler and notes are never searched.
_SEARCHED = frozenset({RowKind.CONTEXT, RowKind.ADDED, RowKind.REMOVED})
# Find stops here: Python's re takes ~50 ms per 100,000 matches, on the UI thread.
MAX_MATCHES = 10_000
_WORD_CHAR = re.compile(r"\w")


@dataclass(frozen=True)
class FindQuery:
    text: str = ""
    case: bool = False  # match case
    word: bool = False  # whole words only
    regex: bool = False

    @property
    def joinable(self) -> bool:
        """The pattern can't match a line break, so it can search every row at once."""
        return not self.regex

    def pattern(self) -> re.Pattern[str] | None:
        """The compiled query; None when there's nothing to find. Raises
        ``ValueError`` for a regex that doesn't compile."""
        if not self.text:
            return None
        body = self.text if self.regex else re.escape(self.text)
        if self.word:
            body = rf"(?<!\w)(?:{body})(?!\w)"
        try:
            return re.compile(body, 0 if self.case else re.IGNORECASE)
        except re.error as e:
            raise ValueError(str(e)) from e


class FindMatch(NamedTuple):  # a tuple: thousands are made at a time
    """A match in a view's rows: on row ``row``, from ``start`` to ``end``
    (Python characters). In a fold row's hidden lines, ``hidden`` is the
    index of the line among them; otherwise None."""

    row: int
    start: int
    end: int
    hidden: int | None = None

    @property
    def order(self) -> tuple[int, int, int]:
        """Where it comes in the rows: a fold row's hidden lines in their order."""
        return self.row, -1 if self.hidden is None else self.hidden, self.start


def find_matches(rows: Sequence[DiffRow], pattern: re.Pattern[str] | None,
                 joinable: bool = False, limit: int | None = None) -> list[FindMatch]:
    """Every match of ``pattern`` in ``rows``, in order (none for no pattern), or
    the first ``limit``. Empty matches are skipped; a match never spans rows. With
    ``joinable`` (the pattern can't match a line break: ``FindQuery.joinable``),
    the rows are searched as one text, which is faster for a long diff."""
    if pattern is None:
        return []
    lines = [(n, None, row.text) for n, row in enumerate(rows) if row.kind in _SEARCHED]
    if any(row.kind is RowKind.FOLD for row in rows):
        lines += [(n, i, line.text) for n, row in enumerate(rows) if row.kind is RowKind.FOLD
                  for i, line in enumerate(row.hidden) if line.kind in _SEARCHED]
        lines.sort(key=lambda line: (line[0], -1 if line[1] is None else line[1]))
    if not joinable:
        return list(islice((FindMatch(n, s, e, i) for n, i, text in lines
                            for s, e in _spans(pattern, text)), limit))
    starts, at = [], 0
    for _n, _i, text in lines:
        starts.append(at)
        at += len(text) + 1
    found = []
    for s, e in islice(_spans(pattern, "\n".join(text for _n, _i, text in lines)), limit):
        k = bisect_right(starts, s) - 1
        n, i, _text = lines[k]
        found.append(FindMatch(n, s - starts[k], e - starts[k], i))
    return found


def word_around(text: str, start: int, end: int) -> tuple[int, int]:
    """The match ``start``..``end`` of ``text`` widened to the whole word it is part
    of: an edge between two word characters moves out to the end of their run."""
    while 0 < start < len(text) and _is_word(text[start - 1]) and _is_word(text[start]):
        start -= 1
    while 0 < end < len(text) and _is_word(text[end - 1]) and _is_word(text[end]):
        end += 1
    return start, end


def _is_word(ch: str) -> bool:
    return _WORD_CHAR.match(ch) is not None  # as the whole-word option's \w


def _spans(pattern: re.Pattern[str], text: str) -> Iterator[tuple[int, int]]:
    for match in pattern.finditer(text):
        if match.end() > match.start():
            yield match.start(), match.end()
