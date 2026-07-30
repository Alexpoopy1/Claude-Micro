#!/usr/bin/env python3
"""Full verification suite for Claude Micro.

Runs standalone (`python3 tests/test_all.py`) and under pytest. Covers four
layers:

  geometry  -- the mesh kernel's invariants, plus every printable part being a
               closed, correctly oriented 2-manifold with printable wall
               thicknesses
  layout    -- key pitch, collisions, bezel clearances, the mechanical stack
  circuit   -- netlist ERC, matrix completeness, LED chain continuity, pin use
  board     -- routing completeness, clearances, drill rules, pour connectivity
               and the integrity of the fabrication outputs
"""

from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import mesh as M          # noqa: E402
import parts              # noqa: E402
import circuit            # noqa: E402
import checks             # noqa: E402
from device import load   # noqa: E402

CFG = load()
BUILD = os.path.join(ROOT, "build")

_board_cache = {}


def board():
    """Route the board once and share it across tests."""
    if "b" not in _board_cache:
        import build_pcb
        b, n, g, c = build_pcb.build(verbose=False)
        _board_cache["b"] = (b, n, g, c)
    return _board_cache["b"]


# ==========================================================================
# geometry kernel
# ==========================================================================

def test_kernel_primitives_are_solid():
    cases = {
        "box": M.box(0, 0, 0, 10, 20, 3),
        "cylinder": M.cylinder(0, 0, 0, 8, 5, 48),
        "tube": M.tube(0, 0, 0, 8, 4, 5, 48),
        "cone": M.cone(0, 0, 0, 10, 4, 6, 48),
        "rounded": M.extrude(M.rounded_rect(0, 0, 40, 30, 5, 10), 0, 4),
        "shell": M.prism_shell(M.rounded_rect(0, 0, 40, 30, 5, 10),
                               M.rounded_rect(0, 0, 34, 24, 3, 10), 0, 8),
        "lathe": M.lathe([(0, 0), (13, 0), (13, 10), (11, 12), (0, 12)], 48),
        "wedge": M.wedge(0, 0, 16, 12, 2, 8),
        "burst": M.emboss(M.claude_burst(0, 0, 11.0), 0, 0.6),
        "d_bore": M.extrude(M.circle(0, 0, 20, 40), 0, 5,
                            holes=[M.d_profile(0, 0, 6.2, 4.7, 32)]),
    }
    for name, m in cases.items():
        r = m.validate()
        assert r["ok"], f"{name}: {r['boundary_edges']} boundary, " \
                        f"{r['nonmanifold_edges']} non-manifold, vol {r['volume_mm3']:.2f}"


def test_kernel_volumes_are_right():
    """Analytic checks so a silently mis-wound solid cannot pass as watertight."""
    assert abs(M.box(0, 0, 0, 10, 20, 3).volume() - 600.0) < 1e-6
    cyl = M.cylinder(0, 0, 0, 10, 4, 512).volume()
    assert abs(cyl - math.pi * 25 * 4) / (math.pi * 25 * 4) < 1e-3
    tube = M.tube(0, 0, 0, 10, 6, 4, 512).volume()
    assert abs(tube - math.pi * (25 - 9) * 4) / (math.pi * 16 * 4) < 1e-3
    # A ring extruded as outline-minus-hole must equal the area difference.
    ring = M.extrude(M.rect(0, 0, 20, 20), 0, 2, holes=[M.rect(0, 0, 10, 10)])
    assert abs(ring.volume() - (400 - 100) * 2) < 1e-6


def test_kernel_survives_random_hole_patterns():
    """Fuzz the scanline capper: holes that share scanlines are where a
    triangulator tears."""
    rng = random.Random(20260728)
    for trial in range(12):
        holes = []
        for _ in range(14):
            cx, cy = rng.uniform(-24, 24), rng.uniform(-14, 14)
            r = rng.uniform(1.4, 3.6)
            cand = (M.circle(cx, cy, r * 2, rng.choice([10, 16, 24, 32]))
                    if trial % 2 else M.rect(cx, cy, r * 2, r * 2))
            if any(math.dist((cx, cy), h[0]) < r + h[1] + 1.0 for h in holes):
                continue
            if abs(cx) + r > 27 or abs(cy) + r > 17:
                continue
            holes.append(((cx, cy), r, cand))
        m = M.extrude(M.rounded_rect(0, 0, 60, 40, 6, 9), 0, 3,
                      holes=[h[2] for h in holes])
        r = m.validate()
        assert r["ok"], f"fuzz trial {trial}: {r}"


def test_ring_with_gap_is_a_single_solid():
    outer = M.rounded_rect(47, 28, 134, 94, 5.5, 10)
    inner = M.rounded_rect(47, 28, 129.2, 89.2, 3.1, 10)
    y_out, y_in = -19.0, -16.6
    poly = M.ring_with_gap(outer, inner, (18.0, y_out), (30.0, y_out),
                           (18.0, y_in), (30.0, y_in))
    m = M.extrude(poly, 0, 8)
    r = m.validate()
    assert r["ok"], r
    # The doorway must actually remove material.
    full = M.extrude(outer, 0, 8, holes=[inner]).volume()
    assert m.volume() < full, "ring_with_gap did not cut a doorway"


# ==========================================================================
# printable parts
# ==========================================================================

def test_every_printable_part_is_watertight():
    for p in parts.printable_parts(CFG):
        r = p["mesh"].validate()
        assert r["ok"], (f"{p['name']}: boundary={r['boundary_edges']} "
                         f"nonmanifold={r['nonmanifold_edges']} "
                         f"misoriented={r['misoriented_edges']} "
                         f"degenerate={r['degenerate_triangles']} "
                         f"volume={r['volume_mm3']:.2f}")


def test_parts_fit_a_normal_printer():
    """220 x 220 x 250 mm is the smallest common bed; nothing may exceed it."""
    for p in parts.printable_parts(CFG):
        w, d, h = p["mesh"].size()
        assert w <= 220 and d <= 220 and h <= 250, f"{p['name']} is {w}x{d}x{h} mm"
        assert w > 0.5 and d > 0.5 and h > 0.5, f"{p['name']} is degenerate"


def test_case_halves_and_pcb_agree():
    bottom = parts.case_bottom(CFG).size()
    top = parts.case_top(CFG).size()
    assert abs(bottom[0] - top[0]) < 0.01 and abs(bottom[1] - top[1]) < 0.01, \
        "case halves have different footprints"
    assert abs(bottom[0] - CFG["case"]["width"]) < 0.01
    assert abs(bottom[1] - CFG["case"]["height"]) < 0.01


def test_keycap_walls_are_printable():
    kc = CFG["layout"]["keycap"]
    nozzle = CFG["case"]["print"]["nozzle"]
    assert kc["wall"] >= 2 * nozzle, "keycap wall is thinner than two extrusions"
    assert CFG["layout"]["switch"]["plate_thickness"] >= 3 * nozzle, \
        "switch plate is too thin to print rigidly"
    agent = parts.keycap(CFG, agent=True).volume()
    command = parts.keycap(CFG, agent=False).volume()
    assert agent < command, "the agent cap should have the thinner, translucent roof"


def test_stl_files_are_written_and_readable():
    import struct
    import build_stl
    report = build_stl.build(verbose=False)
    assert report["ok"], "build_stl reported a bad mesh"
    for p in report["parts"]:
        path = os.path.join(BUILD, p["file"])
        assert os.path.exists(path), f"missing {p['file']}"
        with open(path, "rb") as fh:
            head = fh.read(84)
            n = struct.unpack("<I", head[80:84])[0]
            assert n == p["triangles"], f"{p['file']} header says {n} triangles"
            assert os.path.getsize(path) == 84 + n * 50, f"{p['file']} is truncated"


# ==========================================================================
# layout and mechanical fit
# ==========================================================================

def test_key_grid_matches_the_declared_pitch():
    lay = CFG["layout"]
    xs = sorted({k["x"] for k in lay["keys"]})
    ys = sorted({k["y"] for k in lay["keys"]})
    for a, b in zip(xs, xs[1:]):
        assert abs((b - a) - lay["pitch_x"]) < 1e-9, f"column pitch {b - a}"
    for a, b in zip(ys, ys[1:]):
        assert abs((b - a) - lay["pitch_y"]) < 1e-9, f"row pitch {b - a}"


def test_feature_counts_match_the_spec():
    lay = CFG["layout"]
    assert len(lay["keys"]) == 13
    assert len([k for k in lay["keys"] if k["group"] == "agent"]) == 6
    assert len([k for k in lay["keys"] if k["group"] == "command"]) == 7
    assert lay["touch_strip"]["pads"] == 5
    assert CFG["rgb"]["total"] == len(CFG["rgb"]["chain"]) == 19
    assert len(CFG["layers"]) == 6


def test_mechanical_fit_has_no_errors():
    findings = checks.mechanical(CFG)
    errs = [f for f in findings if f["severity"] == checks.ERROR]
    assert not errs, "\n".join(f["message"] for f in errs)


def test_plate_openings_clear_their_hardware():
    """Every plate cutout must be big enough for what passes through it and
    must not eat into a neighbour."""
    cut = CFG["layout"]["switch"]["plate_cutout"]
    body = CFG["layout"]["switch"]["body_w"]
    assert cut < body, "switch cutout is larger than the switch body flange"
    assert cut > 13.5, "switch cutout is too small for a Choc"
    holes = parts.plate_cutouts(CFG)
    boxes = [M.poly_bbox(h) for h in holes]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            overlap = (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])
            assert not overlap, f"plate cutouts overlap: {a} and {b}"


def test_z_stack_is_monotonic():
    st = CFG["stack"]
    order = ["case_floor_bottom", "case_floor_top", "pcb_bottom", "pcb_top",
             "plate_bottom", "plate_top", "bezel_top"]
    for a, b in zip(order, order[1:]):
        assert st[a] < st[b], f"{a} ({st[a]}) is not below {b} ({st[b]})"
    assert st["keycap_top"] > st["bezel_top"], "keycaps are buried in the bezel"
    assert st["knob_top"] > st["keycap_top"], "the dial should stand proud of the caps"


# ==========================================================================
# circuit
# ==========================================================================

def test_netlist_erc_is_clean():
    b, n, _g, c = board()
    findings = checks.erc(b, n, c)
    errs = [f for f in findings if f["severity"] == checks.ERROR]
    assert not errs, "\n".join(f["message"] for f in errs)


def test_every_key_has_a_switch_and_a_diode():
    net = circuit.build_netlist(CFG)
    comps = {c["ref"]: c for c in net["components"]}
    switches = [c for c in net["components"] if c["footprint"] == "Choc_v1"]
    diodes = [c for c in net["components"] if c["value"] == "1N4148W"]
    assert len(switches) == 13
    assert len(diodes) == len(CFG["matrix"]["nodes"]) == 14
    for node in CFG["matrix"]["nodes"]:
        knet = f"K_{node['id']}"
        nodes = net["nets"][knet]
        assert len(nodes) == 2, f"{knet} has {len(nodes)} connections"
        refs = {r for r, _p in nodes}
        assert any(comps[r]["value"] == "1N4148W" for r in refs), \
            f"{knet} has no diode"


def test_matrix_addresses_are_unique_and_in_range():
    seen = {}
    for node in CFG["matrix"]["nodes"]:
        key = (node["row"], node["col"])
        assert key not in seen, f"{node['id']} collides with {seen.get(key)}"
        seen[key] = node["id"]
        assert 0 <= node["row"] < CFG["matrix"]["rows"]
        assert 0 <= node["col"] < CFG["matrix"]["cols"]
    assert len(seen) <= CFG["matrix"]["rows"] * CFG["matrix"]["cols"]


def test_led_chain_is_one_unbroken_run():
    net = circuit.build_netlist(CFG)
    leds = [c for c in net["components"] if c["ref"].startswith("LED")]
    assert len(leds) == 19
    by_din = {c["pins"]["4"]: c for c in leds}
    # Walk from the buffer output to the end; every LED must be visited once.
    cur = by_din.get("LED_DIN")
    seen = []
    while cur is not None and cur["ref"] not in seen:
        seen.append(cur["ref"])
        cur = by_din.get(cur["pins"]["2"])
    assert len(seen) == 19, f"chain reaches {len(seen)} of 19 LEDs: {seen}"


def test_gpio_assignments_do_not_collide():
    net = circuit.build_netlist(CFG)
    mod = next(c for c in net["components"] if c["ref"] == "MOD1")
    used = {}
    for pin, netname in mod["pins"].items():
        if netname in ("GND", "+3V3", "VBUS", ""):
            continue
        gp = CFG["module"]["pinout"][pin]
        assert gp not in used, f"{gp} is claimed by both {used.get(gp)} and {netname}"
        used[gp] = netname
    for pin in CFG["matrix"]["row_pins"] + CFG["matrix"]["col_pins"]:
        assert pin in used, f"{pin} is declared in config but not wired"


def test_led_rail_has_a_level_shifter_and_a_fuse():
    net = circuit.build_netlist(CFG)
    refs = {c["ref"]: c for c in net["components"]}
    assert refs["U2"]["pins"]["5"] == "+5V"
    assert refs["U2"]["pins"]["1"] == "LED_DIN_3V3"
    assert refs["F1"]["pins"]["1"] == "VBUS" and refs["F1"]["pins"]["2"] == "+5V"
    # Every LED must sit on the fused rail, never straight on VBUS.
    for c in net["components"]:
        if c["ref"].startswith("LED"):
            assert c["pins"]["1"] == "+5V", f"{c['ref']} is not on the fused rail"


def test_touch_strip_is_fully_wired():
    net = circuit.build_netlist(CFG)
    for i in range(CFG["layout"]["touch_strip"]["pads"]):
        r = next(c for c in net["components"] if c["ref"] == f"R{5 + i}")
        assert r["value"] == "1M"
        assert r["pins"]["1"] == "TOUCH_DRV"
        assert r["pins"]["2"] == f"TOUCH{i}"


# ==========================================================================
# board
# ==========================================================================

def test_board_is_fully_routed():
    b, _n, _g, _c = board()
    assert b["stats"]["failed"] == 0, f"unrouted: {b['failed']}"
    assert b["stats"]["connections"] > 100
    assert not b["crowded"], f"crowded pads: {b['crowded']}"


def test_board_drc_is_clean():
    b, _n, g, c = board()
    findings = checks.drc(b, c, g)
    errs = [f for f in findings if f["severity"] == checks.ERROR]
    assert not errs, "\n".join(f["message"] for f in errs)


def test_ground_pour_reaches_every_ground_pad():
    b, _n, _g, c = board()
    findings = checks.pour_connectivity(b, c)
    errs = [f for f in findings if f["severity"] == checks.ERROR]
    assert not errs, "\n".join(f["message"] for f in errs)


def test_all_copper_is_inside_the_board():
    b, _n, _g, c = board()
    outline = b["outline"]
    for t in b["tracks"]:
        for (x, y) in ((t["x0"], t["y0"]), (t["x1"], t["y1"])):
            assert M.point_in_poly((x, y), outline), f"track on {t['net']} exits the board"
    for v in b["vias"]:
        assert M.point_in_poly((v["x"], v["y"]), outline), "via outside the board"
    for pd in b["pads"]:
        assert M.point_in_poly((pd["x"], pd["y"]), outline), \
            f"pad {pd['ref']}.{pd['num']} outside the board"


def test_components_stay_inside_the_case_cavity():
    b, _n, _g, c = board()
    cav = c["case"]
    x0 = cav["outer"]["x0"] + cav["wall"]
    x1 = cav["outer"]["x1"] - cav["wall"]
    y0 = cav["outer"]["y0"] + cav["wall"]
    y1 = cav["outer"]["y1"] - cav["wall"]
    for comp in b["components"]:
        fp = circuit.FOOTPRINTS[comp["footprint"]]
        cw, ch = fp["courtyard"]
        assert x0 <= comp["x"] - cw / 2 and comp["x"] + cw / 2 <= x1, \
            f"{comp['ref']} overhangs the cavity in X"
        assert y0 <= comp["y"] - ch / 2 and comp["y"] + ch / 2 <= y1, \
            f"{comp['ref']} overhangs the cavity in Y"


def test_courtyards_do_not_overlap_on_the_same_side():
    """Two parts on the same face may not share space.

    Two overlaps are legitimate and are allowed explicitly:
      * a per-key LED sits inside its switch's light window;
      * the MCU module bridges the diodes and 0603s beneath it, because it is
        mounted on a 2.54 mm header that lifts it clear of them.
    """
    b, _n, _g, c = board()
    standoff = c["module"]["standoff"]

    items = []
    for comp in b["components"]:
        fp = circuit.FOOTPRINTS[comp["footprint"]]
        cw, ch = fp["courtyard"]
        if comp["rot"] % 180:
            cw, ch = ch, cw
        items.append({"ref": comp["ref"], "side": comp["side"], "x": comp["x"],
                      "y": comp["y"], "w": cw, "h": ch, "fp": comp["footprint"],
                      "z": circuit.part_height(comp["footprint"])})

    def allowed(a, bb):
        pair = {a["fp"], bb["fp"]}
        if pair == {"Choc_v1", "SK6812MINI"}:
            return True     # LED inside the switch's light window
        if "RP2040_Module_Pico" in pair:
            other = bb if a["fp"] == "RP2040_Module_Pico" else a
            return other["z"] <= standoff
        return False

    bad = []
    for i, a in enumerate(items):
        for bb in items[i + 1:]:
            if a["side"] != bb["side"] or allowed(a, bb):
                continue
            dx = abs(a["x"] - bb["x"]) - (a["w"] + bb["w"]) / 2
            dy = abs(a["y"] - bb["y"]) - (a["h"] + bb["h"]) / 2
            if dx < -0.05 and dy < -0.05:
                bad.append(f"{a['ref']} ({a['fp']}, {a['z']} mm) and "
                           f"{bb['ref']} ({bb['fp']}, {bb['z']} mm) overlap by "
                           f"{-max(dx, dy):.2f} mm on the {a['side']}")
    assert not bad, "\n".join(bad[:10])


def test_parts_under_the_module_clear_its_standoff():
    """Anything the module spans has to fit in the gap under it."""
    b, _n, _g, c = board()
    mod = c["module"]
    fp = circuit.FOOTPRINTS["RP2040_Module_Pico"]
    mw, mh = fp["courtyard"]
    tall = []
    for comp in b["components"]:
        if comp["ref"] == "MOD1" or comp["side"] != mod["side"]:
            continue
        if (abs(comp["x"] - mod["x"]) < mw / 2 and abs(comp["y"] - mod["y"]) < mh / 2):
            z = circuit.part_height(comp["footprint"])
            if z > mod["standoff"]:
                tall.append(f"{comp['ref']} is {z} mm tall under a "
                            f"{mod['standoff']} mm standoff")
    assert not tall, "\n".join(tall)


def test_mount_holes_line_up_across_pcb_and_case():
    b, _n, _g, c = board()
    tray = parts.case_bottom(c)
    assert tray.validate()["ok"]
    for h in c["pcb"]["mount_holes"]:
        assert c["pcb"]["x0"] + 2 < h["x"] < c["pcb"]["x1"] - 2
        assert c["pcb"]["y0"] + 2 < h["y"] < c["pcb"]["y1"] - 2
        # far enough from copper features to leave room for the boss
        for pd in b["pads"]:
            d = math.dist((h["x"], h["y"]), (pd["x"], pd["y"]))
            assert d > c["case"]["screw"]["boss_d"] / 2, \
                f"pad {pd['ref']}.{pd['num']} sits under a mounting boss"


# ==========================================================================
# fabrication outputs
# ==========================================================================

def test_fabrication_outputs_exist_and_parse():
    pcb = os.path.join(BUILD, "pcb")
    if not os.path.exists(os.path.join(pcb, "claude-micro.kicad_pcb")):
        import build_pcb
        import outputs
        b, n, g, c = board()
        outputs.write_all(b, n, g, c, verbose=False)

    kpcb = open(os.path.join(pcb, "claude-micro.kicad_pcb")).read()
    assert kpcb.startswith("(kicad_pcb")
    assert kpcb.count("(") == kpcb.count(")"), "unbalanced s-expression in .kicad_pcb"
    assert "(footprint" in kpcb and "(segment" in kpcb and "(zone" in kpcb

    ksch = open(os.path.join(pcb, "claude-micro.kicad_sch")).read()
    assert ksch.startswith("(kicad_sch")
    assert ksch.count("(") == ksch.count(")"), "unbalanced s-expression in .kicad_sch"

    json.load(open(os.path.join(pcb, "claude-micro.kicad_pro")))
    netf = open(os.path.join(pcb, "claude-micro.net")).read()
    assert netf.count("(") == netf.count(")")

    gerb = os.path.join(pcb, "gerbers")
    expected = ["F_Cu.gbr", "B_Cu.gbr", "F_Mask.gbr", "B_Mask.gbr", "F_Silkscreen.gbr",
                "B_Silkscreen.gbr", "F_Paste.gbr", "B_Paste.gbr", "Edge_Cuts.gbr",
                "claude-micro-PTH.drl", "claude-micro-NPTH.drl"]
    for f in expected:
        path = os.path.join(gerb, f)
        assert os.path.exists(path), f"missing {f}"
        body = open(path).read()
        assert len(body) > 200, f"{f} is suspiciously small"
        if f.endswith(".gbr"):
            assert body.startswith("%FSLAX46Y46*%"), f"{f} has no format spec"
            assert body.rstrip().endswith("M02*"), f"{f} is not terminated"
            assert "%MOMM*%" in body, f"{f} does not declare millimetres"
        else:
            assert body.startswith("M48") and "M30" in body, f"{f} is not Excellon"


def test_drill_file_covers_every_hole():
    b, _n, _g, _c = board()
    pth = open(os.path.join(BUILD, "pcb", "gerbers", "claude-micro-PTH.drl")).read()
    npth = open(os.path.join(BUILD, "pcb", "gerbers", "claude-micro-NPTH.drl")).read()
    n_pth = sum(1 for line in pth.splitlines() if line.startswith("X"))
    n_npth = sum(1 for line in npth.splitlines() if line.startswith("X"))
    want_pth = len([p for p in b["pads"] if p["type"] == "tht" and p["drill"] > 0]) \
        + len(b["vias"])
    assert n_pth == want_pth, f"{n_pth} plated holes drilled, expected {want_pth}"
    assert n_npth == len(b["npth"]), \
        f"{n_npth} non-plated holes drilled, expected {len(b['npth'])}"


def test_bom_and_placement_cover_every_component():
    import csv as _csv
    b, _n, _g, _c = board()
    with open(os.path.join(BUILD, "pcb", "bom.csv")) as fh:
        rows = list(_csv.DictReader(fh))
    refs = set()
    for r in rows:
        refs.update(r["References"].split())
        assert int(r["Qty"]) == len(r["References"].split()), f"bad qty on {r['Value']}"
    assert refs == {c["ref"] for c in b["components"]}, "BOM does not match the netlist"

    with open(os.path.join(BUILD, "pcb", "positions.csv")) as fh:
        pos = list(_csv.DictReader(fh))
    assert {p["Ref"] for p in pos} == refs
    for p in pos:
        assert p["Side"] in ("Top", "Bottom")


def test_easyeda_export_is_well_formed():
    """Shape strings must match EasyEDA's documented field orders exactly, and
    every id must be unique -- EasyEDA silently drops shapes otherwise."""
    import build_easyeda
    path = build_easyeda.build(verbose=False)
    doc = json.load(open(path))

    assert doc["docType"] == 5 and len(doc["schematics"]) == 1
    data = doc["schematics"][0]["dataStr"]
    assert set(data) >= {"head", "canvas", "shape", "BBox", "colors"}
    assert data["head"]["docType"] == "1"
    assert data["canvas"].startswith("CA~")
    assert len(data["canvas"].split("~")) == 15

    fields = {"W": 7, "N": 12, "T": 16, "R": 12}
    for s in data["shape"]:
        kind = s.split("~")[0]
        if kind in fields:
            assert len(s.split("~")) == fields[kind], f"bad {kind}: {s[:80]}"
        elif kind == "LIB":
            head, *children = s.split("#@$")
            assert len(head.split("~")) == 7, f"bad LIB header: {head[:80]}"
            assert children, "LIB with no child shapes"
            for ch in children:
                ck = ch.split("~")[0]
                if ck == "P":
                    assert len(ch.split("^^")) == 7, f"bad pin: {ch[:80]}"
                elif ck in fields:
                    assert len(ch.split("~")) == fields[ck], f"bad {ck}: {ch[:80]}"
        else:
            raise AssertionError(f"unexpected shape type {kind}")

    ids = [t for s in data["shape"]
           for t in s.replace("#@$", "~").replace("^^", "~").split("~")
           if t.startswith("gge")]
    assert len(ids) == len(set(ids)), "duplicate gge ids"


def test_easyeda_export_has_the_same_netlist():
    """Rebuild connectivity from the exported file the way EasyEDA does --
    pin dot to wire to net label -- and compare it against the source netlist."""
    import build_easyeda
    from collections import defaultdict
    doc = json.load(open(build_easyeda.build(verbose=False)))
    shapes = doc["schematics"][0]["dataStr"]["shape"]

    pins, wires, labels = {}, defaultdict(set), {}
    for s in shapes:
        if s.startswith("W~"):
            c = [float(v) for v in s.split("~")[1].split()]
            a, b = (c[0], c[1]), (c[2], c[3])
            wires[a].add(b)
            wires[b].add(a)
        elif s.startswith("N~"):
            f = s.split("~")
            labels[(float(f[1]), float(f[2]))] = f[5]
        elif s.startswith("LIB"):
            parts = s.split("#@$")
            ref = next(p.split("~")[12] for p in parts if p.startswith("T~P~"))
            for ch in parts:
                if ch.startswith("P~"):
                    seg = ch.split("^^")
                    x, y = seg[1].split("~")
                    pins[(float(x), float(y))] = (ref, seg[0].split("~")[3])

    recovered = defaultdict(set)
    unlabelled = []
    for pt, node in pins.items():
        seen, stack, found = {pt}, [pt], None
        while stack:
            p = stack.pop()
            if p in labels:
                found = labels[p]
                break
            for q in wires.get(p, ()):
                if q not in seen:
                    seen.add(q)
                    stack.append(q)
        (recovered[found].add(node) if found else unlabelled.append(node))

    net = circuit.build_netlist(CFG)
    expected = defaultdict(set)
    for c in net["components"]:
        for pin, n in c["pins"].items():
            if n:
                expected[n].add((c["ref"], pin))

    assert set(recovered) == set(expected), (
        f"net mismatch: missing {set(expected) - set(recovered)}, "
        f"extra {set(recovered) - set(expected)}")
    for name in expected:
        assert recovered[name] == expected[name], (
            f"net {name} differs: {expected[name] ^ recovered[name]}")

    # Only genuinely no-connect pins may lack a label.
    allowed = {("MOD1", "37"), ("MOD1", "39"), ("U2", "3")}
    assert set(unlabelled) <= allowed, f"unlabelled pins: {set(unlabelled) - allowed}"

    total_pads = sum(len(circuit.pad_positions(c, net["footprints"]))
                     for c in net["components"])
    assert len(pins) == total_pads, f"{len(pins)} pins exported, {total_pads} pads exist"


def test_viewer_is_self_contained():
    html_path = os.path.join(BUILD, "claude-micro.html")
    if not os.path.exists(html_path):
        import build_html
        build_html.build(verbose=False)
    html = open(html_path).read()
    assert "const DATA = {" in html
    # XML namespace URIs are identifiers, not fetches; everything else that
    # looks like a remote resource is a bug.
    stripped = (html.replace("http://www.w3.org/2000/svg", "")
                    .replace("http://www.w3.org/1999/xlink", ""))
    for bad in ("http://", "https://", 'src="//', "cdn.", "fetch(", "XMLHttpRequest"):
        assert bad not in stripped, f"viewer references an external resource ({bad})"
    assert "<svg class=\"schem\"" in html, "schematic missing from the viewer"
    assert html.count("<canvas") >= 2, "3D and PCB canvases expected"
    for tab in ("3D model", "PCB", "Schematic", "Bill of materials",
                "Print & assemble", "Firmware", "Verification"):
        assert tab in html, f"missing tab: {tab}"


def test_firmware_matches_the_config():
    fw = os.path.join(ROOT, "firmware", "qmk", "claude_micro")
    cfgh = open(os.path.join(fw, "config.h")).read()
    info = json.load(open(os.path.join(fw, "keyboard.json")))
    assert info["matrix_pins"]["rows"] == CFG["matrix"]["row_pins"]
    assert info["matrix_pins"]["cols"] == CFG["matrix"]["col_pins"]
    assert info["rgb_matrix"]["led_count"] == CFG["rgb"]["total"]
    assert info["encoder"]["rotary"][0]["pin_a"] == CFG["pins"]["encoder_a"]
    assert info["encoder"]["rotary"][0]["pin_b"] == CFG["pins"]["encoder_b"]
    assert info["ws2812"]["pin"] == CFG["pins"]["led_data"]
    assert len(info["layouts"]["LAYOUT"]["layout"]) == len(CFG["matrix"]["nodes"]) + 1
    assert f'#define TOUCH_PAD_COUNT {CFG["layout"]["touch_strip"]["pads"]}' in cfgh
    km = open(os.path.join(fw, "keymaps", "default", "keymap.c")).read()
    for layer in CFG["layers"]:
        assert layer["name"] in km, f"layer {layer['name']} missing from the keymap"


# ==========================================================================

def main() -> int:
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    width = max(len(k) for k, _ in tests)
    failed = []
    print(f"Claude Micro :: {len(tests)} checks\n")
    for name, fn in tests:
        try:
            fn()
            print(f"  pass  {name.ljust(width)}")
        except AssertionError as e:
            failed.append((name, str(e)))
            print(f"  FAIL  {name.ljust(width)}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, f"{type(e).__name__}: {e}"))
            print(f"  ERROR {name.ljust(width)}")
    print()
    if failed:
        for name, msg in failed:
            print(f"--- {name}\n{msg}\n")
        print(f"{len(failed)} of {len(tests)} checks failed")
        return 1
    print(f"all {len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
