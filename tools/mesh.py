"""
mesh.py -- a small, dependency-free triangle-mesh kernel for Claude Micro.

Design goal: every solid this module produces is a *closed, orientable, 2-manifold*
surface, so the STL files are directly printable and can be verified by
`Mesh.validate()` rather than trusted.

The kernel deliberately avoids general CSG. Instead every part is built from
operations that are watertight by construction:

    * extrude(...)   -- a 2D polygon (with holes) swept along +Z
    * loft(...)      -- two matching polygons at different Z, skinned
    * lathe(...)     -- an (r, z) profile revolved around the Z axis

The hard part is capping an extrusion whose outline has holes. We do that with a
trapezoidal *scanline decomposition*: the outline is cut into horizontal bands at
every vertex Y, each band is filled with x-monotone strips, and then every
boundary edge is re-subdivided so that the caps and the side walls share exactly
the same vertices (no T-junctions). `validate()` proves the result.
"""

from __future__ import annotations

import math
import struct
from typing import Iterable, Sequence

# Coordinates are snapped to this grid before topology comparisons. 1 nm is far
# below any tolerance we care about for a 3D print but comfortably above float
# noise from the interpolation arithmetic.
EPS_Q = 1e-6
Pt2 = tuple[float, float]
Pt3 = tuple[float, float, float]


def q(v: float) -> float:
    """Snap a coordinate onto the topology grid."""
    return round(v / EPS_Q) * EPS_Q


def qp(p: Sequence[float]) -> Pt3:
    return (q(p[0]), q(p[1]), q(p[2]))


# --------------------------------------------------------------------------
# 2D helpers
# --------------------------------------------------------------------------

def signed_area(poly: Sequence[Pt2]) -> float:
    a = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a * 0.5


def ensure_ccw(poly: Sequence[Pt2]) -> list[Pt2]:
    return list(poly) if signed_area(poly) >= 0 else list(reversed(poly))


def ensure_cw(poly: Sequence[Pt2]) -> list[Pt2]:
    return list(poly) if signed_area(poly) <= 0 else list(reversed(poly))


def dedupe(poly: Sequence[Pt2], tol: float = 1e-9) -> list[Pt2]:
    """Snap a contour onto the topology grid and drop repeated points.

    Snapping *here* rather than at mesh-build time is what makes the scanline
    cap conformal with the side walls: once vertex Y values are exactly on the
    grid, evaluating an edge at one of its own endpoint Y values reproduces
    that endpoint's X bit-for-bit.
    """
    out: list[Pt2] = []
    for p in poly:
        pt = (q(p[0]), q(p[1]))
        if not out or abs(pt[0] - out[-1][0]) > tol or abs(pt[1] - out[-1][1]) > tol:
            out.append(pt)
    while len(out) > 1 and abs(out[0][0] - out[-1][0]) <= tol and abs(out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out


def is_convex(poly: Sequence[Pt2]) -> bool:
    n = len(poly)
    if n < 3:
        return False
    sign = 0
    for i in range(n):
        a, b, c = poly[i], poly[(i + 1) % n], poly[(i + 2) % n]
        cr = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
        if abs(cr) < 1e-12:
            continue
        s = 1 if cr > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


def fan_triangulate(poly: Sequence[Pt2]) -> list[tuple[Pt2, Pt2, Pt2]]:
    """Cap a convex or star-shaped polygon without adding boundary vertices.

    Convex polygons fan from vertex 0. Star-shaped ones (a knurled dial, a
    ribbed thumb cap) fan from the centroid instead: that adds a single
    *interior* vertex, which still leaves the cap's boundary identical to the
    input contour, so a lofted side wall stays conformal.
    """
    p = ensure_ccw(dedupe(poly))
    if len(p) < 3:
        return []
    if is_convex(p):
        return [(p[0], p[i], p[i + 1]) for i in range(1, len(p) - 1)
                if _area2(p[0], p[i], p[i + 1]) > 1e-12]

    cx = q(sum(v[0] for v in p) / len(p))
    cy = q(sum(v[1] for v in p) / len(p))
    c = (cx, cy)
    n = len(p)
    tris = [(c, p[i], p[(i + 1) % n]) for i in range(n)]
    for a, b, d in tris:
        if (b[0] - a[0]) * (d[1] - a[1]) - (d[0] - a[0]) * (b[1] - a[1]) <= 1e-12:
            raise ValueError("fan_triangulate() needs a convex or star-shaped polygon")
    return tris


def rect(cx: float, cy: float, w: float, h: float) -> list[Pt2]:
    """Axis-aligned rectangle centred on (cx, cy), CCW."""
    hw, hh = w / 2.0, h / 2.0
    return [(cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh), (cx - hw, cy + hh)]


def rect_corners(x0: float, y0: float, x1: float, y1: float) -> list[Pt2]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def rounded_rect(cx: float, cy: float, w: float, h: float, r: float, seg: int = 8) -> list[Pt2]:
    """Rounded rectangle, CCW, with `seg` segments per corner."""
    r = max(0.0, min(r, w / 2.0 - 1e-6, h / 2.0 - 1e-6))
    if r <= 1e-9:
        return rect(cx, cy, w, h)
    hw, hh = w / 2.0 - r, h / 2.0 - r
    pts: list[Pt2] = []
    corners = [(cx + hw, cy - hh, -90.0), (cx + hw, cy + hh, 0.0),
               (cx - hw, cy + hh, 90.0), (cx - hw, cy - hh, 180.0)]
    for ox, oy, a0 in corners:
        for i in range(seg + 1):
            a = math.radians(a0 + 90.0 * i / seg)
            pts.append((ox + r * math.cos(a), oy + r * math.sin(a)))
    return dedupe(pts)


def circle(cx: float, cy: float, d: float, seg: int = 48) -> list[Pt2]:
    r = d / 2.0
    return [(cx + r * math.cos(2 * math.pi * i / seg), cy + r * math.sin(2 * math.pi * i / seg))
            for i in range(seg)]


def cross(cx: float, cy: float, arm: float, thick: float) -> list[Pt2]:
    """A plus sign: `arm` is the full span of each axis, `thick` the bar width.

    Used for the MX switch stem and, slightly oversized, for the socket in the
    keycap that receives it.
    """
    a, t = arm / 2.0, thick / 2.0
    return [(cx + t, cy + t), (cx + a, cy + t), (cx + a, cy - t), (cx + t, cy - t),
            (cx + t, cy - a), (cx - t, cy - a), (cx - t, cy - t), (cx - a, cy - t),
            (cx - a, cy + t), (cx - t, cy + t), (cx - t, cy + a), (cx + t, cy + a)]


def fluted_circle(cx: float, cy: float, d: float, flutes: int = 40, depth: float = 0.45,
                  per_flute: int = 4) -> list[Pt2]:
    """A circle with a scalloped edge -- the knurl on the reasoning dial."""
    r = d / 2.0
    pts: list[Pt2] = []
    n = flutes * per_flute
    for i in range(n):
        a = 2 * math.pi * i / n
        rr = r - depth * 0.5 * (1.0 - math.cos(2 * math.pi * flutes * i / n))
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def d_profile(cx: float, cy: float, d: float, flat: float, seg: int = 40) -> list[Pt2]:
    """A 'D' shape: circle of diameter `d` with one side cut to `flat` across."""
    r = d / 2.0
    yc = max(-r + 1e-6, min(r - 1e-6, flat - r))  # chord offset from centre, on +y
    a1 = math.asin(yc / r)
    # Sweep the arc that survives the cut in one continuous run (left chord end,
    # round the bottom, to the right chord end); closing the loop draws the flat.
    a_start, a_end = math.pi - a1, 2 * math.pi + a1
    pts = []
    for i in range(seg + 1):
        a = a_start + (a_end - a_start) * i / seg
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return ensure_ccw(dedupe(pts))


def ring_between(outer: Sequence[Pt2], inner: Sequence[Pt2], z: float,
                 up: bool = True) -> "Mesh":
    """Flat annulus at height `z` between two polygons with matching vertex counts.

    Unlike `triangulate`, this introduces no new boundary vertices, so it stays
    conformal with lofted side walls (used for keycap skirts).
    """
    A = ensure_ccw(dedupe(outer))
    B = ensure_ccw(dedupe(inner))
    if len(A) != len(B):
        raise ValueError(f"ring_between() needs matching vertex counts, got {len(A)} and {len(B)}")
    m = Mesh()
    n = len(A)
    for i in range(n):
        a0 = (A[i][0], A[i][1], z)
        a1 = (A[(i + 1) % n][0], A[(i + 1) % n][1], z)
        b1 = (B[(i + 1) % n][0], B[(i + 1) % n][1], z)
        b0 = (B[i][0], B[i][1], z)
        if up:
            m.add_quad(a0, a1, b1, b0)
        else:
            m.add_quad(a0, b0, b1, a1)
    return m


def wedge(cx: float, cy: float, w: float, d: float, h_front: float, h_back: float,
          name: str = "") -> "Mesh":
    """Box with a sloped top -- the tilt feet. `h_front` is at -Y, `h_back` at +Y."""
    x0, x1 = cx - w / 2.0, cx + w / 2.0
    y0, y1 = cy - d / 2.0, cy + d / 2.0
    v = [(x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0),
         (x0, y0, h_front), (x1, y0, h_front), (x1, y1, h_back), (x0, y1, h_back)]
    m = Mesh(name=name)
    m.add_quad(v[0], v[3], v[2], v[1])
    m.add_quad(v[4], v[5], v[6], v[7])
    m.add_quad(v[0], v[1], v[5], v[4])
    m.add_quad(v[1], v[2], v[6], v[5])
    m.add_quad(v[2], v[3], v[7], v[6])
    m.add_quad(v[3], v[0], v[4], v[7])
    return m


def slot(cx: float, cy: float, w: float, h: float, seg: int = 12) -> list[Pt2]:
    """Stadium / obround shape (a rectangle capped with semicircles on the short axis)."""
    if w >= h:
        return rounded_rect(cx, cy, w, h, h / 2.0, seg)
    return rounded_rect(cx, cy, w, h, w / 2.0, seg)


def offset_convex(poly: Sequence[Pt2], d: float) -> list[Pt2]:
    """Scale a polygon about its centroid so its 'radius' grows by ~d.

    Only used for gently-rounded convex outlines (keycaps, bezels), where an
    exact Minkowski offset is overkill.
    """
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    out = []
    for x, y in poly:
        dx, dy = x - cx, y - cy
        L = math.hypot(dx, dy)
        if L < 1e-12:
            out.append((x, y))
        else:
            out.append((x + dx / L * d, y + dy / L * d))
    return out


def transform_poly(poly: Sequence[Pt2], dx: float = 0.0, dy: float = 0.0,
                   rot_deg: float = 0.0) -> list[Pt2]:
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(x * ca - y * sa + dx, x * sa + y * ca + dy) for x, y in poly]


def poly_bbox(poly: Sequence[Pt2]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _param_on(p: Pt2, a: Pt2, b: Pt2) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 < 1e-18:
        return 0.0
    return ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2


def _on_segment(p: Pt2, a: Pt2, b: Pt2, tol: float = 1e-6) -> bool:
    t = _param_on(p, a, b)
    if t < -tol or t > 1 + tol:
        return False
    cx = a[0] + t * (b[0] - a[0])
    cy = a[1] + t * (b[1] - a[1])
    return abs(cx - p[0]) <= tol and abs(cy - p[1]) <= tol


def walk(contour: Sequence[Pt2], p_from: Pt2, p_to: Pt2, tol: float = 1e-6) -> list[Pt2]:
    """Points from `p_from` to `p_to` following `contour`'s own winding order.

    Both points must lie on the contour (on a vertex or anywhere along an edge).
    When both sit on the same edge and `p_to` is *behind* `p_from`, the walk
    goes all the way around -- which is exactly what a ring-with-a-gap needs.
    """
    n = len(contour)
    i = j = -1
    for k in range(n):
        a, b = contour[k], contour[(k + 1) % n]
        if i < 0 and _on_segment(p_from, a, b, tol):
            i = k
        if j < 0 and _on_segment(p_to, a, b, tol):
            j = k
    if i < 0 or j < 0:
        raise ValueError(f"walk(): endpoint not on contour ({p_from} -> {p_to})")

    pts = [p_from]
    if i == j:
        a, b = contour[i], contour[(i + 1) % n]
        if _param_on(p_to, a, b) >= _param_on(p_from, a, b) - tol:
            pts.append(p_to)
            return dedupe(pts)
    k = i
    for _ in range(n + 1):
        k = (k + 1) % n
        pts.append(contour[k])
        if k == j:
            pts.append(p_to)
            break
    return dedupe(pts)


def ring_with_gap(outer: Sequence[Pt2], inner: Sequence[Pt2],
                  p_out_a: Pt2, p_out_b: Pt2, p_in_a: Pt2, p_in_b: Pt2) -> list[Pt2]:
    """A C-shaped wall: `outer` minus `inner`, interrupted between the a and b
    crossing points -- i.e. a ring with a doorway cut through it.

    Returns one simple polygon, so it extrudes to a single watertight shell.
    Used for the USB-C opening in the case wall.
    """
    o = ensure_ccw(dedupe(outer))
    i = ensure_ccw(dedupe(inner))
    # Which of a/b is "first" depends on the winding direction of the edge the
    # doorway sits on, so build both and keep the one that walked the long way
    # round (the short one is a sliver).
    best: list[Pt2] = []
    best_area = -1.0
    for (oa, ob, ia, ib) in ((p_out_a, p_out_b, p_in_a, p_in_b),
                             (p_out_b, p_out_a, p_in_b, p_in_a)):
        cand = dedupe(walk(o, ob, oa) + walk(list(reversed(i)), ia, ib))
        area = abs(signed_area(cand))
        if area > best_area:
            best, best_area = cand, area
    return best


def point_in_poly(pt: Pt2, poly: Sequence[Pt2]) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xin = x0 + (y - y0) / (y1 - y0) * (x1 - x0)
            if x < xin:
                inside = not inside
    return inside


# --------------------------------------------------------------------------
# Trapezoidal scanline triangulation of a polygon with holes
# --------------------------------------------------------------------------

def _edges_of(contours: Sequence[Sequence[Pt2]]) -> list[tuple[Pt2, Pt2]]:
    out = []
    for c in contours:
        n = len(c)
        for i in range(n):
            a, b = c[i], c[(i + 1) % n]
            if abs(a[0] - b[0]) > 1e-12 or abs(a[1] - b[1]) > 1e-12:
                out.append((a, b))
    return out


def _x_at(edge: tuple[Pt2, Pt2], y: float) -> float:
    (x0, y0), (x1, y1) = edge
    if abs(y1 - y0) < 1e-15:
        return x0
    return x0 + (y - y0) / (y1 - y0) * (x1 - x0)


def _cluster_snap(values: Iterable[float], tol: float) -> dict[float, float]:
    vs = sorted(set(values))
    rep: dict[float, float] = {}
    i = 0
    while i < len(vs):
        j = i
        while j + 1 < len(vs) and vs[j + 1] - vs[j] <= tol:
            j += 1
        for k in range(i, j + 1):
            rep[vs[k]] = vs[i]
        i = j + 1
    return rep


def snap_contours(contours: Sequence[Sequence[Pt2]], tol: float = 5e-5) -> list[list[Pt2]]:
    """Collapse coordinates that differ by less than `tol` onto one value.

    Two contours can land a few nanometres apart on the same nominal line (an
    arc tangent point vs. a circle's extreme, say). Left alone that produces a
    sliver scanline band which then gets dropped, tearing a hole in the cap.
    `tol` is 50 nm: far below any manufacturing tolerance, far above float noise.
    """
    cs = [dedupe(c) for c in contours]
    rx = _cluster_snap((p[0] for c in cs for p in c), tol)
    ry = _cluster_snap((p[1] for c in cs for p in c), tol)
    return [dedupe([(rx[p[0]], ry[p[1]]) for p in c]) for c in cs]


def triangulate(contours: Sequence[Sequence[Pt2]]) -> list[tuple[Pt2, Pt2, Pt2]]:
    """Triangulate an outline (contours[0]) with holes (contours[1:]).

    Returns CCW triangles in the XY plane. The returned triangles only ever use
    vertices that lie exactly on the input contours, and the caller can recover
    that vertex set with `boundary_vertices()` to keep side walls conformal.
    """
    contours = [dedupe(c) for c in contours if len(dedupe(c)) >= 3]
    if not contours:
        return []
    edges = _edges_of(contours)

    ys = sorted({q(p[1]) for c in contours for p in c})
    if len(ys) < 2:
        return []

    # x breakpoints that must appear on each scanline so caps stay conformal
    breaks: dict[float, set[float]] = {y: set() for y in ys}
    for c in contours:
        for x, y in c:
            yy = q(y)
            if yy in breaks:
                breaks[yy].add(q(x))

    bands: list[tuple[float, float, list[tuple[float, float, float, float]]]] = []
    for k in range(len(ys) - 1):
        ylo, yhi = ys[k], ys[k + 1]
        if yhi - ylo < EPS_Q:
            continue
        ym = 0.5 * (ylo + yhi)
        xs: list[tuple[float, tuple[Pt2, Pt2]]] = []
        for e in edges:
            (_, y0), (_, y1) = e
            if (y0 <= ym < y1) or (y1 <= ym < y0):
                xs.append((_x_at(e, ym), e))
        if len(xs) < 2:
            continue
        xs.sort(key=lambda t: t[0])
        spans = []
        for i in range(0, len(xs) - 1, 2):
            ea, eb = xs[i][1], xs[i + 1][1]
            a_lo, a_hi = q(_x_at(ea, ylo)), q(_x_at(ea, yhi))
            b_lo, b_hi = q(_x_at(eb, ylo)), q(_x_at(eb, yhi))
            if b_lo < a_lo:
                a_lo, b_lo = b_lo, a_lo
            if b_hi < a_hi:
                a_hi, b_hi = b_hi, a_hi
            spans.append((a_lo, b_lo, a_hi, b_hi))
            breaks[ylo].update((a_lo, b_lo))
            breaks[yhi].update((a_hi, b_hi))
        bands.append((ylo, yhi, spans))

    tris: list[tuple[Pt2, Pt2, Pt2]] = []
    for ylo, yhi, spans in bands:
        blo = sorted(breaks[ylo])
        bhi = sorted(breaks[yhi])
        for a_lo, b_lo, a_hi, b_hi in spans:
            bottom = [a_lo] + [x for x in blo if a_lo + EPS_Q < x < b_lo - EPS_Q] + [b_lo]
            top = [a_hi] + [x for x in bhi if a_hi + EPS_Q < x < b_hi - EPS_Q] + [b_hi]
            bottom = _dedupe_sorted(bottom)
            top = _dedupe_sorted(top)
            B = [(x, ylo) for x in bottom]
            T = [(x, yhi) for x in top]
            tris.extend(_zip_strip(B, T))
    return tris


def _dedupe_sorted(xs: list[float]) -> list[float]:
    out: list[float] = []
    for x in xs:
        if not out or x - out[-1] > EPS_Q:
            out.append(x)
    return out


def _zip_strip(B: list[Pt2], T: list[Pt2]) -> list[tuple[Pt2, Pt2, Pt2]]:
    """Triangulate the strip between two x-monotone chains (B below, T above)."""
    tris = []
    if len(B) < 1 or len(T) < 1:
        return tris
    if len(B) == 1 and len(T) == 1:
        return tris
    i = j = 0
    while i < len(B) - 1 or j < len(T) - 1:
        take_bottom = j >= len(T) - 1 or (i < len(B) - 1 and B[i + 1][0] <= T[j + 1][0])
        if take_bottom:
            a, b, c = B[i], B[i + 1], T[j]
            i += 1
        else:
            a, b, c = B[i], T[j + 1], T[j]
            j += 1
        if _area2(a, b, c) > 1e-12:
            tris.append((a, b, c))
    return tris


def _area2(a: Pt2, b: Pt2, c: Pt2) -> float:
    return abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) * 0.5


def boundary_vertices(tris: Sequence[tuple[Pt2, Pt2, Pt2]]) -> set[Pt2]:
    return {(q(p[0]), q(p[1])) for t in tris for p in t}


def subdivide_contour(contour: Sequence[Pt2], pts: set[Pt2]) -> list[Pt2]:
    """Insert every point of `pts` that lies on an edge of `contour` into it.

    This removes T-junctions between the extrusion caps and its side walls.
    """
    out: list[Pt2] = []
    n = len(contour)
    for i in range(n):
        a = contour[i]
        b = contour[(i + 1) % n]
        out.append(a)
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        if L2 < 1e-18:
            continue
        on = []
        for p in pts:
            px, py = p
            t = ((px - ax) * dx + (py - ay) * dy) / L2
            if t <= 1e-9 or t >= 1 - 1e-9:
                continue
            # perpendicular distance
            cx, cy = ax + t * dx, ay + t * dy
            if abs(cx - px) < 5e-7 and abs(cy - py) < 5e-7:
                on.append((t, p))
        on.sort(key=lambda z: z[0])
        for _, p in on:
            if abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9:
                out.append(p)
    return out


# --------------------------------------------------------------------------
# Mesh
# --------------------------------------------------------------------------

class Mesh:
    """A soup of triangles with an outward-facing winding convention (CCW seen
    from outside, i.e. right-hand rule normals point out of the solid)."""

    __slots__ = ("tris", "name")

    def __init__(self, tris: Iterable[tuple[Pt3, Pt3, Pt3]] | None = None, name: str = ""):
        self.tris: list[tuple[Pt3, Pt3, Pt3]] = list(tris or [])
        self.name = name

    # -- construction ------------------------------------------------------
    def add(self, a: Pt3, b: Pt3, c: Pt3) -> None:
        a, b, c = qp(a), qp(b), qp(c)
        if _tri_area3(a, b, c) < 1e-12:
            return
        self.tris.append((a, b, c))

    def add_quad(self, a: Pt3, b: Pt3, c: Pt3, d: Pt3) -> None:
        self.add(a, b, c)
        self.add(a, c, d)

    def extend(self, other: "Mesh") -> "Mesh":
        self.tris.extend(other.tris)
        return self

    def __add__(self, other: "Mesh") -> "Mesh":
        return Mesh(self.tris + other.tris, self.name)

    def copy(self) -> "Mesh":
        return Mesh(list(self.tris), self.name)

    # -- transforms --------------------------------------------------------
    def translate(self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> "Mesh":
        return Mesh([tuple(qp((p[0] + dx, p[1] + dy, p[2] + dz)) for p in t) for t in self.tris], self.name)

    def scale(self, sx: float = 1.0, sy: float | None = None, sz: float | None = None) -> "Mesh":
        sy = sx if sy is None else sy
        sz = sx if sz is None else sz
        m = Mesh([tuple(qp((p[0] * sx, p[1] * sy, p[2] * sz)) for p in t) for t in self.tris], self.name)
        if sx * sy * sz < 0:
            m = m.flip()
        return m

    def rotate_z(self, deg: float) -> "Mesh":
        a = math.radians(deg)
        ca, sa = math.cos(a), math.sin(a)
        return Mesh([tuple(qp((p[0] * ca - p[1] * sa, p[0] * sa + p[1] * ca, p[2])) for p in t)
                     for t in self.tris], self.name)

    def rotate_x(self, deg: float) -> "Mesh":
        a = math.radians(deg)
        ca, sa = math.cos(a), math.sin(a)
        return Mesh([tuple(qp((p[0], p[1] * ca - p[2] * sa, p[1] * sa + p[2] * ca)) for p in t)
                     for t in self.tris], self.name)

    def rotate_y(self, deg: float) -> "Mesh":
        a = math.radians(deg)
        ca, sa = math.cos(a), math.sin(a)
        return Mesh([tuple(qp((p[0] * ca + p[2] * sa, p[1], -p[0] * sa + p[2] * ca)) for p in t)
                     for t in self.tris], self.name)

    def flip(self) -> "Mesh":
        return Mesh([(t[0], t[2], t[1]) for t in self.tris], self.name)

    # -- queries -----------------------------------------------------------
    def bbox(self) -> tuple[Pt3, Pt3]:
        if not self.tris:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        xs = [p[0] for t in self.tris for p in t]
        ys = [p[1] for t in self.tris for p in t]
        zs = [p[2] for t in self.tris for p in t]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def size(self) -> Pt3:
        lo, hi = self.bbox()
        return (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])

    def volume(self) -> float:
        """Signed volume via the divergence theorem; positive for outward normals."""
        v = 0.0
        for a, b, c in self.tris:
            v += (a[0] * (b[1] * c[2] - c[1] * b[2])
                  - a[1] * (b[0] * c[2] - c[0] * b[2])
                  + a[2] * (b[0] * c[1] - c[0] * b[1]))
        return v / 6.0

    def area(self) -> float:
        return sum(_tri_area3(*t) for t in self.tris)

    def validate(self, require_closed: bool = True) -> dict:
        """Topology report. `ok` is True for a closed, consistently-oriented,
        positively-oriented (outward normal) 2-manifold."""
        edge_count: dict[tuple[Pt3, Pt3], int] = {}
        for a, b, c in self.tris:
            for u, v in ((a, b), (b, c), (c, a)):
                key = (u, v) if u <= v else (v, u)
                sign = 1 if u <= v else -1
                edge_count[key] = edge_count.get(key, 0) + sign
        # Count how many times each undirected edge is used, regardless of direction
        used: dict[tuple[Pt3, Pt3], int] = {}
        for a, b, c in self.tris:
            for u, v in ((a, b), (b, c), (c, a)):
                key = (u, v) if u <= v else (v, u)
                used[key] = used.get(key, 0) + 1

        boundary = [e for e, n in used.items() if n == 1]
        nonmanifold = [e for e, n in used.items() if n > 2]
        misoriented = [e for e, s in edge_count.items() if used[e] == 2 and s != 0]
        degenerate = sum(1 for t in self.tris if _tri_area3(*t) < 1e-12)
        vol = self.volume()

        ok = (not boundary) and (not nonmanifold) and (not misoriented) and degenerate == 0
        if require_closed:
            ok = ok and vol > 0
        return {
            "name": self.name,
            "triangles": len(self.tris),
            "vertices": len({p for t in self.tris for p in t}),
            "boundary_edges": len(boundary),
            "nonmanifold_edges": len(nonmanifold),
            "misoriented_edges": len(misoriented),
            "degenerate_triangles": degenerate,
            "volume_mm3": vol,
            "area_mm2": self.area(),
            "bbox": self.bbox(),
            "watertight": not boundary,
            "manifold": (not boundary) and (not nonmanifold),
            "oriented": not misoriented and vol > 0,
            "ok": ok,
            "sample_boundary": boundary[:4],
            "sample_nonmanifold": nonmanifold[:4],
        }

    # -- export ------------------------------------------------------------
    def to_stl_binary(self) -> bytes:
        out = bytearray()
        header = f"Claude Micro :: {self.name}".encode()[:79]
        out += header.ljust(80, b" ")
        out += struct.pack("<I", len(self.tris))
        for a, b, c in self.tris:
            n = _normal(a, b, c)
            out += struct.pack("<12fH", n[0], n[1], n[2],
                               a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2], 0)
        return bytes(out)

    def save_stl(self, path: str) -> int:
        data = self.to_stl_binary()
        with open(path, "wb") as fh:
            fh.write(data)
        return len(data)

    def to_arrays(self) -> list[float]:
        """Flat [x,y,z, x,y,z, ...] positions, 3 per triangle."""
        out: list[float] = []
        for t in self.tris:
            for p in t:
                out.extend(p)
        return out


def _tri_area3(a: Pt3, b: Pt3, c: Pt3) -> float:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def _normal(a: Pt3, b: Pt3, c: Pt3) -> Pt3:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    L = math.sqrt(cx * cx + cy * cy + cz * cz)
    return (0.0, 0.0, 0.0) if L < 1e-15 else (cx / L, cy / L, cz / L)


# --------------------------------------------------------------------------
# Solid constructors
# --------------------------------------------------------------------------

def extrude(outline: Sequence[Pt2], z0: float, z1: float,
            holes: Sequence[Sequence[Pt2]] = (), name: str = "") -> Mesh:
    """Sweep a polygon with holes from z0 to z1. Watertight by construction."""
    if z1 < z0:
        z0, z1 = z1, z0
    snapped = snap_contours([outline] + [h for h in holes if len(dedupe(h)) >= 3])
    outer = ensure_ccw(snapped[0])
    hs = [ensure_cw(h) for h in snapped[1:] if len(h) >= 3]

    tris2d = triangulate([outer] + hs)
    if not tris2d:
        return Mesh(name=name)
    pts = boundary_vertices(tris2d)

    m = Mesh(name=name)
    for a, b, c in tris2d:
        m.add((a[0], a[1], z1), (b[0], b[1], z1), (c[0], c[1], z1))       # top, +Z
        m.add((a[0], a[1], z0), (c[0], c[1], z0), (b[0], b[1], z0))       # bottom, -Z

    for contour, ccw in [(outer, True)] + [(h, False) for h in hs]:
        sub = subdivide_contour(contour, pts)
        n = len(sub)
        for i in range(n):
            (x0, y0) = sub[i]
            (x1, y1) = sub[(i + 1) % n]
            if abs(x0 - x1) < 1e-12 and abs(y0 - y1) < 1e-12:
                continue
            # For a CCW outer contour the wall normal must face outward; the
            # quad below does that, and hole contours are already CW so the
            # same winding faces into the hole (i.e. still out of the solid).
            m.add_quad((x0, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y0, z1))
    return m


def loft(poly_a: Sequence[Pt2], za: float, poly_b: Sequence[Pt2], zb: float,
         cap_bottom: bool = True, cap_top: bool = True, name: str = "") -> Mesh:
    """Skin between two polygons with the same vertex count (a frustum)."""
    A = ensure_ccw(dedupe(poly_a))
    B = ensure_ccw(dedupe(poly_b))
    if len(A) != len(B):
        raise ValueError(f"loft() needs matching vertex counts, got {len(A)} and {len(B)}")
    m = Mesh(name=name)
    n = len(A)
    for i in range(n):
        a0 = (A[i][0], A[i][1], za)
        a1 = (A[(i + 1) % n][0], A[(i + 1) % n][1], za)
        b1 = (B[(i + 1) % n][0], B[(i + 1) % n][1], zb)
        b0 = (B[i][0], B[i][1], zb)
        m.add_quad(a0, a1, b1, b0)
    if cap_bottom:
        for t in fan_triangulate(A):
            m.add((t[0][0], t[0][1], za), (t[2][0], t[2][1], za), (t[1][0], t[1][1], za))
    if cap_top:
        for t in fan_triangulate(B):
            m.add((t[0][0], t[0][1], zb), (t[1][0], t[1][1], zb), (t[2][0], t[2][1], zb))
    return m


def lathe(profile: Sequence[tuple[float, float]], seg: int = 64, name: str = "") -> Mesh:
    """Revolve a closed (r, z) profile around the Z axis.

    `profile` must be a closed loop ordered so that the enclosed area is
    positive in the (r, z) plane (CCW), which yields outward normals.
    """
    prof = [(max(0.0, r), z) for r, z in profile]
    if signed_area(prof) < 0:
        prof = list(reversed(prof))
    m = Mesh(name=name)
    n = len(prof)
    for i in range(n):
        r0, z0 = prof[i]
        r1, z1 = prof[(i + 1) % n]
        if r0 < 1e-12 and r1 < 1e-12:
            continue
        for s in range(seg):
            a0 = 2 * math.pi * s / seg
            a1 = 2 * math.pi * (s + 1) / seg
            c0, s0 = math.cos(a0), math.sin(a0)
            c1, s1 = math.cos(a1), math.sin(a1)
            p00 = (r0 * c0, r0 * s0, z0)
            p10 = (r1 * c0, r1 * s0, z1)
            p11 = (r1 * c1, r1 * s1, z1)
            p01 = (r0 * c1, r0 * s1, z0)
            # Winding: sweeping +theta with the profile CCW in (r, z) puts the
            # face normal outward only for this vertex order.
            if r0 < 1e-12:
                m.add((0.0, 0.0, z0), p11, p10)
            elif r1 < 1e-12:
                m.add(p00, p01, (0.0, 0.0, z1))
            else:
                m.add_quad(p00, p01, p11, p10)
    return m


def box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float, name: str = "") -> Mesh:
    return extrude(rect_corners(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)),
                   min(z0, z1), max(z0, z1), name=name)


def box_at(cx: float, cy: float, z0: float, w: float, d: float, h: float, name: str = "") -> Mesh:
    return extrude(rect(cx, cy, w, d), z0, z0 + h, name=name)


def cylinder(cx: float, cy: float, z0: float, d: float, h: float, seg: int = 48,
             name: str = "") -> Mesh:
    return extrude(circle(cx, cy, d, seg), z0, z0 + h, name=name)


def tube(cx: float, cy: float, z0: float, d_out: float, d_in: float, h: float,
         seg: int = 48, name: str = "") -> Mesh:
    return extrude(circle(cx, cy, d_out, seg), z0, z0 + h,
                   holes=[circle(cx, cy, d_in, seg)], name=name)


def cone(cx: float, cy: float, z0: float, d0: float, d1: float, h: float, seg: int = 48,
         name: str = "") -> Mesh:
    return loft(circle(cx, cy, d0, seg), z0, circle(cx, cy, d1, seg), z0 + h, name=name)


def prism_shell(outline: Sequence[Pt2], inner: Sequence[Pt2], z0: float, z1: float,
                name: str = "") -> Mesh:
    """A wall: `outline` extruded with `inner` removed (open top and bottom rings)."""
    return extrude(outline, z0, z1, holes=[inner], name=name)


# --------------------------------------------------------------------------
# The Claude sunburst mark, as polygons (used for emboss, silkscreen and HTML)
# --------------------------------------------------------------------------

def claude_burst(cx: float = 0.0, cy: float = 0.0, size: float = 10.0,
                 blades: int = 12, seg: int = 5) -> list[list[Pt2]]:
    """Return one polygon per blade of the Claude sunburst mark.

    Each blade is a slightly tapered bar with a rounded outer tip, radiating
    from a small hub. `size` is the full outside diameter.
    """
    R = size / 2.0
    r_in = 0.10 * R
    r_out = 0.98 * R
    w_in = 0.062 * R
    w_out = 0.105 * R
    out: list[list[Pt2]] = []
    for b in range(blades):
        ang = 2 * math.pi * b / blades - math.pi / 2.0
        ca, sa = math.cos(ang), math.sin(ang)

        def place(u: float, v: float) -> Pt2:
            # u along the blade, v across it
            return (cx + u * ca - v * sa, cy + u * sa + v * ca)

        pts: list[Pt2] = [place(r_in, -w_in), place(r_out - w_out, -w_out)]
        for i in range(seg + 1):  # rounded tip
            a = -math.pi / 2 + math.pi * i / seg
            pts.append(place(r_out - w_out + w_out * math.cos(a), w_out * math.sin(a)))
        pts.append(place(r_out - w_out, w_out))
        pts.append(place(r_in, w_in))
        out.append(ensure_ccw(dedupe(pts)))
    return out


def emboss(polys: Sequence[Sequence[Pt2]], z0: float, h: float, name: str = "") -> Mesh:
    m = Mesh(name=name)
    for p in polys:
        m.extend(extrude(p, z0, z0 + h))
    return m
