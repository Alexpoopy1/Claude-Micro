"""Render the circuit as an SVG functional schematic.

Drawn from the same netlist the PCB is routed from, so the sheet cannot drift
away from the board. Blocks are laid out by hand (a generated rat's nest is
unreadable); the *contents* of each block -- pin names, net names, part counts --
come from the netlist.
"""

from __future__ import annotations

import circuit
from device import load

W, H = 1180, 780
BG = "#161920"
FG = "#e7e9ee"
DIM = "#9aa3b2"
LINE = "#2a2f3a"
PWR = "#d97757"
DIG = "#6ea8fe"
ANA = "#3fb27f"
TCH = "#c8b3ff"


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class Svg:
    def __init__(self):
        self.o: list[str] = []

    def rect(self, x, y, w, h, fill="none", stroke=LINE, rx=7, sw=1.2, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
                      f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def text(self, x, y, s, fill=FG, size=12.5, anchor="start", weight=400, mono=False):
        fam = ("ui-monospace,SFMono-Regular,Menlo,monospace" if mono
               else "ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif")
        self.o.append(f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" '
                      f'font-family="{fam}" font-weight="{weight}" '
                      f'text-anchor="{anchor}">{_esc(s)}</text>')

    def path(self, d, stroke=DIG, sw=1.5, dash=None):
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.o.append(f'<path d="{d}" fill="none" stroke="{stroke}" '
                      f'stroke-width="{sw}" stroke-linecap="round"{da}/>')

    def dot(self, x, y, r=3, fill=DIG):
        self.o.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}"/>')

    def render(self) -> str:
        return (f'<svg class="schem" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
                f'role="img" aria-label="Claude Micro schematic">'
                f'<rect width="{W}" height="{H}" fill="{BG}"/>' + "".join(self.o) + "</svg>")


def _block(s: Svg, x, y, w, h, title, subtitle="", accent=LINE):
    s.rect(x, y, w, h, fill="#1b1f27", stroke=accent)
    s.rect(x, y, w, 26, fill="#222834", stroke=accent)
    s.text(x + 12, y + 18, title, size=12.5, weight=650)
    if subtitle:
        s.text(x + w - 12, y + 18, subtitle, fill=DIM, size=11, anchor="end")


def render(cfg: dict | None = None) -> str:
    cfg = cfg or load()
    net = circuit.build_netlist(cfg)
    comps = {c["ref"]: c for c in net["components"]}
    counts = {}
    for c in net["components"]:
        counts[c["group"]] = counts.get(c["group"], 0) + 1

    s = Svg()
    s.text(24, 30, "Claude Micro", size=16, weight=700)
    s.text(24, 48, cfg["meta"]["description"], fill=DIM, size=11.5)
    s.text(W - 24, 30, f'rev {cfg["meta"]["version"]}', fill=DIM, size=11.5, anchor="end")
    s.text(W - 24, 48, f'{len(net["components"])} components · {len(net["nets"])} nets',
           fill=DIM, size=11.5, anchor="end")

    # ---------------- MCU ----------------
    mx, my, mw, mh = 430, 96, 320, 430
    _block(s, mx, my, mw, mh, "MOD1  ·  RP2040 module", "Pico form factor, 40 pin", DIG)
    s.text(mx + 12, my + 46, cfg["module"]["recommended"], fill=DIM, size=10.5)
    s.text(mx + 12, my + 62, "USB-C · 2-16 MB flash · on-board 3V3 regulator",
           fill=DIM, size=10.5)

    left_pins = [
        ("ROW0..3", "GP2-GP5", DIG), ("COL0..3", "GP6-GP9", DIG),
        ("ENC_A / ENC_B", "GP10, GP11", DIG),
        ("TOUCH0..4", "GP16-GP20", TCH), ("TOUCH_DRV", "GP21", TCH),
    ]
    right_pins = [
        ("LED_DIN_3V3", "GP12", DIG), ("JOY_X / JOY_Y", "GP26, GP27 (ADC)", ANA),
        ("JOY_SW", "GP22", DIG), ("RESET", "RUN", PWR),
        ("+3V3", "3V3 OUT / ADC_VREF", PWR), ("VBUS", "5 V from USB", PWR),
    ]
    y = my + 92
    for name, pin, col in left_pins:
        s.path(f"M{mx - 34} {y} H{mx}", stroke=col, sw=1.4)
        s.dot(mx, y, 2.6, col)
        s.text(mx + 10, y + 4, name, size=11.5, mono=True)
        s.text(mx + mw - 12, y + 4, pin, fill=DIM, size=10.5, anchor="end", mono=True)
        y += 26
    y += 12
    for name, pin, col in right_pins:
        s.path(f"M{mx + mw} {y} H{mx + mw + 34}", stroke=col, sw=1.4)
        s.dot(mx + mw, y, 2.6, col)
        s.text(mx + 10, y + 4, name, size=11.5, mono=True)
        s.text(mx + mw - 12, y + 4, pin, fill=DIM, size=10.5, anchor="end", mono=True)
        y += 26

    # ---------------- switch matrix ----------------
    bx, by, bw, bh = 44, 96, 340, 250
    nodes = cfg["matrix"]["nodes"]
    _block(s, bx, by, bw, bh, "Switch matrix", f'{len(nodes)} nodes · COL2ROW', DIG)
    s.text(bx + 12, by + 46,
           f'{len(cfg["layout"]["keys"])} Kailh Choc v1 + the encoder push, '
           f'each with a 1N4148W', fill=DIM, size=10.5)

    cell, ox, oy = 30, bx + 40, by + 76
    rows, cols = cfg["matrix"]["rows"], cfg["matrix"]["cols"]
    grid = {(n["row"], n["col"]): n["id"] for n in nodes}
    for c in range(cols):
        s.text(ox + c * cell + cell / 2, oy - 10, f"C{c}", fill=DIM, size=10, anchor="middle")
    for r in range(rows):
        s.text(ox - 12, oy + r * cell + cell / 2 + 4, f"R{r}", fill=DIM, size=10, anchor="end")
        for c in range(cols):
            k = grid.get((r, c))
            fill = "#243040" if k else "#191d24"
            s.rect(ox + c * cell + 2, oy + r * cell + 2, cell - 4, cell - 4,
                   fill=fill, stroke=(DIG if k else LINE), rx=5, sw=1)
            if k:
                s.text(ox + c * cell + cell / 2, oy + r * cell + cell / 2 + 4, k,
                       size=9.5, anchor="middle", mono=True)
    s.text(ox + cols * cell + 16, oy + 16, "diode", fill=DIM, size=10)
    s.text(ox + cols * cell + 16, oy + 30, "per node", fill=DIM, size=10)
    s.path(f"M{bx + bw} {by + 130} H{mx - 34}", stroke=DIG, sw=1.4)
    s.path(f"M{bx + bw} {by + 156} H{mx - 34}", stroke=DIG, sw=1.4)

    # ---------------- touch strip ----------------
    tx, ty, tw, th = 44, 366, 340, 160
    ts = cfg["layout"]["touch_strip"]
    _block(s, tx, ty, tw, th, "Capacitive touch strip", f'{ts["pads"]} pads · RC sensing', TCH)
    s.text(tx + 12, ty + 46, "GP21 drives all five pads through 1 MOhm;", fill=DIM, size=10.5)
    s.text(tx + 12, ty + 61, "GP16-GP20 time the rising edge. No touch IC.", fill=DIM, size=10.5)
    pw = (tw - 40) / ts["pads"]
    for i in range(ts["pads"]):
        px = tx + 20 + i * pw
        s.rect(px + 2, ty + 78, pw - 6, 30, fill="#2a2438", stroke=TCH, rx=4, sw=1)
        s.text(px + pw / 2 - 1, ty + 97, f"TP{i + 1}", size=10, anchor="middle", mono=True)
        s.path(f"M{px + pw / 2 - 1} {ty + 108} V{ty + 124}", stroke=TCH, sw=1.2)
        s.rect(px + pw / 2 - 12, ty + 124, 22, 11, fill="#1b1f27", stroke=TCH, rx=3, sw=1)
    s.text(tx + tw / 2, ty + 150, "R5-R9  1 MOhm", fill=DIM, size=10, anchor="middle", mono=True)
    s.path(f"M{tx + tw} {ty + 100} H{mx - 34}", stroke=TCH, sw=1.4)

    # ---------------- encoder + joystick ----------------
    ex, ey, ew, eh = 800, 96, 336, 200
    _block(s, ex, ey, ew, eh, "ENC1  ·  EC11E rotary", "reasoning effort dial", DIG)
    s.text(ex + 12, ey + 46, "A and B pulled up 10k, 100nF to ground (R1/R2, C24/C25).",
           fill=DIM, size=10.5)
    s.text(ex + 12, ey + 62, "Detent switch sits in the matrix at row 3, col 1.",
           fill=DIM, size=10.5)
    s.rect(ex + 24, ey + 82, 96, 96, fill="#20252f", stroke=DIG, rx=48)
    s.rect(ex + 60, ey + 96, 24, 24, fill=PWR, stroke=PWR, rx=12)
    for i, (lbl, col) in enumerate([("A  GP10", DIG), ("B  GP11", DIG),
                                    ("C  GND", PWR), ("SW  matrix", DIG)]):
        yy = ey + 92 + i * 24
        s.path(f"M{ex + 130} {yy} H{ex + 158}", stroke=col, sw=1.4)
        s.text(ex + 166, yy + 4, lbl, size=11, mono=True)

    jx, jy, jw, jh = 800, 316, 336, 210
    _block(s, jx, jy, jw, jh, "JS1  ·  2-axis thumbstick", "skill launcher", ANA)
    s.text(jx + 12, jy + 46, "10k pots to 3V3, each through a 1k / 10nF filter", fill=DIM, size=10.5)
    s.text(jx + 12, jy + 61, "into ADC0 and ADC1. Click goes straight to GP22.",
           fill=DIM, size=10.5)
    s.rect(jx + 24, jy + 82, 96, 96, fill="#20252f", stroke=ANA, rx=14)
    s.dot(jx + 72, jy + 130, 20, "#2c3541")
    s.dot(jx + 72, jy + 130, 8, ANA)
    for i, (lbl, col) in enumerate([("VRX  GP26", ANA), ("VRY  GP27", ANA),
                                    ("SW   GP22", DIG), ("VCC  +3V3", PWR),
                                    ("GND", PWR)]):
        yy = jy + 92 + i * 20
        s.path(f"M{jx + 130} {yy} H{jx + 158}", stroke=col, sw=1.4)
        s.text(jx + 166, yy + 4, lbl, size=11, mono=True)

    # ---------------- power + LED chain ----------------
    px, py, pw2, ph = 44, 552, 700, 196
    _block(s, px, py, pw2, ph, "Power and RGB chain",
           f'{cfg["rgb"]["total"]} x SK6812', PWR)
    stages = [
        ("USB 5 V", "VBUS"), ("F1", "1.1 A PPTC"), ("+5 V", "470uF + 10uF"),
        ("U2", "74LVC1G17"), ("R10", "470R"), ("LED1", "chain in"),
    ]
    sx = px + 24
    for i, (a, b) in enumerate(stages):
        s.rect(sx, py + 56, 96, 46, fill="#20252f", stroke=PWR, rx=6)
        s.text(sx + 48, py + 76, a, size=11.5, anchor="middle", weight=600)
        s.text(sx + 48, py + 92, b, fill=DIM, size=10, anchor="middle", mono=True)
        if i < len(stages) - 1:
            s.path(f"M{sx + 96} {py + 79} H{sx + 116}", stroke=PWR, sw=1.6)
        sx += 116
    s.text(px + 24, py + 128,
           "GP12 is 3V3 logic; the SK6812 data line needs 5 V, so it goes through the buffer.",
           fill=DIM, size=10.5)
    s.text(px + 24, py + 146,
           f'Chain order: 13 per-key LEDs (serpentine A1-A6, K4-K1, K5-K7) then '
           f'{len(cfg["rgb"]["underglow"]["positions"])} underglow.', fill=DIM, size=10.5)
    s.text(px + 24, py + 164,
           f'Worst case {cfg["rgb"]["total"] * cfg["rgb"]["max_current_ma_per_led"]} mA; '
           f'firmware caps the chain at {cfg["rgb"]["firmware_current_limit_ma"]} mA.',
           fill=DIM, size=10.5)

    lx = px + 24
    for i in range(cfg["rgb"]["total"]):
        col = PWR if i < 13 else DIG
        s.rect(lx, py + 176 - 2, 26, 12, fill="#20252f", stroke=col, rx=3, sw=1)
        lx += 30
    s.text(lx + 8, py + 186, "DOUT -> DIN", fill=DIM, size=10, mono=True)

    # ---------------- summary ----------------
    qx, qy = 768, 552
    _block(s, qx, qy, 368, 196, "Netlist summary", "generated", LINE)
    rows_txt = [
        ("Components", f'{len(net["components"])}'),
        ("Nets", f'{len(net["nets"])}'),
        ("Switch matrix", f'{cfg["matrix"]["rows"]} x {cfg["matrix"]["cols"]}, '
                          f'{len(cfg["matrix"]["nodes"])} used'),
        ("Diodes", f'{counts.get("matrix", 0) - len(cfg["layout"]["keys"])} + '
                   f'{len(cfg["layout"]["keys"])}'),
        ("RGB devices", f'{cfg["rgb"]["total"]}'),
        ("Board", f'{cfg["pcb"]["width"]} x {cfg["pcb"]["height"]} mm, '
                  f'{cfg["pcb"]["layers"]} layer'),
        ("Min track / clearance", f'{cfg["pcb"]["design_rules"]["min_track"]} / '
                                  f'{cfg["pcb"]["design_rules"]["min_clearance"]} mm'),
    ]
    yy = qy + 52
    for k, v in rows_txt:
        s.text(qx + 16, yy, k, fill=DIM, size=11.5)
        s.text(qx + 352, yy, v, size=11.5, anchor="end", mono=True)
        yy += 20
    return s.render()


if __name__ == "__main__":
    print(render()[:400])
