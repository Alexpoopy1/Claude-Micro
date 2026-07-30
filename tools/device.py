"""Loader for config/device.json plus the values derived from it.

Everything downstream (STL, PCB, gerbers, firmware headers, HTML) reads the
device through this module, so a change to the config propagates everywhere and
the test suite only has one place to check.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config", "device.json")
BUILD = os.path.join(ROOT, "build")


@lru_cache(maxsize=1)
def load(path: str = CONFIG) -> dict:
    with open(path) as fh:
        cfg = json.load(fh)
    _derive(cfg)
    return cfg


def _derive(cfg: dict) -> None:
    lay = cfg["layout"]
    keys = lay["keys"]

    # Per-key RGB sits just north of the switch centre, in its light window.
    for k in keys:
        k["led"] = [k["x"], k["y"] - cfg["rgb"]["per_key"]["offset_y"]]

    # Full LED chain order: keys first (index 0..12) then underglow (13..18).
    # Serpentine the chain (left->right, then back) so the data line stays short.
    order = ["A1", "A2", "A3", "A4", "A5", "A6",
             "K4", "K3", "K2", "K1",
             "K5", "K6", "K7"]
    by_id = {k["id"]: k for k in keys}
    chain = [{"id": f"LED{i+1}", "x": by_id[kid]["led"][0], "y": by_id[kid]["led"][1],
              "kind": "perkey", "key": kid, "part": cfg["rgb"]["per_key"]["part"]}
             for i, kid in enumerate(order)]
    n = len(chain)
    for j, (x, y) in enumerate(cfg["rgb"]["underglow"]["positions"]):
        chain.append({"id": f"LED{n+j+1}", "x": x, "y": y, "kind": "underglow",
                      "key": None, "part": cfg["rgb"]["underglow"]["part"]})
    cfg["rgb"]["chain"] = chain

    # Every matrix node that exists, including the encoder push and joystick click.
    nodes = [{"id": k["id"], "row": k["matrix"][0], "col": k["matrix"][1],
              "x": k["x"], "y": k["y"], "kind": "switch"} for k in keys]
    enc, joy = lay["encoder"], lay["joystick"]
    nodes.append({"id": "ENC_SW", "row": enc["switch_matrix"][0], "col": enc["switch_matrix"][1],
                  "x": enc["x"], "y": enc["y"], "kind": "encoder_push"})
    cfg["matrix"]["nodes"] = nodes

    ts = lay["touch_strip"]
    span = ts["x1"] - ts["x0"]
    pw = (span - ts["pad_gap"] * (ts["pads"] - 1)) / ts["pads"]
    ts["pad_w"] = pw
    ts["pad_rects"] = [
        {"i": i, "x0": ts["x0"] + i * (pw + ts["pad_gap"]), "y0": ts["y0"],
         "x1": ts["x0"] + i * (pw + ts["pad_gap"]) + pw, "y1": ts["y1"]}
        for i in range(ts["pads"])
    ]

    p = cfg["pcb"]
    p["width"] = p["x1"] - p["x0"]
    p["height"] = p["y1"] - p["y0"]
    p["cx"] = (p["x0"] + p["x1"]) / 2.0
    p["cy"] = (p["y0"] + p["y1"]) / 2.0

    c = cfg["case"]["outer"]
    cfg["case"]["width"] = c["x1"] - c["x0"]
    cfg["case"]["height"] = c["y1"] - c["y0"]
    cfg["case"]["cx"] = (c["x0"] + c["x1"]) / 2.0
    cfg["case"]["cy"] = (c["y0"] + c["y1"]) / 2.0

    w = cfg["case"]["window"]
    w["cx"] = (w["x0"] + w["x1"]) / 2.0
    w["cy"] = (w["y0"] + w["y1"]) / 2.0
    w["w"] = w["x1"] - w["x0"]
    w["h"] = w["y1"] - w["y0"]


def keys(cfg: dict | None = None) -> list[dict]:
    return (cfg or load())["layout"]["keys"]


def agent_keys(cfg: dict | None = None) -> list[dict]:
    return [k for k in keys(cfg) if k["group"] == "agent"]


def command_keys(cfg: dict | None = None) -> list[dict]:
    return [k for k in keys(cfg) if k["group"] == "command"]


def ensure_build(*parts: str) -> str:
    path = os.path.join(BUILD, *parts)
    os.makedirs(path, exist_ok=True)
    return path
