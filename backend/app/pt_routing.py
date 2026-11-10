"""Frequency-based PT reach: walking, waiting, riding and bounded transfers.

Headways without an OSM interval are explicitly mocked. Clipped route pieces
are never joined across a missing segment or by a straight-line shortcut.
"""

from __future__ import annotations
import heapq
import itertools
import json
import math
from pathlib import Path

import networkx as nx
from shapely.geometry import LineString

from . import routing
from .analysis import original_document

MODES = {"bus", "trolleybus", "tram", "minibus", "share_taxi"}
HEADWAYS = json.loads((Path(__file__).parent / "data" / "pt_headways.json").read_text())


def interval_minutes(value):
    try:
        if ":" in value:
            h, m, *s = map(float, value.split(":"))
            return h * 60 + m + (s[0] / 60 if s else 0)
        return float(value)
    except (ValueError, TypeError):
        return None


def routes(doc, overrides=None):
    proj = routing.projection(doc)
    result = []
    for r in doc.relations.values():
        mode = r.tags.get("route")
        if r.tags.get("type") != "route" or mode not in MODES:
            continue
        pieces, current = [], []
        for m in r.members:
            w = doc.ways.get(m.ref) if m.type == "way" and m.role in {"", "forward", "backward"} else None
            if w is None:
                continue
            refs = list(reversed(w.nodes)) if m.role == "backward" else list(w.nodes)
            if current and refs and current[-1] == refs[-1] and m.role != "forward":
                refs.reverse()
            if current and refs and current[-1] != refs[0]:
                pieces.append(current)
                current = []
            # A missing OSM vertex breaks the piece rather than bridging it.
            for nid in refs:
                if nid not in doc.nodes:
                    if current:
                        pieces.append(current)
                        current = []
                elif not current or current[-1] != nid:
                    current.append(nid)
        if current:
            pieces.append(current)
        lines = [LineString([proj.to_xy(doc.nodes[n].lat, doc.nodes[n].lon) for n in p]) for p in pieces if len(p) > 1]
        stops, seen = [], set()
        for m in r.members:
            if (
                m.type != "node"
                or not m.role.startswith(("stop", "platform"))
                or m.ref in seen
                or m.ref not in doc.nodes
            ):
                continue
            seen.add(m.ref)
            n = doc.nodes[m.ref]
            from shapely.geometry import Point

            pt = Point(proj.to_xy(n.lat, n.lon))
            if not lines:
                continue
            pi = min(range(len(lines)), key=lambda i: lines[i].distance(pt))
            if lines[pi].distance(pt) > 100:
                continue
            stops.append({"id": n.id, "piece": pi, "chainage": float(lines[pi].project(pt))})
        interval = interval_minutes(r.tags.get("interval", ""))
        source = "OSM interval" if interval and interval > 0 else "mock"
        match = next((h for h in HEADWAYS if h["mode"] == mode and h["ref"] == r.tags.get("ref")), None)
        if source == "mock" and match:
            interval, source = match["headway_min"], match["source"]
        interval = interval if interval and interval > 0 else 10
        if str(r.id) in (overrides or {}):
            interval, source = overrides[str(r.id)], "изменено"
        result.append(
            dict(
                relation_id=r.id,
                ref=r.tags.get("ref", ""),
                name=r.tags.get("name", ""),
                mode=mode,
                headway_min=interval,
                headway_source=source,
                stops_in_extract=len(stops),
                pieces=len(lines),
                stops=stops,
            )
        )
    return result


def params(doc):
    rs = routes(doc)
    return dict(
        routes=[{k: v for k, v in r.items() if k != "stops"} for r in rs],
        defaults={"pt_kmh": 20, "walk_kmh": 4.5, "wait_factor": 0.5, "max_transfers": 2},
        coverage={
            "pairs_total": len(rs),
            "real_headway": sum(r["headway_source"] != "mock" for r in rs),
            "default": sum(r["headway_source"] == "mock" for r in rs),
        },
    )


def transit_graph(doc, req):
    rg = routing.build_graph(doc, "walk", walk_kmh=req.get("walk_kmh", 4.5))
    start = routing.snap(rg, *routing.point_xy(doc, req["from"]), 80)
    if start is None:
        return rg, None, None, [], []
    rs = routes(doc, req.get("headway_overrides"))
    stop_ids = list(dict.fromkeys(s["id"] for r in rs for s in r["stops"]))
    snaps, used, issues = [start], [], []
    for nid in stop_ids:
        n = doc.nodes[nid]
        s = routing.snap(rg, n.lat, n.lon, 60)
        if s:
            used.append(nid)
            snaps.append(s)
        else:
            issues.append(
                routing.issue(
                    "Остановка не связана с пешеходной сетью",
                    f"Остановка {nid}: нет подхода в 60 м.",
                    [{"kind": "node", "id": nid}],
                    severity="info",
                    mode="walk",
                )
            )
    walk, ids = routing.virtual_graph(rg, snaps)
    g = nx.DiGraph()
    for u, v, e in walk.edges(data=True):
        if not g.has_edge(u, v) or e["t"] < g[u][v]["t"]:
            g.add_edge(u, v, t=e["t"], kind="walk", board=0)
    for nid, vertex, s in zip(used, ids[1:], snaps[1:]):
        stop = ("stop", nid)
        for u, v in [(vertex, stop), (stop, vertex)]:
            g.add_edge(u, v, t=s.distance / (req.get("walk_kmh", 4.5) / 3.6), kind="walk", board=0)
    for r in rs:
        if r["pieces"] > 1:
            issues.append(
                routing.issue(
                    f"Маршрут {r['ref']} разорван в выгрузке",
                    "Разные фрагменты не соединены; поездка вне выгрузки не моделируется.",
                    [{"kind": "relation", "id": r["relation_id"]}],
                    severity="info",
                    mode="walk",
                )
            )
        for i, s in enumerate(r["stops"]):
            if s["id"] not in used:
                continue
            state, stop = ("ride", r["relation_id"], i), ("stop", s["id"])
            g.add_edge(stop, state, t=req.get("wait_factor", 0.5) * r["headway_min"] * 60, kind="wait", board=1)
            g.add_edge(state, stop, t=0, kind="ride", board=0)
            if i + 1 < len(r["stops"]):
                nxt = r["stops"][i + 1]
                if nxt["id"] in used and nxt["piece"] == s["piece"] and nxt["chainage"] >= s["chainage"]:
                    speed = req.get("pt_kmh", {}).get(r["mode"], 20) / 3.6
                    g.add_edge(
                        state,
                        ("ride", r["relation_id"], i + 1),
                        t=(nxt["chainage"] - s["chainage"]) / speed,
                        kind="ride",
                        board=0,
                    )
    return rg, walk, (g, ids[0]), rs, issues


def costs(g, start, budget, max_boardings):
    counter = itertools.count()
    dist = {(start, 0): 0}
    breakdown = {(start, 0): {"walk_s": 0, "wait_s": 0, "ride_s": 0}}
    queue = [(0, next(counter), start, 0)]
    while queue:
        t, _, u, b = heapq.heappop(queue)
        if dist.get((u, b)) != t:
            continue
        for v, e in g[u].items():
            nb = b + e["board"]
            nt = t + e["t"]
            if nb > max_boardings or nt > budget or nt >= dist.get((v, nb), math.inf):
                continue
            dist[v, nb] = nt
            detail = dict(breakdown[u, b])
            detail[e["kind"] + "_s"] += e["t"]
            breakdown[v, nb] = detail
            heapq.heappush(queue, (nt, next(counter), v, nb))
    best = {}
    for (n, b), t in dist.items():
        if n not in best or t < best[n][0]:
            best[n] = (t, breakdown[n, b])
    return best


def isochrone(doc, req):
    rg, walk, pair, rs, issues = transit_graph(doc, req)
    out = dict(
        bands=[],
        issues=issues,
        boundary_limited=False,
        boundary_points={"type": "FeatureCollection", "features": []},
        assumptions=[
            {
                "key": "pt",
                "label": f"Скорость ОТ {req.get('pt_kmh', {})} км/ч, пешком {req.get('walk_kmh', 4.5)} км/ч, ожидание ×{req.get('wait_factor', 0.5)} — допущения. Интервалы без OSM interval — условные (mock). Рельеф и население не учтены.",
            }
        ],
        sources=[],
    )
    if not pair:
        out["issues"] = [
            routing.issue(
                "Точка далеко от сети", "Выберите начало ближе к пешеходной сети.", severity="info", mode="walk"
            )
        ]
        return out
    g, start = pair
    budgets = sorted(set(req.get("bands_min", [5, 10, 15])))
    maximum = max(budgets) * 60
    plain = costs(g, start, maximum, 1)
    transfer = costs(g, start, maximum, 1 + req.get("max_transfers", 2))
    for minutes in budgets:
        band = {"minutes": minutes}
        for name, cost in [("no_transfer", plain), ("with_transfer", transfer)]:
            sources = {n: t for n, (t, _) in cost.items() if n in walk and t <= minutes * 60}
            reached = routing.reach(rg, sources, minutes * 60, g=walk)
            p = routing.polygon(reached, req.get("how", "hull"), 40)
            band[name] = dict(
                area_km2=p.area / 1e6,
                geometry=routing.geometry(rg, p),
                road_km=reached["length"] / 1000,
                stops_reached=sum(
                    t <= minutes * 60 for n, (t, _) in cost.items() if isinstance(n, tuple) and n[0] == "stop"
                ),
                routes_used=sorted(
                    {
                        str(n[1])
                        for n, (t, _) in cost.items()
                        if isinstance(n, tuple) and n[0] == "ride" and t <= minutes * 60
                    }
                ),
            )
            out["boundary_limited"] |= routing.boundary_limited(doc, rg, reached, walk)
        out["bands"].append(band)
    all_stops = list(dict.fromkeys(s['id'] for r in rs for s in r['stops']))
    out['stops'] = [dict(id=nid,coord=[doc.nodes[nid].lon,doc.nodes[nid].lat],reached=('stop',nid) in transfer) for nid in all_stops]
    out["unreachable_stops"] = [s["id"] for r in rs for s in r["stops"] if ("stop", s["id"]) not in transfer]
    out["explain"] = {
        "sample_points": [
            dict(coord=list(reversed(rg.proj.to_latlon(*walk.nodes[n]["xy"]))), **detail)
            for n, (t, detail) in transfer.items()
            if n in walk
        ][:100]
    }
    if req.get("compare") == "original":
        out["compare"] = {"before": isochrone(original_document(doc), {k: v for k, v in req.items() if k != "compare"})}
    return out
