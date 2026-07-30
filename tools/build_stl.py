#!/usr/bin/env python3
"""Write every printable part to build/stl/ as binary STL, print-side down.

Each part is validated before it is written: if a mesh is not a closed,
consistently-oriented 2-manifold the build fails rather than shipping an STL a
slicer would have to guess at.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mesh as M           # noqa: E402
import parts               # noqa: E402
from device import load, ensure_build   # noqa: E402

# Parts whose modelled orientation is upside-down relative to the build plate.
FLIP = {"case_top", "keycap_agent", "keycap_command"}


def orient_for_print(m: M.Mesh, name: str) -> M.Mesh:
    if name in FLIP:
        m = m.rotate_x(180.0)
    lo, _ = m.bbox()
    return m.translate(-lo[0], -lo[1], -lo[2])


def build(verbose: bool = True) -> dict:
    cfg = load()
    outdir = ensure_build("stl")
    report = {"parts": [], "ok": True,
              "device": cfg["meta"]["name"], "version": cfg["meta"]["version"]}

    total_tris = 0
    total_vol = 0.0
    for p in parts.printable_parts(cfg):
        m = orient_for_print(p["mesh"], p["name"])
        m.name = p["name"]
        v = m.validate()
        path = os.path.join(outdir, f"{p['name']}.stl")
        size_bytes = m.save_stl(path)
        lo, hi = v["bbox"]
        entry = {
            "name": p["name"],
            "file": f"stl/{p['name']}.stl",
            "qty": p["qty"],
            "material": p["material"],
            "orientation": p["orientation"],
            "notes": p["notes"],
            "triangles": v["triangles"],
            "volume_mm3": round(v["volume_mm3"], 2),
            "volume_cm3": round(v["volume_mm3"] / 1000.0, 3),
            "bbox_mm": [round(hi[i] - lo[i], 2) for i in range(3)],
            "bytes": size_bytes,
            "watertight": v["watertight"],
            "manifold": v["manifold"],
            "oriented": v["oriented"],
            "ok": v["ok"],
        }
        report["parts"].append(entry)
        if not v["ok"]:
            report["ok"] = False
        total_tris += v["triangles"]
        total_vol += v["volume_mm3"] * p["qty"]
        if verbose:
            flag = "ok  " if v["ok"] else "FAIL"
            print(f"  [{flag}] {p['name']:16s} x{p['qty']}  "
                  f"{entry['bbox_mm'][0]:6.1f} x {entry['bbox_mm'][1]:5.1f} x {entry['bbox_mm'][2]:5.1f} mm  "
                  f"{v['triangles']:6d} tri  {entry['volume_cm3']:6.2f} cm3")

    # A single combined mesh so the whole device can be dropped into a viewer.
    asm = M.Mesh(name="assembly")
    for item in parts.assembly(cfg):
        asm.extend(item["mesh"])
    asm.save_stl(os.path.join(outdir, "assembly.stl"))

    # Filament estimate at ~1.24 g/cm3 (PETG), assuming 25% infill / 3 walls.
    solid_cm3 = total_vol / 1000.0
    report["totals"] = {
        "printable_parts": len(report["parts"]),
        "pieces_to_print": sum(p["qty"] for p in report["parts"]),
        "triangles": total_tris,
        "solid_volume_cm3": round(solid_cm3, 2),
        "estimated_filament_g": round(solid_cm3 * 0.55 * 1.24, 1),
        "assembly_triangles": len(asm.tris),
    }

    with open(os.path.join(ensure_build(), "stl_report.json"), "w") as fh:
        json.dump(report, fh, indent=2)
    if verbose:
        t = report["totals"]
        print(f"  -- {t['pieces_to_print']} pieces, {t['solid_volume_cm3']} cm3 solid, "
              f"~{t['estimated_filament_g']} g filament")
    return report


if __name__ == "__main__":
    r = build()
    print("STL build:", "OK" if r["ok"] else "FAILED")
    sys.exit(0 if r["ok"] else 1)
