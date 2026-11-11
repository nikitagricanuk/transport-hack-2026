"""Traffic signal timing: approaches, two-phase plans, Webster optimisation.

OSM has no standard tag for signal programs (SUMO builds phases itself), so the
timing lives in the editor's scenario next to the load model. It feeds the
load model: an approach to a signal has capacity c = lanes · s · g/C — saturation
flow s of its road class, green g of its phase, cycle C.

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


def approaches(doc: OsmDocument, node_id: int, ways: list | None = None) -> list[dict[str, Any]]:
    """Every road arm at the node with its bearing and phase group (`ways`: the ways at the node)."""
    n = doc.nodes.get(node_id)
    if n is None:
        return []
    proj = projection(doc)
    origin = proj.to_xy(n.lat, n.lon)
    arms: list[dict[str, Any]] = []
    for w in (ways if ways is not None else doc.ways.values()):
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


# Saturation flow per lane during green, veh/h — the flow a queue discharges at.
# Assumption: HCM base saturation flow 1900 pc/h/lane, reduced for narrower and busier
# urban classes. Not calibrated to Irkutsk counts.
SAT_FLOW = {
    "motorway": 1900, "trunk": 1900, "primary": 1800, "secondary": 1700, "tertiary": 1600,
    "unclassified": 1500, "residential": 1500, "living_street": 1200, "service": 1200, "road": 1500,
}
SAT_SOURCE = "HCM, базовый поток насыщения 1900 прив. ед./ч на полосу; понижен для классов — допущение"


def sat_flow(highway: str) -> float:
    return float(SAT_FLOW.get(highway.removesuffix("_link"), 1500))


def timing(cfg: dict[str, Any] | None) -> tuple[int, float, int, int]:
    """cycle C, main share, green of the main and of the cross phase (s); phases add up to C."""
    cfg = cfg or {}
    cycle = int(cfg.get("cycle", DEFAULT_CYCLE))
    share = float(cfg.get("main_share", 0.5))
    green = cycle - LOST_TIME_S
    g_main = round(green * share)
    return cycle, share, g_main, green - g_main


# ------------------------------------------------------------ phase program
# Movements a phase can release. A group ("main", "cross") is its whole approach: through
# and turns. An arrow is an additional section (ГОСТ Р 52289-2019): it releases only one
# turn of the group, while the group's main signal is red or together with it.
MOVES = {
    "main": "Главное направление", "cross": "Пересекающее направление",
    "main_left": "Стрелка налево с главного", "main_right": "Стрелка направо с главного",
    "cross_left": "Стрелка налево с пересекающего", "cross_right": "Стрелка направо с пересекающего",
}
# Share of an approach's flow that turns left / right: an arrow phase serves only that part.
# Assumption for an urban junction without turn counts.
TURN_SHARE = {"left": 0.15, "right": 0.20}
TURN_SOURCE = "Доля поворотов налево 15 %, направо 20 % — допущение при отсутствии картограммы"
INTERGREEN_S = YELLOW_S + ALL_RED_S   # yellow 3 s + all-red 2 s after every phase


def program(cfg: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The phase list of a signal: the stored program, or two phases from cycle and main share."""
    cfg = cfg or {}
    if cfg.get("program"):
        return [{"name": str(p.get("name") or f"Фаза {i + 1}"), "green_s": int(p["green_s"]),
                 "moves": [m for m in p.get("moves", []) if m in MOVES]}
                for i, p in enumerate(cfg["program"])]
    _cycle, _share, g_main, g_cross = timing(cfg)
    return [{"name": "Главное направление", "green_s": g_main, "moves": ["main"]},
            {"name": "Пересекающее направление", "green_s": g_cross, "moves": ["cross"]}]


def program_cycle(phases: list[dict[str, Any]]) -> int:
    """C = sum of (green + yellow + all-red) over the phases."""
    return sum(p["green_s"] + INTERGREEN_S for p in phases)


def effective_green(phases: list[dict[str, Any]], group: str) -> float:
    """Green time an approach of ``group`` effectively gets per cycle, s.

    Its own phases count in full. A phase that only shows an arrow for one of its turns
    counts with the share of the flow that turns there (TURN_SHARE): only those vehicles go.
    """
    g = 0.0
    for p in phases:
        moves = set(p["moves"])
        if group in moves:
            g += p["green_s"]
            continue
        g += sum(p["green_s"] * TURN_SHARE[t] for t in ("left", "right") if f"{group}_{t}" in moves)
    return g


def signal_approaches(doc: OsmDocument, settings: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """For every way that approaches a signal: its green g, cycle C and saturation flow s.

    Every signal counts, not only the ones with a custom plan: an untouched signal runs
    the default two-phase plan (C = 90 s, even split). A way with signals at both ends
    takes the stricter one (smaller g/C).
    """
    index = doc.node_way_index()
    out: dict[int, dict[str, Any]] = {}
    for node_id, n in doc.nodes.items():
        if not is_signal(n.tags):
            continue
        ways = [doc.ways[x] for x in index.get(node_id, []) if x in doc.ways and is_drivable(doc.ways[x].tags)]
        if len(ways) < 2 and not any(node_id in w.nodes[1:-1] for w in ways):
            continue   # a signal on a dead end controls nothing
        phases = program(settings.get(node_id))
        cycle = program_cycle(phases)
        greens = {grp: effective_green(phases, grp) for grp in ("main", "cross")}
        for arm in approaches(doc, node_id, ways):
            g = round(greens[arm["group"]], 1)
            info = {"node_id": node_id, "group": arm["group"], "green_s": g, "cycle_s": cycle,
                    "arrows": len(phases) > 2 or any(len(p["moves"]) > 1 for p in phases),
                    "g_c": g / cycle, "sat_flow": sat_flow(arm["highway"]), "custom": node_id in settings}
            prev = out.get(arm["way_id"])
            if prev is None or info["g_c"] < prev["g_c"]:
                out[arm["way_id"]] = info
    return out


def capacity_factors(doc: OsmDocument, settings: dict[int, dict[str, Any]]) -> dict[int, float]:
    """Capacity multiplier per way so that a signalised approach has c = lanes · s · g/C.

    The load model multiplies lanes × class capacity per lane by this factor, so the
    factor is s · g/C divided by the class capacity per lane.
    """
    from .analysis import LANE_CAPACITY

    out: dict[int, float] = {}
    for wid, info in signal_approaches(doc, settings).items():
        hw = doc.ways[wid].tags.get("highway", "")
        out[wid] = info["sat_flow"] * info["g_c"] / LANE_CAPACITY.get(hw, 500)
    return out


def plan(doc: OsmDocument, node_id: int, cfg: dict[str, Any] | None, rows: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Current two-phase plan of a signal and the Webster suggestion for it."""
    cfg = cfg or {}
    prog = program(cfg)
    cycle = program_cycle(prog)
    g_eff = {grp: effective_green(prog, grp) for grp in ("main", "cross")}
    share = round(g_eff["main"] / ((g_eff["main"] + g_eff["cross"]) or 1), 2)
    arms = approaches(doc, node_id)
    for a in arms:
        r = rows.get(a["way_id"])
        a["volume"] = r["volume"] if r else None
        a["capacity"] = r["capacity"] if r else None
        a["vc"] = r["vc"] if r else None
    # flow ratio y = v / s, saturation flow of the approach s = lanes · s(class)
    for a in arms:
        r = rows.get(a["way_id"])
        a["saturation"] = round((r["lanes"] if r else 1) * sat_flow(a["highway"]))

    def y(group: str) -> float:
        vals = [a["volume"] / a["saturation"] for a in arms
                if a["group"] == group and a["volume"] is not None and a["saturation"]]
        return max(vals, default=0.0)
    y_main, y_cross = y("main"), y("cross")
    total = y_main + y_cross
    if total >= 0.95:
        best_cycle = 150
    else:
        best_cycle = int(max(40, min(150, round((1.5 * LOST_TIME_S + 5) / (1 - total)))))
    best_share = round(max(MIN_SHARE, min(MAX_SHARE, y_main / total if total > 0 else 0.5)), 2)
    phases = [{**p, "group": "main" if "main" in p["moves"] else "cross" if "cross" in p["moves"] else "arrow",
               "yellow_s": YELLOW_S, "all_red_s": ALL_RED_S,
               "moves_ru": [MOVES[m] for m in p["moves"]]} for p in prog]
    return {
        "program": bool(cfg.get("program")),
        "effective_green": {k: round(v, 1) for k, v in g_eff.items()},
        "moves": MOVES, "turn_share": TURN_SHARE, "turn_source": TURN_SOURCE,
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
        stale = [k for k in n.tags if k.startswith(TAG_PREFIX) and k not in {TAG_PREFIX + "arrow", TAG_PREFIX + "tram"}]
        if stale:
            out[n.id] = dict.fromkeys(stale)
    for node_id, cfg in settings.items():
        n = doc.nodes.get(node_id)
        if n is None or not is_signal(n.tags):
            continue
        prog = program(cfg)
        cycle = program_cycle(prog)
        g_main, g_cross = effective_green(prog, "main"), effective_green(prog, "cross")
        share = g_main / ((g_main + g_cross) or 1)
        tags = {
            TAG_PREFIX + "cycle": str(cycle),
            TAG_PREFIX + "main_share": f"{share:.2f}",
            TAG_PREFIX + "green_main": str(round(g_main)),
            TAG_PREFIX + "green_cross": str(round(g_cross)),
            TAG_PREFIX + "yellow": str(YELLOW_S),
            TAG_PREFIX + "all_red": str(ALL_RED_S),
        }
        if cfg.get("program"):
            # green:movements per phase, e.g. 40:main,main_right;8:main_left;30:cross
            tags[TAG_PREFIX + "program"] = ";".join(f"{p['green_s']}:{','.join(p['moves'])}" for p in prog)
        out[node_id] = {**out.get(node_id, {}), **tags}
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
            gm = float(n.tags.get(TAG_PREFIX + 'green_main', 'nan'))
            gc = float(n.tags.get(TAG_PREFIX + 'green_cross', 'nan'))
            if gm > 0 and gc > 0 and abs(gm + gc + LOST_TIME_S - cycle) < .001:
                share = gm / (gm + gc)
        except ValueError:
            continue
        if 12 <= cycle <= 180 and 0 < share < 1:
            out[n.id] = {"cycle": cycle, "main_share": share}
        prog = _parse_program(n.tags.get(TAG_PREFIX + "program", ""))
        if prog:
            out[n.id] = {"program": prog}
    return out


def _parse_program(value: str) -> list[dict[str, Any]] | None:
    phases = []
    for i, part in enumerate(p for p in value.split(";") if p.strip()):
        green, _, moves = part.partition(":")
        try:
            g = int(green)
        except ValueError:
            return None
        ms = [m for m in moves.split(",") if m in MOVES]
        if not ms or not 1 <= g <= 170:
            return None
        phases.append({"name": f"Фаза {i + 1}", "green_s": g, "moves": ms})
    return phases if len(phases) >= 2 else None
