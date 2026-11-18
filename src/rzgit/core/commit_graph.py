"""History's branch graph: which lane each commit is in, and the lines between
them (spec §5.4, RZG-58). Plain Python, no Qt.

Each lane holds the commit it waits for, and no two lanes wait for the same one.
Going down the commits in order (every commit above its parents):

- A commit takes the lane waiting for it. A commit no lane waits for (a branch
  tip) opens the leftmost free lane.
- Its first parent continues in its lane. If another lane already waits for that
  parent (a fork, seen from below), the two join at once, into the left one.
- Each other parent (a merge) joins the lane already waiting for it, or opens the
  leftmost free one.

Lanes never move sideways, and a free lane is reused. A new lane takes the next
colour in turn that no open lane has, and keeps it until it ends.

Rows are worked out when first asked for, and kept, so an appended page carries
on from the open lanes and the rows already shown never change.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from rzgit.git.git_models import Commit

# A line through half a row: (lane at its top, lane at its bottom, colour). In the
# top half it runs from the row's top edge to the dot's height; in the bottom half
# from the dot's height to the bottom edge. Its lanes differ where it changes lane.
Line = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class GraphRow:
    lane: int  # the commit's lane
    color: int  # its colour: an index into the theme's lane colours
    upper: tuple[Line, ...]  # the row's top half: the lane into the dot, and lanes passing by
    lower: tuple[Line, ...]  # its bottom half: the dot to its parents' lanes, and lanes passing by

    @property
    def width(self) -> int:
        """How many lanes the row spans."""
        return 1 + max((self.lane, *(max(a, b) for a, b, _c in (*self.upper, *self.lower))))


class CommitGraph:
    """The graph rows of ``commits``, in their order. ``colors`` is how many lane
    colours there are, so a new lane can take one no open lane has."""

    def __init__(self, commits: Iterable[Commit] = (), colors: int = 8):
        self._commits: list[Commit] = list(commits)
        self._colors = max(1, colors)
        self._rows: list[GraphRow] = []
        self._lanes: list[str | None] = []  # the sha each lane waits for; None: free
        self._lane_colors: list[int] = []
        self._next_color = 0

    def __len__(self) -> int:
        return len(self._commits)

    def extend(self, commits: Iterable[Commit]) -> None:
        """Add commits after the last (the next page): the lanes carry on."""
        self._commits.extend(commits)

    def row(self, index: int) -> GraphRow:
        """The row of the commit at ``index``, worked out (with those above it)
        if it hasn't been yet."""
        if not 0 <= index < len(self._commits):
            raise IndexError(index)
        rows = self._rows
        while len(rows) <= index:
            rows.append(self._step(self._commits[len(rows)]))
        return rows[index]

    @property
    def rows_built(self) -> int:
        """How many rows have been worked out so far."""
        return len(self._rows)

    # ---- the layout ------------------------------------------------------------

    def _step(self, commit: Commit) -> GraphRow:
        lanes, colors = self._lanes, self._lane_colors
        lane = self._waiting_for(commit.sha)
        # Lines only change lane in a bottom half: the top half is straight down.
        upper = tuple((i, i, colors[i]) for i, s in enumerate(lanes) if s is not None)
        if lane < 0:  # a branch tip
            lane = self._free_lane()
            colors[lane] = self._new_color(lane)
        color = colors[lane]
        lanes[lane] = None
        lower: list[Line] = []
        done: set[int] = set()  # lanes this row has drawn the bottom half of
        parents = tuple(dict.fromkeys(commit.parents))  # a parent named twice is one line
        if parents:
            first = parents[0]
            other = self._waiting_for(first)
            if other < 0:  # the first parent continues in this lane
                lanes[lane] = first
                lower.append((lane, lane, color))
                done.add(lane)
            elif other < lane:  # this lane joins the one left of it
                lower.append((lane, other, colors[other]))
            else:  # the lane right of it joins this one
                lanes[lane], lanes[other] = first, None
                lower += [(lane, lane, color), (other, lane, colors[other])]
                done |= {lane, other}
        for parent in parents[1:]:
            target = self._waiting_for(parent)
            if target < 0:
                target = self._free_lane()
                lanes[target] = parent
                colors[target] = self._new_color(target)
                done.add(target)
            lower.append((lane, target, colors[target]))
        lower.extend((i, i, colors[i]) for i, s in enumerate(lanes)
                     if s is not None and i not in done)
        while lanes and lanes[-1] is None:
            lanes.pop()
            colors.pop()
        return GraphRow(lane, color, upper, tuple(lower))

    def _waiting_for(self, sha: str) -> int:
        """The lane waiting for ``sha``, or -1."""
        try:
            return self._lanes.index(sha)
        except ValueError:
            return -1

    def _free_lane(self) -> int:
        """The leftmost free lane, made if there is none. It's marked taken (with
        an empty sha) until the caller says what it waits for."""
        lanes = self._lanes
        for i, s in enumerate(lanes):
            if s is None:
                lanes[i] = ""
                return i
        lanes.append("")
        self._lane_colors.append(0)
        return len(lanes) - 1

    def _new_color(self, lane: int) -> int:
        """The next colour in turn that no other open lane has; failing that, one
        its neighbours don't have; failing that, the next in turn."""
        lanes, colors = self._lanes, self._lane_colors
        used = {colors[i] for i, s in enumerate(lanes) if i != lane and s is not None}
        near = {colors[i] for i in (lane - 1, lane + 1)
                if 0 <= i < len(lanes) and lanes[i] is not None}
        turn = [(self._next_color + k) % self._colors for k in range(self._colors)]
        color = next((c for c in turn if c not in used),
                     next((c for c in turn if c not in near), turn[0]))
        self._next_color = (color + 1) % self._colors
        return color
