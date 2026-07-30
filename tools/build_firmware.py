#!/usr/bin/env python3
"""Regenerate the QMK descriptors that must track config/device.json.

Only keyboard.json is generated; the hand-written C (matrix.c, claude_micro.c,
keymap.c) reads its pins through QMK's generated headers, and tests/test_all.py
fails if any of it drifts away from the config.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from device import load, ROOT   # noqa: E402

FW = os.path.join(ROOT, "firmware", "qmk", "claude_micro")


def build(verbose: bool = True) -> str:
    cfg = load()
    nodes = cfg["matrix"]["nodes"]
    lay = cfg["layout"]

    layout = [{"matrix": [n["row"], n["col"]],
               "x": round(n["x"] / lay["pitch_x"], 2),
               "y": round(n["y"] / lay["pitch_y"], 2),
               "label": n["id"]} for n in nodes]
    # The thumbstick click has its own GPIO but is folded into the spare
    # matrix intersection by matrix.c, so it is an ordinary key here.
    layout.append({"matrix": [3, 3], "x": 3.5, "y": 2.3, "label": "JOY_SW"})

    led_layout = []
    for i, led in enumerate(cfg["rgb"]["chain"]):
        pos = nodes[i] if i < len(nodes) else None
        entry = {"x": max(0, min(224, round(led["x"] * 2.0 + 40))),
                 "y": max(0, min(64, round(led["y"] * 0.9 + 12))),
                 "flags": 4 if led["kind"] == "perkey" else 2}
        if pos is not None:
            entry["matrix"] = [pos["row"], pos["col"]]
        led_layout.append(entry)

    info = {
        "manufacturer": cfg["meta"]["usb"]["manufacturer"],
        "keyboard_name": cfg["meta"]["usb"]["product"],
        "maintainer": "claude-micro",
        "processor": "RP2040",
        "bootloader": "rp2040",
        "board": "GENERIC_RP_RP2040",
        "usb": {"vid": cfg["meta"]["usb"]["vid"], "pid": cfg["meta"]["usb"]["pid"],
                "device_version": "1.0.0"},
        "diode_direction": cfg["matrix"]["diode_direction"],
        "matrix_pins": {"rows": cfg["matrix"]["row_pins"],
                        "cols": cfg["matrix"]["col_pins"]},
        "encoder": {"rotary": [{"pin_a": cfg["pins"]["encoder_a"],
                                "pin_b": cfg["pins"]["encoder_b"],
                                "resolution": 4}]},
        "ws2812": {"pin": cfg["pins"]["led_data"], "driver": "vendor"},
        "rgb_matrix": {
            "driver": "ws2812",
            "led_count": cfg["rgb"]["total"],
            "max_brightness": 160,
            "sleep": True,
            "animations": {"solid_color": True, "breathing": True,
                           "cycle_left_right": True, "solid_reactive_simple": True},
            "layout": led_layout,
        },
        "features": {"bootmagic": True, "extrakey": True, "nkro": True,
                     "console": True, "command": False, "mousekey": False,
                     "encoder": True, "rgb_matrix": True, "raw": True},
        "layouts": {"LAYOUT": {"layout": layout}},
    }
    path = os.path.join(FW, "keyboard.json")
    with open(path, "w") as fh:
        json.dump(info, fh, indent=2)
        fh.write("\n")
    if verbose:
        print(f"  wrote {path}  ({len(layout)} keys, {cfg['rgb']['total']} LEDs)")
    return path


if __name__ == "__main__":
    build()
