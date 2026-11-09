"""Traffic signal timing: approaches, two-phase plans, Webster optimisation.

OSM has no standard tag for signal programs (SUMO builds phases itself), so the
timing lives in the editor's scenario next to the load model. It feeds the
load model: the green share of an approach scales its capacity.

Approaches are grouped geometrically: the main axis follows the highest-class
road at the node, arms within 45 degrees of that axis (either direction) are
"main", the others "cross".
"""

from __future__ import annotations

import math
from typing import Any

from .analysis import projection
from .osm_model import OsmDocument
from .roads import is_drivable

DEFAULT_CYCLE = 90
LOST_TIME_S = 10      # start-up and clearance lost per cycle (two phases)
YELLOW_S = 3
MIN_SHARE, MAX_SHARE = 0.2, 0.8
CLASS_RANK = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
              "living_street", "service", "road"]


def _rank(highway: str) -> int:
    base = highway.removesuffix("_link")
    return CLASS_RANK.index(base) if base in CLASS_RANK else len(CLASS_RANK)


def _angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def approaches(doc: OsmDocument, node_id: int) -> list[dict[str, Any]]:
    """Every road arm at the node with its bearing and phase group."""
    n = doc.nodes.get(node_id)
    if n is None:
        return []
    proj = projection(doc)
    origin = proj.to_xy(n.lat, n.lon)
    arms: list[dict[str, Any]] = []
    for w in doc.ways.values():
        if not is_drivable(w.tags) or node_id not in w.nodes:
            continue
        for i, ref in enumerate(w.nodes):
            if ref != node_id:
                continue
            for j in (i - 1, i + 1):
                if 0 <= j < len(w.nodes) and w.nodes[j] in doc.nodes:
                    m = doc.nodes[w.nodes[j]]
                    x, y = proj.to_xy(m.lat, m.lon)
                    bearing = (math.degrees(math.atan2(x - origin[0], y - origin[1])) + 360) % 360
                    arms.append({"way_id": w.id, "name": w.tags.get("name", ""), "highway": w.tags.get("highway", ""),
                                 "bearing": round(bearing)})
    if not arms:
        return []
    axis = min(arms, key=lambda a: (_rank(a["highway"]), a["way_id"]))["bearing"]
    for a in arms:
        main = _angle_diff(a["bearing"], axis) <= 45 or _angle_diff(a["bearing"], axis + 180) <= 45
        a["group"] = "main" if main else "cross"
    return arms


def capacity_factors(doc: OsmDocument, settings: dict[int, dict[str, Any]]) -> dict[int, float]:
    """Capacity multiplier per way from the green share its approach gets (1.0 = even split)."""
    out: dict[int, float] = {}
    for node_id, cfg in settings.items():
        share = float(cfg.get("main_share", 0.5))
        for arm in approaches(doc, node_id):
            g = share if arm["group"] == "main" else 1 - share
            f = max(0.3, min(1.7, g / 0.5))
            out[arm["way_id"]] = min(out.get(arm["way_id"], f), f)
    return out


def plan(doc: OsmDocument, node_id: int, cfg: dict[str, Any] | None, rows: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Current two-phase plan of a signal and the Webster suggestion for it."""
    cfg = cfg or {}
    cycle = int(cfg.get("cycle", DEFAULT_CYCLE))
    share = float(cfg.get("main_share", 0.5))
    arms = approaches(doc, node_id)
    for a in arms:
        r = rows.get(a["way_id"])
        a["volume"] = r["volume"] if r else None
        a["capacity"] = r["capacity"] if r else None
        a["vc"] = r["vc"] if r else None
    # flow ratio y = v / s, with saturation flow s = capacity at an even split / 0.5
    def y(group: str) -> float:
        vals = []
        for a in arms:
            if a["group"] != group or not a["capacity"]:
                continue
            factor = (share if group == "main" else 1 - share) / 0.5
            sat = a["capacity"] / max(0.3, min(1.7, factor)) / 0.5
            vals.append(a["volume"] / sat if sat else 0.0)
        return max(vals, default=0.0)
    y_main, y_cross = y("main"), y("cross")
    total = y_main + y_cross
    if total >= 0.95:
        best_cycle = 150
    else:
        best_cycle = int(max(40, min(150, round((1.5 * LOST_TIME_S + 5) / (1 - total)))))
    best_share = round(max(MIN_SHARE, min(MAX_SHARE, y_main / total if total > 0 else 0.5)), 2)
    green = cycle - LOST_TIME_S
    phases = [
        {"name": "Главное направление", "group": "main", "green_s": round(green * share), "yellow_s": YELLOW_S},
        {"name": "Пересекающее направление", "group": "cross", "green_s": round(green * (1 - share)), "yellow_s": YELLOW_S},
    ]
    return {
        "node_id": node_id,
        "cycle": cycle,
        "main_share": share,
        "custom": bool(cfg),
        "approaches": arms,
        "phases": phases,
        "flow_ratio": {"main": round(y_main, 3), "cross": round(y_cross, 3)},
        "webster": {"cycle": best_cycle, "main_share": best_share,
                    "saturated": total >= 0.95},
    }
