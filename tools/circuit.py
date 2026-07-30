"""The Claude Micro schematic, as data.

Footprints are defined here rather than pulled from a library so the whole
board -- netlist, gerbers, drill, 3D view -- comes out of one file with no
external dependencies. Coordinates are in the same layout frame as the rest of
the project: millimetres, X right, Y toward the user, origin at key A1.

Design notes
------------
* The MCU is an RP2040 module in the Raspberry Pi Pico form factor. That keeps
  USB, the crystal, the QSPI flash and the switching regulator on a $4 module,
  leaves the carrier board hand-solderable, and means the only fine-pitch part
  is a SOT-23-5.
* The touch strip is read with plain RC charge-time sensing: one drive pin
  feeds five pads through 1 MOhm resistors, and five sense pins time the edge.
  No touch controller, no I2C.
* SK6812s want a 5 V logic-level data line, so GP12 goes through a 74LVC1G17
  buffer running off VBUS.
"""

from __future__ import annotations

from device import load

# --------------------------------------------------------------------------
# footprint primitives
# --------------------------------------------------------------------------


def pad(num, x, y, w, h, shape="rect", ptype="smd", drill=0.0, name=""):
    return {"num": str(num), "name": name or str(num), "x": x, "y": y,
            "w": w, "h": h, "shape": shape, "type": ptype, "drill": drill}


def _rect_outline(w, d):
    return [(-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)]


def fp_chip(code: str, pad_w: float, pad_h: float, span: float, body_w: float, body_d: float):
    """Two-terminal SMD chip package (0603, 1206, ...)."""
    return {"name": code, "kind": "smd",
            "pads": [pad(1, -span / 2, 0, pad_w, pad_h), pad(2, span / 2, 0, pad_w, pad_h)],
            "outline": _rect_outline(body_w, body_d),
            "courtyard": (span + pad_w + 0.5, max(pad_h, body_d) + 0.5)}


FOOTPRINTS: dict[str, dict] = {}

# Height above the board face a part is mounted on (mm). Used by the fit checks
# to decide whether two things that overlap in plan actually collide.
PART_HEIGHT = {
    "R_0603": 0.5, "C_0603": 0.5, "C_0805": 0.7, "F_1206": 0.9,
    "D_SOD123": 0.9, "C_1210_BULK": 2.5, "SOT-23-5": 1.1,
    "SK6812MINI": 1.4, "SK6812_3535": 1.4, "Choc_v1": 5.0,
    "EC11E": 6.5, "Joystick_20mm": 12.0, "SW_Push_6mm": 4.3,
    "RP2040_Module_Pico": 4.0,
}


def part_height(footprint: str) -> float:
    return PART_HEIGHT.get(footprint, 1.5)


def _reg(fp):
    FOOTPRINTS[fp["name"]] = fp
    return fp


_reg(fp_chip("R_0603", 0.9, 0.95, 1.6, 1.6, 0.8))
_reg(fp_chip("C_0603", 0.9, 0.95, 1.6, 1.6, 0.8))
_reg(fp_chip("C_0805", 1.15, 1.4, 2.0, 2.0, 1.25))
_reg(fp_chip("F_1206", 1.5, 1.8, 3.0, 3.2, 1.6))
_reg(fp_chip("D_SOD123", 1.2, 1.4, 2.8, 2.7, 1.6))
_reg(fp_chip("C_1210_BULK", 1.8, 2.6, 3.6, 3.2, 2.5))

_reg({
    "name": "SOT-23-5", "kind": "smd",
    "pads": [pad(1, -0.95, 1.1, 0.6, 1.1), pad(2, 0.0, 1.1, 0.6, 1.1),
             pad(3, 0.95, 1.1, 0.6, 1.1), pad(4, 0.95, -1.1, 0.6, 1.1),
             pad(5, -0.95, -1.1, 0.6, 1.1)],
    "outline": _rect_outline(2.9, 1.6), "courtyard": (3.6, 3.6),
})

# Kailh Choc v1 (PG1350), solder-in. Two switch terminals plus the centre boss
# and the two alignment posts.
_reg({
    "name": "Choc_v1", "kind": "tht",
    # Terminals sit on the south side of the switch; the north half is left
    # clear for the per-key LED inside the Choc light window.
    "pads": [pad(1, 0.0, 5.9, 3.0, 3.0, "circle", "tht", 1.2),
             pad(2, 5.0, 3.8, 3.0, 3.0, "circle", "tht", 1.2)],
    "npth": [{"x": 0.0, "y": 0.0, "d": 3.45},
             {"x": -5.5, "y": 0.0, "d": 1.9},
             {"x": 5.5, "y": 0.0, "d": 1.9}],
    "outline": _rect_outline(13.8, 13.8), "courtyard": (14.6, 14.6),
})

# SK6812MINI, top mount, sits inside the Choc LED window.
_reg({
    "name": "SK6812MINI", "kind": "smd",
    "pads": [pad(1, -1.65, 1.0, 1.1, 0.9, name="VDD"),
             pad(2, -1.65, -1.0, 1.1, 0.9, name="DOUT"),
             pad(3, 1.65, -1.0, 1.1, 0.9, name="GND"),
             pad(4, 1.65, 1.0, 1.1, 0.9, name="DIN")],
    "outline": _rect_outline(3.5, 3.5), "courtyard": (4.6, 4.2),
})

_reg({
    "name": "SK6812_3535", "kind": "smd",
    "pads": [pad(1, -1.7, 1.05, 1.2, 1.0, name="VDD"),
             pad(2, -1.7, -1.05, 1.2, 1.0, name="DOUT"),
             pad(3, 1.7, -1.05, 1.2, 1.0, name="GND"),
             pad(4, 1.7, 1.05, 1.2, 1.0, name="DIN")],
    "outline": _rect_outline(3.5, 3.5), "courtyard": (4.8, 4.3),
})

# Alps EC11E vertical rotary encoder with push switch.
_reg({
    "name": "EC11E", "kind": "tht",
    "pads": [pad("A", -2.5, 7.0, 1.8, 1.8, "circle", "tht", 1.0, "A"),
             pad("C", 0.0, 7.0, 1.8, 1.8, "circle", "tht", 1.0, "C"),
             pad("B", 2.5, 7.0, 1.8, 1.8, "circle", "tht", 1.0, "B"),
             pad("S1", -2.5, -7.0, 1.8, 1.8, "circle", "tht", 1.0, "SW1"),
             pad("S2", 2.5, -7.0, 1.8, 1.8, "circle", "tht", 1.0, "SW2")],
    "npth": [{"x": -5.9, "y": 0.0, "d": 3.2}, {"x": 5.9, "y": 0.0, "d": 3.2}],
    "outline": _rect_outline(12.0, 13.5), "courtyard": (14.0, 17.0),
})

# Compact 2-axis analog thumbstick, 20 x 20 body, pins on the north edge.
_reg({
    "name": "Joystick_20mm", "kind": "tht",
    "pads": [pad(1, -5.08, -8.0, 1.7, 1.7, "circle", "tht", 1.0, "GND"),
             pad(2, -2.54, -8.0, 1.7, 1.7, "circle", "tht", 1.0, "VCC"),
             pad(3, 0.0, -8.0, 1.7, 1.7, "circle", "tht", 1.0, "VRX"),
             pad(4, 2.54, -8.0, 1.7, 1.7, "circle", "tht", 1.0, "VRY"),
             pad(5, 5.08, -8.0, 1.7, 1.7, "circle", "tht", 1.0, "SW")],
    "npth": [{"x": -8.0, "y": 7.5, "d": 2.2}, {"x": 8.0, "y": 7.5, "d": 2.2}],
    "outline": _rect_outline(20.0, 20.0), "courtyard": (21.0, 21.0),
})

_reg({
    "name": "SW_Push_6mm", "kind": "tht",
    "pads": [pad(1, -3.25, -2.25, 1.8, 1.8, "circle", "tht", 1.1),
             pad(2, 3.25, -2.25, 1.8, 1.8, "circle", "tht", 1.1),
             pad(3, -3.25, 2.25, 1.8, 1.8, "circle", "tht", 1.1),
             pad(4, 3.25, 2.25, 1.8, 1.8, "circle", "tht", 1.1)],
    "outline": _rect_outline(6.0, 6.0), "courtyard": (7.6, 6.4),
})


def _pico_footprint(cfg) -> dict:
    """40-pin Pico-form-factor module: castellated SMD pads doubled with
    through-holes so the module can be soldered flat or socketed."""
    mod = cfg["module"]
    n = mod["pins_per_side"]
    pitch = mod["pin_pitch"]
    hx = mod["row_spacing"] / 2.0
    y0 = -(n - 1) * pitch / 2.0
    pads = []
    for i in range(n):                     # pins 1..20 down the left side
        pads.append(pad(i + 1, -hx, y0 + i * pitch, 1.6, 1.6, "circle", "tht", 1.0))
    for j in range(n):                     # pins 21..40 back up the right side
        pads.append(pad(n + j + 1, hx, -(y0 + j * pitch), 1.6, 1.6, "circle", "tht", 1.0))
    return _reg({
        "name": "RP2040_Module_Pico", "kind": "tht",
        "pads": pads,
        "outline": _rect_outline(mod["body_w"], mod["body_l"]),
        "courtyard": (mod["body_w"] + 1.0, mod["body_l"] + 1.0),
    })


# --------------------------------------------------------------------------
# the schematic
# --------------------------------------------------------------------------

GND = "GND"
V5 = "+5V"
V33 = "+3V3"
VBUS = "VBUS"


def build_netlist(cfg: dict | None = None) -> dict:
    cfg = cfg or load()
    _pico_footprint(cfg)
    lay = cfg["layout"]
    comps: list[dict] = []

    def C(ref, value, fp, x, y, rot=0, side="top", pins=None, mpn="", desc="", group=""):
        comps.append({"ref": ref, "value": value, "footprint": fp, "x": x, "y": y,
                      "rot": rot, "side": side, "pins": pins or {}, "mpn": mpn,
                      "desc": desc, "group": group})

    # ---- MCU module -----------------------------------------------------
    mod = cfg["module"]
    net_of_pin = {
        1: "GP0_TX", 2: "GP1_RX", 3: GND, 4: "ROW0", 5: "ROW1", 6: "ROW2", 7: "ROW3",
        8: GND, 9: "COL0", 10: "COL1", 11: "COL2", 12: "COL3", 13: GND,
        14: "ENC_A", 15: "ENC_B", 16: "LED_DIN_3V3", 17: "GP13_SPARE", 18: GND,
        19: "GP14_SPARE", 20: "GP15_SPARE", 21: "TOUCH0", 22: "TOUCH1", 23: GND,
        24: "TOUCH2", 25: "TOUCH3", 26: "TOUCH4", 27: "TOUCH_DRV", 28: GND,
        29: "JOY_SW", 30: "RESET", 31: "JOY_X", 32: "JOY_Y", 33: GND,
        34: "GP28_SPARE", 35: V33, 36: V33, 37: "", 38: GND, 39: "", 40: VBUS,
    }
    C("MOD1", "RP2040 Pico-form-factor module", "RP2040_Module_Pico",
      mod["x"], mod["y"], mod["rot"], mod["side"],
      {str(k): v for k, v in net_of_pin.items() if v},
      mpn="YD-RP2040 / RP2040-Plus / Raspberry Pi Pico",
      desc="RP2040 + 2-16 MB flash + USB + 3V3 regulator", group="mcu")

    # ---- switch matrix --------------------------------------------------
    # COL2ROW: column -> switch -> diode anode (pin 2), cathode (pin 1) -> row.
    for i, node in enumerate(cfg["matrix"]["nodes"]):
        r, c = node["row"], node["col"]
        knet = f"K_{node['id']}"
        if node["kind"] == "switch":
            C(f"SW{i + 1}", "Kailh Choc v1", "Choc_v1", node["x"], node["y"], 0, "top",
              {"1": f"COL{c}", "2": knet},
              mpn="PG1350 (Choc v1)", desc=f"Key {node['id']}", group="matrix")
            dx, dy = node["x"] - 5.6, node["y"] - 5.2
        else:
            # The encoder push lives in the matrix too; keep its diode clear of
            # the EC11 body and its mounting lugs.
            dx, dy = node["x"] - 11.0, node["y"] + 11.0
        C(f"D{i + 1}", "1N4148W", "D_SOD123", dx, dy, 0, "bottom",
          {"1": f"ROW{r}", "2": knet},
          mpn="1N4148W SOD-123", desc=f"Matrix diode for {node['id']}", group="matrix")

    # ---- RGB chain ------------------------------------------------------
    chain = cfg["rgb"]["chain"]
    C("R10", "470R", "R_0603", 24.0, 63.0, 0, "bottom",
      {"1": "LED_DIN_5V", "2": "LED_DIN"}, desc="Data line series damping", group="rgb")
    prev = "LED_DIN"
    for i, led in enumerate(chain):
        nxt = f"LED_D{i + 1}" if i < len(chain) - 1 else "LED_DEND"
        fp = "SK6812MINI" if led["kind"] == "perkey" else "SK6812_3535"
        side = "top" if led["kind"] == "perkey" else "bottom"
        C(led["id"], led["part"], fp, led["x"], led["y"], 0, side,
          {"1": V5, "2": nxt, "3": GND, "4": prev},
          mpn=led["part"], desc=("Per-key status RGB" if led["kind"] == "perkey"
                                 else "Underglow RGB"), group="rgb")
        cap_x, cap_y = ((led["x"] + 4.8, led["y"] - 0.5) if led["kind"] == "perkey"
                        else (led["x"], led["y"] + 3.4))
        C(f"C{i + 1}", "100nF", "C_0603", cap_x, cap_y, 0, "bottom",
          {"1": V5, "2": GND}, desc=f"Decoupling for {led['id']}", group="rgb")
        prev = nxt

    # ---- 5 V level shifter for the LED data line ------------------------
    C("U2", "74LVC1G17", "SOT-23-5", 29.5, 63.0, 0, "bottom",
      {"1": "LED_DIN_3V3", "2": GND, "4": "LED_DIN_5V", "5": V5},
      mpn="SN74LVC1G17DBVR", desc="Schmitt buffer, 3V3 -> 5V for SK6812 data",
      group="power")
    C("C20", "100nF", "C_0603", 34.0, 63.0, 0, "bottom",
      {"1": V5, "2": GND}, desc="U2 decoupling", group="power")

    # ---- power ----------------------------------------------------------
    C("F1", "1.1A PPTC", "F_1206", 2.0, 63.0, 0, "bottom",
      {"1": VBUS, "2": V5}, mpn="MF-MSMF110", desc="Resettable fuse on the 5 V rail",
      group="power")
    C("C21", "470uF 10V", "C_1210_BULK", 8.5, 63.0, 0, "bottom",
      {"1": V5, "2": GND}, mpn="Low-ESR bulk", desc="LED rail bulk capacitance",
      group="power")
    C("C22", "10uF", "C_0805", 14.0, 63.0, 0, "bottom",
      {"1": V5, "2": GND}, desc="5 V ceramic bulk", group="power")
    C("C23", "10uF", "C_0805", 19.0, 63.0, 0, "bottom",
      {"1": V33, "2": GND}, desc="3V3 bulk", group="power")

    # ---- rotary encoder --------------------------------------------------
    enc = lay["encoder"]
    C("ENC1", "EC11E + switch", "EC11E", enc["x"], enc["y"], 0, "top",
      {"A": "ENC_A", "C": GND, "B": "ENC_B", "S1": "COL1", "S2": "K_ENC_SW"},
      mpn="EC11E15244Ax (9 mm shaft)", desc="Reasoning-effort dial", group="input")
    C("R1", "10k", "R_0603", enc["x"] - 6.0, enc["y"] + 12.0, 0, "bottom",
      {"1": V33, "2": "ENC_A"}, desc="Encoder A pull-up", group="input")
    C("R2", "10k", "R_0603", enc["x"] + 6.0, enc["y"] + 12.0, 0, "bottom",
      {"1": V33, "2": "ENC_B"}, desc="Encoder B pull-up", group="input")
    C("C24", "100nF", "C_0603", enc["x"] - 6.0, enc["y"] + 14.5, 0, "bottom",
      {"1": "ENC_A", "2": GND}, desc="Encoder A debounce", group="input")
    C("C25", "100nF", "C_0603", enc["x"] + 6.0, enc["y"] + 14.5, 0, "bottom",
      {"1": "ENC_B", "2": GND}, desc="Encoder B debounce", group="input")

    # ---- joystick --------------------------------------------------------
    joy = lay["joystick"]
    # The thumbstick's click has one terminal committed to GND inside the
    # module, so it cannot sit in the matrix; it gets its own pin instead.
    C("JS1", "2-axis analog thumbstick", "Joystick_20mm", joy["x"], joy["y"], 0, "top",
      {"1": GND, "2": V33, "3": "JOY_XR", "4": "JOY_YR", "5": "JOY_SW"},
      mpn="20 mm planar thumbstick, 10k pots", desc="Skill launcher", group="input")
    C("R3", "1k", "R_0603", joy["x"] - 4.0, joy["y"] - 11.5, 0, "bottom",
      {"1": "JOY_XR", "2": "JOY_X"}, desc="Joystick X RC filter", group="input")
    C("R4", "1k", "R_0603", joy["x"] + 0.5, joy["y"] - 11.5, 0, "bottom",
      {"1": "JOY_YR", "2": "JOY_Y"}, desc="Joystick Y RC filter", group="input")
    C("C26", "10nF", "C_0603", joy["x"] - 4.0, joy["y"] - 14.0, 0, "bottom",
      {"1": "JOY_X", "2": GND}, desc="Joystick X filter cap", group="input")
    C("C27", "10nF", "C_0603", joy["x"] + 0.5, joy["y"] - 14.0, 0, "bottom",
      {"1": "JOY_Y", "2": GND}, desc="Joystick Y filter cap", group="input")

    # ---- capacitive touch strip -----------------------------------------
    ts = lay["touch_strip"]
    for i, r in enumerate(ts["pad_rects"]):
        cx = (r["x0"] + r["x1"]) / 2.0
        C(f"R{5 + i}", "1M", "R_0603", cx, ts["y1"] + 3.0, 0, "bottom",
          {"1": "TOUCH_DRV", "2": f"TOUCH{i}"},
          desc=f"RC touch drive resistor, pad {i}", group="touch")

    # ---- reset -----------------------------------------------------------
    C("SW_RST", "Reset", "SW_Push_6mm", -8.0, 63.0, 0, "bottom",
      {"1": "RESET", "2": "RESET", "3": GND, "4": GND},
      desc="Pulls RUN low", group="mcu")

    nets = _nets_from(comps)
    return {"components": comps, "nets": nets,
            "footprints": FOOTPRINTS,
            "touch_pads": ts["pad_rects"],
            "power_nets": [GND, V5, V33, VBUS]}


def _nets_from(comps: list[dict]) -> dict[str, list[tuple[str, str]]]:
    nets: dict[str, list[tuple[str, str]]] = {}
    for c in comps:
        for pin, net in c["pins"].items():
            if not net:
                continue
            nets.setdefault(net, []).append((c["ref"], pin))
    return nets


def pad_positions(comp: dict, footprints: dict) -> list[dict]:
    """Absolute pad geometry for a placed component.

    Bottom-side parts are mirrored in X (viewed from the top, as a fabricator
    sees the board), and their copper lands on the back layer.
    """
    import math
    fp = footprints[comp["footprint"]]
    a = math.radians(comp["rot"])
    ca, sa = math.cos(a), math.sin(a)
    flip = -1.0 if comp["side"] == "bottom" else 1.0
    out = []
    for p in fp["pads"]:
        px, py = p["x"] * flip, p["y"]
        x = comp["x"] + px * ca - py * sa
        y = comp["y"] + px * sa + py * ca
        w, h = (p["w"], p["h"]) if comp["rot"] % 180 == 0 else (p["h"], p["w"])
        layer = "B.Cu" if comp["side"] == "bottom" else "F.Cu"
        out.append({"ref": comp["ref"], "num": p["num"], "name": p["name"],
                    "x": x, "y": y, "w": w, "h": h, "shape": p["shape"],
                    "type": p["type"], "drill": p["drill"],
                    "layer": "*.Cu" if p["type"] == "tht" else layer,
                    "net": comp["pins"].get(p["num"], "")})
    return out


def npth_positions(comp: dict, footprints: dict) -> list[dict]:
    import math
    fp = footprints[comp["footprint"]]
    if "npth" not in fp:
        return []
    a = math.radians(comp["rot"])
    ca, sa = math.cos(a), math.sin(a)
    flip = -1.0 if comp["side"] == "bottom" else 1.0
    out = []
    for h in fp["npth"]:
        px, py = h["x"] * flip, h["y"]
        out.append({"x": comp["x"] + px * ca - py * sa,
                    "y": comp["y"] + px * sa + py * ca, "d": h["d"], "ref": comp["ref"]})
    return out
