"""Every physical part of the Claude Micro, as watertight meshes.

Printable parts are built at print orientation with their own local origin at
z=0 so they drop straight onto a build plate. `PLACEMENT` then says where each
one lives in the assembled device, which is what the exploded viewer animates.

Some parts (PCB, switches, connectors, ICs) are render-only: they are bought,
not printed, but the viewer needs them and the fit checks measure against them.
"""

from __future__ import annotations

import math

import mesh as M
from device import load, agent_keys, command_keys


# --------------------------------------------------------------------------
# shared outlines
# --------------------------------------------------------------------------

def case_outer_poly(cfg: dict, inset: float = 0.0, seg: int = 12) -> list[M.Pt2]:
    c = cfg["case"]["outer"]
    r = cfg["case"]["corner_radius"] - inset
    return M.rounded_rect(cfg["case"]["cx"], cfg["case"]["cy"],
                          cfg["case"]["width"] - 2 * inset,
                          cfg["case"]["height"] - 2 * inset, max(0.4, r), seg)


def case_inner_poly(cfg: dict, extra: float = 0.0, seg: int = 12) -> list[M.Pt2]:
    return case_outer_poly(cfg, inset=cfg["case"]["wall"] - extra, seg=seg)


def window_poly(cfg: dict, grow: float = 0.0, seg: int = 8) -> list[M.Pt2]:
    w = cfg["case"]["window"]
    return M.rounded_rect(w["cx"], w["cy"], w["w"] + 2 * grow, w["h"] + 2 * grow,
                          w["radius"] + grow, seg)


def pcb_poly(cfg: dict, seg: int = 8) -> list[M.Pt2]:
    p = cfg["pcb"]
    return M.rounded_rect(p["cx"], p["cy"], p["width"], p["height"], p["corner_radius"], seg)


def plate_cutouts(cfg: dict) -> list[list[M.Pt2]]:
    """Switch, encoder, joystick and touch-window openings in the switch plate."""
    lay = cfg["layout"]
    cut = lay["switch"]["plate_cutout"]
    holes = [M.rect(k["x"], k["y"], cut, cut) for k in lay["keys"]]
    enc = lay["encoder"]
    holes.append(M.circle(enc["x"], enc["y"], 8.4, 32))
    joy = lay["joystick"]
    holes.append(M.circle(joy["x"], joy["y"], joy["cap_d"] + 2.4, 40))
    ts = lay["touch_strip"]
    holes.append(M.rounded_rect((ts["x0"] + ts["x1"]) / 2, (ts["y0"] + ts["y1"]) / 2,
                                ts["x1"] - ts["x0"] + 0.6, ts["y1"] - ts["y0"] + 0.6, 1.6, 6))
    return holes


# --------------------------------------------------------------------------
# printable parts
# --------------------------------------------------------------------------

def case_bottom(cfg: dict) -> M.Mesh:
    """Tray: floor, perimeter wall with the USB-C doorway, PCB standoffs.

    Local z=0 is the underside of the case, which is also its position in the
    assembly, so this part needs no placement offset.
    """
    st, cs = cfg["stack"], cfg["case"]
    scr = cs["screw"]
    floor_top = st["case_floor_top"]
    wall_top = st["plate_bottom"]

    outer = case_outer_poly(cfg)
    inner = case_inner_poly(cfg)

    m = M.Mesh(name="case_bottom")

    # Floor, split into two slabs so the screw heads get a counterbore.
    cb_d = scr["head_d"] + 0.4
    counterbores = [M.circle(h["x"], h["y"], cb_d, 24) for h in cfg["pcb"]["mount_holes"]]
    clearance = [M.circle(h["x"], h["y"], scr["clearance_d"], 20) for h in cfg["pcb"]["mount_holes"]]
    m.extend(M.extrude(outer, 0.0, scr["head_h"] + 0.1, holes=counterbores))
    m.extend(M.extrude(outer, scr["head_h"], floor_top, holes=clearance))

    # Perimeter wall with a doorway for the USB-C receptacle. The doorway is
    # open to the top of the wall; the bezel closes it off above the port.
    usb = cfg["pcb"]["usb"]
    half = usb["width"] / 2.0 + 0.9
    y_out = cs["outer"]["y0"]
    y_in = y_out + cs["wall"]
    ring = M.ring_with_gap(outer, inner,
                           (usb["x"] - half, y_out), (usb["x"] + half, y_out),
                           (usb["x"] - half, y_in), (usb["x"] + half, y_in))
    m.extend(M.extrude(ring, floor_top - 0.1, wall_top))

    # PCB standoffs: pillars up to the underside of the board, bored for the screw.
    for h in cfg["pcb"]["mount_holes"]:
        m.extend(M.tube(h["x"], h["y"], floor_top - 0.1, scr["boss_d"],
                        scr["clearance_d"], st["pcb_bottom"] - floor_top + 0.1, 24))

    # Anti-flex ribs across the floor, kept clear of the standoffs and of the
    # USB-C receptacle hanging under the board.
    for x in (45.0, 100.0):
        m.extend(M.box_at(x, 32.5, floor_top - 0.1, 2.0, 75.0, 1.4))

    m.name = "case_bottom"
    return m


def case_top(cfg: dict) -> M.Mesh:
    """Bezel frame with the switch plate integrated, printed upside down.

    Modelled in assembly position (z = plate_bottom .. bezel_top); `PLACEMENT`
    records the flip that puts it on the build plate.
    """
    st, cs = cfg["stack"], cfg["case"]
    scr = cs["screw"]
    z0 = st["plate_bottom"]
    z_plate = st["plate_top"]
    z_top = st["bezel_top"]
    insert_top = z0 + scr["insert_h"] + 0.85

    outer = case_outer_poly(cfg)
    win = window_poly(cfg)
    inserts = [M.circle(h["x"], h["y"], scr["insert_d"], 20) for h in cfg["pcb"]["mount_holes"]]

    m = M.Mesh(name="case_top")
    # Built as three full-width slabs rather than a frame plus a separate plate:
    # the plate is simply the lowest slab, and the window only opens above it.
    # That keeps every hole comfortably inside a single outline.
    m.extend(M.extrude(outer, z0, z_plate, holes=plate_cutouts(cfg) + inserts))
    m.extend(M.extrude(outer, z_plate - 0.1, insert_top, holes=[win] + inserts))
    m.extend(M.extrude(outer, insert_top - 0.1, z_top, holes=[win]))

    # Bosses that bear on the PCB and deepen the insert bores. They run 0.1 mm
    # past the slab so the union is a true overlap rather than two solids
    # meeting on a shared plane, and their bore is a touch wider so the two
    # cylinder walls are not coincident either.
    for h in cfg["pcb"]["mount_holes"]:
        m.extend(M.tube(h["x"], h["y"], st["pcb_top"] + 0.05, scr["boss_d"],
                        scr["insert_d"] + 0.15, z0 - st["pcb_top"] + 0.05, 20))

    # Locating lip that drops inside the tray wall.
    lip_o = case_inner_poly(cfg, extra=-0.15)
    lip_i = case_inner_poly(cfg, extra=-1.55)
    m.extend(M.extrude(lip_o, z0 - 1.4, z0 + 0.1, holes=[lip_i]))

    # Claude mark, raised on the plate.
    lg = cfg["layout"]["logo"]
    m.extend(M.emboss(M.claude_burst(lg["x"], lg["y"], lg["size"]),
                      z_plate - 0.05, lg["emboss"] + 0.05))

    m.name = "case_top"
    return m


def keycap(cfg: dict, agent: bool = False) -> M.Mesh:
    """MX-compatible keycap, printed upside down.

    The agent variant has a thinner roof so a cap printed in natural or clear
    filament diffuses the status LED underneath it.
    """
    kc = cfg["layout"]["keycap"]
    h = kc["h"]
    wall = kc["wall"]
    roof = 1.0 if agent else kc["top_inset"]
    seg = 6
    r = 1.5
    taper = 3.5           # how much the cap narrows from skirt to top face

    out_b = M.rounded_rect(0, 0, kc["w"], kc["d"], r, seg)
    out_t = M.rounded_rect(0, 0, kc["w"] - taper, kc["d"] - taper, r, seg)
    in_b = M.rounded_rect(0, 0, kc["w"] - 2 * wall, kc["d"] - 2 * wall,
                          max(0.4, r - wall), seg)
    in_t = M.rounded_rect(0, 0, kc["w"] - taper - 2 * wall, kc["d"] - taper - 2 * wall,
                          max(0.4, r - wall), seg)

    m = M.Mesh(name="keycap_agent" if agent else "keycap_command")
    m.extend(M.loft(out_b, 0.0, out_t, h, cap_bottom=False, cap_top=True))
    m.extend(M.loft(in_b, 0.0, in_t, h - roof, cap_bottom=False, cap_top=True).flip())
    m.extend(M.ring_between(out_b, in_b, 0.0, up=False))

    # The MX socket: a boss reaching the roof with a cross-shaped bore through
    # it, sized a hair over the stem so it presses on without splitting.
    m.extend(M.extrude(M.circle(0, 0, kc["socket_d"], 32), 0.0, h - roof + 0.02,
                       holes=[M.cross(0, 0, kc["socket_arm"], kc["socket_thick"])]))
    return m


def knob(cfg: dict) -> M.Mesh:
    """Reasoning dial: knurled body with a D-shaped bore for the EC11 shaft."""
    enc = cfg["layout"]["encoder"]
    d, h = enc["knob_d"], enc["knob_h"]
    flutes, per = 36, 4

    # The knurl is the knob's own outline rather than a ring applied on top of
    # it, so nothing self-intersects and the bore stays a clean D.
    grip = M.fluted_circle(0, 0, d, flutes=flutes, depth=0.7, per_flute=per)
    crown = M.circle(0, 0, d - 3.2, flutes * per)
    bore = M.d_profile(0, 0, enc["shaft_d"] + 0.25, enc["shaft_flat"] + 0.2, 32)

    m = M.Mesh(name="knob")
    m.extend(M.extrude(grip, 0.0, h - 2.2, holes=[bore]))
    m.extend(M.loft(grip, h - 2.3, crown, h))
    return m


def joystick_cap(cfg: dict) -> M.Mesh:
    """Dished thumb cap; single lathe because the bore is on the axis."""
    joy = cfg["layout"]["joystick"]
    R = joy["cap_d"] / 2.0
    H = joy["cap_h"]
    bore_r = 2.15
    z_split = H * 0.42

    m = M.Mesh(name="joystick_cap")
    # Ribbed skirt: the ribs are the outline, so no ring can cut into the body.
    ribs = M.fluted_circle(0, 0, joy["cap_d"], flutes=24, depth=0.7, per_flute=4)
    m.extend(M.extrude(ribs, 0.0, z_split, holes=[M.circle(0, 0, bore_r * 2, 32)]))

    # Dished crown, revolved -- the bore is on the axis so it lathes cleanly.
    profile = [
        (bore_r, z_split - 0.1), (R, z_split - 0.1),
        (R - 0.7, H * 0.72), (R - 1.6, H * 0.92), (R - 2.4, H),
        (R * 0.62, H - 0.55), (R * 0.40, H - 0.95), (bore_r, H - 1.15),
    ]
    m.extend(M.lathe(profile, seg=64))
    m.name = "joystick_cap"
    return m


def foot(cfg: dict, rear: bool) -> M.Mesh:
    """Tilt foot. The top face is sloped by the case tilt so it mates flat."""
    f = cfg["case"]["foot"]
    rise = f["d"] * math.tan(math.radians(f["tilt_deg"]))
    h0 = f["h_rear"] if rear else f["h_front"]
    m = M.wedge(0, 0, f["w"], f["d"], h0, h0 + rise,
                name="foot_rear" if rear else "foot_front")
    return m


def diffuser(cfg: dict) -> M.Mesh:
    """Light guide that drops into the touch-strip window in the plate."""
    ts = cfg["layout"]["touch_strip"]
    cx = (ts["x0"] + ts["x1"]) / 2.0
    cy = (ts["y0"] + ts["y1"]) / 2.0
    w = ts["x1"] - ts["x0"] + 0.4
    d = ts["y1"] - ts["y0"] + 0.4
    t = cfg["layout"]["switch"]["plate_thickness"]
    m = M.Mesh(name="diffuser")
    m.extend(M.extrude(M.rounded_rect(cx, cy, w, d, 1.5, 6), 0.0, t))
    # Retaining flange under the plate.
    m.extend(M.extrude(M.rounded_rect(cx, cy, w + 2.4, d + 2.0, 2.0, 6), -0.8, 0.05))
    m.name = "diffuser"
    return m


# --------------------------------------------------------------------------
# render-only parts (purchased components)
# --------------------------------------------------------------------------

def pcb(cfg: dict) -> M.Mesh:
    st = cfg["stack"]
    holes = [M.circle(h["x"], h["y"], h["d"], 20) for h in cfg["pcb"]["mount_holes"]]
    return M.extrude(pcb_poly(cfg), st["pcb_bottom"], st["pcb_top"], holes=holes, name="pcb")


def mx_switch(cfg: dict, x: float = 0.0, y: float = 0.0) -> M.Mesh:
    """Clear MX-style switch: wide bottom housing under the plate, a 14 mm
    waist through the plate cutout, a tapered top housing, and the cross stem."""
    sw = cfg["layout"]["switch"]
    st = cfg["stack"]
    z = st["pcb_top"]
    m = M.Mesh(name="switch")
    # bottom housing, too wide to pass through the plate
    m.extend(M.extrude(M.rounded_rect(x, y, sw["body_w"], sw["body_d"], 0.8, 4),
                       z, st["plate_bottom"]))
    # waist through the plate cutout
    m.extend(M.extrude(M.rounded_rect(x, y, sw["upper_w"], sw["upper_w"], 0.6, 4),
                       st["plate_bottom"] - 0.05, st["plate_top"]))
    # top housing, tapering the way an MX does
    m.extend(M.loft(M.rounded_rect(x, y, sw["upper_w"], sw["upper_w"], 0.6, 4),
                    st["plate_top"] - 0.05,
                    M.rounded_rect(x, y, sw["top_w"], sw["top_w"], 0.6, 4),
                    z + sw["body_h"]))
    # cross stem
    m.extend(M.extrude(M.cross(x, y, sw["stem_arm"], sw["stem_thick"]),
                       z + sw["body_h"] - 0.5, z + sw["body_h"] + sw["stem_h"]))
    return m


def encoder_body(cfg: dict) -> M.Mesh:
    enc = cfg["layout"]["encoder"]
    st = cfg["stack"]
    z = st["pcb_top"]
    m = M.Mesh(name="encoder")
    m.extend(M.box_at(enc["x"], enc["y"], z, enc["body_w"], enc["body_d"], enc["body_h"]))
    m.extend(M.cylinder(enc["x"], enc["y"], z + enc["body_h"], 7.0, 1.2, 32))
    m.extend(M.extrude(M.d_profile(enc["x"], enc["y"], enc["shaft_d"], enc["shaft_flat"], 32),
                       z + enc["body_h"] + 1.2, z + enc["body_h"] + enc["shaft_len"]))
    return m


def joystick_body(cfg: dict) -> M.Mesh:
    joy = cfg["layout"]["joystick"]
    st = cfg["stack"]
    z = st["pcb_top"]
    m = M.Mesh(name="joystick")
    m.extend(M.extrude(M.rounded_rect(joy["x"], joy["y"], joy["body_w"], joy["body_d"], 1.5, 5),
                       z, z + 6.5))
    m.extend(M.cone(joy["x"], joy["y"], z + 6.5, 13.0, 8.0, 3.0, 32))
    m.extend(M.cylinder(joy["x"], joy["y"], z + 9.4, 4.0,
                        st["joy_cap_bottom"] + 6.0 - (z + 9.4), 24))
    return m


def usb_c(cfg: dict) -> M.Mesh:
    st = cfg["stack"]
    u = cfg["pcb"]["usb"]
    z1 = st["pcb_bottom"]
    m = M.Mesh(name="usb_c")
    m.extend(M.extrude(M.rounded_rect(u["x"], u["y"] + 3.6, 9.0, 7.4, 0.8, 4), z1 - 3.26, z1))
    m.extend(M.extrude(M.slot(u["x"], u["y"] - 0.2, 8.9, 2.7, 6), z1 - 2.9, z1 - 0.3))
    return m


def smd(cfg: dict, x: float, y: float, w: float, d: float, h: float, top: bool = False) -> M.Mesh:
    st = cfg["stack"]
    z = st["pcb_top"] if top else st["pcb_bottom"] - h
    return M.box_at(x, y, z, w, d, h)


def led_mesh(cfg: dict, x: float, y: float) -> M.Mesh:
    st = cfg["stack"]
    return M.box_at(x, y, st["pcb_top"], 3.5, 3.5, 1.4)


# --------------------------------------------------------------------------
# assembly manifest
# --------------------------------------------------------------------------

def printable_parts(cfg: dict) -> list[dict]:
    """Parts the user prints, with print orientation and quantity."""
    st = cfg["stack"]
    return [
        {"name": "case_bottom", "qty": 1, "mesh": case_bottom(cfg),
         "material": cfg["case"]["print"]["material_case"],
         "orientation": "As modelled, flat on the plate. No supports.",
         "notes": "Tray, USB-C doorway, PCB standoffs, screw counterbores."},
        {"name": "case_top", "qty": 1, "mesh": case_top(cfg).translate(dz=-st["plate_bottom"]),
         "material": cfg["case"]["print"]["material_case"],
         "orientation": "Rotate 180 deg about X so the plate face is down. No supports.",
         "notes": "Bezel + integrated 1.3 mm switch plate + raised Claude mark."},
        {"name": "keycap_agent", "qty": 6, "mesh": keycap(cfg, agent=True),
         "material": "Natural / clear " + cfg["case"]["print"]["material_keycap"],
         "orientation": "Upside down (open side up). No supports.",
         "notes": "1.0 mm roof so the RGB status colour reads through."},
        {"name": "keycap_command", "qty": 7, "mesh": keycap(cfg, agent=False),
         "material": cfg["case"]["print"]["material_keycap"],
         "orientation": "Upside down (open side up). No supports.",
         "notes": "Opaque command caps."},
        {"name": "knob", "qty": 1, "mesh": knob(cfg),
         "material": cfg["case"]["print"]["material_case"],
         "orientation": "Bore down on the plate. No supports.",
         "notes": "D-bore press fit onto the EC11 shaft."},
        {"name": "joystick_cap", "qty": 1, "mesh": joystick_cap(cfg),
         "material": cfg["case"]["print"]["material_case"],
         "orientation": "Bore down on the plate. No supports.",
         "notes": "Dished thumb cap."},
        {"name": "foot_rear", "qty": 2, "mesh": foot(cfg, rear=True),
         "material": "TPU (or PETG + rubber pad)",
         "orientation": "Flat, sloped face up.",
         "notes": f"Gives the {cfg['case']['foot']['tilt_deg']} deg typing tilt."},
        {"name": "foot_front", "qty": 2, "mesh": foot(cfg, rear=False),
         "material": "TPU (or PETG + rubber pad)",
         "orientation": "Flat, sloped face up.",
         "notes": "Front pads."},
        {"name": "diffuser", "qty": 1, "mesh": diffuser(cfg),
         "material": cfg["case"]["print"]["material_diffuser"],
         "orientation": "Flat. No supports.",
         "notes": "Touch-strip light guide; capacitive sensing reads through it."},
    ]


def assembly(cfg: dict) -> list[dict]:
    """Every part in assembled position, with the vector it flies along when
    the viewer explodes the model."""
    st = cfg["stack"]
    lay = cfg["layout"]
    col = cfg["colors"]
    items: list[dict] = []

    def add(name, mesh, color, explode, group, opacity=1.0):
        items.append({"name": name, "mesh": mesh, "color": color,
                      "explode": explode, "group": group, "opacity": opacity})

    add("Case bottom", case_bottom(cfg), col["case_bottom"], (0, 0, -34), "case")
    add("PCB", pcb(cfg), col["pcb"], (0, 0, 10), "pcb")

    for u in usb_and_ics(cfg):
        add(u["name"], u["mesh"], u["color"], (0, 0, 10 + u.get("dz", 0)), "pcb")

    leds = M.Mesh(name="leds")
    for l in cfg["rgb"]["chain"]:
        leds.extend(led_mesh(cfg, l["x"], l["y"]))
    add("RGB LEDs (19)", leds, "#f6f4ee", (0, 0, 12), "pcb")

    sw = M.Mesh(name="switches")
    for k in lay["keys"]:
        sw.extend(mx_switch(cfg, k["x"], k["y"]))
    # Clear housings, so they render translucent.
    add("MX switches (13)", sw, col["switch"], (0, 0, 34), "switches", opacity=0.45)

    add("Rotary encoder", encoder_body(cfg), "#8d939c", (0, 0, 26), "switches")
    add("Joystick module", joystick_body(cfg), "#8d939c", (0, 0, 26), "switches")

    add("Case top + plate", case_top(cfg), col["case_top"], (0, 0, 52), "case")
    add("Diffuser", diffuser(cfg).translate(dz=st["plate_bottom"]), col["diffuser"], (0, 0, 58),
        "case", opacity=0.6)

    agent = M.Mesh()
    for k in agent_keys(cfg):
        agent.extend(keycap(cfg, agent=True).translate(k["x"], k["y"], st["keycap_bottom"]))
    add("Agent keycaps (6)", agent, col["keycap_agent"], (0, 0, 74), "keycaps")

    cmd = M.Mesh()
    for k in command_keys(cfg):
        cmd.extend(keycap(cfg, agent=False).translate(k["x"], k["y"], st["keycap_bottom"]))
    add("Command keycaps (7)", cmd, col["keycap_command"], (0, 0, 74), "keycaps")

    add("Reasoning dial", knob(cfg).translate(lay["encoder"]["x"], lay["encoder"]["y"],
                                              st["knob_bottom"]),
        col["knob"], (0, 0, 84), "keycaps")
    add("Joystick cap", joystick_cap(cfg).translate(lay["joystick"]["x"], lay["joystick"]["y"],
                                                    st["joy_cap_bottom"]),
        col["joystick"], (0, 0, 84), "keycaps")

    f = cfg["case"]["foot"]
    feet = M.Mesh()
    for fx in (cfg["case"]["outer"]["x0"] + 16, cfg["case"]["outer"]["x1"] - 16):
        feet.extend(foot(cfg, rear=True).translate(fx, cfg["case"]["outer"]["y1"] - 10,
                                                   -f["h_rear"] - 1.26))
        feet.extend(foot(cfg, rear=False).translate(fx, cfg["case"]["outer"]["y0"] + 10,
                                                    -f["h_front"] - 1.26))
    add("Tilt feet (4)", feet, "#16181c", (0, 0, -48), "case")

    return items


def usb_and_ics(cfg: dict) -> list[dict]:
    """Bottom-side silhouette of the major components, for the 3D view."""
    st = cfg["stack"]
    out = [{"name": "USB-C receptacle", "mesh": usb_c(cfg), "color": "#b9bec7", "dz": -2}]

    body = M.Mesh()
    body.extend(smd(cfg, 47.0, 6.0, 7.0, 7.0, 0.9))          # RP2040 QFN-56
    body.extend(smd(cfg, 47.0, 16.0, 5.3, 5.3, 0.8))          # QSPI flash SOIC-8
    body.extend(smd(cfg, 33.0, 0.0, 3.0, 1.6, 0.9))           # LDO
    body.extend(smd(cfg, 61.0, 0.0, 3.2, 2.5, 0.7))           # crystal
    body.extend(smd(cfg, 20.0, 44.0, 3.0, 3.0, 0.8))          # touch controller
    body.extend(smd(cfg, 33.0, 44.0, 2.0, 1.3, 0.7))          # level shifter
    out.append({"name": "Silicon (MCU, flash, LDO, touch)", "mesh": body, "color": "#0e1013", "dz": -1})

    d = M.Mesh()
    for n in cfg["matrix"]["nodes"]:
        d.extend(smd(cfg, n["x"] + 5.6, n["y"] + 4.6, 1.8, 1.3, 0.6))
    out.append({"name": "Diodes (15)", "mesh": d, "color": "#2a2d33", "dz": -1})
    return out
