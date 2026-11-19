"""The outlines of the usage guide's screenshots (RZG-95, ARCHITECTURE.md §10.1).

Subjects that overlap, such as a menu over its panel, get one outline around
their union instead of a rectangle each with crossing lines. Every corner of
that outline is rounded, inside corners too. No side effects on import, unlike
make_screenshots.py, so the tests can use it.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRect, QRectF
from PySide6.QtGui import QPainterPath

KAPPA = 0.5523  # a cubic Bézier's handle length for a quarter circle, as a share of its radius


def overlap_groups(rects: Sequence[QRect]) -> list[list[QRect]]:
    """``rects`` in groups that overlap, directly or through others, in the order
    each group's first rectangle comes. Rectangles that only touch, or sit a
    pixel apart (panels in a split), stay apart."""
    parent = list(range(len(rects)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(rects):
        for j in range(i + 1, len(rects)):
            if a.intersects(rects[j]):
                parent[root(j)] = root(i)
    groups: dict[int, list[QRect]] = {}
    for i, rect in enumerate(rects):
        groups.setdefault(root(i), []).append(rect)
    return list(groups.values())


def outline_path(rects: Sequence[QRect], radius: float) -> QPainterPath:
    """The union of ``rects`` as one outline, with every corner rounded by
    ``radius``, or less where an edge is too short for it."""
    union = QPainterPath()
    for rect in rects:
        piece = QPainterPath()
        piece.addRect(QRectF(rect))
        union = union.united(piece)  # one outline (simplified() would leave seams)
    path = QPainterPath()
    for polygon in union.toSubpathPolygons():
        corners = _corners([QPointF(p) for p in polygon])
        if len(corners) >= 3:
            _add_rounded(path, corners, radius)
    return path


def _corners(points: list[QPointF]) -> list[QPointF]:
    """``points`` (a closed polygon) without the repeated last point and without
    points in the middle of a straight edge."""
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    corners = []
    for i, p in enumerate(points):
        before, after = points[i - 1], points[(i + 1) % len(points)]
        cross = (p.x() - before.x()) * (after.y() - p.y()) - (p.y() - before.y()) * (after.x() - p.x())
        if abs(cross) > 1e-9:
            corners.append(p)
    return corners


def _add_rounded(path: QPainterPath, corners: list[QPointF], radius: float) -> None:
    """Add the closed polygon ``corners`` to ``path``, each corner a quarter-circle
    curve that starts and ends ``radius`` (or half the shorter edge) from it."""
    turns = []
    for i, corner in enumerate(corners):
        before, after = corners[i - 1], corners[(i + 1) % len(corners)]
        r = min(radius, _length(corner - before) / 2, _length(after - corner) / 2)
        start = corner + (before - corner) * (r / _length(before - corner))
        end = corner + (after - corner) * (r / _length(after - corner))
        turns.append((start, corner, end))
    path.moveTo(turns[0][0])
    for start, corner, end in turns:
        path.lineTo(start)
        path.cubicTo(start + (corner - start) * KAPPA, end + (corner - end) * KAPPA, end)
    path.closeSubpath()


def _length(p: QPointF) -> float:
    return (p.x() ** 2 + p.y() ** 2) ** 0.5
