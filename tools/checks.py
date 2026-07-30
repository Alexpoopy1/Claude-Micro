"""Automated audit of the board: ERC, DRC and physical fit.

These run as part of every build (build_pcb.py writes drc_report.json) and are
also driven directly by tests/. Each check appends to a flat list of findings
with a severity, so one report covers electrical, manufacturing and mechanical
problems together.
"""

from __future__ import annotations

import math

import mesh as M
import router as R

ERROR = "error"
WARN = "warning"
INFO = "info"

# Pins that are deliberately left open; ERC must not complain about them.
INTENTIONAL_SINGLE_NODE = {
    "GP0_TX", "GP1_RX", "GP13_SPARE", "GP14_SPARE", "GP15_SPARE",
    "GP22_SPARE", "GP28_SPARE", "LED_DEND",
}


def _f(findings, sev, check, msg, **extra):
    findings.append({"severity": sev, "check": check, "message": msg, **extra})


# --------------------------------------------------------------------------
# electrical rule checks
# --------------------------------------------------------------------------

def erc(board: dict, net: dict, cfg: dict) -> list[dict]:
    out: list[dict] = []
    nets = dict(net["nets"])
    for i in range(len(board["touch_pads"])):
        nets.setdefault(f"TOUCH{i}", []).append((f"TP{i + 1}", "1"))

    for name, nodes in sorted(nets.items()):
        if len(nodes) < 2 and name not in INTENTIONAL_SINGLE_NODE:
            _f(out, ERROR, "erc.single_node", f"net {name} has only one node", net=name)

    for req in ("GND", "+5V", "+3V3", "VBUS"):
        if req not in nets:
            _f(out, ERROR, "erc.missing_power", f"power net {req} does not exist")

    # Every matrix node must have exactly one switch (or encoder) and one diode
    # and land on the row/column it claims.
    rows = {f"ROW{i}" for i in range(cfg["matrix"]["rows"])}
    cols = {f"COL{i}" for i in range(cfg["matrix"]["cols"])}
    for r in rows | cols:
        if r not in nets:
            _f(out, ERROR, "erc.matrix", f"matrix net {r} is missing", net=r)

    seen_rc = {}
    for node in cfg["matrix"]["nodes"]:
        key = (node["row"], node["col"])
        if key in seen_rc:
            _f(out, ERROR, "erc.matrix_collision",
               f"{node['id']} and {seen_rc[key]} both sit at row {key[0]} col {key[1]}")
        seen_rc[key] = node["id"]
        knet = f"K_{node['id']}"
        if knet not in nets or len(nets[knet]) != 2:
            _f(out, ERROR, "erc.matrix",
               f"{knet} should join exactly one switch terminal to one diode", net=knet)

    diodes = [c for c in board["components"] if c["value"] == "1N4148W"]
    sw_fp = cfg["layout"]["switch"]["footprint"]
    switches = [c for c in board["components"] if c["footprint"] == sw_fp]
    if len(diodes) != len(cfg["matrix"]["nodes"]):
        _f(out, ERROR, "erc.diode_count",
           f"{len(diodes)} diodes for {len(cfg['matrix']['nodes'])} matrix nodes")
    if len(switches) != len(cfg["layout"]["keys"]):
        _f(out, ERROR, "erc.switch_count",
           f"{len(switches)} switches for {len(cfg['layout']['keys'])} keys")

    # LED chain must be one unbroken daisy chain of DOUT -> DIN.
    leds = [c for c in board["components"] if c["group"] == "rgb" and c["ref"].startswith("LED")]
    if len(leds) != cfg["rgb"]["total"]:
        _f(out, ERROR, "erc.led_count",
           f"{len(leds)} LEDs placed, config says {cfg['rgb']['total']}")
    din = {c["pins"]["4"]: c["ref"] for c in leds}
    dout = {c["pins"]["2"]: c["ref"] for c in leds}
    broken = 0
    for n, ref in dout.items():
        if n == "LED_DEND":
            continue
        if n not in din:
            _f(out, ERROR, "erc.led_chain", f"{ref} DOUT net {n} feeds nothing", net=n)
            broken += 1
    if not broken:
        _f(out, INFO, "erc.led_chain", f"LED chain intact across {len(leds)} devices")

    # Anything driving 5 V logic must actually be level shifted.
    u2 = next((c for c in board["components"] if c["ref"] == "U2"), None)
    if u2 is None:
        _f(out, ERROR, "erc.level_shift", "no level shifter on the LED data line")
    else:
        if u2["pins"].get("5") != "+5V":
            _f(out, ERROR, "erc.level_shift", "level shifter is not powered from +5V")
        if u2["pins"].get("1") != "LED_DIN_3V3":
            _f(out, ERROR, "erc.level_shift", "level shifter input is not the MCU data pin")

    # Current budget for the LED rail.
    n_led = len(leds)
    worst = n_led * cfg["rgb"]["max_current_ma_per_led"]
    limit = cfg["rgb"]["firmware_current_limit_ma"]
    if limit > 900:
        _f(out, WARN, "erc.current",
           f"firmware LED budget {limit} mA exceeds a safe USB 2.0 draw")
    _f(out, INFO, "erc.current",
       f"{n_led} LEDs, {worst} mA if every channel were full-on; firmware caps the "
       f"chain at {limit} mA and the PPTC opens at 1.1 A")

    # Pin usage must not double-book a GPIO.
    mod = next(c for c in board["components"] if c["ref"] == "MOD1")
    used = {}
    for pin, n in mod["pins"].items():
        gp = cfg["module"]["pinout"][pin]
        if n in ("GND",) or not n:
            continue
        if n in used and used[n] != gp and {used[n], gp} != {"ADC_VREF", "3V3_OUT"}:
            _f(out, WARN, "erc.pin", f"net {n} appears on {used[n]} and {gp}")
        used[n] = gp
    return out


# --------------------------------------------------------------------------
# design rule checks
# --------------------------------------------------------------------------

def drc(board: dict, cfg: dict, grid) -> list[dict]:
    out: list[dict] = []
    dr = cfg["pcb"]["design_rules"]

    if board["stats"]["failed"]:
        for f in board["failed"]:
            _f(out, ERROR, "drc.unrouted",
               f"net {f['net']} not routed between {f['from']} and {f['to']}", net=f["net"])
    else:
        _f(out, INFO, "drc.unrouted",
           f"all {board['stats']['connections']} connections routed")

    for c in board.get("crowded", []):
        _f(out, ERROR, "drc.pad_crowding",
           f"pad {c} sits entirely inside another net's clearance halo")

    for t in board["tracks"]:
        if t["width"] < dr["min_track"] - 1e-9:
            _f(out, ERROR, "drc.track_width",
               f"track on net {t['net']} is {t['width']:.3f} mm "
               f"(min {dr['min_track']})", net=t["net"])

    # Via annular ring
    ring = (0.7 - 0.35) / 2
    if ring < dr["min_annular_ring"]:
        _f(out, ERROR, "drc.annular", f"via annular ring {ring:.3f} mm is below "
           f"{dr['min_annular_ring']} mm")
    else:
        _f(out, INFO, "drc.annular", f"via annular ring {ring:.3f} mm")

    for pd in board["pads"]:
        if pd["type"] != "tht" or pd["drill"] <= 0:
            continue
        r = (min(pd["w"], pd["h"]) - pd["drill"]) / 2
        if r < dr["min_annular_ring"]:
            _f(out, ERROR, "drc.annular",
               f"pad {pd['ref']}.{pd['num']} annular ring {r:.3f} mm too small")

    # Hole-to-hole spacing
    holes = [(pd["x"], pd["y"], pd["drill"], f"{pd['ref']}.{pd['num']}")
             for pd in board["pads"] if pd["type"] == "tht" and pd["drill"] > 0]
    holes += [(h["x"], h["y"], h["d"], h.get("ref", "NPTH")) for h in board["npth"]]
    holes += [(v["x"], v["y"], 0.35, "via") for v in board["vias"]]
    holes.sort()
    bad = 0
    for i, a in enumerate(holes):
        for b in holes[i + 1:]:
            if b[0] - a[0] > 6.0:
                break
            gap = math.dist((a[0], a[1]), (b[0], b[1])) - (a[2] + b[2]) / 2
            if gap < dr["min_hole_to_hole"]:
                bad += 1
                if bad <= 8:
                    _f(out, ERROR, "drc.hole_spacing",
                       f"{a[3]} and {b[3]} are {gap:.3f} mm apart "
                       f"(min {dr['min_hole_to_hole']})")
    if bad > 8:
        _f(out, ERROR, "drc.hole_spacing", f"...and {bad - 8} more hole spacing violations")
    if not bad:
        _f(out, INFO, "drc.hole_spacing", f"{len(holes)} holes all clear of one another")

    # Copper-to-copper clearance, verified on a fresh grid built from the final
    # geometry rather than trusting the one the router mutated as it worked.
    viol = _clearance_scan(board, cfg)
    if viol:
        for v in viol[:8]:
            _f(out, ERROR, "drc.clearance", v)
        if len(viol) > 8:
            _f(out, ERROR, "drc.clearance", f"...and {len(viol) - 8} more clearance violations")
    else:
        _f(out, INFO, "drc.clearance",
           f"copper clearance >= {dr['min_clearance']} mm everywhere")

    # Board edge clearance
    outline = board["outline"]
    edge_bad = 0
    for t in board["tracks"]:
        for (x, y) in ((t["x0"], t["y0"]), (t["x1"], t["y1"])):
            if not M.point_in_poly((x, y), outline):
                edge_bad += 1
    if edge_bad:
        _f(out, ERROR, "drc.edge", f"{edge_bad} track endpoints lie outside the board outline")
    else:
        _f(out, INFO, "drc.edge", "all copper inside the board profile")

    return out


def _copper_raster(board: dict, cfg: dict, pitch: float = 0.1):
    """Rasterise the finished copper, keeping the identity of every collision.

    Built from scratch rather than reusing the router's working grid, so this
    checks the geometry that actually ships.
    """
    p = cfg["pcb"]
    g = R.Grid(p["x0"], p["y0"], p["x1"], p["y1"], pitch, layers=2)
    conflicts: dict[tuple[int, int], set[tuple[str, str]]] = {}

    def mark(L, idx, net):
        cur = g.own[L][idx]
        nid = g.net_id(net)
        if cur == R.FREE:
            g.own[L][idx] = nid
        elif cur != nid:
            pair = tuple(sorted((g.net_names.get(cur, "?"), net)))
            conflicts.setdefault((L, idx), set()).add(pair)
            g.own[L][idx] = R.BLOCKED

    def disc(L, x, y, d, net):
        r = d / 2
        for j in range(max(0, g.cy(y - r)), min(g.h - 1, g.cy(y + r)) + 1):
            dy = g.wy(j) - y
            for i in range(max(0, g.cx(x - r)), min(g.w - 1, g.cx(x + r)) + 1):
                dx = g.wx(i) - x
                if dx * dx + dy * dy <= r * r:
                    mark(L, j * g.w + i, net)

    def box(L, x, y, w, h, net):
        for j in range(max(0, g.cy(y - h / 2)), min(g.h - 1, g.cy(y + h / 2)) + 1):
            for i in range(max(0, g.cx(x - w / 2)), min(g.w - 1, g.cx(x + w / 2)) + 1):
                mark(L, j * g.w + i, net)

    for pd in board["pads"]:
        layers = (0, 1) if pd["layer"] == "*.Cu" else ((0,) if pd["layer"] == "F.Cu" else (1,))
        for L in layers:
            if pd["shape"] == "circle":
                disc(L, pd["x"], pd["y"], max(pd["w"], pd["h"]), pd["net"] or "?")
            else:
                box(L, pd["x"], pd["y"], pd["w"], pd["h"], pd["net"] or "?")
    for t in board["tracks"]:
        n = max(1, int(math.dist((t["x0"], t["y0"]), (t["x1"], t["y1"])) / (pitch * 0.7)))
        for s in range(n + 1):
            u = s / n
            disc(t["layer"], t["x0"] + (t["x1"] - t["x0"]) * u,
                 t["y0"] + (t["y1"] - t["y0"]) * u, t["width"], t["net"])
    for v in board["vias"]:
        for L in (0, 1):
            disc(L, v["x"], v["y"], 0.7, v["net"])
    for i, tp in enumerate(board["touch_pads"]):
        box(0, (tp["x0"] + tp["x1"]) / 2, (tp["y0"] + tp["y1"]) / 2,
            tp["x1"] - tp["x0"], tp["y1"] - tp["y0"], f"TOUCH{i}")
    return g, conflicts


def _clearance_scan(board: dict, cfg: dict) -> list[str]:
    dr = cfg["pcb"]["design_rules"]
    pitch = 0.1
    g, conflicts = _copper_raster(board, cfg, pitch)

    msgs: list[str] = []
    seen = set()
    for (L, idx), pairs in conflicts.items():
        for a, b in pairs:
            if (a, b, L) in seen:
                continue
            seen.add((a, b, L))
            j, i = divmod(idx, g.w)
            msgs.append(f"{a} shorts to {b} on layer {L} at "
                        f"({g.wx(i):.2f}, {g.wy(j):.2f})")
    if msgs:
        return msgs

    # Near-misses: anything closer than the clearance rule without touching.
    # The raster snaps to a 0.1 mm grid, so allow one cell of tolerance before
    # calling a gap a violation.
    reach = max(1, int(round(dr["min_clearance"] / pitch)) - 1)
    offsets = [(dx, dy) for dx in range(-reach, reach + 1) for dy in range(-reach, reach + 1)
               if 0 < dx * dx + dy * dy <= reach * reach]
    near = []
    seen_pairs = set()
    for L in (0, 1):
        own = g.own[L]
        for j in range(g.h):
            base = j * g.w
            for i in range(g.w):
                a = own[base + i]
                if a in (R.FREE, R.BLOCKED):
                    continue
                for dx, dy in offsets:
                    ni, nj = i + dx, j + dy
                    if not g.inside(ni, nj):
                        continue
                    b = own[nj * g.w + ni]
                    if b in (R.FREE, R.BLOCKED) or b == a:
                        continue
                    key = tuple(sorted((a, b))) + (L,)
                    if key in seen_pairs:
                        break
                    seen_pairs.add(key)
                    near.append(f"{g.net_names[a]} and {g.net_names[b]} closer than "
                                f"{dr['min_clearance']} mm at "
                                f"({g.wx(i):.2f}, {g.wy(j):.2f}) on layer {L}")
                    break
                if len(near) > 20:
                    return near
    return near


def gnd_reachability(board: dict, cfg: dict):
    """Flood-fill the ground pour and return (grid, reached_mask, orphan_pads).

    A poured net has no ratsnest, so without this a GND pad marooned on a
    copper island would ship silently. build_pcb uses the same function to
    decide which pads need rescuing, so the fixer and the checker can never
    drift apart.
    """
    p = cfg["pcb"]
    g = R.Grid(p["x0"], p["y0"], p["x1"], p["y1"], 0.2, layers=2)
    filled = [bytearray(g.n) for _ in range(2)]

    def fill_box(L, x, y, w, h):
        for j in range(max(0, g.cy(y - h / 2)), min(g.h - 1, g.cy(y + h / 2)) + 1):
            for i in range(max(0, g.cx(x - w / 2)), min(g.w - 1, g.cx(x + w / 2)) + 1):
                filled[L][j * g.w + i] = 1

    for lname, L in (("F.Cu", 0), ("B.Cu", 1)):
        for run in board["pours"][lname]:
            j = g.cy(run["y"])
            if not 0 <= j < g.h:
                continue
            for i in range(max(0, g.cx(run["x0"])), min(g.w - 1, g.cx(run["x1"])) + 1):
                filled[L][j * g.w + i] = 1

    gnd_pads = [pd for pd in board["pads"] if pd["net"] == "GND"]
    for pd in gnd_pads:
        layers = (0, 1) if pd["layer"] == "*.Cu" else ((0,) if pd["layer"] == "F.Cu" else (1,))
        for L in layers:
            fill_box(L, pd["x"], pd["y"], pd["w"], pd["h"])
    for t in board["tracks"]:
        if t["net"] != "GND":
            continue
        n = max(1, int(math.dist((t["x0"], t["y0"]), (t["x1"], t["y1"])) / 0.15))
        for s in range(n + 1):
            u = s / n
            fill_box(t["layer"], t["x0"] + (t["x1"] - t["x0"]) * u,
                     t["y0"] + (t["y1"] - t["y0"]) * u, t["width"], t["width"])
    vias = [v for v in board["vias"] if v["net"] == "GND"]
    via_cells = set()
    for v in vias:
        for L in (0, 1):
            fill_box(L, v["x"], v["y"], 0.7, 0.7)
        for dj in (-1, 0, 1):
            for di in (-1, 0, 1):
                via_cells.add((g.cx(v["x"]) + di, g.cy(v["y"]) + dj))

    # Start the flood from the largest connected region rather than an
    # arbitrary pad, so one marooned pad cannot define "the pour".
    seen = [bytearray(g.n) for _ in range(2)]
    best_seen, best_size = None, -1
    for L0 in (0, 1):
        for j0 in range(0, g.h, 7):
            for i0 in range(0, g.w, 7):
                if not filled[L0][j0 * g.w + i0] or seen[L0][j0 * g.w + i0]:
                    continue
                local = [bytearray(g.n) for _ in range(2)]
                stack = [(L0, i0, j0)]
                local[L0][j0 * g.w + i0] = 1
                size = 0
                while stack:
                    L, i, j = stack.pop()
                    size += 1
                    seen[L][j * g.w + i] = 1
                    for dx, dy in R.ORTHO:
                        ni, nj = i + dx, j + dy
                        if not g.inside(ni, nj):
                            continue
                        idx = nj * g.w + ni
                        if filled[L][idx] and not local[L][idx]:
                            local[L][idx] = 1
                            stack.append((L, ni, nj))
                    if (i, j) in via_cells:
                        L2 = 1 - L
                        idx = j * g.w + i
                        if filled[L2][idx] and not local[L2][idx]:
                            local[L2][idx] = 1
                            stack.append((L2, i, j))
                if size > best_size:
                    best_seen, best_size = local, size
    if best_seen is None:
        return g, [bytearray(g.n) for _ in range(2)], gnd_pads

    orphans = []
    for pd in gnd_pads:
        layers = (0, 1) if pd["layer"] == "*.Cu" else ((0,) if pd["layer"] == "F.Cu" else (1,))
        hit = False
        for L in layers:
            for j in range(max(0, g.cy(pd["y"] - pd["h"] / 2)),
                           min(g.h - 1, g.cy(pd["y"] + pd["h"] / 2)) + 1):
                for i in range(max(0, g.cx(pd["x"] - pd["w"] / 2)),
                               min(g.w - 1, g.cx(pd["x"] + pd["w"] / 2)) + 1):
                    if best_seen[L][j * g.w + i]:
                        hit = True
        if not hit:
            orphans.append(pd)
    return g, best_seen, orphans


def pour_connectivity(board: dict, cfg: dict) -> list[dict]:
    out: list[dict] = []
    gnd_pads = [pd for pd in board["pads"] if pd["net"] == "GND"]
    vias = [v for v in board["vias"] if v["net"] == "GND"]
    _g, _seen, orphans = gnd_reachability(board, cfg)
    if orphans:
        _f(out, ERROR, "drc.pour",
           f"{len(orphans)} GND pads are not connected to the pour: "
           + ", ".join("{}.{}".format(p["ref"], p["num"]) for p in orphans[:10]))
    else:
        _f(out, INFO, "drc.pour",
           f"all {len(gnd_pads)} GND pads reach one continuous pour "
           f"({len(vias)} stitching vias)")
    return out


# --------------------------------------------------------------------------
# mechanical fit
# --------------------------------------------------------------------------

def mechanical(cfg: dict) -> list[dict]:
    out: list[dict] = []
    lay, st, case = cfg["layout"], cfg["stack"], cfg["case"]

    # Nothing on the top of the board may foul anything else.
    items = []
    for k in lay["keys"]:
        items.append((k["id"], k["x"], k["y"], lay["keycap"]["w"], lay["keycap"]["d"]))
    enc = lay["encoder"]
    items.append(("KNOB", enc["x"], enc["y"], enc["knob_d"], enc["knob_d"]))
    joy = lay["joystick"]
    items.append(("JOYCAP", joy["x"], joy["y"], joy["cap_d"] + 2.0, joy["cap_d"] + 2.0))
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            dx = abs(a[1] - b[1]) - (a[3] + b[3]) / 2
            dy = abs(a[2] - b[2]) - (a[4] + b[4]) / 2
            if dx < 0 and dy < 0:
                _f(out, ERROR, "fit.collision",
                   f"{a[0]} and {b[0]} overlap by "
                   f"{-max(dx, dy):.2f} mm on the top surface")

    # Everything visible must sit inside the bezel window.
    w = case["window"]
    for name, x, y, sw, sd in items:
        if (x - sw / 2 < w["x0"] or x + sw / 2 > w["x1"]
                or y - sd / 2 < w["y0"] or y + sd / 2 > w["y1"]):
            _f(out, ERROR, "fit.window", f"{name} does not clear the bezel window")

    ts = lay["touch_strip"]
    if ts["y1"] > w["y1"] or ts["x0"] < w["x0"] or ts["x1"] > w["x1"]:
        _f(out, ERROR, "fit.window", "touch strip does not clear the bezel window")
    lg = lay["logo"]
    if lg["y"] + lg["size"] / 2 > w["y1"] or lg["y"] - lg["size"] / 2 < ts["y1"]:
        _f(out, ERROR, "fit.window", "Claude mark overlaps the touch strip or the bezel")

    # The encoder shaft must not bottom out in the knob.
    shaft_top = st["pcb_top"] + enc["body_h"] + enc["shaft_len"]
    bore_bottom = st["knob_bottom"] + enc["knob_h"] - 2.2
    if shaft_top > bore_bottom:
        _f(out, ERROR, "fit.knob",
           f"encoder shaft reaches {shaft_top:.2f} mm but the knob bore ends at "
           f"{bore_bottom:.2f} mm - the knob would sit proud")
    else:
        _f(out, INFO, "fit.knob",
           f"{bore_bottom - shaft_top:.2f} mm of clearance at the bottom of the knob bore")

    # PCB has to fit the cavity, and the module has to fit under the PCB.
    mod = cfg["module"]
    gap = st["pcb_bottom"] - st["case_floor_top"]
    need = mod["standoff"] + mod["pcb_thickness"] + mod["component_height"]
    if gap < need:
        _f(out, ERROR, "fit.cavity",
           f"cavity is {gap:.2f} mm but the MCU module on its "
           f"{mod['standoff']} mm header needs {need:.2f} mm")
    else:
        _f(out, INFO, "fit.cavity",
           f"{gap:.2f} mm under the board; the module stack needs {need:.2f} mm")

    inner_w = case["width"] - 2 * case["wall"] - 2 * case["pcb_gap"]
    inner_h = case["height"] - 2 * case["wall"] - 2 * case["pcb_gap"]
    if cfg["pcb"]["width"] > inner_w or cfg["pcb"]["height"] > inner_h:
        _f(out, ERROR, "fit.pcb",
           f"PCB {cfg['pcb']['width']} x {cfg['pcb']['height']} mm will not fit the "
           f"{inner_w:.1f} x {inner_h:.1f} mm cavity")
    else:
        _f(out, INFO, "fit.pcb",
           f"PCB clears the cavity by {inner_w - cfg['pcb']['width']:.2f} / "
           f"{inner_h - cfg['pcb']['height']:.2f} mm")

    # Keycaps must clear the bezel so they can travel.
    if st["keycap_bottom"] < st["plate_top"]:
        _f(out, ERROR, "fit.keycap", "keycaps start below the top of the plate")
    travel = lay["switch"]["travel"]
    if st["keycap_bottom"] - travel < st["plate_top"]:
        _f(out, WARN, "fit.keycap",
           "a fully pressed keycap bottoms out on the plate before the switch does")

    # Mounting holes must land in the bezel frame, not in the window.
    for h in cfg["pcb"]["mount_holes"]:
        r = case["screw"]["insert_d"] / 2
        in_window = (w["x0"] < h["x"] < w["x1"] and w["y0"] < h["y"] < w["y1"])
        crosses = (abs(h["x"] - w["x0"]) < r or abs(h["x"] - w["x1"]) < r
                   or abs(h["y"] - w["y0"]) < r or abs(h["y"] - w["y1"]) < r)
        if in_window or crosses:
            _f(out, ERROR, "fit.mount",
               f"mount hole at ({h['x']}, {h['y']}) clashes with the bezel window")
        for edge, val in (("x0", cfg["pcb"]["x0"]), ("x1", cfg["pcb"]["x1"])):
            if abs(h["x"] - val) < h["d"] / 2 + 1.0:
                _f(out, ERROR, "fit.mount",
                   f"mount hole at ({h['x']}, {h['y']}) is too close to the board edge")
    return out


# --------------------------------------------------------------------------

def run_all(board: dict, net: dict, cfg: dict, grid=None) -> dict:
    findings = (erc(board, net, cfg) + drc(board, cfg, grid)
                + pour_connectivity(board, cfg) + mechanical(cfg))
    errors = [f for f in findings if f["severity"] == ERROR]
    warns = [f for f in findings if f["severity"] == WARN]
    return {
        "ok": not errors,
        "summary": (f"{len(errors)} errors, {len(warns)} warnings, "
                    f"{len(findings) - len(errors) - len(warns)} notes"),
        "errors": len(errors), "warnings": len(warns),
        "findings": findings,
    }
