"""A grid maze router and copper-pour generator for two-layer boards.

The board is rasterised onto a fixed grid. Every piece of copper -- pads,
tracks, vias, the board edge, non-plated holes -- is stamped into an ownership
map together with a clearance halo, so "can this cell carry net N?" is a single
array lookup. Nets are then routed one at a time with A* over that map, with a
cost for changing layer, and the leftover free space becomes the ground pour.

The router does not rip up and retry: it routes shortest connections first and
reports anything it could not complete, which the DRC step then fails on.
"""

from __future__ import annotations

import heapq
import math
from array import array

FREE = 0
BLOCKED = 255

ORTHO = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIAG = ((1, 1), (1, -1), (-1, 1), (-1, -1))


class Grid:
    def __init__(self, x0: float, y0: float, x1: float, y1: float, pitch: float = 0.2,
                 layers: int = 2):
        self.pitch = pitch
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.w = int(math.ceil((x1 - x0) / pitch)) + 1
        self.h = int(math.ceil((y1 - y0) / pitch)) + 1
        self.layers = layers
        self.n = self.w * self.h
        # own[layer] holds the net id that owns each cell (0 free, 255 blocked)
        self.own = [bytearray(self.n) for _ in range(layers)]
        # Cells where a via may not be drilled (too close to another hole).
        self.no_via = bytearray(self.n)
        self.net_ids: dict[str, int] = {}
        self.net_names: dict[int, str] = {}

    # -- coordinate helpers ------------------------------------------------
    def cx(self, x: float) -> int:
        return int(round((x - self.x0) / self.pitch))

    def cy(self, y: float) -> int:
        return int(round((y - self.y0) / self.pitch))

    def wx(self, i: int) -> float:
        return self.x0 + i * self.pitch

    def wy(self, j: int) -> float:
        return self.y0 + j * self.pitch

    def inside(self, i: int, j: int) -> bool:
        return 0 <= i < self.w and 0 <= j < self.h

    def net_id(self, name: str) -> int:
        if name not in self.net_ids:
            nid = len(self.net_ids) + 1
            if nid >= BLOCKED:
                raise ValueError("too many nets for a byte-wide ownership map")
            self.net_ids[name] = nid
            self.net_names[nid] = name
        return self.net_ids[name]

    # -- stamping ----------------------------------------------------------
    def _mark(self, layer: int, idx: int, nid: int, force: bool = False) -> None:
        if force:
            self.own[layer][idx] = nid
            return
        cur = self.own[layer][idx]
        if cur == FREE:
            self.own[layer][idx] = nid
        elif cur != nid:
            self.own[layer][idx] = BLOCKED

    def stamp_rect(self, layer: int, x: float, y: float, w: float, h: float,
                   net: str, halo: float = 0.0, force: bool = False) -> None:
        nid = self.net_id(net) if net else BLOCKED
        i0, i1 = self.cx(x - w / 2 - halo), self.cx(x + w / 2 + halo)
        j0, j1 = self.cy(y - h / 2 - halo), self.cy(y + h / 2 + halo)
        for j in range(max(0, j0), min(self.h - 1, j1) + 1):
            base = j * self.w
            for i in range(max(0, i0), min(self.w - 1, i1) + 1):
                self._mark(layer, base + i, nid, force)

    def stamp_disc(self, layer: int, x: float, y: float, d: float, net: str,
                   halo: float = 0.0, force: bool = False) -> None:
        nid = self.net_id(net) if net else BLOCKED
        r = d / 2 + halo
        i0, i1 = self.cx(x - r), self.cx(x + r)
        j0, j1 = self.cy(y - r), self.cy(y + r)
        r2 = r * r
        for j in range(max(0, j0), min(self.h - 1, j1) + 1):
            dy = self.wy(j) - y
            base = j * self.w
            for i in range(max(0, i0), min(self.w - 1, i1) + 1):
                dx = self.wx(i) - x
                if dx * dx + dy * dy <= r2:
                    self._mark(layer, base + i, nid, force)

    def stamp_segment(self, layer: int, x0: float, y0: float, x1: float, y1: float,
                      width: float, net: str, halo: float = 0.0) -> None:
        r = width / 2 + halo
        steps = max(1, int(math.hypot(x1 - x0, y1 - y0) / (self.pitch * 0.7)))
        for s in range(steps + 1):
            t = s / steps
            self.stamp_disc(layer, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, r * 2, net)

    def block_via_zone(self, x: float, y: float, drill: float,
                       hole_to_hole: float = 0.5, via_drill: float = 0.35) -> None:
        """Forbid via drilling near an existing hole.

        Copper clearance alone does not stop a via landing inside a pad it
        shares a net with, which would still break the drill spacing rule.
        """
        r = hole_to_hole + (drill + via_drill) / 2.0
        for j in range(max(0, self.cy(y - r)), min(self.h - 1, self.cy(y + r)) + 1):
            dy = self.wy(j) - y
            base = j * self.w
            for i in range(max(0, self.cx(x - r)), min(self.w - 1, self.cx(x + r)) + 1):
                dx = self.wx(i) - x
                if dx * dx + dy * dy <= r * r:
                    self.no_via[base + i] = 1

    def block_outside(self, poly, margin: float) -> None:
        """Block every cell that is outside the board outline (or too close to it)."""
        from mesh import point_in_poly
        m = margin
        for j in range(self.h):
            y = self.wy(j)
            base = j * self.w
            for i in range(self.w):
                x = self.wx(i)
                ok = (point_in_poly((x, y), poly)
                      and point_in_poly((x + m, y), poly) and point_in_poly((x - m, y), poly)
                      and point_in_poly((x, y + m), poly) and point_in_poly((x, y - m), poly))
                if not ok:
                    for L in range(self.layers):
                        self.own[L][base + i] = BLOCKED

    # -- routing -----------------------------------------------------------
    def passable(self, layer: int, idx: int, nid: int) -> bool:
        v = self.own[layer][idx]
        return v == FREE or v == nid

    def route(self, net: str, start: list[tuple[int, int, int]],
              goal: set[tuple[int, int, int]], via_cost: int = 60,
              layer_bias: tuple[int, ...] = (0, 0)) -> list[tuple[int, int, int]] | None:
        """A* from any `start` cell to any `goal` cell. Cells are (layer, i, j)."""
        nid = self.net_id(net)
        if not start or not goal:
            return None
        gset = set(goal)
        gx = sum(g[1] for g in goal) / len(goal)
        gy = sum(g[2] for g in goal) / len(goal)

        def hcost(i, j):
            dx, dy = abs(i - gx), abs(j - gy)
            return int(10 * max(dx, dy) + 4 * min(dx, dy))

        openq: list[tuple[int, int, tuple[int, int, int]]] = []
        gscore: dict[tuple[int, int, int], int] = {}
        came: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        # Seed only cells this net may legally occupy. Starting on a cell that
        # belongs to someone else would let the very first track segment sit
        # inside another net's clearance.
        seeded = 0
        for s in start:
            if not self.inside(s[1], s[2]):
                continue
            if not self.passable(s[0], s[2] * self.w + s[1], nid):
                continue
            gscore[s] = 0
            seeded += 1
            heapq.heappush(openq, (hcost(s[1], s[2]), 0, s))
        if not seeded:
            return None
        gset = {g for g in gset if self.passable(g[0], g[2] * self.w + g[1], nid)}
        if not gset:
            return None

        visited = 0
        limit = self.n * self.layers
        while openq:
            f, g, cur = heapq.heappop(openq)
            if g > gscore.get(cur, 1 << 30):
                continue
            if cur in gset:
                path = [cur]
                while cur in came:
                    cur = came[cur]
                    path.append(cur)
                path.reverse()
                return path
            visited += 1
            if visited > limit:
                break
            L, i, j = cur
            for dx, dy in ORTHO + DIAG:
                ni, nj = i + dx, j + dy
                if not self.inside(ni, nj):
                    continue
                idx = nj * self.w + ni
                if not self.passable(L, idx, nid):
                    continue
                if dx and dy:
                    # do not cut a diagonal through two blocked orthogonal neighbours
                    if not (self.passable(L, j * self.w + ni, nid)
                            and self.passable(L, nj * self.w + i, nid)):
                        continue
                    step = 14
                else:
                    step = 10
                step += layer_bias[L]
                nxt = (L, ni, nj)
                ng = g + step
                if ng < gscore.get(nxt, 1 << 30):
                    gscore[nxt] = ng
                    came[nxt] = cur
                    heapq.heappush(openq, (ng + hcost(ni, nj), ng, nxt))
            # layer change (via)
            idx = j * self.w + i
            if self.no_via[idx]:
                continue
            for L2 in range(self.layers):
                if L2 == L:
                    continue
                if not self.passable(L2, idx, nid):
                    continue
                nxt = (L2, i, j)
                ng = g + via_cost
                if ng < gscore.get(nxt, 1 << 30):
                    gscore[nxt] = ng
                    came[nxt] = cur
                    heapq.heappush(openq, (ng + hcost(i, j), ng, nxt))
        return None


def path_to_tracks(grid: Grid, path: list[tuple[int, int, int]], width: float,
                   net: str) -> tuple[list[dict], list[dict]]:
    """Collapse a cell path into straight track segments plus vias."""
    tracks: list[dict] = []
    vias: list[dict] = []
    if not path:
        return tracks, vias
    run_start = path[0]
    prev = path[0]
    prev_dir = None
    for cur in path[1:]:
        if cur[0] != prev[0]:                       # layer change -> via
            if prev != run_start:
                tracks.append(_seg(grid, run_start, prev, width, net))
            vias.append({"x": grid.wx(prev[1]), "y": grid.wy(prev[2]), "net": net})
            run_start = cur
            prev_dir = None
            prev = cur
            continue
        d = (cur[1] - prev[1], cur[2] - prev[2])
        if prev_dir is not None and d != prev_dir:
            tracks.append(_seg(grid, run_start, prev, width, net))
            run_start = prev
        prev_dir = d
        prev = cur
    if prev != run_start:
        tracks.append(_seg(grid, run_start, prev, width, net))
    return tracks, vias


def _seg(grid: Grid, a, b, width, net):
    return {"layer": a[0], "x0": grid.wx(a[1]), "y0": grid.wy(a[2]),
            "x1": grid.wx(b[1]), "y1": grid.wy(b[2]), "width": width, "net": net}


def pour_runs(grid: Grid, layer: int, net: str, keepouts: list[tuple] = ()) -> list[dict]:
    """Horizontal fill runs for a copper pour on one layer.

    Anything already owned by the pour's own net, or free, becomes copper. The
    result is emitted as scanline runs, which flash to gerber as short wide
    strokes -- a real filled zone with correct clearances and a tiny file.
    """
    nid = grid.net_id(net)
    runs = []
    ko = list(keepouts)
    for j in range(grid.h):
        base = j * grid.w
        y = grid.wy(j)
        if any(y0 <= y <= y1 for (_, y0, _, y1) in ko):
            row_ko = [(x0, x1) for (x0, y0, x1, y1) in ko if y0 <= y <= y1]
        else:
            row_ko = []
        i = 0
        while i < grid.w:
            v = grid.own[layer][base + i]
            if v == FREE or v == nid:
                s = i
                while i < grid.w and grid.own[layer][base + i] in (FREE, nid):
                    i += 1
                for x_a, x_b in _subtract_intervals(grid.wx(s), grid.wx(i - 1), row_ko):
                    runs.append({"y": y, "x0": x_a, "x1": x_b})
            else:
                i += 1
    return runs


def _subtract_intervals(a: float, b: float, cuts: list[tuple[float, float]]):
    """Yield the parts of [a, b] left after removing every cut interval."""
    pieces = [(a, b)]
    for c0, c1 in sorted(cuts):
        nxt = []
        for p0, p1 in pieces:
            if c1 < p0 or c0 > p1:
                nxt.append((p0, p1))
                continue
            if p0 < c0:
                nxt.append((p0, c0))
            if c1 < p1:
                nxt.append((c1, p1))
        pieces = nxt
    return [p for p in pieces if p[1] - p[0] >= 0]
