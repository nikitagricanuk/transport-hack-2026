"""Network analytics beyond one road: OD matrix, closures with detours, task checking.

* OD matrix: the area is cut into a grid of zones; residents come from living
  floor area, jobs from the other buildings; trips are distributed with a
  singly-constrained gravity model on the shortest travel time by road.
* Closures (accident, works): a closed road carries nothing, its traffic takes
  the fastest detour between the road's ends, and that extra load shows up in
  the load model.
* Tasks: a list of checkable requirements (Packet Tracer style); "check" tells
  how many are met.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

import networkx as nx

from .analysis import _speed, projection
from .osm_model import OsmDocument
from .roads import direction, is_drivable, parse_int
from .transit import DEFAULT_LEVELS, M2_PER_PERSON, RESIDENTIAL

M2_PER_JOB = 25.0
PEAK_TRIP_RATE = 0.12   # car trips per resident in the peak hour
BETA = 0.12             # gravity deterrence per minute
INTRA_MIN = 3.0         # travel time inside one zone, minutes


# ------------------------------------------------------------ road graph
def road_graph(doc: OsmDocument, closed: set[int] | frozenset[int] = frozenset()) -> nx.DiGraph:
    """Directed road graph weighted by free-flow time in seconds."""
    proj = projection(doc)
    g = nx.DiGraph()
    for w in doc.ways.values():
        if w.id in closed or not is_drivable(w.tags) or len(w.nodes) < 2:
            continue
        speed = _speed(w.tags) / 3.6
        d = direction(w.tags)
        refs = [r for r in w.nodes if r in doc.nodes]
        for a, b in zip(refs, refs[1:]):
            na, nb = doc.nodes[a], doc.nodes[b]
            t = math.dist(proj.to_xy(na.lat, na.lon), proj.to_xy(nb.lat, nb.lon)) / speed
            if d >= 0:
                g.add_edge(a, b, t=t, way=w.id)
            if d <= 0:
                g.add_edge(b, a, t=t, way=w.id)
    return g


# ------------------------------------------------------------- closures
def detours(doc: OsmDocument, rows: list[dict[str, Any]], closed: set[int]) -> dict[int, dict[str, Any]]:
    """For each closed road: the ways of the fastest detour and the volume moved onto it."""
    if not closed:
        return {}
    g = road_graph(doc, closed)
    by_id = {r["id"]: r for r in rows}
    out: dict[int, dict[str, Any]] = {}
    for wid in closed:
        w = doc.ways.get(wid)
        row = by_id.get(wid)
        if w is None or row is None:
            continue
        a, b = w.nodes[0], w.nodes[-1]
        legs = [(a, b), (b, a)] if direction(w.tags) == 0 else [(a, b) if direction(w.tags) > 0 else (b, a)]
        per_leg = row["volume"] / len(legs)
        ways: list[int] = []
        times: list[float] = []
        for s, t in legs:
            try:
                path = nx.shortest_path(g, s, t, weight="t")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue  # this direction has no way round: its traffic is lost to the network
            ways.extend(g[u][v]["way"] for u, v in zip(path, path[1:]))
            times.append(sum(g[u][v]["t"] for u, v in zip(path, path[1:])))
        out[wid] = {"ways": sorted(set(ways)), "volume": per_leg * len(times) / max(len(times), 1),
                    "moved": round(per_leg * len(times)), "found": bool(times), "partial": 0 < len(times) < len(legs),
                    "detour_s": round(sum(times) / len(times) - row["t0_s"], 1) if times else 0.0}
    return out


def apply_closures(doc: OsmDocument, rows: list[dict[str, Any]], closed: set[int]) -> dict[int, dict[str, Any]]:
    """Mutate the load rows: closed roads carry nothing, detours take their traffic."""
    from .analysis import los

    info = detours(doc, rows, closed)
    by_id = {r["id"]: r for r in rows}
    for wid, d in info.items():
        for detour_way in d["ways"]:
            r = by_id.get(detour_way)
            if r is not None:
                r["volume"] = round(r["volume"] + d["volume"])
                r["detour"] = True
    for r in rows:
        if r["id"] in closed:
            r.update(volume=0, vc=0.0, los="A", closed=True, t_s=r["t0_s"])
        elif r.get("detour"):
            vc = r["volume"] / r["capacity"] if r["capacity"] else 0.0
            r["vc"] = round(vc, 3)
            r["los"] = los(vc)
            r["t_s"] = round(r["t0_s"] * (1 + 0.15 * vc ** 4), 1)
    return info


# ------------------------------------------------------------ OD matrix
def _zone_name(ix: int, iy: int, n: int) -> str:
    ns = ["Юг", "Центр", "Север"] if n == 3 else [str(i + 1) for i in range(n)]
    ew = ["запад", "центр", "восток"] if n == 3 else [chr(ord("А") + i) for i in range(n)]
    return f"{ns[iy]}-{ew[ix]}" if n == 3 else f"{ew[ix]}{ns[iy]}"


def od_matrix(doc: OsmDocument, grid: int = 3, trip_rate: float = PEAK_TRIP_RATE, beta: float = BETA) -> dict[str, Any]:
    proj = projection(doc)
    b = doc.bounds() or (0.0, 0.0, 0.0, 0.0)
    (x0, y0), (x1, y1) = proj.to_xy(b[0], b[1]), proj.to_xy(b[2], b[3])
    cw, chh = (x1 - x0) / grid or 1.0, (y1 - y0) / grid or 1.0
    cells: dict[tuple[int, int], dict[str, float]] = {}
    for w in doc.ways.values():
        btype = w.tags.get("building")
        if not btype or not w.is_closed:
            continue
        pts = [proj.to_xy(doc.nodes[r].lat, doc.nodes[r].lon) for r in w.nodes if r in doc.nodes]
        if len(pts) < 4:
            continue
        cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        area = abs(sum(a[0] * c[1] - c[0] * a[1] for a, c in zip(pts, pts[1:]))) / 2
        levels = parse_int(w.tags.get("building:levels")) or DEFAULT_LEVELS.get(btype, 3)
        floor = area * max(levels, 1)
        key = (min(grid - 1, max(0, int((cx - x0) / cw))), min(grid - 1, max(0, int((cy - y0) / chh))))
        c = cells.setdefault(key, {"res": 0.0, "jobs": 0.0, "sx": 0.0, "sy": 0.0, "n": 0})
        flats = parse_int(w.tags.get("building:flats"))
        if btype in RESIDENTIAL:
            c["res"] += flats * 2.5 if flats else floor / M2_PER_PERSON
        else:
            c["jobs"] += floor / M2_PER_JOB
        c["sx"] += cx
        c["sy"] += cy
        c["n"] += 1
    keys = sorted((k for k, c in cells.items() if c["res"] + c["jobs"] >= 1), key=lambda k: (-k[1], k[0]))
    zones = []
    for k in keys:
        c = cells[k]
        lat, lon = proj.to_latlon(c["sx"] / c["n"], c["sy"] / c["n"])
        zones.append({"id": f"{k[0]}-{k[1]}", "name": _zone_name(k[0], k[1], grid),
                      "residents": round(c["res"]), "jobs": round(c["jobs"]), "center": [lon, lat]})
    if not zones:
        return {"zones": [], "trips": [], "shares": [], "minutes": [], "total": 0}

    g = road_graph(doc)
    nodes = [(nid, proj.to_xy(doc.nodes[nid].lat, doc.nodes[nid].lon)) for nid in g.nodes]
    anchors = []
    for z in zones:
        zx, zy = proj.to_xy(z["center"][1], z["center"][0])
        anchors.append(min(nodes, key=lambda n: (n[1][0] - zx) ** 2 + (n[1][1] - zy) ** 2)[0] if nodes else None)
    minutes: list[list[float]] = []
    for a in anchors:
        dist = nx.single_source_dijkstra_path_length(g, a, weight="t") if a is not None else {}
        minutes.append([INTRA_MIN if a == t else round(dist[t] / 60, 1) if t in dist else math.inf for t in anchors])
    attraction = [z["jobs"] + 0.15 * z["residents"] for z in zones]
    trips, shares = [], []
    for i, z in enumerate(zones):
        weights = [attraction[j] * math.exp(-beta * minutes[i][j]) if math.isfinite(minutes[i][j]) else 0.0
                   for j in range(len(zones))]
        total_w = sum(weights) or 1.0
        produced = z["residents"] * trip_rate
        trips.append([round(produced * w / total_w) for w in weights])
        shares.append([round(100 * w / total_w, 1) for w in weights])
    return {
        "zones": zones, "trips": trips, "shares": shares,
        "minutes": [[m if math.isfinite(m) else None for m in row] for row in minutes],
        "total": sum(map(sum, trips)), "trip_rate": trip_rate, "beta": beta, "grid": grid,
    }


# ----------------------------------------------------------------- tasks
CRITERIA_RU = {
    "no_issues": "Нет проблем графа",
    "components_max": "Число компонент связности не больше",
    "max_vc": "Максимальная загрузка v/c не больше",
    "avg_vc": "Средняя загрузка v/c не больше",
    "tag": "Тег объекта",
    "signal_at": "Светофор на узле",
    "lanes_min": "Полос на ребре не меньше",
}


def builtin_tasks(doc: OsmDocument) -> list[dict[str, Any]]:
    return [
        {"id": "fix-graph", "builtin": True, "title": "Почините граф",
         "description": "Уберите разорванные и склеенные перекрёстки и изолированные фрагменты, чтобы MatSim построил все маршруты.",
         "criteria": [
             {"type": "no_issues", "category": "junction", "label": "Нет проблем перекрёстков"},
             {"type": "no_issues", "category": "connectivity", "severity": "critical", "label": "Нет изолированных фрагментов"},
             {"type": "components_max", "value": 1, "label": "Сеть связна (1 компонента)"},
         ]},
        {"id": "no-jams", "builtin": True, "title": "Разгрузите сеть",
         "description": "Измените полосы, светофоры или маршруты так, чтобы ни одна дорога не была перегружена.",
         "criteria": [
             {"type": "max_vc", "value": 1.0, "label": "Нет рёбер с v/c > 1"},
             {"type": "avg_vc", "value": 0.5, "label": "Средняя загрузка ≤ 0,5"},
         ]},
        {"id": "sumo-ready", "builtin": True, "title": "Готовность к SUMO",
         "description": "Исправьте направления, полосы и светофоры, которые ломают построение сети в SUMO.",
         "criteria": [
             {"type": "no_issues", "category": "direction", "severity": "critical", "label": "Нет тупиков на односторонних"},
             {"type": "no_issues", "category": "lanes", "label": "Полосы согласованы"},
             {"type": "no_issues", "category": "junction", "label": "Светофоры и переходы на своих местах"},
         ]},
    ]


def new_task(title: str, description: str, criteria: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": uuid.uuid4().hex[:8], "builtin": False, "title": title.strip() or "Задание",
            "description": description.strip(), "criteria": criteria}


def check_task(doc: OsmDocument, task: dict[str, Any], validation: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    issues = validation["issues"]
    results = []
    for c in task["criteria"]:
        t = c.get("type")
        ok, detail = False, ""
        if t == "no_issues":
            found = [i for i in issues if (not c.get("category") or i["category"] == c["category"])
                     and (not c.get("severity") or i["severity"] == c["severity"])]
            ok, detail = not found, f"найдено: {len(found)}"
        elif t == "components_max":
            n = validation["summary"]["components"]
            ok, detail = n <= int(c["value"]), f"компонент: {n}"
        elif t in ("max_vc", "avg_vc"):
            vcs = [r["vc"] for r in rows if not r.get("closed")]
            if t == "max_vc":
                v = max(vcs, default=0.0)
            else:
                total = sum(r["length_m"] for r in rows) or 1.0
                v = sum(r["vc"] * r["length_m"] for r in rows) / total
            ok, detail = v <= float(c["value"]), f"сейчас {v:.2f}"
        elif t == "tag":
            el = doc.get(c["kind"], int(c["id"]))
            cur = el.tags.get(c["key"]) if el is not None else None
            ok, detail = cur == c.get("value"), f"сейчас: {cur or '—'}"
        elif t == "signal_at":
            n = doc.nodes.get(int(c["node_id"]))
            ok = n is not None and (n.tags.get("highway") == "traffic_signals" or n.tags.get("crossing") == "traffic_signals")
            detail = "есть" if ok else "нет"
        elif t == "lanes_min":
            w = doc.ways.get(int(c["way_id"]))
            lanes = parse_int(w.tags.get("lanes")) if w is not None else None
            ok, detail = (lanes or 0) >= int(c["value"]), f"сейчас: {lanes or '—'}"
        else:
            detail = "неизвестный тип требования"
        results.append({"label": c.get("label") or CRITERIA_RU.get(t, t), "ok": ok, "detail": detail})
    passed = sum(r["ok"] for r in results)
    return {"task_id": task["id"], "passed": passed, "total": len(results), "results": results,
            "done": passed == len(results) and bool(results)}
