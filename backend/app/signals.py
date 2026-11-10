"""Traffic signal timing: approaches, two-phase plans, Webster optimisation.

OSM has no standard tag for signal programs (SUMO builds phases itself), so the
timing lives in the editor's scenario next to the load model. It feeds the
load model: the green share of an approach scales its capacity.

Approaches are grouped geometrically: the main axis follows the highest-class
road at the node, arms within 45 degrees of that axis (either direction) are
"main", the others "cross".

On export the timing is written onto the signal node as namespaced tags
(`uds:signal:*`, see `timing_tags`). Adapters ignore unknown namespaces, so the
file stays valid for MatSim/SUMO/Webots, and a re-import restores the plan.
"""

from __future__ import annotations

import math
from typing import Any

from .analysis import projection
from .osm_model import OsmDocument
from .roads import is_drivable

DEFAULT_CYCLE = 90
LOST_TIME_S = 10      # start-up and clearance lost per cycle (two phases)
YELLOW_S = 3          # ГОСТ Р 52289-2019: жёлтый сигнал 3 с
ALL_RED_S = (LOST_TIME_S - 2 * YELLOW_S) // 2   # all-red clearance after each yellow: 2 s
# so per phase green + 3 s yellow + 2 s all-red, and the two phases add up to the cycle exactly
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
    g_main = round(green * share)
    phases = [
        {"name": "Главное направление", "group": "main", "green_s": g_main, "yellow_s": YELLOW_S,
         "all_red_s": ALL_RED_S},
        {"name": "Пересекающее направление", "group": "cross", "green_s": green - g_main, "yellow_s": YELLOW_S,
         "all_red_s": ALL_RED_S},
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


TAG_PREFIX = "uds:signal:"


def is_signal(tags: dict[str, str]) -> bool:
    return tags.get("highway") == "traffic_signals" or tags.get("crossing") == "traffic_signals"


def timing_tags(doc: OsmDocument, settings: dict[int, dict[str, Any]]) -> dict[int, dict[str, str | None]]:
    """`uds:signal:*` tag overrides per node for the configured timing.

    Writes cycle, main_share and the derived green/yellow durations in seconds,
    so the plan is readable without the editor. Nodes that carry such tags from
    an earlier export but have no timing now (reset, or no longer a signal) get
    None values, which drop the stale tags from the output.
    """
    out: dict[int, dict[str, str | None]] = {}
    for n in doc.nodes.values():
        stale = [k for k in n.tags if k.startswith(TAG_PREFIX)]
        if stale:
            out[n.id] = dict.fromkeys(stale)
    for node_id, cfg in settings.items():
        n = doc.nodes.get(node_id)
        if n is None or not is_signal(n.tags):
            continue
        cycle = int(cfg.get("cycle", DEFAULT_CYCLE))
        share = float(cfg.get("main_share", 0.5))
        green = cycle - LOST_TIME_S
        g_main = round(green * share)
        out[node_id] = {**out.get(node_id, {}),
            TAG_PREFIX + "cycle": str(cycle),
            TAG_PREFIX + "main_share": f"{share:.2f}",
            TAG_PREFIX + "green_main": str(g_main),
            TAG_PREFIX + "green_cross": str(green - g_main),
            TAG_PREFIX + "yellow": str(YELLOW_S),
            TAG_PREFIX + "all_red": str(ALL_RED_S),
        }
    return out


def timing_from_tags(doc: OsmDocument) -> dict[int, dict[str, Any]]:
    """Read back timing written by `timing_tags` (invalid values are skipped)."""
    out: dict[int, dict[str, Any]] = {}
    for n in doc.nodes.values():
        if not is_signal(n.tags) or TAG_PREFIX + "cycle" not in n.tags:
            continue
        try:
            cycle = int(n.tags[TAG_PREFIX + "cycle"])
            share = float(n.tags.get(TAG_PREFIX + "main_share", "0.5"))
        except ValueError:
            continue
        if 30 <= cycle <= 180 and MIN_SHARE <= share <= MAX_SHARE:
            out[n.id] = {"cycle": cycle, "main_share": share}
    return out
