# Claude Micro

A 13-key low-profile agent macropad: six frosted status keys, seven command
keys, a rotary reasoning dial, a planar joystick, a five-pad capacitive touch
strip and a 19-LED RGB chain. Fully printable, fully routed, fully checked.

Design study of the [Work Louder × OpenAI Codex Micro][codex] — same feature
set (13 switches, 1 encoder, 1 planar joystick, 1 touch sensor, 6 agent keys),
rebuilt from scratch as open hardware with the Claude mark on the front.

[codex]: https://openai.com/supply/co-lab/work-louder/

```
make            # build everything, then verify it
open build/claude-micro.html
```

## What you get

| Output | Where | What it is |
| --- | --- | --- |
| **Interactive viewer** | `build/claude-micro.html` | One self-contained file: WebGL model with an exploded-view slider, PCB layer viewer, schematic, BOM, print guide, firmware reference and the full check report. No network, no CDN. |
| **Printable parts** | `build/stl/*.stl` | 9 parts, 22 pieces, ~62 g of filament. Every one proven watertight before it is written. |
| **PCB** | `build/pcb/claude-micro.kicad_pcb` | Open in KiCad 7/8. Placed, routed, poured, zoned. Also `.kicad_sch`, `.kicad_pro`, `.net`. |
| **Fabrication** | `build/pcb/gerbers/` | RS-274X gerbers for all nine layers plus separate plated and non-plated Excellon drill files. Upload as-is. |
| **Assembly** | `build/pcb/bom.csv`, `positions.csv` | 18 BOM lines, 89 components, pick-and-place positions. |
| **Firmware** | `firmware/qmk/claude_micro/` | Drop-in QMK keyboard folder. |
| **Reports** | `build/pcb/drc_report.json`, `build/stl_report.json` | Machine-readable results of every check. |

## Hardware

|  |  |
| --- | --- |
| Keys | 13 × Kailh Choc v1 on an 18 × 17 mm grid — 6 agent, 7 command |
| Dial | EC11E rotary encoder with push, on a 26 mm knurled knob |
| Joystick | 20 mm 2-axis analog thumbstick into ADC0/ADC1, click on its own pin |
| Touch | 5-pad strip read by RC charge time — no touch controller |
| RGB | 19 × SK6812 (13 per-key + 6 underglow), 5 V through a 74LVC1G17 buffer |
| MCU | RP2040 module, Raspberry Pi Pico form factor (USB-C variant recommended) |
| Board | 128 × 88 mm, 2 layer, 1.6 mm, 0.2 mm rules |
| Case | 134 × 94 × 16.4 mm printed two-piece, 6° tilt feet |

### Agent keys

The six frosted keys mirror six host threads. The host pushes state over raw
HID and the board renders it:

| State | Colour | |
| --- | --- | --- |
| idle | white | nothing running |
| thinking | blue, breathing | model is reasoning |
| running | light blue, breathing | tools are executing |
| waiting | amber | needs your input |
| done | green | finished cleanly |
| error | red | failed |

If the host goes quiet for 30 s every key falls back to idle, so a crashed
bridge never leaves stale status on the desk.

### The dial

Turning it walks reasoning effort — low → medium → high → max. Pressing it is
an ordinary matrix key (row 3, col 1), so it can be remapped like any other.

## How it is built

Everything derives from **`config/device.json`**. Change the key pitch there
and the STLs, the board, the firmware descriptors and the viewer all move
together; the test suite fails if any of them disagree.

```
config/device.json      one source of truth
        │
tools/  device.py       loader + derived layout (LED chain, matrix nodes, pads)
        mesh.py         watertight mesh kernel (no CSG, no dependencies)
        parts.py        every physical part, printable and purchased
        circuit.py      schematic as data: components, footprints, nets
        router.py       grid maze router + copper pour generator
        build_pcb.py    place → route → pour → check
        outputs.py      KiCad, gerbers, Excellon, BOM, board.json
        checks.py       ERC + DRC + mechanical fit
        schematic.py    SVG schematic
        build_stl.py    printable parts → binary STL
        build_html.py   the single-file viewer
        build_firmware.py  QMK descriptors
tests/  test_all.py     34 checks across geometry, layout, circuit and board
```

No third-party packages. Python 3.11 standard library only.

### The mesh kernel

There is no CSG. Every solid is built from operations that are watertight by
construction — polygon extrusion, lofting, and lathing — and the awkward case
(capping an outline riddled with holes) is handled by a trapezoidal scanline
decomposition that re-subdivides the side walls so caps and walls share exactly
the same vertices. `Mesh.validate()` then *proves* each part is a closed,
consistently oriented 2-manifold with positive volume, and `build_stl.py`
refuses to write a file that fails.

### The router

The board is rasterised onto a 0.2 mm grid where every piece of copper is
stamped along with its clearance halo, so "may net N occupy this cell?" is one
array lookup. Nets are then routed shortest-first with A* over that grid, with
a cost for changing layer and a separate keepout map that stops a via being
drilled too close to another hole. Leftover free space becomes the ground pour,
emitted as scanline runs. Any GND pad the pour cannot reach gets found by a
flood fill and routed in with a track — the same flood fill the DRC uses, so
the fixer and the checker cannot drift apart.

## Verification

`make test` runs 34 checks:

* **Geometry** — kernel invariants, analytic volume checks, a fuzz pass over
  random hole patterns, and every printable part proven watertight, manifold,
  correctly oriented, printer-sized and thick enough to print.
* **Layout** — key pitch, top-surface collisions, bezel window clearances, a
  monotonic Z stack, and that the encoder shaft does not bottom out in the knob.
* **Circuit** — ERC, one switch and one diode per matrix node, unique matrix
  addresses, an unbroken LED chain, no double-booked GPIO, the LED rail fused
  and level-shifted, the touch strip fully wired.
* **Board** — every connection routed, ≥ 0.2 mm copper clearance verified on a
  fresh raster of the finished geometry, annular rings, hole-to-hole spacing,
  all copper inside the profile, the ground pour reaching every GND pad, no
  courtyard overlaps, and nothing under the MCU module taller than its standoff.
* **Outputs** — balanced s-expressions in the KiCad files, well-formed gerbers
  and Excellon, drill counts matching the board, the BOM covering every
  component, the viewer free of external references, and the firmware agreeing
  with the config on pins, LED count, layers and layout size.

Current status: **0 DRC errors, 0 warnings, 141/141 connections routed, 9/9
meshes watertight.**

## Building one

1. **Print** at 0.2 mm, 3 walls, 25% infill. No supports on any part. Print the
   six agent keycaps in natural or clear filament — the roof is 1.0 mm so the
   status colour reads through.
2. **Order the PCB** — upload `build/pcb/gerbers/` as a zip. Nothing exotic:
   2 layer, 1.6 mm, 0.2 mm track and clearance.
3. **Populate** — bottom side first (diodes, 0603s, the SOT-23-5, then the
   RP2040 module on a 2.54 mm header), then the top (LEDs, switches, encoder,
   thumbstick).
4. **Flash** — `qmk compile -kb claude_micro -km default`, hold BOOTSEL, copy
   the UF2 across.
5. **Assemble** — six M2 heat-set inserts in the bezel, drop the diffuser into
   the touch window, six M2 × 6 mm screws from underneath.

Full step-by-step with per-part orientation is in the viewer's
**Print & assemble** tab.

## Licence

Hardware and firmware are open. The Claude mark is Anthropic's; it is used here
on a personal build, not a product for sale.
