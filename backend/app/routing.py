"""Read-only routing on the current OSM document, in local metres and seconds.

Snaps are virtual: queries never insert OSM nodes or touch edit history.
Parallel directed edges retain their way identity, including at restrictions.
"""
from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass
from typing import Hashable, Literal

import networkx as nx
from shapely import concave_hull
from shapely.geometry import GeometryCollection, LineString, MultiPoint, mapping
from shapely.ops import transform, unary_union

from .analysis import _speed, projection
from .geo import GridIndex, LocalProjection, project_on_segment
from .roads import direction, is_drivable

Mode = Literal["car", "walk"]
WALK = {"footway", "path", "steps", "pedestrian", "corridor", "track", "living_street", "residential",
        "service", "unclassified", "tertiary", "tertiary_link", "secondary", "secondary_link", "primary", "primary_link"}


def walkable(tags) -> bool:
    foot, highway = tags.get("foot"), tags.get("highway")
    explicit = foot in {"yes", "designated"}
    if tags.get("area") == "yes" or foot in {"no", "use_sidepath"}:
        return False
    if tags.get("access") in {"no", "private"} and foot not in {"yes", "designated", "permissive"}:
        return False
    if tags.get("sidewalk") == "separate":
        return False
    if (tags.get("sidewalk") == "no" or tags.get("sidewalk:both") == "no") and not explicit:
        return False
    return highway in WALK or (highway in {"cycleway", "motorway", "trunk", "motorway_link", "trunk_link"} and explicit)


@dataclass
class RoutingGraph:
    g: nx.MultiDiGraph
    mode: Mode
    proj: LocalProjection
    seg_index: GridIndex
    restrictions: dict
    skipped_restrictions: list[int]
    params: dict
    segments: list
    applied: int = 0


@dataclass(frozen=True)
class Snap:
    a: int
    b: int
    way: int
    fraction: float
    xy: tuple[float, float]
    distance: float


def build_graph(doc, mode, *, closed=frozenset(), walk_kmh=4.5, use_restrictions=True):
    proj, g, index, segments = projection(doc), nx.MultiDiGraph(), GridIndex(80), []
    for w in doc.ways.values():
        if w.id in closed or not (is_drivable(w.tags) if mode == "car" else walkable(w.tags)):
            continue
        speed = _speed(w.tags) / 3.6 if mode == "car" else walk_kmh / 3.6
        d = direction(w.tags) if mode == "car" else (1 if w.tags.get("oneway:foot") == "yes" else 0)
        # Never bridge over a missing OSM vertex.
        for a, b in zip(w.nodes, w.nodes[1:]):
            if a not in doc.nodes or b not in doc.nodes or a == b:
                continue
            pa, pb = (proj.to_xy(doc.nodes[n].lat, doc.nodes[n].lon) for n in (a, b))
            length = math.dist(pa, pb)
            if length <= 0:
                continue
            g.add_node(a, xy=pa)
            g.add_node(b, xy=pb)
            attrs = dict(l=length, t=length / speed, way=w.id, kind="road" if mode == "car" else "walk",
                         segment=(w.id, a, b))
            if d >= 0:
                g.add_edge(a, b, key=w.id, **attrs)
            if d <= 0:
                g.add_edge(b, a, key=w.id, **attrs)
            segment = (a, b, w.id, pa, pb)
            segments.append(segment)
            index.insert_box(*pa, *pb, segment)
    restrictions, skipped, applied = {}, [], 0
    if mode == "car" and use_restrictions:
        for rel in doc.relations.values():
            if rel.tags.get("type") != "restriction":
                continue
            members = [[m for m in rel.members if m.role == role] for role in ("from", "to", "via")]
            if any(len(ms) != 1 for ms in members):
                skipped.append(rel.id)
                continue
            f, t, v = [ms[0] for ms in members]
            kind = rel.tags.get("restriction", "")
            if (f.type != "way" or t.type != "way" or v.type != "node" or v.ref not in g
                    or not any(e[2]["way"] == f.ref for e in g.in_edges(v.ref, data=True))
                    or not any(e[2]["way"] == t.ref for e in g.out_edges(v.ref, data=True))
                    or not kind.startswith(("only_", "no_"))):
                skipped.append(rel.id)
                continue
            rules = restrictions.setdefault((f.ref, v.ref), {"no": set(), "only": None, "ids": []})
            if kind.startswith("only_"):
                rules["only"] = ({t.ref} if rules["only"] is None else rules["only"] & {t.ref})
            else:
                rules["no"].add(t.ref)
            rules["ids"].append(rel.id)
            applied += 1
    return RoutingGraph(g, mode, proj, index, restrictions, skipped, {"walk_kmh": walk_kmh}, segments, applied)


def snap(rg, lat, lon, max_m):
    p = rg.proj.to_xy(lat, lon)
    best = None
    for a, b, wid, pa, pb in rg.seg_index.query(*p, max_m):
        q, fraction, distance = project_on_segment(p, pa, pb)
        if distance <= max_m and (best is None or distance < best.distance):
            best = Snap(a, b, wid, fraction, q, distance)
    return best


def virtual_graph(rg, snaps):
    """Split every direction of a physical edge at all its snaps, on a copy."""
    g = rg.g.copy()
    groups, nodes = {}, []
    for i, s in enumerate(snaps):
        if s.fraction <= 1e-10:
            nodes.append(s.a)
        elif s.fraction >= 1 - 1e-10:
            nodes.append(s.b)
        else:
            nid = ("snap", i)
            nodes.append(nid)
            g.add_node(nid, xy=s.xy)
            groups.setdefault((s.a, s.b, s.way), []).append((s.fraction, nid))
    for (a, b, way), splits in groups.items():
        points = [(0, a), *sorted(splits), (1, b)]
        for u, v, reverse in ((a, b, False), (b, a, True)):
            if not g.has_edge(u, v, way):
                continue
            attrs = dict(g[u][v][way])
            g.remove_edge(u, v, way)
            for (x, n), (y, m) in zip(points, points[1:]):
                src, dst = (m, n) if reverse else (n, m)
                g.add_edge(src, dst, key=way, **{**attrs, "l": attrs["l"] * (y-x), "t": attrs["t"] * (y-x)})
    return g, nodes


def search(rg, g, sources, metric, budget=math.inf, target=None):
    """Dijkstra over (vertex, incoming way), preserving turn legality."""
    weight = "t" if metric == "time" else "l"
    counter = itertools.count()
    costs, prev, queue = {}, {}, []
    for node, cost in sources.items():
        state = (node, None)
        costs[state] = cost
        heapq.heappush(queue, (cost, next(counter), state))
    end = None
    while queue:
        cost, _, state = heapq.heappop(queue)
        if cost != costs[state] or cost > budget:
            continue
        u, incoming = state
        if u == target:
            end = state
            break
        rules = rg.restrictions.get((incoming, u))
        for _, v, key, edge in g.out_edges(u, keys=True, data=True):
            if rules and (edge["way"] in rules["no"] or
                          (rules["only"] is not None and edge["way"] not in rules["only"])):
                continue
            nxt, value = (v, edge["way"]), cost + edge[weight]
            if value <= budget and value < costs.get(nxt, math.inf):
                costs[nxt], prev[nxt] = value, (state, (u, v, key))
                heapq.heappush(queue, (value, next(counter), nxt))
    return costs, prev, end


def shortest_path(rg, a, b, metric):
    g, (start, end) = virtual_graph(rg, [a, b])
    costs, prev, state = search(rg, g, {start: 0}, metric, target=end)
    if state is None:
        return None
    edges = []
    while state in prev:
        state, edge = prev[state]
        edges.append(edge)
    edges.reverse()
    coords = [g.nodes[start]["xy"]] + [g.nodes[v]["xy"] for _, v, _ in edges]
    if len(coords) == 1:
        coords.append(coords[0])
    return {"length_m": sum(g[u][v][k]["l"] for u,v,k in edges),
            "time_s": sum(g[u][v][k]["t"] for u,v,k in edges),
            "coordinates": [list(reversed(rg.proj.to_latlon(*xy))) for xy in coords],
            "edges": [(u, v, g[u][v][k]) for u,v,k in edges]}


@dataclass
class Reach:
    cost: dict
    pieces: list
    candidates: list
    length_m: float


def reach(rg, sources: dict[Hashable, float], budget, metric, *, graph=None):
    g = rg.g if graph is None else graph
    costs, _, _ = search(rg, g, sources, metric, budget)
    return reached(rg, g, costs, budget, metric)


def reached(rg, g, costs, budget, metric):
    weight = "t" if metric == "time" else "l"
    node_cost, intervals = {}, {}
    for (node, incoming), cost in costs.items():
        node_cost[node] = min(cost, node_cost.get(node, math.inf))
        if cost > budget:
            continue
        rules = rg.restrictions.get((incoming, node))
        for _, v, _, e in g.out_edges(node, keys=True, data=True):
            if rules and (e["way"] in rules["no"] or
                          (rules["only"] is not None and e["way"] not in rules["only"])):
                continue
            fraction = min(1, max(0, (budget-cost) / e[weight])) if e[weight] else 1
            if fraction == 0:
                continue
            a, b = g.nodes[node]["xy"], g.nodes[v]["xy"]
            # Coordinate ordering unifies both travel directions and virtual pieces.
            key = (e["segment"], tuple(sorted((a,b))))
            interval = (0, fraction) if a <= b else (1-fraction, 1)
            intervals.setdefault(key, []).append(interval)
    pieces, candidates, length = [], [], 0.0
    for (_, (a,b)), spans in intervals.items():
        merged = []
        for lo, hi in sorted(spans):
            if merged and lo <= merged[-1][1] + 1e-10:
                merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
            else:
                merged.append((lo,hi))
        for lo,hi in merged:
            p, q = [(a[0]+f*(b[0]-a[0]), a[1]+f*(b[1]-a[1])) for f in (lo,hi)]
            if p != q:
                pieces.append(LineString([p,q]))
                length += math.dist(p,q)
                if lo > 0:
                    candidates.append(p)
                if hi < 1:
                    candidates.append(q)
    return Reach(node_cost, pieces, candidates, length)


def polygon(result, how="buffer", buffer_m=40):
    if not result.pieces:
        return GeometryCollection()
    if how == "hull":
        pts = MultiPoint([xy for line in result.pieces for xy in line.coords])
        hull = concave_hull(pts, ratio=0.3)
        return hull if hull.geom_type in {"Polygon", "MultiPolygon"} else hull.buffer(buffer_m)
    return unary_union(result.pieces).buffer(buffer_m)


def geojson(rg, geometry):
    if geometry.is_empty:
        return {"type": "GeometryCollection", "geometries": []}
    return mapping(transform(lambda x,y,z=None: tuple(reversed(rg.proj.to_latlon(x,y))), geometry))


def reach_polygons(rg, result, bands=None, how="buffer", buffer_m=40):
    return geojson(rg, polygon(result, how, buffer_m))
