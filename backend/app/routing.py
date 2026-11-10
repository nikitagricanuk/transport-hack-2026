"""Read-only network routing, virtual edge snaps and partial-edge accessibility.

Graphs retain parallel OSM ways. Dijkstra labels include the incoming way so
node-via turn restrictions apply equally to routes and accessibility bands.
"""

from __future__ import annotations

import heapq
import itertools
import math
import time
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
from shapely.geometry import LineString, MultiPoint, Point, Polygon, box, mapping
from shapely.ops import transform, unary_union
from shapely import concave_hull

from .analysis import _speed, original_document, projection
from .geo import GridIndex, project_on_segment
from .roads import direction, is_drivable

WALK = {
    "footway",
    "path",
    "steps",
    "pedestrian",
    "corridor",
    "track",
    "living_street",
    "residential",
    "service",
    "unclassified",
    "tertiary",
    "secondary",
    "primary",
    "tertiary_link",
    "secondary_link",
    "primary_link",
}
ALLOW = {"yes", "designated", "permissive"}


def walkable(tags):
    foot, hw = tags.get("foot"), tags.get("highway")
    if tags.get("area") == "yes" or foot in {"no", "use_sidepath"}:
        return False
    if tags.get("access") in {"no", "private"} and foot not in ALLOW:
        return False
    if tags.get("sidewalk") == "separate":
        return False
    if (tags.get("sidewalk") == "no" or tags.get("sidewalk:both") == "no") and foot not in {"yes", "designated"}:
        return False
    return hw in WALK or (
        hw in {"cycleway", "motorway", "trunk", "motorway_link", "trunk_link"} and foot in {"yes", "designated"}
    )


@dataclass
class RoutingGraph:
    g: nx.MultiDiGraph
    mode: str
    proj: Any
    seg_index: GridIndex
    restrictions: dict = field(default_factory=dict)
    skipped_restrictions: list = field(default_factory=list)
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Snap:
    u: Any
    v: Any
    key: Any
    fraction: float
    xy: tuple
    distance: float


def build_graph(doc, mode, *, closed=frozenset(), walk_kmh=4.5, use_restrictions=True):
    proj, g, index = projection(doc), nx.MultiDiGraph(), GridIndex(80)
    for w in doc.ways.values():
        if w.id in closed or not (is_drivable(w.tags) if mode == "car" else walkable(w.tags)):
            continue
        speed = (_speed(w.tags) if mode == "car" else walk_kmh) / 3.6
        d = direction(w.tags) if mode == "car" else (1 if w.tags.get("oneway:foot") == "yes" else 0)
        # Do not bridge a missing node by filtering it out first.
        for i, (u, v) in enumerate(zip(w.nodes, w.nodes[1:])):
            if u not in doc.nodes or v not in doc.nodes or u == v:
                continue
            a, b = doc.nodes[u], doc.nodes[v]
            pa, pb = proj.to_xy(a.lat, a.lon), proj.to_xy(b.lat, b.lon)
            g.add_node(u, xy=pa)
            g.add_node(v, xy=pb)
            length = math.dist(pa, pb)
            attrs = dict(l=length, t=length / speed, way=w.id, segment=(w.id, i), kind=mode)
            if d >= 0:
                g.add_edge(u, v, key=(w.id, i), **attrs)
                index.insert_box(*pa, *pb, (u, v, (w.id, i)))
            if d <= 0:
                g.add_edge(v, u, key=(w.id, i), **attrs)
                if d < 0:
                    index.insert_box(*pa, *pb, (v, u, (w.id, i)))
    rg = RoutingGraph(g, mode, proj, index, params={"walk_kmh": walk_kmh})
    if mode == "car" and use_restrictions:
        for r in doc.relations.values():
            if r.tags.get("type") != "restriction":
                continue
            fr = [m.ref for m in r.members if m.role == "from" and m.type == "way"]
            to = [m.ref for m in r.members if m.role == "to" and m.type == "way"]
            via = [m.ref for m in r.members if m.role == "via" and m.type == "node"]
            tag = r.tags.get("restriction", "")
            if (
                len(fr) != 1
                or len(to) != 1
                or len(via) != 1
                or fr[0] not in doc.ways
                or to[0] not in doc.ways
                or via[0] not in g
                or not tag.startswith(("no_", "only_"))
                or via[0] not in (doc.ways[fr[0]].nodes[0], doc.ways[fr[0]].nodes[-1])
                or via[0] not in (doc.ways[to[0]].nodes[0], doc.ways[to[0]].nodes[-1])
            ):
                rg.skipped_restrictions.append(r.id)
                continue
            rg.restrictions.setdefault((fr[0], via[0]), []).append((tag.startswith("only_"), to[0], r.id))
    return rg


def snap(rg, lat, lon, max_m):
    p = rg.proj.to_xy(lat, lon)
    best = None
    for u, v, key in rg.seg_index.query(*p, max_m):
        q, t, d = project_on_segment(p, rg.g.nodes[u]["xy"], rg.g.nodes[v]["xy"])
        if d <= max_m and (best is None or d < best.distance):
            best = Snap(u, v, key, t, q, d)
    return best


def point_xy(doc, obj):
    if "node_id" in obj:
        n = doc.nodes.get(obj["node_id"])
        if n is None:
            raise ValueError("Узел не найден")
        return n.lat, n.lon
    if "way_id" in obj:
        w = doc.ways.get(obj["way_id"])
        nodes = [doc.nodes[n] for n in w.nodes if n in doc.nodes] if w else []
        if not nodes:
            raise ValueError("Объект не найден")
        # Do not count the repeated closing vertex twice.
        if w.is_closed:
            nodes = nodes[:-1]
        return sum(n.lat for n in nodes) / len(nodes), sum(n.lon for n in nodes) / len(nodes)
    return obj["lat"], obj["lon"]


def virtual_graph(rg, snaps):
    """Copy and split each affected physical segment once, in fractional order."""
    g = rg.g.copy()
    groups, ids = {}, []
    for i, s in enumerate(snaps):
        if s.fraction < 1e-9:
            ids.append(s.u)
            continue
        if s.fraction > 1 - 1e-9:
            ids.append(s.v)
            continue
        nid = ("snap", i)
        g.add_node(nid, xy=s.xy)
        ids.append(nid)
        groups.setdefault(s.key, []).append((s, nid))
    for key, points in groups.items():
        first = points[0][0]
        u, v = first.u, first.v
        ordered = [(0, u)] + sorted((s.fraction if s.u == u else 1 - s.fraction, nid) for s, nid in points) + [(1, v)]
        for a, b, reverse in [(u, v, False), (v, u, True)]:
            if not g.has_edge(a, b, key):
                continue
            attrs = dict(g[a][b][key])
            g.remove_edge(a, b, key)
            seq = list(reversed(ordered)) if reverse else ordered
            for (t0, n0), (t1, n1) in zip(seq, seq[1:]):
                g.add_edge(n0, n1, key=key, **{**attrs, "l": attrs["l"] * abs(t1 - t0), "t": attrs["t"] * abs(t1 - t0)})
    return g, ids


def dijkstra(rg, g, sources, metric="t", budget=math.inf, target=None):
    costs, prev, counter, queue = {}, {}, itertools.count(), []
    for n, cost in sources.items():
        state = (n, None)
        costs[state] = cost
        heapq.heappush(queue, (cost, next(counter), state))
    end = None
    while queue:
        cost, _, state = heapq.heappop(queue)
        if cost != costs.get(state) or cost > budget:
            continue
        u, incoming = state
        if u == target:
            end = state
            break
        for _, v, key, e in g.out_edges(u, keys=True, data=True):
            if any(
                (e["way"] != to if only else e["way"] == to) for only, to, _ in rg.restrictions.get((incoming, u), [])
            ):
                continue
            nxt, nc = (v, e["way"]), cost + e[metric]
            if nc <= budget and nc < costs.get(nxt, math.inf):
                costs[nxt], prev[nxt] = nc, (state, key)
                heapq.heappush(queue, (nc, next(counter), nxt))
    return costs, prev, end


def shortest_path(rg, a, b, metric="time"):
    g, (start, end) = virtual_graph(rg, [a, b])
    _, prev, state = dijkstra(rg, g, {start: 0}, "t" if metric == "time" else "l", target=end)
    if state is None:
        return None
    edges, nodes = [], [state[0]]
    while state in prev:
        old, key = prev[state]
        edges.append(g[old[0]][state[0]][key])
        state = old
        nodes.append(state[0])
    edges.reverse()
    nodes.reverse()
    coords = [list(reversed(rg.proj.to_latlon(*g.nodes[n]["xy"]))) for n in nodes]
    if len(coords) == 1:
        coords *= 2
    return {
        "edges": edges,
        "coordinates": coords,
        "length_m": sum(e["l"] for e in edges),
        "time_s": sum(e["t"] for e in edges),
    }


def issue(title, description, refs=None, fixes=None, severity="warning", mode="car"):
    fixes = fixes or []
    return dict(
        id="route-" + title,
        severity=severity,
        category="connectivity",
        title=title,
        description=description,
        refs=refs or [],
        location=None,
        fixes=fixes,
        fix=fixes[0] if fixes else None,
        levels=["matsim", "sumo"] if mode == "car" else [],
    )


def diagnose(doc, rg, a, b):
    g, (start, end) = virtual_graph(rg, [a, b])
    und = g.to_undirected()
    ca, cb = nx.node_connected_component(und, start), nx.node_connected_component(und, end)
    if end not in ca:
        # Spatial index limits work for whole-city extracts.
        index = GridIndex(30)
        for n in cb:
            if isinstance(n, int):
                index.insert_point(*g.nodes[n]["xy"], n)
        best = None
        for n in ca:
            if not isinstance(n, int):
                continue
            xy = g.nodes[n]["xy"]
            for m in index.query(*xy, 30):
                d = math.dist(xy, g.nodes[m]["xy"])
                if d <= 30 and (best is None or d < best[0]):
                    best = (d, n, m)
        fixes, refs = [], []
        if best:
            d, n, m = best
            refs = [{"kind": "node", "id": n}, {"kind": "node", "id": m}]
            fixes.append(
                dict(
                    label=f"Присоединить ({d:.0f} м) — проверьте по снимку",
                    op="add_way",
                    args={
                        "points": [{"id": n}, {"id": m}],
                        "tags": {"highway": "footway" if rg.mode == "walk" else "service"},
                    },
                )
            )
        card = issue(
            "Нет пути: граф разорван",
            f"Фрагменты: {len(ca)} и {len(cb)} узлов. Проверьте связь по снимку.",
            refs,
            fixes,
            mode=rg.mode,
        )
        if best:
            card["location"] = [doc.nodes[best[1]].lon, doc.nodes[best[1]].lat]
        return [card]

    if nx.has_path(g, start, end) and rg.restrictions:
        fixes, refs = [], []
        ids = list(dict.fromkeys(rid for rules in rg.restrictions.values() for _, _, rid in rules))
        for rid in ids[:20]:
            trial = RoutingGraph(
                g,
                rg.mode,
                rg.proj,
                rg.seg_index,
                restrictions={k: [r for r in rules if r[2] != rid] for k, rules in rg.restrictions.items()},
            )
            _, _, found = dijkstra(trial, g, {start: 0}, target=end)
            if found is not None:
                refs.append({"kind": "relation", "id": rid})
                fixes.append(
                    dict(
                        label=f"Удалить запрет {rid} — проверьте знак на месте",
                        op="delete",
                        args={"kind": "relation", "id": rid},
                    )
                )
        return [
            issue(
                "Нет пути: запрет поворота",
                "Без запретов путь есть. Если знак реален, путь действительно невозможен.",
                refs,
                fixes,
            )
        ]
    reachable = nx.descendants(g, start) | {start}
    reverse = nx.ancestors(g, end) | {end}
    candidates = [
        w.id for w in doc.ways.values() if any(n in reachable for n in w.nodes) and any(n in reverse for n in w.nodes)
    ][:20]
    fixes, refs = [], []
    deadline = time.monotonic() + 2
    for wid in candidates:
        if time.monotonic() > deadline:
            break
        w = doc.ways[wid]
        if direction(w.tags) == 0:
            continue
        trial = g.copy()
        for u, v, key, e in list(g.edges(keys=True, data=True)):
            if e["way"] == wid:
                trial.add_edge(v, u, key=key, **e)
        if nx.has_path(trial, start, end):
            refs.append({"kind": "way", "id": wid})
            fixes.extend(
                [
                    dict(
                        label=f"Сделать двусторонним {wid}",
                        op="set_tags",
                        args={"kind": "way", "id": wid, "tags": {"oneway": None}},
                    ),
                    dict(label=f"Развернуть направление {wid}", op="reverse_way", args={"id": wid}),
                ]
            )
    return [issue("Нет пути: односторонние рёбра", "Проверьте направления на месте.", refs, fixes, mode=rg.mode)]


def route(doc, req, rg=None):
    rg = rg or build_graph(
        doc,
        req.get("mode", "car"),
        walk_kmh=req.get("walk_kmh", 4.5),
        use_restrictions=req.get("use_restrictions", True),
        closed=req.get("_closed", frozenset()),
    )
    snaps = [snap(rg, *point_xy(doc, req[k]), 60 if rg.mode == "car" else 80) for k in ("from", "to")]
    out = dict(
        found=False,
        mode=rg.mode,
        metric=req.get("metric", "time"),
        path=None,
        stats=None,
        assumptions=[
            {"key": "speed", "label": "Скорости: maxspeed или классовые допущения; пешком 4,5 км/ч — допущение"}
        ],
        sources=[{"label": "ПДД РФ п.10.2", "url": "https://www.consultant.ru/document/cons_doc_LAW_2709/"}],
        issues=[],
    )
    if any(s is None for s in snaps):
        out["issues"] = [
            issue(
                "Точка далеко от сети",
                "Выберите точку ближе к сети (авто ≤60 м, пешком ≤80 м).",
                severity="info",
                mode=rg.mode,
            )
        ]
    else:
        a, b = snaps
        path = shortest_path(rg, a, b, req.get("metric", "time"))
        if path:
            streets = {}
            for e in path["edges"]:
                w = doc.ways[e["way"]]
                item = streets.setdefault(
                    w.id,
                    dict(
                        id=w.id,
                        name=w.tags.get("name", ""),
                        highway=w.tags.get("highway"),
                        length_m=0,
                        time_s=0,
                        maxspeed_kmh=_speed(w.tags),
                    ),
                )
                item["length_m"] += e["l"]
                item["time_s"] += e["t"]
            out.update(
                found=True,
                path={
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": path["coordinates"]},
                    "properties": {"kind": "route"},
                },
                stats=dict(
                    length_m=path["length_m"],
                    time_s=path["time_s"],
                    avg_kmh=path["length_m"] / path["time_s"] * 3.6 if path["time_s"] else 0,
                    edges=len(path["edges"]),
                    ways=list(streets.values()),
                    snap={"from_m": a.distance, "to_m": b.distance},
                    restrictions_applied=sum(map(len, rg.restrictions.values())),
                    restrictions_skipped=rg.skipped_restrictions,
                ),
            )
        else:
            out["issues"] = diagnose(doc, rg, a, b)
    if req.get("compare") == "original":
        before = route(original_document(doc), {k: v for k, v in req.items() if k not in {"compare", "_closed"}})
        out["compare"] = dict(
            before=before,
            delta_s=(out["stats"]["time_s"] - before["stats"]["time_s"]) if out["found"] and before["found"] else None,
            delta_m=(out["stats"]["length_m"] - before["stats"]["length_m"])
            if out["found"] and before["found"]
            else None,
        )
    return out


def reach(rg, sources, budget, metric="t", g=None):
    g = g if g is not None else rg.g
    costs, _, _ = dijkstra(rg, g, sources, metric, budget)
    nodecost = {}
    pieces = {}
    for (u, incoming), cost in costs.items():
        nodecost[u] = min(cost, nodecost.get(u, math.inf))
        for _, v, key, e in g.out_edges(u, keys=True, data=True):
            if any(
                (e["way"] != to if only else e["way"] == to) for only, to, _ in rg.restrictions.get((incoming, u), [])
            ):
                continue
            f = min(1, (budget - cost) / e[metric]) if e[metric] else 1
            if f <= 0:
                continue
            a, b = g.nodes[u]["xy"], g.nodes[v]["xy"]
            q = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            pieces.setdefault(e["segment"], []).append(LineString([a, q]))
    # Union removes overlapping opposite directions and virtual snap fragments.
    lines = [unary_union(p) for p in pieces.values()]
    return dict(cost=nodecost, lines=lines, length=sum(p.length for p in lines), ways={k[0] for k in pieces})


def polygon(reached, how="buffer", buffer_m=30):
    if not reached["lines"]:
        return Polygon()
    lines = unary_union(reached["lines"])
    if how == "hull":
        points = [
            p
            for line in reached["lines"]
            for part in (list(line.geoms) if hasattr(line, "geoms") else [line])
            for p in part.coords
        ]
        hull = concave_hull(MultiPoint(points), ratio=0.3)
        return hull if hull.geom_type in {"Polygon", "MultiPolygon"} else hull.buffer(buffer_m)
    return lines.buffer(buffer_m)


def geometry(rg, poly):
    return mapping(transform(lambda x, y, z=None: tuple(reversed(rg.proj.to_latlon(x, y))), poly))


def boundary_limited(doc, rg, reached, g):
    bounds = doc.bounds()
    if not bounds:
        return False
    x0, y0 = rg.proj.to_xy(bounds[0], bounds[1])
    x1, y1 = rg.proj.to_xy(bounds[2], bounds[3])
    return any(
        min(abs(x - x0), abs(x - x1), abs(y - y0), abs(y - y1)) <= 40
        for n in reached["cost"]
        for x, y in [g.nodes[n]["xy"]]
    )


def isochrone(doc, req, rg=None):
    rg = rg or build_graph(
        doc,
        req.get("mode", "car"),
        walk_kmh=req.get("walk_kmh", 4.5),
        use_restrictions=req.get("use_restrictions", True),
        closed=req.get("_closed", frozenset()),
    )
    s = snap(rg, *point_xy(doc, req["from"]), 60 if rg.mode == "car" else 80)
    out = dict(
        mode=rg.mode,
        bands=[],
        boundary_limited=False,
        issues=[],
        assumptions=[
            {"key": "buffer", "label": "Коридор 30 м; скорости по классу и BPR — допущения. Население: нет данных."}
        ],
        sources=[],
    )
    if s is None:
        out["issues"] = [issue("Точка далеко от сети", "Выберите начало ближе к сети.", severity="info")]
        return out
    g, (start,) = virtual_graph(rg, [s])
    previous = 0
    for i in range(1, req.get("count", 5) + 1):
        minutes = i * req.get("step_min", 1)
        r = reach(rg, {start: 0}, minutes * 60, g=g)
        p = polygon(r, req.get("how", "buffer"), req.get("buffer_m", 30))
        out["bands"].append(
            dict(
                minutes=minutes,
                area_km2=p.area / 1e6,
                road_km=r["length"] / 1000,
                ring_road_km=(r["length"] - previous) / 1000,
                nodes=len(r["cost"]),
                ways=len(r["ways"]),
                geometry=geometry(rg, p),
            )
        )
        previous = r["length"]
        out["boundary_limited"] |= boundary_limited(doc, rg, r, g)
    out["network_road_km"] = sum(e["l"] for u, v, e in rg.g.edges(data=True) if u < v or not rg.g.has_edge(v, u)) / 1000
    if req.get("compare") == "original":
        out["compare"] = {
            "before": isochrone(
                original_document(doc), {k: v for k, v in req.items() if k not in {"compare", "_closed"}}
            )
        }
    return out


def catchment(doc, req, rg=None):
    rg = rg or build_graph(doc, "walk", walk_kmh=req.get("walk_kmh", 4.5))
    limit = (
        req.get("limit_m", 500)
        if req.get("limit_min") is None
        else req["limit_min"] * req.get("walk_kmh", 4.5) * 1000 / 60
    )
    polys, reaches, stops, issues = [], [], [], []
    for label in ("a", "b"):
        snaps = []
        for nid in req.get("set_" + label, []):
            n = doc.nodes.get(nid)
            s = snap(rg, n.lat, n.lon, 60) if n else None
            stops.append(
                dict(
                    id=nid,
                    name=n.tags.get("name", "") if n else "",
                    set=label.upper(),
                    snap_m=s.distance if s else None,
                    used=s is not None,
                )
            )
            if s:
                snaps.append(s)
            else:
                issues.append(
                    issue(
                        "Остановка не связана с пешеходной сетью",
                        f"Остановка {nid}: нет пешеходного ребра в 60 м.",
                        [{"kind": "node", "id": nid}],
                        severity="info",
                        mode="walk",
                    )
                )
        g, ids = virtual_graph(rg, snaps)
        r = reach(rg, {nid: s.distance for nid, s in zip(ids, snaps)}, limit, "l", g=g)
        reaches.append(r)
        polys.append(polygon(r, buffer_m=req.get("buffer_m", 40)))
    a, b = polys
    buildings, footprints = [], []
    combined = a.union(b)
    for w in doc.ways.values():
        if w.tags.get("building") and w.is_closed:
            coords = [rg.proj.to_xy(doc.nodes[n].lat, doc.nodes[n].lon) for n in w.nodes if n in doc.nodes]
            if len(coords) >= 4:
                p = Polygon(coords)
                if p.is_valid:
                    entrances = [Point(rg.proj.to_xy(doc.nodes[n].lat,doc.nodes[n].lon)) for n in w.nodes if n in doc.nodes and doc.nodes[n].tags.get('entrance') not in {None,'no'}]
                    origin = min(entrances,key=lambda point:combined.distance(point)) if entrances else p.centroid
                    buildings.append((w.id,origin))
                    footprints.append(p)
    study = unary_union(footprints).convex_hull if footprints else Polygon()
    bounds=doc.bounds()
    if bounds:
        x0,y0=rg.proj.to_xy(bounds[0],bounds[1])
        x1,y1=rg.proj.to_xy(bounds[2],bounds[3])
        study=study.intersection(box(x0,y0,x1,y1))
    counts = dict(total=len(buildings), both=0, one_side=0, none=0, none_ids=[])
    for wid, p in buildings:
        hits = int(a.covers(p)) + int(b.covers(p))
        counts[["none", "one_side", "both"][hits]] += 1
        if not hits:
            counts["none_ids"].append(wid)
    features = [
        dict(type="Feature", properties={"set": label, "area_km2": p.area / 1e6}, geometry=geometry(rg, p))
        for label, p in [("A", a), ("B", b), ("AB", a.intersection(b)), ("uncovered", study.difference(a.union(b)))]
    ]
    out = dict(
        limit_m=limit,
        relief_correction=False,
        polygons={"type": "FeatureCollection", "features": features},
        buildings=counts,
        network=dict(walk_km_reached_a=reaches[0]["length"] / 1000, walk_km_reached_b=reaches[1]["length"] / 1000),
        stops=stops,
        issues=issues,
        assumptions=[
            {
                "key": "walk",
                "label": f"Коридор {req.get('buffer_m', 40)} м и скорость {req.get('walk_kmh', 4.5)} км/ч — допущения. Высоты и население отсутствуют.",
            }
        ],
        sources=[{"label": "СП 42.13330.2016 п.11.24", "url": "https://docs.cntd.ru/document/456054209"}],
    )
    if req.get("compare") == "original":
        out["compare"] = {"before": catchment(original_document(doc), {k: v for k, v in req.items() if k != "compare"})}
    return out
