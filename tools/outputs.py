"""Fabrication and viewer outputs for the routed board.

Writes a KiCad project (schematic + PCB + netlist), a complete RS-274X gerber
set with Excellon drill files, assembly CSVs, and the JSON the HTML viewer
renders. Nothing here is hand-maintained -- it is all a projection of the
board dict produced by build_pcb.build().
"""

from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict

import mesh as M
import circuit
from device import ensure_build

PCB_LAYERS = [
    ("F.Cu", "F_Cu.gbr", "Copper,L1,Top"),
    ("B.Cu", "B_Cu.gbr", "Copper,L2,Bot"),
    ("F.Mask", "F_Mask.gbr", "Soldermask,Top"),
    ("B.Mask", "B_Mask.gbr", "Soldermask,Bot"),
    ("F.SilkS", "F_Silkscreen.gbr", "Legend,Top"),
    ("B.SilkS", "B_Silkscreen.gbr", "Legend,Bot"),
    ("F.Paste", "F_Paste.gbr", "Paste,Top"),
    ("B.Paste", "B_Paste.gbr", "Paste,Bot"),
    ("Edge.Cuts", "Edge_Cuts.gbr", "Profile,NP"),
]


# --------------------------------------------------------------------------
# gerber
# --------------------------------------------------------------------------

class Gerber:
    """Minimal RS-274X writer: circular/rect apertures, draws, flashes, regions."""

    def __init__(self, function: str, x0: float, y1: float):
        self.fn = function
        self.x0, self.y1 = x0, y1          # gerber origin: board bottom-left, Y up
        self.apertures: dict[str, int] = {}
        self.body: list[str] = []
        self.cur = None

    def _xy(self, x: float, y: float) -> str:
        gx = round((x - self.x0) * 1e6)
        gy = round((self.y1 - y) * 1e6)
        return f"X{gx}Y{gy}"

    def aperture(self, spec: str) -> int:
        if spec not in self.apertures:
            self.apertures[spec] = 10 + len(self.apertures)
        return self.apertures[spec]

    def _use(self, spec: str) -> None:
        d = self.aperture(spec)
        if self.cur != d:
            self.body.append(f"D{d}*")
            self.cur = d

    def circle(self, d: float) -> str:
        return f"C,{d:.6f}"

    def rect(self, w: float, h: float) -> str:
        return f"R,{w:.6f}X{h:.6f}"

    def obround(self, w: float, h: float) -> str:
        return f"O,{w:.6f}X{h:.6f}"

    def flash(self, spec: str, x: float, y: float) -> None:
        self._use(spec)
        self.body.append(f"{self._xy(x, y)}D03*")

    def draw(self, spec: str, x0: float, y0: float, x1: float, y1: float) -> None:
        self._use(spec)
        self.body.append(f"{self._xy(x0, y0)}D02*")
        self.body.append(f"{self._xy(x1, y1)}D01*")

    def region(self, pts) -> None:
        self.body.append("G36*")
        self.body.append(f"{self._xy(*pts[0])}D02*")
        for p in pts[1:]:
            self.body.append(f"{self._xy(*p)}D01*")
        self.body.append(f"{self._xy(*pts[0])}D01*")
        self.body.append("G37*")

    def render(self) -> str:
        out = [
            "%FSLAX46Y46*%",
            "%MOMM*%",
            f"%TF.FileFunction,{self.fn}*%",
            "%TF.Part,Single*%",
            "%TF.GenerationSoftware,ClaudeMicro,build_pcb.py,1.0*%",
            "%LPD*%",
            "G01*",
        ]
        for spec, num in sorted(self.apertures.items(), key=lambda kv: kv[1]):
            out.append(f"%ADD{num}{spec}*%")
        out.extend(self.body)
        out.append("M02*")
        return "\n".join(out) + "\n"


def _pad_spec(g: Gerber, pd: dict, grow: float = 0.0) -> str:
    if pd["shape"] == "circle":
        return g.circle(max(pd["w"], pd["h"]) + 2 * grow)
    if pd["shape"] == "oval":
        return g.obround(pd["w"] + 2 * grow, pd["h"] + 2 * grow)
    return g.rect(pd["w"] + 2 * grow, pd["h"] + 2 * grow)


def write_gerbers(board: dict, cfg: dict, outdir: str) -> list[str]:
    p = cfg["pcb"]
    x0, y1 = p["x0"], p["y1"]
    written = []
    files: dict[str, Gerber] = {name: Gerber(fn, x0, y1) for name, _f, fn in
                               [(n, f, fu) for n, f, fu in PCB_LAYERS]}

    def on(layer_name):
        return files[layer_name]

    # --- copper ---------------------------------------------------------
    for li, lname in ((0, "F.Cu"), (1, "B.Cu")):
        g = on(lname)
        pour_spec = g.rect(0.22, 0.22)
        for run in board["pours"][lname]:
            g.draw(pour_spec, run["x0"], run["y"], run["x1"], run["y"])
        for t in board["tracks"]:
            if t["layer"] != li:
                continue
            g.draw(g.circle(t["width"]), t["x0"], t["y0"], t["x1"], t["y1"])
        for v in board["vias"]:
            g.flash(g.circle(0.7), v["x"], v["y"])
        for pd in board["pads"]:
            if pd["layer"] == "*.Cu" or pd["layer"] == lname:
                g.flash(_pad_spec(g, pd), pd["x"], pd["y"])
        for tp in board["touch_pads"]:
            if lname == "F.Cu":
                g.region([(tp["x0"], tp["y0"]), (tp["x1"], tp["y0"]),
                          (tp["x1"], tp["y1"]), (tp["x0"], tp["y1"])])

    # --- soldermask (openings over pads) ---------------------------------
    for lname in ("F.Mask", "B.Mask"):
        g = on(lname)
        side = "F.Cu" if lname == "F.Mask" else "B.Cu"
        for pd in board["pads"]:
            if pd["layer"] == "*.Cu" or pd["layer"] == side:
                g.flash(_pad_spec(g, pd, 0.05), pd["x"], pd["y"])
        for v in board["vias"]:
            g.flash(g.circle(0.4), v["x"], v["y"])
        if lname == "F.Mask":
            for tp in board["touch_pads"]:
                g.region([(tp["x0"] - 0.05, tp["y0"] - 0.05), (tp["x1"] + 0.05, tp["y0"] - 0.05),
                          (tp["x1"] + 0.05, tp["y1"] + 0.05), (tp["x0"] - 0.05, tp["y1"] + 0.05)])

    # --- paste (SMD pads only) -------------------------------------------
    for lname in ("F.Paste", "B.Paste"):
        g = on(lname)
        side = "F.Cu" if lname == "F.Paste" else "B.Cu"
        for pd in board["pads"]:
            if pd["type"] == "smd" and pd["layer"] == side:
                g.flash(_pad_spec(g, pd), pd["x"], pd["y"])

    # --- silkscreen -------------------------------------------------------
    for lname in ("F.SilkS", "B.SilkS"):
        g = on(lname)
        side = "top" if lname == "F.SilkS" else "bottom"
        line = g.circle(0.15)
        for c in board["components"]:
            if c["side"] != side:
                continue
            fp = circuit.FOOTPRINTS[c["footprint"]]
            pts = M.transform_poly(fp["outline"], c["x"], c["y"], c["rot"])
            for i in range(len(pts)):
                a, b = pts[i], pts[(i + 1) % len(pts)]
                g.draw(line, a[0], a[1], b[0], b[1])
        if lname == "F.SilkS":
            lg = cfg["layout"]["logo"]
            for blade in M.claude_burst(lg["x"], lg["y"], lg["size"]):
                g.region(blade)
            for tp in board["touch_pads"]:
                pass

    # --- board profile ----------------------------------------------------
    g = on("Edge.Cuts")
    line = g.circle(0.1)
    o = board["outline"]
    for i in range(len(o)):
        a, b = o[i], o[(i + 1) % len(o)]
        g.draw(line, a[0], a[1], b[0], b[1])

    for name, fname, _fn in PCB_LAYERS:
        path = os.path.join(outdir, fname)
        with open(path, "w") as fh:
            fh.write(files[name].render())
        written.append(fname)
    return written


def write_drill(board: dict, cfg: dict, outdir: str) -> list[str]:
    """Excellon files, split into plated and non-plated as fabs expect."""
    p = cfg["pcb"]

    def emit(path, holes, plated):
        by_size = defaultdict(list)
        for h in holes:
            by_size[round(h["d"], 3)].append(h)
        lines = ["M48", "; CLAUDE MICRO", "FMAT,2", "METRIC,TZ",
                 f"; {'PLATED' if plated else 'NON-PLATED'}"]
        for i, d in enumerate(sorted(by_size), start=1):
            lines.append(f"T{i}C{d:.3f}")
        lines += ["%", "G90", "G05"]
        for i, d in enumerate(sorted(by_size), start=1):
            lines.append(f"T{i}")
            for h in by_size[d]:
                lines.append(f"X{h['x'] - p['x0']:.3f}Y{p['y1'] - h['y']:.3f}")
        lines += ["T0", "M30", ""]
        with open(path, "w") as fh:
            fh.write("\n".join(lines))

    plated = [{"x": pd["x"], "y": pd["y"], "d": pd["drill"]}
              for pd in board["pads"] if pd["type"] == "tht" and pd["drill"] > 0]
    plated += [{"x": v["x"], "y": v["y"], "d": 0.35} for v in board["vias"]]
    nonplated = [{"x": h["x"], "y": h["y"], "d": h["d"]} for h in board["npth"]]

    emit(os.path.join(outdir, "claude-micro-PTH.drl"), plated, True)
    emit(os.path.join(outdir, "claude-micro-NPTH.drl"), nonplated, False)
    return ["claude-micro-PTH.drl", "claude-micro-NPTH.drl"]


# --------------------------------------------------------------------------
# KiCad
# --------------------------------------------------------------------------

def _kicad_layer(l):
    return {0: "F.Cu", 1: "B.Cu"}[l]


def write_kicad_pcb(board: dict, net: dict, cfg: dict, path: str) -> None:
    p = cfg["pcb"]
    net_names = ["", *sorted(set(list(net["nets"].keys())
                                 + [f"TOUCH{i}" for i in range(len(board['touch_pads']))]))]
    net_idx = {n: i for i, n in enumerate(net_names)}

    out = ['(kicad_pcb (version 20221018) (generator claude_micro)',
           '  (general (thickness %.2f))' % p["thickness"],
           '  (paper "A4")',
           '  (layers',
           '    (0 "F.Cu" signal) (31 "B.Cu" signal)',
           '    (32 "B.Adhes" user) (33 "F.Adhes" user)',
           '    (34 "B.Paste" user) (35 "F.Paste" user)',
           '    (36 "B.SilkS" user) (37 "F.SilkS" user)',
           '    (38 "B.Mask" user) (39 "F.Mask" user)',
           '    (44 "Edge.Cuts" user) (45 "Margin" user)',
           '  )',
           '  (setup (pad_to_mask_clearance 0.05))']
    for n, i in net_idx.items():
        out.append(f'  (net {i} "{n}")')

    o = board["outline"]
    for i in range(len(o)):
        a, b = o[i], o[(i + 1) % len(o)]
        out.append(f'  (gr_line (start {a[0]:.4f} {a[1]:.4f}) (end {b[0]:.4f} {b[1]:.4f}) '
                   f'(stroke (width 0.1) (type solid)) (layer "Edge.Cuts"))')

    for c in board["components"]:
        fp = circuit.FOOTPRINTS[c["footprint"]]
        layer = "F.Cu" if c["side"] == "top" else "B.Cu"
        out.append(f'  (footprint "claude-micro:{c["footprint"]}" (layer "{layer}")')
        out.append(f'    (at {c["x"]:.4f} {c["y"]:.4f} {c["rot"]})')
        out.append(f'    (property "Reference" "{c["ref"]}")')
        out.append(f'    (property "Value" "{c["value"]}")')
        out.append(f'    (attr {"smd" if fp["kind"] == "smd" else "through_hole"})')
        out.append(f'    (fp_text reference "{c["ref"]}" (at 0 -2.2) (layer "F.SilkS") '
                   f'(effects (font (size 0.8 0.8) (thickness 0.12))))')
        out.append(f'    (fp_text value "{c["value"]}" (at 0 2.2) (layer "F.Fab") hide '
                   f'(effects (font (size 0.8 0.8) (thickness 0.12))))')
        pts = fp["outline"]
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            sl = "F.SilkS" if c["side"] == "top" else "B.SilkS"
            out.append(f'    (fp_line (start {a[0]:.3f} {a[1]:.3f}) (end {b[0]:.3f} {b[1]:.3f}) '
                       f'(stroke (width 0.12) (type solid)) (layer "{sl}"))')
        flip = -1.0 if c["side"] == "bottom" else 1.0
        for pdf in fp["pads"]:
            n = c["pins"].get(pdf["num"], "")
            ni = net_idx.get(n, 0)
            if pdf["type"] == "tht":
                shape = "circle" if pdf["shape"] == "circle" else "rect"
                lay = '(layers "*.Cu" "*.Mask")'
                drill = f'(drill {pdf["drill"]:.3f})'
                ptype = "thru_hole"
            else:
                shape = "roundrect" if pdf["shape"] == "rect" else "circle"
                side = "F" if c["side"] == "top" else "B"
                lay = f'(layers "{side}.Cu" "{side}.Paste" "{side}.Mask")'
                drill = ""
                ptype = "smd"
            rr = " (roundrect_rratio 0.25)" if shape == "roundrect" else ""
            out.append(f'    (pad "{pdf["num"]}" {ptype} {shape} '
                       f'(at {pdf["x"] * flip:.3f} {pdf["y"]:.3f}) '
                       f'(size {pdf["w"]:.3f} {pdf["h"]:.3f}) {drill} {lay}{rr} '
                       f'(net {ni} "{n}"))')
        for h in fp.get("npth", []):
            out.append(f'    (pad "" np_thru_hole circle (at {h["x"] * flip:.3f} {h["y"]:.3f}) '
                       f'(size {h["d"]:.3f} {h["d"]:.3f}) (drill {h["d"]:.3f}) '
                       f'(layers "*.Cu" "*.Mask"))')
        out.append('  )')

    for h in cfg["pcb"]["mount_holes"]:
        out.append(f'  (footprint "claude-micro:MountingHole" (layer "F.Cu") '
                   f'(at {h["x"]:.3f} {h["y"]:.3f}) (attr through_hole) '
                   f'(pad "" np_thru_hole circle (at 0 0) (size {h["d"]:.2f} {h["d"]:.2f}) '
                   f'(drill {h["d"]:.2f}) (layers "*.Cu" "*.Mask")))')

    for t in board["tracks"]:
        out.append(f'  (segment (start {t["x0"]:.4f} {t["y0"]:.4f}) '
                   f'(end {t["x1"]:.4f} {t["y1"]:.4f}) (width {t["width"]:.3f}) '
                   f'(layer "{_kicad_layer(t["layer"])}") (net {net_idx.get(t["net"], 0)}))')
    for v in board["vias"]:
        out.append(f'  (via (at {v["x"]:.4f} {v["y"]:.4f}) (size 0.7) (drill 0.35) '
                   f'(layers "F.Cu" "B.Cu") (net {net_idx.get(v["net"], 0)}))')

    # Real KiCad zones, so a refill in KiCad reproduces the pour natively.
    for layer in ("F.Cu", "B.Cu"):
        pts = " ".join(f"(xy {x:.3f} {y:.3f})" for x, y in board["outline"])
        out.append(f'  (zone (net {net_idx.get("GND", 0)}) (net_name "GND") (layer "{layer}") '
                   f'(hatch edge 0.5) (connect_pads (clearance 0.25)) '
                   f'(min_thickness 0.2) (filled_areas_thickness no) '
                   f'(fill yes (thermal_gap 0.3) (thermal_bridge_width 0.5)) '
                   f'(polygon (pts {pts})))')

    out.append(')')
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")


def write_kicad_sch(board: dict, net: dict, cfg: dict, path: str) -> None:
    """Flat schematic where every pin carries a global label.

    Generating routed wires for 89 parts is not worth it; net labels give KiCad
    exactly the same connectivity and stay readable.
    """
    import hashlib

    def uuid_for(*parts):
        h = hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    syms: dict[str, dict] = {}
    for c in board["components"]:
        fp = circuit.FOOTPRINTS[c["footprint"]]
        syms.setdefault(c["footprint"], {"pins": [p["num"] for p in fp["pads"]],
                                         "names": {p["num"]: p["name"] for p in fp["pads"]}})

    out = ['(kicad_sch (version 20230121) (generator claude_micro)',
           f'  (uuid {uuid_for("root")})', '  (paper "A2")',
           '  (title_block (title "Claude Micro") '
           f'(date "") (rev "{cfg["meta"]["version"]}") '
           '(company "Open Hardware"))',
           '  (lib_symbols']
    for fpname, s in syms.items():
        n = len(s["pins"])
        h = max(2.54 * (n + 1), 7.62)
        out.append(f'    (symbol "claude-micro:{fpname}" (pin_names (offset 0.5)) '
                   f'(in_bom yes) (on_board yes)')
        out.append(f'      (property "Reference" "U" (at 0 {h/2+1.27:.2f} 0) '
                   f'(effects (font (size 1.27 1.27))))')
        out.append(f'      (property "Value" "{fpname}" (at 0 {-h/2-1.27:.2f} 0) '
                   f'(effects (font (size 1.27 1.27))))')
        out.append(f'      (symbol "{fpname}_0_1"')
        out.append(f'        (rectangle (start -6.35 {h/2:.2f}) (end 6.35 {-h/2:.2f}) '
                   f'(stroke (width 0.254) (type default)) (fill (type background))))')
        out.append(f'      (symbol "{fpname}_1_1"')
        for i, pn in enumerate(s["pins"]):
            y = h / 2 - 2.54 * (i + 1)
            out.append(f'        (pin passive line (at -11.43 {y:.2f} 0) (length 5.08) '
                       f'(name "{s["names"][pn]}" (effects (font (size 1.0 1.0)))) '
                       f'(number "{pn}" (effects (font (size 1.0 1.0)))))')
        out.append('      )')
        out.append('    )')
    out.append('  )')

    # Place parts on a grid; each pin gets a stub wire and a global label.
    col_w, row_h = 63.5, 0.0
    x, y = 30.0, 30.0
    col = 0
    for c in sorted(board["components"], key=lambda c: (c["group"], c["ref"])):
        fp = circuit.FOOTPRINTS[c["footprint"]]
        n = len(fp["pads"])
        h = max(2.54 * (n + 1), 7.62)
        if y + h > 560:
            col += 1
            x, y = 30.0 + col * col_w, 30.0
        cy = y + h / 2
        uid = uuid_for(c["ref"])
        out.append(f'  (symbol (lib_id "claude-micro:{c["footprint"]}") '
                   f'(at {x:.2f} {cy:.2f} 0) (unit 1) (in_bom yes) (on_board yes) (uuid {uid})')
        out.append(f'    (property "Reference" "{c["ref"]}" (at {x:.2f} {y - 1.27:.2f} 0) '
                   f'(effects (font (size 1.27 1.27))))')
        out.append(f'    (property "Value" "{c["value"]}" (at {x:.2f} {y + h + 1.27:.2f} 0) '
                   f'(effects (font (size 1.0 1.0))))')
        out.append(f'    (property "Footprint" "claude-micro:{c["footprint"]}" '
                   f'(at {x:.2f} {cy:.2f} 0) (effects (font (size 1.27 1.27)) hide))')
        out.append('  )')
        for i, pdf in enumerate(fp["pads"]):
            py = cy + h / 2 - 2.54 * (i + 1)
            netn = c["pins"].get(pdf["num"], "")
            if not netn:
                continue
            out.append(f'  (wire (pts (xy {x - 11.43:.2f} {py:.2f}) (xy {x - 20.32:.2f} {py:.2f})) '
                       f'(stroke (width 0) (type default)) (uuid {uuid_for(c["ref"], pdf["num"])}))')
            out.append(f'  (global_label "{netn}" (shape input) '
                       f'(at {x - 20.32:.2f} {py:.2f} 180) '
                       f'(effects (font (size 1.0 1.0)) (justify right)) '
                       f'(uuid {uuid_for("L", c["ref"], pdf["num"])}))')
        y += h + 5.08
    out.append(')')
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")


def write_kicad_pro(cfg: dict, path: str) -> None:
    dr = cfg["pcb"]["design_rules"]
    pro = {
        "board": {"design_settings": {
            "rules": {"min_clearance": dr["min_clearance"],
                      "min_track_width": dr["min_track"],
                      "min_through_hole_diameter": dr["min_via_drill"],
                      "min_via_annular_width": dr["min_annular_ring"],
                      "min_hole_to_hole": dr["min_hole_to_hole"]}}},
        "meta": {"filename": os.path.basename(path), "version": 1},
        "net_settings": {"classes": [{"name": "Default", "clearance": dr["min_clearance"],
                                      "track_width": 0.25, "via_diameter": 0.7,
                                      "via_drill": 0.35}]},
        "sheets": [["00000000-0000-0000-0000-000000000000", "Root"]],
    }
    with open(path, "w") as fh:
        json.dump(pro, fh, indent=2)


def write_netlist(board: dict, net: dict, path: str) -> None:
    out = ['(export (version "E")', '  (design (source "claude-micro") (tool "claude_micro"))',
           '  (components']
    for c in board["components"]:
        out.append(f'    (comp (ref "{c["ref"]}") (value "{c["value"]}") '
                   f'(footprint "claude-micro:{c["footprint"]}") '
                   f'(fields (field (name "MPN") "{c["mpn"]}") '
                   f'(field (name "Description") "{c["desc"]}")))')
    out.append('  )')
    out.append('  (nets')
    allnets = dict(net["nets"])
    for i, tp in enumerate(board["touch_pads"]):
        allnets.setdefault(f"TOUCH{i}", []).append((f"TP{i + 1}", "1"))
    for i, (name, nodes) in enumerate(sorted(allnets.items()), start=1):
        out.append(f'    (net (code {i}) (name "{name}")')
        for ref, pin in nodes:
            out.append(f'      (node (ref "{ref}") (pin "{pin}"))')
        out.append('    )')
    out.append('  )')
    out.append(')')
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")


# --------------------------------------------------------------------------
# assembly data
# --------------------------------------------------------------------------

def write_bom(board: dict, path: str) -> list[dict]:
    groups: dict[tuple, list[str]] = defaultdict(list)
    meta: dict[tuple, dict] = {}
    for c in board["components"]:
        k = (c["value"], c["footprint"], c["mpn"])
        groups[k].append(c["ref"])
        meta[k] = c
    rows = []
    for (value, fp, mpn), refs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        rows.append({"Qty": len(refs), "Value": value, "Footprint": fp,
                     "MPN / Notes": mpn or "-", "References": " ".join(sorted(refs)),
                     "Description": meta[(value, fp, mpn)]["desc"],
                     "Group": meta[(value, fp, mpn)]["group"]})
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return rows


def write_positions(board: dict, path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Ref", "Val", "Package", "PosX", "PosY", "Rot", "Side"])
        for c in sorted(board["components"], key=lambda c: c["ref"]):
            w.writerow([c["ref"], c["value"], c["footprint"],
                        f'{c["x"]:.3f}', f'{c["y"]:.3f}', c["rot"], c["side"].capitalize()])


def write_board_json(board: dict, cfg: dict, path: str) -> None:
    """Compact board description for the HTML viewer."""
    def r(v, n=3):
        return round(v, n)

    data = {
        "meta": {"name": cfg["meta"]["name"], "version": cfg["meta"]["version"]},
        "bounds": [cfg["pcb"]["x0"], cfg["pcb"]["y0"], cfg["pcb"]["x1"], cfg["pcb"]["y1"]],
        "outline": [[r(x), r(y)] for x, y in board["outline"]],
        "tracks": [[t["layer"], r(t["x0"]), r(t["y0"]), r(t["x1"]), r(t["y1"]),
                    r(t["width"], 2), t["net"]] for t in board["tracks"]],
        "vias": [[r(v["x"]), r(v["y"]), v["net"]] for v in board["vias"]],
        "pads": [[0 if pd["layer"] == "F.Cu" else (1 if pd["layer"] == "B.Cu" else 2),
                  r(pd["x"]), r(pd["y"]), r(pd["w"], 2), r(pd["h"], 2),
                  pd["shape"][0], r(pd["drill"], 2), pd["net"], f'{pd["ref"]}.{pd["num"]}']
                 for pd in board["pads"]],
        "npth": [[r(h["x"]), r(h["y"]), r(h["d"], 2)] for h in board["npth"]],
        "pours": {k: [[r(run["y"]), r(run["x0"]), r(run["x1"])] for run in v]
                  for k, v in board["pours"].items()},
        "touch_pads": [[r(t["x0"]), r(t["y0"]), r(t["x1"]), r(t["y1"])]
                       for t in board["touch_pads"]],
        "components": [{"ref": c["ref"], "val": c["value"], "fp": c["footprint"],
                        "x": r(c["x"]), "y": r(c["y"]), "rot": c["rot"],
                        "side": c["side"], "group": c["group"], "desc": c["desc"],
                        "outline": [[r(a), r(b)] for a, b in
                                    M.transform_poly(circuit.FOOTPRINTS[c["footprint"]]["outline"],
                                                     c["x"], c["y"], c["rot"])]}
                       for c in board["components"]],
        "logo": [[[r(a), r(b)] for a, b in blade] for blade in
                 M.claude_burst(cfg["layout"]["logo"]["x"], cfg["layout"]["logo"]["y"],
                                cfg["layout"]["logo"]["size"])],
        "stats": board["stats"],
    }
    with open(path, "w") as fh:
        json.dump(data, fh, separators=(",", ":"))


def write_all(board: dict, net: dict, grid, cfg: dict, verbose: bool = True) -> dict:
    out = ensure_build("pcb")
    gerb = ensure_build("pcb", "gerbers")
    slug = cfg["meta"]["slug"]

    write_kicad_pcb(board, net, cfg, os.path.join(out, f"{slug}.kicad_pcb"))
    write_kicad_sch(board, net, cfg, os.path.join(out, f"{slug}.kicad_sch"))
    write_kicad_pro(cfg, os.path.join(out, f"{slug}.kicad_pro"))
    write_netlist(board, net, os.path.join(out, f"{slug}.net"))
    files = write_gerbers(board, cfg, gerb)
    files += write_drill(board, cfg, gerb)
    bom = write_bom(board, os.path.join(out, "bom.csv"))
    write_positions(board, os.path.join(out, "positions.csv"))
    write_board_json(board, cfg, os.path.join(out, "board.json"))

    import checks
    report = checks.run_all(board, net, cfg, grid)
    with open(os.path.join(out, "drc_report.json"), "w") as fh:
        json.dump(report, fh, indent=2)

    if verbose:
        print(f"  wrote KiCad project, {len(files)} fabrication files, "
              f"BOM ({len(bom)} lines), board.json")
        print(f"  DRC: {report['summary']}")
    return report
