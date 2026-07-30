#!/usr/bin/env python3
"""Export the schematic as an EasyEDA Standard source file.

Writes build/pcb/claude-micro-easyeda.json, which opens directly in EasyEDA
(File -> Open -> EasyEDA) and imports into EasyEDA Pro via
File -> Import -> EasyEDA Standard. From there it can be published to OSHWLab
or pushed straight at JLCPCB, which is the usual reason to want this format.

Format notes
------------
EasyEDA stores each shape as one `~`-delimited string, with `#@$` separating a
component's child shapes and `^^` separating the segments of a pin. Field
orders below were taken from EasyEDA's published document format and checked
against a real exported schematic, not guessed:

    LIB ~x~y~attrs~rotation~importFlag~id            then #@$ child shapes
    R   ~x~y~rx~ry~w~h~stroke~strokeW~style~fill~id
    T   ~mark~x~y~rot~fill~family~size~weight~style~baseline~type~text~vis~anchor~id
    P   ~show~electric~num~x~y~rot~id ^^dot ^^path~colour ^^name ^^number ^^dot ^^clock
    W   ~x1 y1 x2 y2~stroke~strokeW~style~fill~id
    N   ~dotX~dotY~rot~fill~name~id~anchor~textX~textY~family~size

Every pin gets a short wire stub ending in a net label rather than routed
wires between parts. That is the same approach as the generated KiCad sheet:
connectivity is identical and a 265-pin rat's nest stays readable.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import circuit                          # noqa: E402
from device import load, ensure_build   # noqa: E402

# Layout constants, in EasyEDA's 10 px = 0.1 inch schematic grid.
GRID = 10
PIN_PITCH = 10
BOX_W = 70
STUB = 20
COL_PITCH = 320
SHEET_TOP = 60
SHEET_MAX_Y = 1500
GAP = 30

# Pin electrical types EasyEDA understands.
ELEC_UNDEFINED, ELEC_IN, ELEC_OUT, ELEC_BIDIR, ELEC_POWER = 0, 1, 2, 3, 4

POWER_NETS = {"GND", "+5V", "+3V3", "VBUS"}


def _clean(s) -> str:
    """Strip the characters EasyEDA uses as delimiters."""
    return (str(s).replace("~", "-").replace("`", "'")
            .replace("^^", " ").replace("#@$", " "))


class Sheet:
    """Accumulates shapes and hands out the `gge<n>` ids EasyEDA expects."""

    def __init__(self):
        self.shapes: list[str] = []
        self._id = 0
        self.xs: list[float] = []
        self.ys: list[float] = []

    def gid(self) -> str:
        self._id += 1
        return f"gge{self._id}"

    def add(self, shape: str) -> None:
        self.shapes.append(shape)

    def track(self, x: float, y: float) -> None:
        self.xs.append(x)
        self.ys.append(y)

    # -- primitives ------------------------------------------------------
    def wire(self, x1, y1, x2, y2, colour="#008800") -> str:
        gid = self.gid()
        self.add(f"W~{x1} {y1} {x2} {y2}~{colour}~1~0~none~{gid}")
        self.track(x1, y1)
        self.track(x2, y2)
        return gid

    def netlabel(self, x, y, name, anchor="end", colour="#000080") -> str:
        gid = self.gid()
        tx = x - 3 if anchor == "end" else x + 3
        self.add(f"N~{x}~{y}~0~{colour}~{_clean(name)}~{gid}~{anchor}~{tx}~{y + 3}"
                 f"~Times New Roman~9pt")
        self.track(x, y)
        return gid

    def note(self, x, y, text, size="12pt", colour="#7c8899", anchor="start") -> str:
        gid = self.gid()
        self.add(f"T~L~{x}~{y}~0~{colour}~Arial~{size}~normal~normal~~comment~"
                 f"{_clean(text)}~1~{anchor}~{gid}")
        self.track(x, y)
        return gid


def _pin(sheet: Sheet, dot_x, dot_y, number, name, electric) -> str:
    """A left-side pin: the dot is the connection point, the body runs right."""
    gid = sheet.gid()
    body_x = dot_x + STUB
    seg = [
        f"P~show~{electric}~{number}~{dot_x}~{dot_y}~180~{gid}",
        f"{dot_x}~{dot_y}",
        f"M {dot_x} {dot_y} h {STUB}~#0000FF",
        # name, sitting just inside the body
        f"1~{body_x + 3}~{dot_y + 3}~0~{_clean(name)}~start~~7pt",
        # number, sitting above the stub
        f"1~{body_x - 3}~{dot_y - 2}~0~{_clean(number)}~end~~7pt",
        "0~0~0",
        "0~",
    ]
    sheet.track(dot_x, dot_y)
    return "^^".join(seg)


def _component(sheet: Sheet, comp: dict, fp: dict, x: float, y: float) -> float:
    """Emit one component with a net-labelled stub on every pin.

    `x`, `y` is the top-left of the body. Returns the height consumed.
    """
    pads = fp["pads"]
    body_h = PIN_PITCH * (len(pads) + 1)
    body_x = x
    dot_x = x - STUB

    attrs = {
        "package": _clean(comp["footprint"]),
        "spicePre": comp["ref"].rstrip("0123456789") or "U",
        "spiceSymbolName": _clean(comp["value"]),
    }
    if comp["mpn"]:
        attrs["BOM_Manufacturer Part"] = _clean(comp["mpn"])
    attr_str = "".join(f"{k}`{v}`" for k, v in attrs.items())

    lib_id = sheet.gid()
    children = [
        # reference designator (mark P) and value (mark N)
        f"T~P~{body_x}~{y - 14}~0~#000080~Arial~9pt~bold~normal~~comment~"
        f"{_clean(comp['ref'])}~1~start~{sheet.gid()}",
        f"T~N~{body_x}~{y - 3}~0~#000080~Arial~7pt~normal~normal~~comment~"
        f"{_clean(comp['value'])}~1~start~{sheet.gid()}",
        f"R~{body_x}~{y}~0~0~{BOX_W}~{body_h}~#880000~1~0~#FFFFFF~{sheet.gid()}",
    ]

    for i, pad in enumerate(pads):
        py = y + PIN_PITCH * (i + 1)
        net = comp["pins"].get(pad["num"], "")
        electric = ELEC_POWER if net in POWER_NETS else ELEC_BIDIR
        children.append(_pin(sheet, dot_x, py, pad["num"], pad["name"], electric))
        if net:
            # stub out to the left, then name the net there
            sheet.wire(dot_x, py, dot_x - STUB, py)
            sheet.netlabel(dot_x - STUB, py, net)

    sheet.add(f"LIB~{body_x}~{y}~{attr_str}~~0~{lib_id}#@$" + "#@$".join(children))
    sheet.track(body_x + BOX_W, y + body_h)
    sheet.track(dot_x - STUB - 60, y)
    return body_h


# --------------------------------------------------------------------------

def build(verbose: bool = True) -> str:
    cfg = load()
    net = circuit.build_netlist(cfg)
    sheet = Sheet()

    sheet.note(60, 30, f"{cfg['meta']['name']}  ·  rev {cfg['meta']['version']}",
               size="16pt", colour="#333333")
    sheet.note(60, 46, cfg["meta"]["description"], size="9pt")
    sheet.note(60, 58, "Generated from config/device.json. Every pin carries a net "
                       "label; nets of the same name are connected.", size="9pt")

    # Group by function so the sheet reads in the same order as the docs.
    order = ["mcu", "matrix", "input", "touch", "rgb", "power"]
    comps = sorted(net["components"],
                   key=lambda c: (order.index(c["group"]) if c["group"] in order else 99,
                                  c["ref"]))

    x = 140.0
    y = float(SHEET_TOP + 40)
    col_group = None
    for comp in comps:
        fp = net["footprints"][comp["footprint"]]
        h = PIN_PITCH * (len(fp["pads"]) + 1)
        if y + h + GAP > SHEET_MAX_Y:
            x += COL_PITCH
            y = float(SHEET_TOP + 40)
            col_group = None
        if comp["group"] != col_group:
            col_group = comp["group"]
            sheet.note(x - STUB * 2, y - 26, col_group.upper(), size="10pt",
                       colour="#d97757")
            y += 10
        _component(sheet, comp, fp, x, y)
        y += h + GAP

    # Canvas sized to the content, rounded up to the grid.
    w = int((max(sheet.xs) + 120) // GRID * GRID + GRID)
    hgt = int((max(sheet.ys) + 120) // GRID * GRID + GRID)
    x0 = min(sheet.xs)
    y0 = min(sheet.ys)

    data_str = {
        "head": {
            "docType": "1",
            "editorVersion": "6.5.36",
            "c_para": {"Prefix Start": "1"},
            "c_spiceCmd": "null",
            "x": "0",
            "y": "0",
            "portOfADImportHack": "",
            "importFlag": 0,
            "transformList": "",
        },
        "canvas": f"CA~{w}~{hgt}~#FFFFFF~yes~#CCCCCC~5~{w}~{hgt}~line~5~pixel~5~0~0",
        "shape": sheet.shapes,
        "BBox": {"x": x0, "y": y0,
                 "width": max(sheet.xs) - x0, "height": max(sheet.ys) - y0},
        "colors": [],
    }
    doc = {
        "schematics": [{
            "docType": 1,
            "title": f"{cfg['meta']['name']} schematic",
            "dataStr": data_str,
        }],
        "docType": 5,
        "title": cfg["meta"]["name"],
        "name": cfg["meta"]["name"],
    }

    out = os.path.join(ensure_build("pcb"), f"{cfg['meta']['slug']}-easyeda.json")
    with open(out, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    if verbose:
        print(f"  wrote {out}  ({len(sheet.shapes)} shapes, "
              f"{len(net['components'])} parts, {w} x {hgt} px)")
    return out


if __name__ == "__main__":
    build()
