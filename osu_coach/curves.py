"""Slider path construction.

Every curve type is flattened to a polyline, which is then cut (or extended
along its last segment) to the slider's pixel length, as the game does.
"""

import math

Point = tuple[float, float]


def _dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _bezier(points: list[Point]) -> list[Point]:
    if len(points) < 2:
        return list(points)
    control_len = sum(_dist(points[i], points[i + 1]) for i in range(len(points) - 1))
    steps = max(2, min(1000, int(control_len / 2)))
    out = []
    for s in range(steps + 1):
        t = s / steps
        pts = list(points)
        while len(pts) > 1:  # de Casteljau
            pts = [(pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t,
                    pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t) for i in range(len(pts) - 1)]
        out.append(pts[0])
    return out


def _multi_bezier(points: list[Point]) -> list[Point]:
    # A repeated control point ("red anchor") splits the curve into segments.
    out: list[Point] = []
    segment = [points[0]]
    for p in points[1:]:
        if p == segment[-1]:
            out.extend(_bezier(segment))
            segment = [p]
        else:
            segment.append(p)
    out.extend(_bezier(segment))
    return out


def _catmull(points: list[Point]) -> list[Point]:
    out = []
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else (2 * p2[0] - p1[0], 2 * p2[1] - p1[1])
        for s in range(50):
            t = s / 50
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t
                       + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                       + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3)
                for k in range(2)))
    out.append(points[-1])
    return out


def _perfect(points: list[Point]) -> list[Point] | None:
    (ax, ay), (bx, by), (cx, cy) = points
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-6:
        return None  # collinear: fall back to bezier
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    radius = math.hypot(ax - ux, ay - uy)

    tau = 2 * math.pi
    start = math.atan2(ay - uy, ax - ux)
    mid = (math.atan2(by - uy, bx - ux) - start) % tau
    end = (math.atan2(cy - uy, cx - ux) - start) % tau
    # Sweep in whichever direction passes through the middle point.
    sweep = end if mid < end else end - tau
    steps = max(2, int(abs(sweep) * radius / 2))
    return [(ux + radius * math.cos(start + sweep * s / steps),
             uy + radius * math.sin(start + sweep * s / steps)) for s in range(steps + 1)]


class SliderPath:
    def __init__(self, curve_type: str, points: list[Point], pixel_length: float):
        poly = None
        if curve_type == "L":
            poly = list(points)
        elif curve_type == "P" and len(points) == 3:
            poly = _perfect(points)
        elif curve_type == "C":
            poly = _catmull(points)
        if poly is None:
            poly = _multi_bezier(points)

        # Drop zero-length segments.
        cleaned = [poly[0]]
        for p in poly[1:]:
            if _dist(p, cleaned[-1]) > 1e-9:
                cleaned.append(p)
        self.points = cleaned

        self.cumulative = [0.0]
        for i in range(1, len(cleaned)):
            self.cumulative.append(self.cumulative[-1] + _dist(cleaned[i - 1], cleaned[i]))

        self.curve_length = self.cumulative[-1]
        self.length = pixel_length if pixel_length > 0 else self.curve_length

    def position_at_distance(self, d: float) -> Point:
        pts, cum = self.points, self.cumulative
        if len(pts) == 1:
            return pts[0]
        d = max(0.0, d)
        # Binary search for the segment containing d; extrapolate on the last one.
        lo, hi = 0, len(cum) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if cum[mid] <= d:
                lo = mid
            else:
                hi = mid
        seg_len = cum[hi] - cum[lo]
        t = (d - cum[lo]) / seg_len if seg_len > 0 else 0.0
        a, b = pts[lo], pts[hi]
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    def position_at(self, progress: float) -> Point:
        """Position at progress in [0, 1] along the (length-limited) path."""
        return self.position_at_distance(min(1.0, max(0.0, progress)) * self.length)


class PathLength:
    """A slider path once its play is judged and its features read (collect.slim): only its length, all that the
    statistics over many plays use of it."""
    __slots__ = ("length",)

    def __init__(self, length: float):
        self.length = length
