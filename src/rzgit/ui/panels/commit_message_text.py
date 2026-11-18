"""A hard-wrapped commit message body, as paragraphs (spec §5.4, Commit Details).

Commit messages are wrapped at about 72 characters by their authors. A narrow
panel would wrap those lines again, into ragged text, so ``reflow`` joins the
lines of each paragraph and leaves the wrapping to the label. Plain Python.
"""

from __future__ import annotations

import re
from enum import Enum, auto

SHORT = 50  # a line shorter than this, then a capitalised one: a headline, kept

_LIST_ITEM = re.compile(r"^(\s*)([-*+•]|\d+[.)])\s+")
_TRAILER = re.compile(r"^[A-Za-z][A-Za-z0-9-]*: \S")
_INDENT = re.compile(r"^(?: {2,}|\t)")


class _Kind(Enum):
    BLANK = auto()
    LIST_ITEM = auto()
    INDENTED = auto()
    TRAILER = auto()
    PROSE = auto()


def _kinds(lines: list[str]) -> list[_Kind]:
    """Each line's kind. A ``Token: value`` line is a trailer only in a paragraph of
    nothing else, as git reads trailers: in prose, "thread: positions only" is a
    wrapped line that happens to start with a word and a colon."""
    kinds = []
    for line in lines:
        if not line.strip():
            kinds.append(_Kind.BLANK)
        elif _LIST_ITEM.match(line):
            kinds.append(_Kind.LIST_ITEM)
        elif _INDENT.match(line):
            kinds.append(_Kind.INDENTED)
        else:
            kinds.append(_Kind.PROSE)
    start = 0
    for end in range(len(lines) + 1):
        if end == len(lines) or kinds[end] is _Kind.BLANK:
            paragraph = range(start, end)
            if paragraph and all(_TRAILER.match(lines[i]) for i in paragraph):
                for i in paragraph:
                    kinds[i] = _Kind.TRAILER
            start = end + 1
    return kinds


def reflow(body: str) -> str:
    """``body`` with the lines of each prose paragraph, and of each wrapped list
    item, joined by one space. Blank, indented and trailer lines, list items and
    short headlines keep their line breaks: when in doubt, the break stays."""
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    kinds: list[_Kind] = []  # of the lines in ``out``
    item_indent: int | None = None  # the text column of the list item being continued
    last = ""  # the line before, as written (not as joined)
    for line, kind in zip(lines, _kinds(lines)):
        previous = kinds[-1] if kinds else None
        before, last = last, line
        if kind is _Kind.INDENTED and item_indent is not None and (
                len(line) - len(line.lstrip()) == item_indent):
            out[-1] = f"{out[-1].rstrip()} {line.strip()}"  # the item wraps on
            continue
        if (kind is _Kind.PROSE and previous is _Kind.PROSE
                and not (len(before.rstrip()) < SHORT and line[:1].isupper())):
            out[-1] = f"{out[-1].rstrip()} {line.strip()}"
            continue
        match = _LIST_ITEM.match(line) if kind is _Kind.LIST_ITEM else None
        item_indent = match.end() if match is not None else None  # any other line ends it
        out.append(line if kind in (_Kind.INDENTED, _Kind.BLANK) else line.rstrip())
        kinds.append(kind)
    return "\n".join(out)
