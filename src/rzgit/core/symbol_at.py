"""The symbol at a column of a line, and where it is in a file (spec §8, RZG-75).

Go to Definition in VS Code takes the identifier under the text cursor of a
diff, and looks for it in the working-tree file near the diff's line, as the
file may have moved on since. Plain Python.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

SYMBOL = re.compile(r"[^\W\d]\w*")  # an identifier: a letter or _, then word characters
NEAR_LINES = 20  # how far from the diff's line the symbol is looked for


def symbol_at(text: str, col: int) -> tuple[int, str] | None:
    """The identifier of ``text`` that column ``col`` (0-based) is in or next
    to, as (its start, it); None if there's none."""
    for match in SYMBOL.finditer(text):
        if match.start() <= col <= match.end():
            return match.start(), match.group()
        if match.start() > col:
            break
    return None


def utf16_col(text: str, i: int) -> int:
    """The 1-based UTF-16 column of ``text[i]``, as VS Code counts."""
    return len(text[:i].encode("utf-16-le")) // 2 + 1


def find_symbol_near(lines: Sequence[str], line: int | None, symbol: str,
                     radius: int = NEAR_LINES) -> tuple[int, int] | None:
    """Where ``symbol`` is as a whole word in ``lines``, as (1-based line,
    1-based UTF-16 column): on ``line`` (1-based), else the nearest line within
    ``radius`` (the one above first). None if it isn't there, or no line is given."""
    if line is None or not symbol:
        return None
    word = re.compile(rf"(?<!\w){re.escape(symbol)}(?!\w)")
    for distance in range(radius + 1):
        for n in (line - distance, line + distance) if distance else (line,):
            if 1 <= n <= len(lines):
                found = word.search(lines[n - 1])
                if found is not None:
                    return n, utf16_col(lines[n - 1], found.start())
    return None
