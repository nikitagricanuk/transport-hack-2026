"""Small planar geometry helpers.

The editor works on city-sized extracts, so a local equirectangular projection
around the document centre is accurate to centimetres and needs no pyproj.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_M_PER_DEG_LAT = 110_540.0
_M_PER_DEG_LON_EQ = 111_320.0


@dataclass(frozen=True)
class LocalProjection:
    lat0: float
    lon0: float

    @property
    def kx(self) -> float:
        return _M_PER_DEG_LON_EQ * math.cos(math.radians(self.lat0))

    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        return ((lon - self.lon0) * self.kx, (lat - self.lat0) * _M_PER_DEG_LAT)

    def to_latlon(self, x: float, y: float) -> tuple[float, float]:
        return (self.lat0 + y / _M_PER_DEG_LAT, self.lon0 + x / self.kx)


def dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def segment_intersection(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> tuple[float, float] | None:
    """Parameters ``(t, u)`` where segment ``ab`` meets ``cd``, both in ``[0, 1]``.

    ``None`` when the segments are parallel or do not meet. A shared endpoint is
    a real hit (``t`` or ``u`` is 0 or 1); callers decide whether that counts as
    a new junction.
    """
    ax, ay = a
    bx, by = b
    cx, cy = c
    dx, dy = d
    rx, ry = bx - ax, by - ay
    sx, sy = dx - cx, dy - cy
    denom = rx * sy - ry * sx
    if abs(denom) < 1e-9:
        return None
    qpx, qpy = cx - ax, cy - ay
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * ry - qpy * rx) / denom
    eps = 1e-9
    if -eps <= t <= 1.0 + eps and -eps <= u <= 1.0 + eps:
        return (min(1.0, max(0.0, t)), min(1.0, max(0.0, u)))
    return None


def project_on_segment(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> tuple[tuple[float, float], float, float]:
    """Return (closest point, t in [0,1], distance) of p onto segment ab."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0:
        return a, 0.0, dist(p, a)
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / seg2
    t = max(0.0, min(1.0, t))
    q = (ax + t * dx, ay + t * dy)
    return q, t, dist(p, q)


class GridIndex:
    """Uniform grid spatial hash for points or segment bounding boxes."""

    def __init__(self, cell: float = 20.0) -> None:
        self.cell = cell
        self.cells: dict[tuple[int, int], list] = {}

    def _key(self, x: float, y: float) -> tuple[int, int]:
        return (int(math.floor(x / self.cell)), int(math.floor(y / self.cell)))

    def insert_point(self, x: float, y: float, item) -> None:
        self.cells.setdefault(self._key(x, y), []).append(item)

    def insert_box(self, x0: float, y0: float, x1: float, y1: float, item) -> None:
        kx0, ky0 = self._key(min(x0, x1), min(y0, y1))
        kx1, ky1 = self._key(max(x0, x1), max(y0, y1))
        for i in range(kx0, kx1 + 1):
            for j in range(ky0, ky1 + 1):
                self.cells.setdefault((i, j), []).append(item)

    def query(self, x: float, y: float, radius: float) -> list:
        kx0, ky0 = self._key(x - radius, y - radius)
        kx1, ky1 = self._key(x + radius, y + radius)
        seen: list = []
        ids: set[int] = set()
        for i in range(kx0, kx1 + 1):
            for j in range(ky0, ky1 + 1):
                for item in self.cells.get((i, j), ()):
                    if id(item) not in ids:
                        ids.add(id(item))
                        seen.append(item)
        return seen
