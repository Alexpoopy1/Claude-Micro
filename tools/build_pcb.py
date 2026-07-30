#!/usr/bin/env python3
"""Place, route, pour and check the Claude Micro board, then write every output.

Produces, in build/pcb/:
    claude-micro.kicad_pro / .kicad_sch / .kicad_pcb   -- open in KiCad
    claude-micro.net                                   -- KiCad netlist
    gerbers/*.gbr + *.drl                              -- fabrication set
    bom.csv, positions.csv                             -- assembly
    board.json                                         -- what the HTML viewer draws
    drc_report.json                                    -- clearance / connectivity audit

Everything is derived: change config/device.json and rerun.
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mesh as M                                   # noqa: E402
import circuit                                     # noqa: E402
import router as R                                 # noqa: E402
from device import load, ensure_build              # noqa: E402

TRACK = 0.3
TRACK_POWER = 0.3
VIA_D = 0.7
VIA_DRILL = 0.35
GRID = 0.2


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

def board_outline(cfg: dict) -> list[tuple[float, float]]:
    p = cfg["pcb"]
    return M.rounded_rect(p["cx"], p["cy"], p["width"], p["height"], p["corner_radius"], 10)


def collect_pads(cfg: dict, net: dict) -> list[dict]:
    pads = []
    for c in net["components"]:
        pads.extend(circuit.pad_positions(c, net["footprints"]))
    return pads


def collect_npth(cfg: dict, net: dict) -> list[dict]:
    holes = []
    for c in net["components"]:
        holes.extend(circuit.npth_positions(c, net["footprints"]))
    for h in cfg["pcb"]["mount_holes"]:
        holes.append({"x": h["x"], "y": h["y"], "d": h["d"], "ref": "MH"})
    return holes


# --------------------------------------------------------------------------
# main build
# --------------------------------------------------------------------------

def build(verbose: bool = True) -> dict:
    t0 = time.time()
    cfg = load()
    net = circuit.build_netlist(cfg)
    dr = cfg["pcb"]["design_rules"]
    clearance = dr["min_clearance"]
    outline = board_outline(cfg)

    pads = collect_pads(cfg, net)
    npth = collect_npth(cfg, net)
    touch_pads = net["touch_pads"]

    p = cfg["pcb"]
    grid = R.Grid(p["x0"], p["y0"], p["x1"], p["y1"], GRID, layers=2)
    for n in net["nets"]:
        grid.net_id(n)
    grid.net_id("GND")

    if verbose:
        print(f"  grid {grid.w} x {grid.h} x 2 = {grid.n * 2:,} cells @ {GRID} mm")

    # ---- stamp the immovable copper -------------------------------------
    grid.block_outside(outline, dr["edge_clearance"] + TRACK / 2)

    halo = clearance + TRACK / 2

    def _stamp_pads(with_halo: bool):
        """Two passes: halos first, then pad cores forced on top.

        A pad's clearance halo can otherwise land on a neighbouring pad and mark
        it BLOCKED, which would make that pad unreachable even to its own net.
        """
        for pd in pads:
            layers = ((0, 1) if pd["layer"] == "*.Cu"
                      else ((0,) if pd["layer"] == "F.Cu" else (1,)))
            for L in layers:
                if pd["shape"] == "circle":
                    grid.stamp_disc(L, pd["x"], pd["y"], max(pd["w"], pd["h"]),
                                    pd["net"], halo if with_halo else 0.0,
                                    force=not with_halo)
                else:
                    grid.stamp_rect(L, pd["x"], pd["y"], pd["w"], pd["h"],
                                    pd["net"], halo if with_halo else 0.0,
                                    force=not with_halo)

    _stamp_pads(with_halo=True)

    for h in npth:
        for L in (0, 1):
            grid.stamp_disc(L, h["x"], h["y"], h["d"], "", clearance + 0.2)
        grid.block_via_zone(h["x"], h["y"], h["d"], dr["min_hole_to_hole"], VIA_DRILL)
    for pd in pads:
        if pd["type"] == "tht" and pd["drill"] > 0:
            grid.block_via_zone(pd["x"], pd["y"], pd["drill"], dr["min_hole_to_hole"], VIA_DRILL)

    # Touch pads are copper islands on the top layer; nothing may pour under
    # them on the bottom or the sensitivity collapses.
    keepouts = []
    for i, tp in enumerate(touch_pads):
        cx, cy = (tp["x0"] + tp["x1"]) / 2, (tp["y0"] + tp["y1"]) / 2
        w, h = tp["x1"] - tp["x0"], tp["y1"] - tp["y0"]
        grid.stamp_rect(0, cx, cy, w, h, f"TOUCH{i}", halo)
        keepouts.append((tp["x0"] - 1.0, tp["y0"] - 1.0, tp["x1"] + 1.0, tp["y1"] + 1.0))

    # Any pad whose own copper got marked BLOCKED is sitting inside another
    # net's clearance halo. That is a placement error, not something to route
    # around, so surface it instead of forcing the cell back open.
    crowded = []
    for pd in pads:
        if not pd["net"]:
            continue
        layers = (0, 1) if pd["layer"] == "*.Cu" else ((0,) if pd["layer"] == "F.Cu" else (1,))
        nid = grid.net_id(pd["net"])
        free = 0
        for L in layers:
            for j in range(grid.cy(pd["y"] - pd["h"] / 2), grid.cy(pd["y"] + pd["h"] / 2) + 1):
                for i in range(grid.cx(pd["x"] - pd["w"] / 2), grid.cx(pd["x"] + pd["w"] / 2) + 1):
                    if grid.inside(i, j) and grid.own[L][j * grid.w + i] == nid:
                        free += 1
        if free == 0:
            crowded.append(f"{pd['ref']}.{pd['num']} ({pd['net']})")

    # ---- build the connection list --------------------------------------
    pad_index: dict[str, list[dict]] = {}
    for pd in pads:
        if pd["net"]:
            pad_index.setdefault(pd["net"], []).append(pd)
    for i, tp in enumerate(touch_pads):
        pad_index.setdefault(f"TOUCH{i}", []).append({
            "ref": f"TP{i + 1}", "num": "1", "x": (tp["x0"] + tp["x1"]) / 2,
            "y": (tp["y0"] + tp["y1"]) / 2, "w": tp["x1"] - tp["x0"],
            "h": tp["y1"] - tp["y0"], "shape": "rect", "type": "smd",
            "drill": 0.0, "layer": "F.Cu", "net": f"TOUCH{i}"})

    # Only GND is poured. +5V is routed as a tree so every LED gets a real
    # connection instead of relying on a second pour we do not generate.
    poured = {"GND"}
    connections = []
    for name, plist in sorted(pad_index.items()):
        if name in poured or len(plist) < 2:
            continue
        for a, b in _mst_edges(plist):
            connections.append((name, a, b, math.dist((a["x"], a["y"]), (b["x"], b["y"]))))
    connections.sort(key=lambda c: c[3])

    # ---- route ------------------------------------------------------------
    tracks: list[dict] = []
    vias: list[dict] = []
    failed: list[dict] = []
    routed_cells: dict[str, set] = {}

    def pad_cells(pd):
        """Grid cells actually covered by a pad's copper (its shape, not its
        bounding box), so a track never launches from outside the pad."""
        cells = []
        layers = (0, 1) if pd["layer"] == "*.Cu" else ((0,) if pd["layer"] == "F.Cu" else (1,))
        i0, i1 = grid.cx(pd["x"] - pd["w"] / 2), grid.cx(pd["x"] + pd["w"] / 2)
        j0, j1 = grid.cy(pd["y"] - pd["h"] / 2), grid.cy(pd["y"] + pd["h"] / 2)
        rad = max(pd["w"], pd["h"]) / 2
        for L in layers:
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    if not grid.inside(i, j):
                        continue
                    if pd["shape"] == "circle":
                        if math.dist((grid.wx(i), grid.wy(j)), (pd["x"], pd["y"])) > rad:
                            continue
                    cells.append((L, i, j))
        return cells or [(layers[0], grid.cx(pd["x"]), grid.cy(pd["y"]))]

    for name, a, b, _d in connections:
        width = TRACK_POWER if name in ("+3V3", "VBUS", "+5V") else TRACK
        start = pad_cells(a) + list(routed_cells.get(name, ()))
        goal = set(pad_cells(b))
        path = grid.route(name, start, goal, via_cost=70)
        if path is None:
            failed.append({"net": name, "from": f"{a['ref']}.{a['num']}",
                           "to": f"{b['ref']}.{b['num']}"})
            continue
        segs, vs = R.path_to_tracks(grid, path, width, name)
        tracks.extend(segs)
        vias.extend(vs)
        routed_cells.setdefault(name, set()).update(path)
        for s in segs:
            grid.stamp_segment(s["layer"], s["x0"], s["y0"], s["x1"], s["y1"],
                               width, name, halo)
        for v in vs:
            for L in (0, 1):
                grid.stamp_disc(L, v["x"], v["y"], VIA_D, name, halo)
            grid.block_via_zone(v["x"], v["y"], VIA_DRILL, dr["min_hole_to_hole"], VIA_DRILL)

    if verbose:
        print(f"  routed {len(connections) - len(failed)}/{len(connections)} connections, "
              f"{len(tracks)} segments, {len(vias)} vias  ({time.time() - t0:.1f}s)")

    # ---- ground pour on both layers ---------------------------------------
    gid = grid.net_id("GND")

    def make_pours():
        return {"F.Cu": R.pour_runs(grid, 0, "GND", keepouts),
                "B.Cu": R.pour_runs(grid, 1, "GND", keepouts)}

    pours = make_pours()

    # Stitching vias tie the two pours together. Placed before the rescue pass
    # so the connectivity flood fill can use them to hop layers.
    stitch = []
    via_clear = VIA_D / 2 + clearance
    for gx in range(int(p["x0"]) + 6, int(p["x1"]) - 4, 12):
        for gy in range(int(p["y0"]) + 6, int(p["y1"]) - 4, 12):
            i, j = grid.cx(gx), grid.cy(gy)
            if not grid.inside(i, j) or grid.no_via[j * grid.w + i]:
                continue
            if not _via_site_clear(grid, gx, gy, via_clear, gid):
                continue
            stitch.append({"x": float(gx), "y": float(gy), "net": "GND"})
            for L in (0, 1):
                grid.stamp_disc(L, float(gx), float(gy), VIA_D, "GND", halo)
            grid.block_via_zone(float(gx), float(gy), VIA_DRILL,
                                dr["min_hole_to_hole"], VIA_DRILL)
    vias.extend(stitch)
    pours = make_pours()

    # A pad the pour cannot reach has no ratsnest to flag it, so hunt for
    # marooned GND pads and route them to the nearest poured copper. The
    # orphan test is the same one the DRC uses, so the two cannot disagree.
    import checks as _checks
    rescued = 0
    for _ in range(3):
        probe = {"pours": pours, "pads": pads, "vias": vias, "tracks": tracks}
        _g, _seen, orphans = _checks.gnd_reachability(probe, cfg)
        if not orphans:
            break
        for pd in orphans:
            # Aim at the main ground region specifically -- landing on another
            # isolated island would leave the pad just as marooned.
            target = _main_pour_cells(grid, _seen, pd)
            if not target:
                continue
            path = grid.route("GND", pad_cells(pd), target, via_cost=70)
            if path is None:
                failed.append({"net": "GND", "from": f"{pd['ref']}.{pd['num']}",
                               "to": "ground pour"})
                continue
            segs, vs = R.path_to_tracks(grid, path, TRACK, "GND")
            tracks.extend(segs)
            vias.extend(vs)
            rescued += 1
            for s in segs:
                grid.stamp_segment(s["layer"], s["x0"], s["y0"], s["x1"], s["y1"],
                                   TRACK, "GND", halo)
            for v in vs:
                for L in (0, 1):
                    grid.stamp_disc(L, v["x"], v["y"], VIA_D, "GND", halo)
                grid.block_via_zone(v["x"], v["y"], VIA_DRILL,
                                    dr["min_hole_to_hole"], VIA_DRILL)
        pours = make_pours()

    if verbose and (rescued or crowded):
        print(f"  pour: {rescued} GND pads tied in with tracks; "
              f"{len(crowded)} crowded pads")

    board = {
        "meta": cfg["meta"], "pcb": p, "outline": outline,
        "pads": pads, "npth": npth, "tracks": tracks, "vias": vias,
        "pours": pours, "touch_pads": touch_pads,
        "components": net["components"], "nets": {k: v for k, v in net["nets"].items()},
        "stats": {"components": len(net["components"]), "pads": len(pads),
                  "nets": len(net["nets"]), "tracks": len(tracks), "vias": len(vias),
                  "connections": len(connections), "failed": len(failed),
                  "pour_runs": len(pours["F.Cu"]) + len(pours["B.Cu"]),
                  "gnd_rescued": rescued, "crowded_pads": len(crowded),
                  "build_seconds": round(time.time() - t0, 1)},
        "failed": failed, "crowded": crowded,
    }
    return board, net, grid, cfg


def _via_site_clear(grid, x, y, r, gid):
    """True when a via body at (x, y) touches nothing but free space or GND."""
    for L in (0, 1):
        for j in range(max(0, grid.cy(y - r)), min(grid.h - 1, grid.cy(y + r)) + 1):
            dy = grid.wy(j) - y
            for i in range(max(0, grid.cx(x - r)), min(grid.w - 1, grid.cx(x + r)) + 1):
                dx = grid.wx(i) - x
                if dx * dx + dy * dy > r * r:
                    continue
                if grid.own[L][j * grid.w + i] not in (R.FREE, gid):
                    return False
    return True


def _pour_mask(grid, pours):
    mask = [bytearray(grid.n) for _ in range(2)]
    for lname, L in (("F.Cu", 0), ("B.Cu", 1)):
        for run in pours[lname]:
            j = grid.cy(run["y"])
            if not 0 <= j < grid.h:
                continue
            for i in range(max(0, grid.cx(run["x0"])), min(grid.w - 1, grid.cx(run["x1"])) + 1):
                mask[L][j * grid.w + i] = 1
    return mask


def _orphan_gnd(grid, pours, gnd_pads, gid):
    mask = _pour_mask(grid, pours)
    out = []
    for pd in gnd_pads:
        layers = (0, 1) if pd["layer"] == "*.Cu" else ((0,) if pd["layer"] == "F.Cu" else (1,))
        touching = False
        for L in layers:
            for j in range(grid.cy(pd["y"] - pd["h"] / 2) - 1,
                           grid.cy(pd["y"] + pd["h"] / 2) + 2):
                for i in range(grid.cx(pd["x"] - pd["w"] / 2) - 1,
                               grid.cx(pd["x"] + pd["w"] / 2) + 2):
                    if grid.inside(i, j) and mask[L][j * grid.w + i]:
                        touching = True
        if not touching:
            out.append(pd)
    return out


def _main_pour_cells(grid, seen, pd, radius=20.0):
    """Cells of the main ground region within `radius` of a pad."""
    out = set()
    r = int(radius / grid.pitch)
    ci, cj = grid.cx(pd["x"]), grid.cy(pd["y"])
    for L in (0, 1):
        for j in range(max(0, cj - r), min(grid.h - 1, cj + r) + 1):
            base = j * grid.w
            for i in range(max(0, ci - r), min(grid.w - 1, ci + r) + 1):
                if seen[L][base + i]:
                    out.add((L, i, j))
    return out


def _mst_edges(plist: list[dict]) -> list[tuple[dict, dict]]:
    """Minimum spanning tree over a net's pads, so each net routes as a tree."""
    if len(plist) < 2:
        return []
    inside = [plist[0]]
    outside = plist[1:]
    edges = []
    while outside:
        best = None
        for a in inside:
            for b in outside:
                d = (a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2
                if best is None or d < best[0]:
                    best = (d, a, b)
        _, a, b = best
        edges.append((a, b))
        inside.append(b)
        outside.remove(b)
    return edges


if __name__ == "__main__":
    import outputs
    board, net, grid, cfg = build()
    outputs.write_all(board, net, grid, cfg)
    ok = board["stats"]["failed"] == 0
    print("PCB build:", "OK" if ok else f"INCOMPLETE ({board['stats']['failed']} unrouted)")
    sys.exit(0 if ok else 1)
