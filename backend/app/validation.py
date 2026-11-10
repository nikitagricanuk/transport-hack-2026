"""Graph checks that predict failures in MatSim / SUMO / Webots."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import networkx as nx

from .geo import GridIndex, LocalProjection, dist, project_on_segment
from .osm_model import Member, OsmDocument
from .roads import ONEWAY_VALID, can_connect, default_turns, direction, is_access_way, is_drivable, parse_int
from .edits import ATTACH_MAX_M, restriction_way_matches, signal_spot, way_level
from .norms import (GRADE_SOURCE, LIVING_KMH, SPEED_SHORTHAND, SPEED_SOURCE, URBAN_KMH, default_limit,
                    max_grade, speed_kmh)

Severity = Literal["critical", "warning", "info"]
Category = Literal["connectivity", "direction", "junction", "lanes", "geometry", "data", "pedestrian"]

GAP_M = 8.0          # dangling end closer than this to another road = broken junction
CLOSE_JUNCTION_M = 2.0
BOUNDARY_M = 40.0    # dead ends near the extract border are expected
FRINGE_PAIR_M = 90.0  # opposite carriageway ends this close at the border get a turnaround
FRAGMENT_LINK_M = 30.0  # an isolated fragment this close to the network gets a connector
OUTSIDE = "outside"   # virtual node: the rest of the city beyond the extract
STOP_FAR_M = 25.0
# road classes whose speed matters most for the simulation (and their _link ramps)
MAIN_CLASSES = {"motorway", "trunk", "primary", "secondary", "tertiary"}
CONNECT_FIX_M = 30.0  # offer "connect to a nearby road" only inside this radius
ENTRANCE_M = 12.0     # driveway/sidewalk end this close to a door is not a dead end


@dataclass
class Issue:
    id: str
    severity: Severity
    category: Category
    title: str
    description: str
    refs: list[dict[str, Any]]
    location: list[float] | None = None  # [lon, lat]
    locations: list[list[float]] | None = None  # every highlighted point, [lon, lat]
    fix: dict[str, Any] | None = None    # primary option, kept for older clients; equals fixes[0]
    fixes: list[dict[str, Any]] = field(default_factory=list)  # every alternative the UI can apply
    levels: list[str] = field(default_factory=list)  # affected: matsim/sumo/webots


def _is_entrance(tags: dict[str, str]) -> bool:
    """OSM door: ``entrance=*`` other than ``no``, or the older ``building=entrance``."""
    if tags.get("entrance") not in (None, "no"):
        return True
    return tags.get("building") == "entrance"


def issue_key(issue: dict[str, Any] | Issue) -> str:
    """Stable identity of an issue: its title and the objects it points at (ids are positional)."""
    d = asdict(issue) if isinstance(issue, Issue) else issue
    refs = sorted(f"{r['kind']}/{r['id']}" for r in d["refs"])
    return d["title"] + "|" + ",".join(refs)


def validate(doc: OsmDocument, dismissed: set[str] | None = None) -> dict[str, Any]:
    """Every issue of the graph. Issues the user dismissed as false (`dismissed` keys) stay in
    the list marked ``dismissed`` but no longer count in the totals or the adapter levels."""
    v = _Validator(doc)
    v.run()
    issues = sorted(v.issues, key=lambda i: {"critical": 0, "warning": 1, "info": 2}[i.severity])
    dismissed = dismissed or set()
    active = [i for i in issues if issue_key(i) not in dismissed]
    out = []
    for i in issues:
        d = asdict(i)
        d["key"] = issue_key(d)
        d["dismissed"] = d["key"] in dismissed
        out.append(d)
    summary = _summary(active, v)
    summary["dismissed"] = len(issues) - len(active)
    return {"issues": out, "summary": summary}


def _summary(issues: list[Issue], v: "_Validator") -> dict[str, Any]:
    def level(name: str) -> str:
        rel = [i for i in issues if name in i.levels]
        if any(i.severity == "critical" for i in rel):
            return "fail"
        if any(i.severity == "warning" for i in rel):
            return "risk"
        return "ok"

    return {
        "counts": {s: sum(1 for i in issues if i.severity == s) for s in ("critical", "warning", "info")},
        "components": v.component_count,
        "strong_components": v.strong_component_count,
        "outside_strong": v.outside_strong,
        "levels": {name: level(name) for name in ("matsim", "sumo", "webots")},
        # Webots itself is not executed. "ok" means the static geometry checks passed.
        "level_notes": {
            "matsim": "Связность, разрывы и односторонние ловушки направленного графа.",
            "sumo": "Направления, полосы, перекрёстки, светофоры и геометрия.",
            "webots": "Проверены layer мостов и тоннелей, что layer — целое число, "
                      "и что у проезжих рёбер нет сегментов нулевой длины. Сам Webots не запускался.",
        },
    }


class _Validator:
    def __init__(self, doc: OsmDocument) -> None:
        self.doc = doc
        b = doc.bounds() or (0, 0, 0, 0)
        self.bounds = b
        # Only a real extract (<bounds> in the file) has roads that continue outside it.
        self.extract = any(el.tag == "bounds" for el in doc.head)
        self.proj = LocalProjection((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        self.issues: list[Issue] = []
        self.road_ways = [w for w in doc.ways.values() if is_drivable(w.tags) and len(w.nodes) >= 2]
        self.road_node_ways: dict[int, list[int]] = {}
        for w in self.road_ways:
            for ref in dict.fromkeys(w.nodes):
                self.road_node_ways.setdefault(ref, []).append(w.id)
        self.component_count = 0
        self.strong_component_count = 0
        self.outside_strong = 0
        self.main_nodes: set[int] = set()
        self._xy_cache: dict[int, tuple[float, float]] = {}

    # ---------------------------------------------------------------- utils
    def xy(self, nid: int) -> tuple[float, float] | None:
        if nid in self._xy_cache:
            return self._xy_cache[nid]
        n = self.doc.nodes.get(nid)
        if n is None:
            return None
        p = self.proj.to_xy(n.lat, n.lon)
        self._xy_cache[nid] = p
        return p

    def loc(self, nid: int) -> list[float] | None:
        n = self.doc.nodes.get(nid)
        return [n.lon, n.lat] if n else None

    def near_boundary(self, nid: int) -> bool:
        n = self.doc.nodes.get(nid)
        if n is None:
            return False
        minlat, minlon, maxlat, maxlon = self.bounds
        p = self.proj.to_xy(n.lat, n.lon)
        corners = [self.proj.to_xy(minlat, minlon), self.proj.to_xy(maxlat, maxlon)]
        (x0, y0), (x1, y1) = corners
        # Overpass returns whole ways, so their ends stick out of <bounds> by tens or
        # hundreds of metres. Everything outside the box is the fringe too.
        if not (x0 <= p[0] <= x1 and y0 <= p[1] <= y1):
            return True
        return min(p[0] - x0, x1 - p[0], p[1] - y0, y1 - p[1]) < BOUNDARY_M

    def name(self, wid: int) -> str:
        w = self.doc.ways.get(wid)
        if not w:
            return f"way/{wid}"
        return w.tags.get("name") or f"{w.tags.get('highway', 'ребро')} way/{wid}"

    def add(self, **kw: Any) -> None:
        fixes = kw.pop("fixes", None)
        if fixes is None:
            fix = kw.get("fix")
            fixes = [fix] if fix else []
        else:
            kw["fix"] = fixes[0] if fixes else None
        kw["fixes"] = fixes
        kw.setdefault("id", f"{kw['category']}-{len(self.issues)}")
        way_ids = kw.pop("affected_ways", None)
        if way_ids is None:
            way_ids = [ref["id"] for ref in kw.get("refs", []) if ref["kind"] == "way"]
        levels = kw.get("levels", [])
        if "matsim" in levels and way_ids and all(
            wid in self.doc.ways and self.doc.ways[wid].tags.get("highway") == "service" for wid in way_ids
        ):
            kw["levels"] = [level for level in levels if level != "matsim"]
        self.issues.append(Issue(**kw))

    # ------------------------------------------------------------------ run
    def run(self) -> None:
        self._prepare_entrances()
        self.check_data()
        self.check_connectivity()
        self.check_strong_connectivity()
        self.check_gaps()
        self.check_close_junctions()
        self.check_directions()
        self.check_lanes()
        self.check_speed()
        self.check_restrictions()
        self.check_geometry()
        self.check_elevation()
        self.check_signals_and_stops()
        self.check_connections()
        self.check_pedestrian()

    def check_pedestrian(self) -> None:
        from .pedestrian import check
        self.issues.extend(Issue(**item) for item in check(self.doc))

    def check_data(self) -> None:
        for w in self.doc.ways.values():
            missing = [r for r in w.nodes if r not in self.doc.nodes]
            if missing:
                self.add(category="data", severity="critical", title="Ссылка на несуществующий узел",
                         description=f"{self.name(w.id)} ссылается на {len(missing)} отсутствующих узлов — "
                                     "конвертеры отбросят ребро.",
                         refs=[{"kind": "way", "id": w.id}], levels=["matsim", "sumo", "webots"],
                         fix={"label": "Удалить ребро", "op": "delete", "args": {"kind": "way", "id": w.id}})
            elif len(set(w.nodes)) < 2:
                self.add(category="data", severity="critical", title="Вырожденное ребро",
                         description=f"{self.name(w.id)} содержит меньше двух разных узлов.",
                         refs=[{"kind": "way", "id": w.id}], levels=["matsim", "sumo"],
                         fix={"label": "Удалить", "op": "delete", "args": {"kind": "way", "id": w.id}})
            ow = w.tags.get("oneway")
            if is_drivable(w.tags) and ow is not None and ow not in ONEWAY_VALID:
                self.add(category="direction", severity="warning", title="Некорректное значение oneway",
                         description=f"oneway={ow} на {self.name(w.id)}; допустимо yes, no, -1.",
                         refs=[{"kind": "way", "id": w.id}], levels=["sumo"],
                         location=self.loc(w.nodes[0]) if w.nodes else None,
                         fixes=[
                             {"label": "oneway=yes", "op": "set_tags",
                              "args": {"kind": "way", "id": w.id, "tags": {"oneway": "yes"}}},
                             {"label": "oneway=no", "op": "set_tags",
                              "args": {"kind": "way", "id": w.id, "tags": {"oneway": "no"}}},
                             {"label": "oneway=-1", "op": "set_tags",
                              "args": {"kind": "way", "id": w.id, "tags": {"oneway": "-1"}}},
                         ])

    def check_connectivity(self) -> None:
        g = nx.Graph()
        for w in self.road_ways:
            refs = [r for r in w.nodes if r in self.doc.nodes]
            for a, b in zip(refs, refs[1:]):
                g.add_edge(a, b, way=w.id)
        if g.number_of_nodes() == 0:
            return
        comps = sorted(nx.connected_components(g), key=len, reverse=True)
        self.component_count = len(comps)
        self.main_nodes = comps[0]
        for comp in comps[1:]:
            ways = sorted({d["way"] for _, _, d in g.subgraph(comp).edges(data=True)})
            # A driveway or sidewalk that ends at a building door is supposed to stop
            # there. It is not a broken piece of the car network.
            if self._access_component(ways, comp):
                continue
            first = next(iter(comp))
            single = len(ways) == 1
            fixes: list[dict[str, Any]] = []
            if single:
                connect = self._connect_fix(ways[0])
                if connect:
                    fixes.append(connect)
                fixes.append({"label": "Удалить", "op": "delete", "args": {"kind": "way", "id": ways[0]}})
            else:
                link = self._fragment_link_fix(comp, ways)
                if link:
                    fixes.append(link)
                fixes.append({"label": "Удалить фрагмент", "op": "delete_ways", "args": {"ids": ways}})
            self.add(category="connectivity", severity="critical",
                     title="Изолированное ребро" if single else "Изолированный фрагмент сети",
                     description=(f"{self.name(ways[0])} не связано с основной сетью" if single
                                  else f"{len(ways)} рёбер не связаны с основной сетью"),
                     refs=[{"kind": "way", "id": w} for w in ways[:20]],
                     location=self.loc(first), affected_ways=ways,
                     levels=["matsim", "sumo"], fixes=fixes)

    def _fragment_link_fix(self, comp: set[int], ways: list[int]) -> dict[str, Any] | None:
        """A short connector from the fragment to the nearest road of the main network."""
        seg_idx = self._segment_index()
        best = None
        for nid in comp:
            p = self.xy(nid)
            if p is None:
                continue
            for (wid, a, b) in seg_idx.query(p[0], p[1], FRAGMENT_LINK_M):
                if wid in ways or a not in self.main_nodes:
                    continue
                q, _t, d = project_on_segment(p, self.xy(a), self.xy(b))  # type: ignore[arg-type]
                if d <= FRAGMENT_LINK_M and (best is None or d < best[0]):
                    best = (d, nid, wid, q)
        if best is None:
            return None
        d, nid, wid, q = best
        lat, lon = self.proj.to_latlon(*q)
        hw = self.doc.ways[ways[0]].tags.get("highway", "service")
        return {"label": f"Присоединить ({d:.0f} м)", "op": "add_way",
                "args": {"points": [{"id": nid}, {"lat": lat, "lon": lon, "way_id": wid}],
                         "tags": {"highway": hw if hw in ("service", "residential", "unclassified") else "service"}}}

    def _segment_index(self) -> GridIndex:
        idx = GridIndex(cell=25.0)
        for w in self.road_ways:
            for a, b in zip(w.nodes, w.nodes[1:]):
                pa, pb = self.xy(a), self.xy(b)
                if pa is None or pb is None:
                    continue
                idx.insert_box(pa[0], pa[1], pb[0], pb[1], (w.id, a, b))
        return idx

    def check_gaps(self) -> None:
        seg_idx = self._segment_index()
        for nid, wids in self.road_node_ways.items():
            if len(wids) != 1:
                continue
            w = self.doc.ways[wids[0]]
            if nid not in (w.nodes[0], w.nodes[-1]) or w.is_closed:
                continue
            if self._exempt_access_end(nid):
                continue
            p = self.xy(nid)
            if p is None:
                continue
            nearest = None
            allowed = None
            for (owid, a, b) in seg_idx.query(p[0], p[1], GAP_M):
                if owid == w.id:
                    continue
                other = self.doc.ways.get(owid)
                pa, pb = self.xy(a), self.xy(b)
                if other is None or pa is None or pb is None:
                    continue
                _, _, d = project_on_segment(p, pa, pb)
                if d > GAP_M:
                    continue
                # A short spur already joined to that road at its other end is not a gap.
                far = w.nodes[-1] if nid == w.nodes[0] else w.nodes[0]
                if far in other.nodes and (q := self.xy(far)) is not None and dist(p, q) <= 2 * GAP_M:
                    continue
                if nearest is None or d < nearest[0]:
                    nearest = (d, owid)
                # «Соединить» must be a target op_connect_endpoint will accept.
                if can_connect(w.tags, other.tags):
                    continue
                if allowed is None or d < allowed[0]:
                    allowed = (d, owid)
            if nearest is None:
                continue
            d, owid = nearest
            fixes = []
            if allowed is not None:
                fixes.append({"label": "Соединить", "op": "connect_endpoint",
                              "args": {"node_id": nid, "way_id": allowed[1]}})
            if self._fully_dangling(w):
                fixes.append({"label": "Удалить", "op": "delete", "args": {"kind": "way", "id": w.id}})
            self.add(category="junction", severity="warning", title="Разорванный перекрёсток",
                     description=f"{self.name(w.id)} заканчивается в {d:.1f} м от {self.name(owid)} "
                                 "без общего узла.",
                     refs=[{"kind": "node", "id": nid}, {"kind": "way", "id": owid}], location=self.loc(nid),
                     affected_ways=[w.id, owid],
                     levels=["matsim", "sumo"], fixes=fixes)

    def check_close_junctions(self) -> None:
        junctions = [n for n, ws in self.road_node_ways.items() if len(ws) >= 2]
        idx = GridIndex(cell=10.0)
        for nid in junctions:
            p = self.xy(nid)
            if p:
                idx.insert_point(p[0], p[1], nid)
        reported: set[frozenset[int]] = set()
        for nid in junctions:
            p = self.xy(nid)
            if not p:
                continue
            for other in idx.query(p[0], p[1], CLOSE_JUNCTION_M):
                if other == nid:
                    continue
                pair = frozenset((nid, other))
                q = self.xy(other)
                if pair in reported or q is None or dist(p, q) > CLOSE_JUNCTION_M:
                    continue
                reported.add(pair)
                self.add(category="junction", severity="warning", title="Склеенные перекрёстки",
                         description=f"Два узла перекрёстка в {dist(p, q):.1f} м друг от друга — "
                                     "SUMO создаст лишний перекрёсток с короткой связью.",
                         refs=[{"kind": "node", "id": nid}, {"kind": "node", "id": other}],
                         location=self.loc(nid), levels=["sumo"],
                         fix={"label": "Объединить", "op": "merge_nodes",
                              "args": {"keep_id": nid, "remove_id": other}})

    def check_strong_connectivity(self) -> None:
        """One-way traps: nodes that are not in the largest strongly connected component.

        Edges follow ``oneway`` (including ``-1`` and implicit motorway/roundabout
        oneway) and skip ways ``is_drivable`` rejects for ``access``. A node you
        can drive into but not back out of is a trap even when every junction has
        both an incoming and an outgoing edge locally.
        """
        dg = nx.DiGraph()
        for w in self.road_ways:
            refs = [r for r in w.nodes if r in self.doc.nodes]
            d = direction(w.tags)
            for a, b in zip(refs, refs[1:]):
                if d >= 0:
                    dg.add_edge(a, b)
                if d <= 0:
                    dg.add_edge(b, a)
        if dg.number_of_nodes() == 0:
            return
        # A road that runs off the extract continues in the city: its loose end is
        # joined to a virtual "outside" node both ways, so leaving the fragment is not a trap.
        fringe = [n for n in dg.nodes if (dg.in_degree(n) == 0 or dg.out_degree(n) == 0) and self.near_boundary(n)]
        for n in fringe if self.extract else []:
            dg.add_edge(n, OUTSIDE)
            dg.add_edge(OUTSIDE, n)
        sccs = [set(c) for c in nx.strongly_connected_components(dg)]
        sccs.sort(key=lambda c: (OUTSIDE in c and len(c) > 1, len(c)), reverse=True)
        sccs = [c for c in sccs if c != {OUTSIDE}] + [c for c in sccs if c == {OUTSIDE}]
        real = [c - {OUTSIDE} for c in sccs if c - {OUTSIDE}]
        self.strong_component_count = len(real)
        self.outside_strong = sum(len(c) for c in real[1:])
        if len(real) == 1:
            return
        cond = nx.condensation(dg, sccs)
        sccs = [c - {OUTSIDE} for c in sccs]
        reachable = nx.descendants(cond, 0)
        reaches_main = nx.ancestors(cond, 0)
        groups: dict[str, list[set[int]]] = {"trap": [], "unreachable": [], "island": []}
        for i, comp in enumerate(sccs[1:], start=1):
            if not comp:
                continue
            # Separate undirected pieces are already reported as isolated fragments.
            if self.main_nodes and not (comp & self.main_nodes):
                continue
            # The door-end of a one-way driveway is a dead end on purpose.
            comp = {n for n in comp if not self._exempt_access_end(n)}
            if not comp:
                continue
            if i in reachable and i not in reaches_main:
                groups["trap"].append(comp)
            elif i in reaches_main and i not in reachable:
                groups["unreachable"].append(comp)
            else:
                groups["island"].append(comp)
        titles = {
            "trap": ("Односторонняя ловушка", "Односторонние тупики-ловушки",
                     "сюда можно въехать из основной сети, но выехать обратно нельзя"),
            "unreachable": ("Недостижимый участок", "Узлы без въезда с основной сети",
                            "с основной сети сюда не попасть: односторонние рёбра смотрят наружу"),
            "island": ("Направленно изолированный участок", "Направленно изолированные узлы",
                       "участок не сообщается с основной сетью по направлению движения"),
        }
        for kind, comps in groups.items():
            multi = [c for c in comps if len(c) >= 2]
            singles: set[int] = set()
            for comp in comps:
                if len(comp) < 2:
                    singles |= comp
            for comp in multi:
                self._add_directed_area(kind, comp, titles[kind][0], titles[kind][2], multi=True)
            if singles:
                self._add_directed_area(kind, singles, titles[kind][1], titles[kind][2], multi=False)

    def _add_directed_area(self, kind: str, nodes: set[int], title: str, why: str, multi: bool) -> None:
        ordered = sorted(nodes)
        locs = [loc for n in ordered if (loc := self.loc(n))]
        ways: set[int] = set()
        for n in ordered:
            ways.update(self.road_node_ways.get(n, []))
        interior = any(not self.near_boundary(n) for n in nodes)
        if not interior:
            severity: Severity = "info"
        elif multi:
            severity = "critical"
        else:
            severity = "warning"
        noun = "узлов" if len(nodes) != 1 else "узел"
        fixes = self._trap_fixes(nodes) if kind == "trap" else None
        self.add(category="connectivity", severity=severity, title=title,
                 description=f"{len(nodes)} {noun}: {why}.",
                 refs=[{"kind": "node", "id": ordered[0]}] + [{"kind": "way", "id": w} for w in sorted(ways)[:12]],
                 location=locs[0] if locs else None, locations=locs[:800] or None, affected_ways=ways,
                 levels=["matsim", "sumo"], fixes=fixes)

    def check_directions(self) -> None:
        dg = nx.DiGraph()
        for w in self.road_ways:
            refs = [r for r in w.nodes if r in self.doc.nodes]
            d = direction(w.tags)
            for a, b in zip(refs, refs[1:]):
                if d >= 0:
                    dg.add_edge(a, b, way=w.id)
                if d <= 0:
                    dg.add_edge(b, a, way=w.id)
        if dg.number_of_nodes() == 0:
            return
        turnarounds = self._fringe_turnarounds(dg)
        for nid in dg.nodes:
            ind, outd = dg.in_degree(nid), dg.out_degree(nid)
            if (ind == 0) == (outd == 0):
                continue
            if self._exempt_access_end(nid):
                continue
            wids = self.road_node_ways.get(nid, [])
            oneway_ids = [w for w in wids if direction(self.doc.ways[w].tags) != 0]
            if not oneway_ids and len(wids) <= 1:
                continue  # plain two-way dead end: fine for simulation
            wid = oneway_ids[0] if oneway_ids else wids[0]
            boundary = self.near_boundary(nid)
            kind = "Тупик: въезд без выезда" if outd == 0 else "Узел без въезда"
            self.add(category="direction", severity="info" if boundary else "critical", title=kind,
                     description=("Узел на границе выгрузки — проверьте, что поток уходит за пределы участка."
                                  if boundary else
                                  "Односторонние рёбра сходятся так, что машины застрянут (SUMO) или маршрут "
                                  "не построится (MatSim)."),
                     refs=[{"kind": "node", "id": nid}] + [{"kind": "way", "id": w} for w in wids],
                     location=self.loc(nid), levels=["matsim", "sumo"],
                     fixes=([turnarounds[nid]] if nid in turnarounds else []) + self._oneway_fixes(nid, wid))

    def _fringe_turnarounds(self, dg: nx.DiGraph) -> dict[int, dict[str, Any]]:
        """Pair the loose ends of a divided road at the edge of the extract.

        One carriageway leaves the area (no exit), the opposite one enters it (no
        entry), a few dozen metres apart. A short one-way link from the first to
        the second keeps both carriageways in MatSim's strongly connected network
        without changing the real roads; it is tagged uds:fringe=yes.
        """
        if not self.extract:
            return {}
        sinks = [n for n in dg.nodes if dg.out_degree(n) == 0 and dg.in_degree(n) > 0 and self.near_boundary(n)]
        sources = [n for n in dg.nodes if dg.in_degree(n) == 0 and dg.out_degree(n) > 0 and self.near_boundary(n)]
        out: dict[int, dict[str, Any]] = {}
        used: set[int] = set()
        pairs = sorted((dist(self.xy(a), self.xy(b)), a, b) for a in sinks for b in sources  # type: ignore[arg-type]
                       if self.xy(a) and self.xy(b))
        for d, a, b in pairs:
            if d > FRINGE_PAIR_M or a in used or b in used:
                continue
            used |= {a, b}
            wa = self.doc.ways[self.road_node_ways[a][0]]
            fix = {"label": f"Граничный разворот — фиктивное ребро ({d:.0f} м)", "op": "add_way",
                   "args": {"points": [{"id": a}, {"id": b}],
                            "tags": {"highway": wa.tags.get("highway", "road"), "oneway": "yes", "lanes": "1",
                                     "uds:fringe": "yes",
                                     "note": "Фиктивное ребро (граница выгрузки): связывает проезжие части "
                                             "у края выгрузки, чтобы MatSim мог развернуть машины"}}}
            out[a] = fix
            out[b] = fix
        return out

    def _lane_fix(self, wid: int, fw: int, bw: int, label: str) -> dict[str, Any]:
        tags = {"lanes": str(fw), "lanes:forward": None, "lanes:backward": None} if bw == 0 else \
            {"lanes": str(fw + bw), "lanes:forward": str(fw), "lanes:backward": str(bw)}
        return {"label": label, "op": "set_tags", "args": {"kind": "way", "id": wid, "tags": tags}}

    def check_lane_completeness(self, w, t: dict[str, str], lanes: int | None, fw: int | None, bw: int | None,
                                loc: list[float] | None, ref: list[dict[str, Any]]) -> None:
        """Lane counts the adapters would otherwise guess differently.

        Without lanes MatSim assumes 1 lane per direction and SUMO 2 on a primary road;
        an odd total on a two-way road is halved by MatSim (lanes=3 → 1.5 each way).
        """
        base = t.get("highway", "").removesuffix("_link")
        two_way = direction(t) == 0
        if lanes is None and "lanes" not in t and base in ("motorway", "trunk", "primary", "secondary"):
            fixes = ([self._lane_fix(w.id, 1, 1, "lanes=2 (по 1 в каждую сторону)"),
                      self._lane_fix(w.id, 2, 2, "lanes=4 (по 2 в каждую сторону)")] if two_way else
                     [self._lane_fix(w.id, 1, 0, "lanes=1"), self._lane_fix(w.id, 2, 0, "lanes=2")])
            self.add(category="lanes", severity="warning", title="Не указано число полос",
                     description=f"{self.name(w.id)} ({t.get('highway')}) без lanes: MatSim поставит 1 полосу "
                                 "в каждую сторону, SUMO — по таблице класса (у primary 2). "
                                 "Сверьте число полос со снимком и выберите вариант.",
                     refs=ref, location=loc, levels=["matsim", "sumo"], fixes=fixes)
        elif two_way and lanes is not None and lanes >= 3 and lanes % 2 and fw is None and bw is None:
            big, small = (lanes + 1) // 2, lanes // 2
            self.add(category="lanes", severity="warning", title="Нечётное число полос без разбивки",
                     description=f"{self.name(w.id)}: lanes={lanes} на двусторонней дороге без lanes:forward/"
                                 f"backward. MatSim поделит поровну ({lanes / 2:g} в каждую сторону), SUMO — "
                                 "по-своему. Укажите по снимку, в какую сторону полос больше.",
                     refs=ref, location=loc, levels=["matsim", "sumo"],
                     fixes=[self._lane_fix(w.id, big, small, f"{big} по ходу линии, {small} против"),
                            self._lane_fix(w.id, small, big, f"{small} по ходу линии, {big} против")])

    def check_lanes(self) -> None:
        for w in self.road_ways:
            t = w.tags
            lanes = parse_int(t.get("lanes"))
            fw, bw = parse_int(t.get("lanes:forward")), parse_int(t.get("lanes:backward"))
            loc = self.loc(w.nodes[len(w.nodes) // 2])
            ref = [{"kind": "way", "id": w.id}]
            self.check_lane_completeness(w, t, lanes, fw, bw, loc, ref)
            if "lanes" in t and lanes is None:
                self.add(category="lanes", severity="warning", title="lanes не число",
                         description=f"lanes={t['lanes']} на {self.name(w.id)}.", refs=ref, location=loc,
                         levels=["sumo"])
            if lanes is not None and fw is not None and bw is not None and fw + bw != lanes and direction(t) == 0:
                lane_fixes = [
                    {"label": f"lanes={fw + bw}", "op": "set_tags",
                     "args": {"kind": "way", "id": w.id, "tags": {"lanes": str(fw + bw)}}},
                ]
                if lanes > fw:
                    lane_fixes.append({
                        "label": f"lanes:backward={lanes - fw}", "op": "set_tags",
                        "args": {"kind": "way", "id": w.id, "tags": {"lanes:backward": str(lanes - fw)}},
                    })
                self.add(category="lanes", severity="warning", title="lanes ≠ forward + backward",
                         description=f"lanes={lanes}, а lanes:forward + lanes:backward = {fw + bw}.",
                         refs=ref, location=loc, levels=["sumo"], fixes=lane_fixes)
            for key, expected in (
                ("turn:lanes", lanes if direction(t) != 0 else None),
                ("turn:lanes:forward", fw),
                ("turn:lanes:backward", bw),
            ):
                val = t.get(key)
                if val is None or expected is None:
                    continue
                n = len(val.split("|"))
                if n != expected:
                    self.add(category="lanes", severity="warning", title=f"Число {key} ≠ числу полос",
                             description=f"{key} содержит {n} значений, а полос {expected} ({self.name(w.id)}).",
                             refs=ref, location=loc, levels=["sumo"],
                             fix={"label": "Пересчитать", "op": "set_tags",
                                  "args": {"kind": "way", "id": w.id,
                                           "tags": {key: "|".join(default_turns(expected))}}})

    def check_speed(self) -> None:
        """Speed limits the three simulators read the same way (ПДД 10.2).

        Without maxspeed each adapter falls back to its own class table: ул. Лермонтова
        (secondary) gets 100 km/h in SUMO's default typemap and 30 km/h in MatSim.
        «RU:urban» is read as 60 by SUMO, but MatSim parses only numbers.
        """
        for w in self.road_ways:
            t = w.tags
            hw = t.get("highway", "")
            base = hw.removesuffix("_link")
            ref = [{"kind": "way", "id": w.id}]
            loc = self.loc(w.nodes[len(w.nodes) // 2])
            raw = t.get("maxspeed")

            def fix(kmh: int, label: str = "") -> dict[str, Any]:
                return {"label": label or f"maxspeed={kmh} ({SPEED_SOURCE})", "op": "set_tags",
                        "args": {"kind": "way", "id": w.id, "tags": {"maxspeed": str(kmh)}}}

            if raw is None:
                if base in MAIN_CLASSES:
                    self.add(category="data", severity="warning", title="Нет ограничения скорости",
                             description=f"{self.name(w.id)} ({hw}) без maxspeed: каждый симулятор подставит своё "
                                         "значение по классу дороги (SUMO — до 100 км/ч, MatSim — 30 км/ч). "
                                         f"В городе действует {URBAN_KMH} км/ч ({SPEED_SOURCE}), если нет знака.",
                             refs=ref, location=loc, levels=["matsim", "sumo"], fixes=[fix(URBAN_KMH)])
                elif base in ("residential", "unclassified"):
                    self.add(category="data", severity="info", title="Нет ограничения скорости",
                             description=f"{self.name(w.id)} без maxspeed: скорость возьмётся из таблицы "
                                         f"симулятора. По {SPEED_SOURCE} в городе — {URBAN_KMH} км/ч, "
                                         f"в жилой зоне — {LIVING_KMH} км/ч.",
                             refs=ref, location=loc, levels=["matsim", "sumo"],
                             fixes=[fix(URBAN_KMH), fix(LIVING_KMH, f"maxspeed={LIVING_KMH} (жилая зона)")])
                continue
            kmh = speed_kmh(raw)
            if raw.strip() in SPEED_SHORTHAND:
                self.add(category="data", severity="info", title="Скорость задана сокращением",
                         description=f"{self.name(w.id)}: maxspeed={raw}. SUMO поймёт это как {kmh} км/ч, "
                                     "а MatSim читает только число и подставит своё значение.",
                         refs=ref, location=loc, levels=["matsim"], fixes=[fix(kmh, f"Заменить на {kmh}")])
            elif kmh is None:
                self.add(category="data", severity="warning", title="maxspeed не распознан",
                         description=f"{self.name(w.id)}: maxspeed={raw} — ни один симулятор его не поймёт. "
                                     f"Значение по умолчанию для этого класса: {default_limit(hw)} км/ч ({SPEED_SOURCE}).",
                         refs=ref, location=loc, levels=["matsim", "sumo"],
                         fixes=[fix(default_limit(hw))])
            elif kmh > URBAN_KMH and base not in ("motorway", "trunk"):
                self.add(category="data", severity="info", title="Скорость выше городской нормы",
                         description=f"{self.name(w.id)}: {kmh} км/ч при {URBAN_KMH} км/ч в городе "
                                     f"({SPEED_SOURCE}). Сверьте со знаками на месте.",
                         refs=ref, location=loc, levels=["matsim", "sumo"], fixes=[fix(URBAN_KMH)])

    def restriction_fix(self, relation_id: int, froms: list[Member], tos: list[Member],
                        vias: list[Member]) -> dict | None:
        if len(froms) > 1 or len(tos) > 1 or len(vias) > 1:
            return None
        if any(m.type != "way" for m in froms + tos) or (vias and vias[0].type != "node"):
            return None
        fw = self.doc.ways.get(froms[0].ref) if froms else None
        tw = self.doc.ways.get(tos[0].ref) if tos else None
        via = vias[0].ref if vias else None
        point = self.xy(via) if via is not None else None
        if fw and tw and len(fw.nodes) >= 2:
            shared = [n for n in dict.fromkeys((fw.nodes[0], fw.nodes[-1])) if n in self.doc.nodes
                      and restriction_way_matches(fw, "from", n) and restriction_way_matches(tw, "to", n)]
            if shared and via not in shared and (point is not None or len(shared) == 1):
                node = min(shared, key=lambda n: dist(point, self.xy(n))) if point is not None else shared[0]
                return {"label": f"Исправить via → узел {node}", "op": "set_restriction_via",
                        "args": {"relation_id": relation_id, "node_id": node}}
        if via is None or point is None:
            return None
        from_matches = restriction_way_matches(fw, "from", via)
        to_matches = restriction_way_matches(tw, "to", via)
        if from_matches == to_matches:
            return None
        role, broken, original, other = ("to", tos, tw, fw) if from_matches else ("from", froms, fw, tw)
        candidates = []
        for wid in self.road_node_ways.get(via, []):
            way = self.doc.ways[wid]
            if wid == other.id or not restriction_way_matches(way, role, via):
                continue
            if original is not None and way_level(original.tags) != way_level(way.tags):
                continue
            candidates.append(way)
        if len(candidates) != 1:
            # A missing reference carries no geometry to distinguish several roads at the same junction.
            return None
        wid = candidates[0].id
        return {"label": f"Исправить {role} → ребро {wid}", "op": "replace_restriction_way",
                "args": {"relation_id": relation_id, "role": role, "old_id": broken[0].ref if broken else None,
                         "way_id": wid, "via": via}}

    def check_restrictions(self) -> None:
        """Turn restrictions SUMO can apply: one from, one to, a via they both end at.

        In an extract, restrictions often reference ways outside it; netconvert then
        ignores the relation («Ignoring restriction relation … unknown from-way»).
        """
        for rel in self.doc.relations.values():
            if not rel.tags.get("type", "").startswith("restriction"):
                continue
            froms = [m for m in rel.members if m.role == "from"]
            tos = [m for m in rel.members if m.role == "to"]
            vias = [m for m in rel.members if m.role == "via"]
            problems: list[str] = []
            if len(froms) != 1 or len(tos) != 1:
                problems.append(f"участников from: {len(froms)}, to: {len(tos)} (нужно по одному)")
            missing = [m.ref for m in froms + tos + vias
                       if (m.type == "way" and m.ref not in self.doc.ways)
                       or (m.type == "node" and m.ref not in self.doc.nodes)]
            if missing:
                problems.append(f"ссылается на объекты вне выгрузки: {', '.join(map(str, sorted(set(missing))))}")
            ways = [self.doc.ways.get(m.ref) for m in froms + tos if m.type == "way"]
            if any(w is not None and not is_drivable(w.tags) for w in ways):
                problems.append("from или to — не проезжая дорога")
            if not vias:
                problems.append("нет узла via")
            if not problems and len(vias) == 1 and vias[0].type == "node":
                fw, tw = self.doc.ways.get(froms[0].ref), self.doc.ways.get(tos[0].ref)
                via = vias[0].ref
                if fw is not None and tw is not None and fw.nodes and tw.nodes:
                    ends_f, ends_t = {fw.nodes[0], fw.nodes[-1]}, {tw.nodes[0], tw.nodes[-1]}
                    if via not in ends_f or via not in ends_t:
                        problems.append("узел via не на концах обоих рёбер — SUMO не поймёт, где поворот")
            if not problems:
                continue
            kind = rel.tags.get("restriction") or rel.tags.get("type")
            node = next((m.ref for m in vias if m.type == "node" and m.ref in self.doc.nodes), None)
            way = next((w for w in ways if w is not None), None)
            loc = self.loc(node) if node else (self.loc(way.nodes[len(way.nodes) // 2]) if way else None)
            repair = self.restriction_fix(rel.id, froms, tos, vias)
            fixes = [repair] if repair else []
            fixes.append({"label": "Удалить отношение", "op": "delete", "args": {"kind": "relation", "id": rel.id}})
            self.add(category="junction", severity="warning", title="Запрет поворота не применится",
                     description=f"Отношение {rel.id} ({kind}): {'; '.join(problems)}. "
                                 "SUMO пропустит такой запрет, и машины будут поворачивать там, где нельзя.",
                     refs=[{"kind": "relation", "id": rel.id}] + ([{"kind": "node", "id": node}] if node else []),
                     location=loc, levels=["sumo"], fixes=fixes)

    def check_geometry(self) -> None:
        for w in self.doc.ways.values():
            layer = w.tags.get("layer")
            if layer is not None:
                try:
                    int(layer)
                except ValueError:
                    self.add(category="geometry", severity="warning", title="layer не целое число",
                             description=f"{self.name(w.id)}: layer={layer}. Webots поднимает полотно по целому layer.",
                             refs=[{"kind": "way", "id": w.id}],
                             location=self.loc(w.nodes[len(w.nodes) // 2]) if w.nodes else None,
                             levels=["webots"])
            bridge = w.tags.get("bridge") not in (None, "no")
            tunnel = w.tags.get("tunnel") not in (None, "no")
            if bridge and tunnel and w.nodes:
                self.add(category="geometry", severity="warning", title="Мост и тоннель на одном ребре",
                         description=f"{self.name(w.id)} помечен и bridge, и tunnel — Webots не выберет высоту.",
                         refs=[{"kind": "way", "id": w.id}],
                         location=self.loc(w.nodes[len(w.nodes) // 2]), levels=["webots"])
            if is_drivable(w.tags):
                prev: tuple[float, float] | None = None
                for ref in w.nodes:
                    n = self.doc.nodes.get(ref)
                    if n is None:
                        continue
                    cur = (n.lat, n.lon)
                    if prev is not None and prev == cur:
                        self.add(category="geometry", severity="warning", title="Нулевая длина сегмента",
                                 description=f"{self.name(w.id)} содержит подряд два узла в одной точке. "
                                             "Webots и SUMO строят из этого вырожденное полотно.",
                                 refs=[{"kind": "way", "id": w.id}], location=[n.lon, n.lat],
                                 levels=["webots", "sumo"])
                        break
                    prev = cur
            if w.tags.get("bridge") not in (None, "no") and "layer" not in w.tags and w.nodes:
                self.add(category="geometry", severity="warning", title="Мост без layer",
                         description=f"{self.name(w.id)}: bridge без layer — Webots положит полотно на землю.",
                         refs=[{"kind": "way", "id": w.id}], location=self.loc(w.nodes[len(w.nodes) // 2]),
                         levels=["webots"],
                         fixes=[
                             {"label": "layer=1", "op": "set_tags",
                              "args": {"kind": "way", "id": w.id, "tags": {"layer": "1"}}},
                             {"label": "Убрать мост", "op": "set_tags",
                              "args": {"kind": "way", "id": w.id, "tags": {"bridge": None}}},
                         ])
            if w.tags.get("tunnel") not in (None, "no") and "layer" not in w.tags and w.nodes:
                self.add(category="geometry", severity="info", title="Тоннель без layer",
                         description=f"{self.name(w.id)}: tunnel без layer.", refs=[{"kind": "way", "id": w.id}],
                         location=self.loc(w.nodes[0]), levels=["webots"],
                         fixes=[
                             {"label": "layer=-1", "op": "set_tags",
                              "args": {"kind": "way", "id": w.id, "tags": {"layer": "-1"}}},
                             {"label": "Убрать тоннель", "op": "set_tags",
                              "args": {"kind": "way", "id": w.id, "tags": {"tunnel": None}}},
                         ])

    def check_elevation(self) -> None:
        """Z jumps: a bridge raised with ele next to ground nodes, or a level change with no ramp."""
        has_ele = {nid for nid, n in self.doc.nodes.items() if "ele" in n.tags}

        def ele(nid: int) -> float:
            try:
                return float(self.doc.nodes[nid].tags.get("ele", "0").replace(",", "."))
            except (KeyError, ValueError):
                return 0.0

        reported: set[int] = set()
        for w in self.road_ways:
            level = way_level(w.tags)
            if level != 0 and not has_ele.intersection(w.nodes):
                self.add(category="geometry", severity="info", title="Резкий Z-переход",
                         description=f"{self.name(w.id)}: уровень {level}, но у узлов нет высоты ele — "
                                     "съезд с моста получится ступенькой.",
                         refs=[{"kind": "way", "id": w.id}], location=self.loc(w.nodes[len(w.nodes) // 2]),
                         levels=["webots"],
                         fix={"label": "Плавный Z", "op": "smooth_z", "args": {"way_id": w.id}})
                continue
            if not has_ele.intersection(w.nodes):
                continue
            for a, b in zip(w.nodes, w.nodes[1:]):
                pa, pb = self.xy(a), self.xy(b)
                if pa is None or pb is None:
                    continue
                run = dist(pa, pb)
                rise = abs(ele(a) - ele(b))
                limit = max_grade(w.tags)
                # ele is stored to the centimetre: allow that rounding on short segments
                if run < 0.5 or (rise - 0.01) / run <= limit or w.id in reported:
                    continue
                reported.add(w.id)
                bridge = next((o for o in (self.doc.ways.get(x) for x in self.road_node_ways.get(a, []) +
                                           self.road_node_ways.get(b, []))
                               if o is not None and way_level(o.tags) != 0), None)
                self.add(category="geometry", severity="warning", title="Резкий перепад высоты",
                         description=f"{self.name(w.id)}: уклон {rise / run * 1000:.0f} ‰ на {run:.0f} м "
                                     f"(для {w.tags.get('highway')} допустимо до {limit * 1000:.0f} ‰, {GRADE_SOURCE}).",
                         refs=[{"kind": "way", "id": w.id}], location=self.loc(a), levels=["webots"],
                         fix={"label": "Плавный Z", "op": "smooth_z", "args": {"way_id": bridge.id}} if bridge else None)

    # ------------------------------------------------------- fixes & entrances
    def _prepare_entrances(self) -> None:
        self._entrance_idx = GridIndex(cell=20.0)
        self._entrance_nodes: set[int] = set()
        for n in self.doc.nodes.values():
            if not _is_entrance(n.tags):
                continue
            self._entrance_nodes.add(n.id)
            p = self.xy(n.id)
            if p:
                self._entrance_idx.insert_point(p[0], p[1], p)
        self._entrance_building_nodes: set[int] = set()
        for w in self.doc.ways.values():
            if "building" not in w.tags:
                continue
            nodes = [r for r in dict.fromkeys(w.nodes) if r in self.doc.nodes]
            if not any(r in self._entrance_nodes or self._near_entrance(r) for r in nodes):
                continue
            for r in nodes:
                if r in self._entrance_nodes or self._near_entrance(r):
                    self._entrance_building_nodes.add(r)

    def _near_entrance(self, nid: int) -> bool:
        p = self.xy(nid)
        if p is None:
            return False
        return any(dist(p, e) <= ENTRANCE_M for e in self._entrance_idx.query(p[0], p[1], ENTRANCE_M))

    def _at_entrance(self, nid: int) -> bool:
        n = self.doc.nodes.get(nid)
        if n is not None and _is_entrance(n.tags):
            return True
        if nid in self._entrance_building_nodes:
            return True
        return self._near_entrance(nid)

    def _exempt_access_end(self, nid: int) -> bool:
        """Driveway/sidewalk endpoint at a door: not a connectivity error."""
        wids = self.road_node_ways.get(nid, [])
        if len(wids) != 1:
            return False
        w = self.doc.ways.get(wids[0])
        if w is None or not is_access_way(w.tags) or w.is_closed:
            return False
        if nid not in (w.nodes[0], w.nodes[-1]):
            return False
        return self._at_entrance(nid)

    def _access_component(self, ways: list[int], nodes: set[int]) -> bool:
        if not ways or not all(is_access_way(self.doc.ways[w].tags) for w in ways if w in self.doc.ways):
            return False
        return any(self._exempt_access_end(n) for n in nodes)

    def _fully_dangling(self, w) -> bool:
        if w.is_closed or len(w.nodes) < 2:
            return False
        return all(len(self.road_node_ways.get(n, [])) <= 1 for n in (w.nodes[0], w.nodes[-1]))

    def _nearest_way(self, nid: int, exclude: set[int]) -> tuple[float, int] | None:
        p = self.xy(nid)
        if p is None:
            return None
        src = [self.doc.ways[w] for w in self.road_node_ways.get(nid, []) if w in self.doc.ways]
        best: tuple[float, int] | None = None
        for w in self.road_ways:
            if w.id in exclude or nid in w.nodes:
                continue
            if any(can_connect(s.tags, w.tags) for s in src):
                continue
            for a, b in zip(w.nodes, w.nodes[1:]):
                pa, pb = self.xy(a), self.xy(b)
                if pa is None or pb is None:
                    continue
                _q, _t, d = project_on_segment(p, pa, pb)
                if d <= CONNECT_FIX_M and (best is None or d < best[0]):
                    best = (d, w.id)
        return best

    def _connect_fix(self, wid: int) -> dict[str, Any] | None:
        w = self.doc.ways.get(wid)
        if w is None or w.is_closed or len(w.nodes) < 2:
            return None
        best: tuple[float, int, int] | None = None
        for nid in (w.nodes[0], w.nodes[-1]):
            near = self._nearest_way(nid, {wid})
            if near is not None and (best is None or near[0] < best[0]):
                best = (near[0], nid, near[1])
        if best is None:
            return None
        _d, nid, target = best
        return {"label": "Соединить", "op": "connect_endpoint", "args": {"node_id": nid, "way_id": target}}

    def _oneway_fixes(self, nid: int, wid: int) -> list[dict[str, Any]]:
        fixes: list[dict[str, Any]] = [
            {"label": "Сделать двусторонним", "op": "set_tags",
             "args": {"kind": "way", "id": wid, "tags": {"oneway": None}}},
            {"label": "Развернуть направление", "op": "reverse_way", "args": {"id": wid}},
        ]
        near = self._nearest_way(nid, {wid})
        if near is not None:
            fixes.append({"label": "Соединить с ближайшей", "op": "connect_endpoint",
                          "args": {"node_id": nid, "way_id": near[1]}})
        return fixes

    def _trap_fixes(self, nodes: set[int]) -> list[dict[str, Any]]:
        for nid in sorted(nodes):
            for wid in self.road_node_ways.get(nid, []):
                w = self.doc.ways.get(wid)
                if w is None or direction(w.tags) == 0 or not w.nodes:
                    continue
                end = w.nodes[-1] if direction(w.tags) > 0 else w.nodes[0]
                if end in nodes:
                    return self._oneway_fixes(end, wid)
        return []

    def check_connections(self) -> None:
        """Warn where two highways already share a node they are not allowed to."""
        seen: set[tuple[int, int]] = set()
        index: dict[int, list[int]] = {}
        for w in self.doc.ways.values():
            if "highway" not in w.tags or w.tags.get("area") == "yes" or len(w.nodes) < 2:
                continue
            for ref in dict.fromkeys(w.nodes):
                index.setdefault(ref, []).append(w.id)
        for nid, wids in index.items():
            if len(wids) < 2:
                continue
            ways = [self.doc.ways[w] for w in wids]
            for i, a in enumerate(ways):
                for b in ways[i + 1:]:
                    if a.id == b.id:
                        continue
                    msg = can_connect(a.tags, b.tags)
                    if not msg:
                        continue
                    key = (min(a.id, b.id), max(a.id, b.id))
                    if key in seen:
                        continue
                    seen.add(key)
                    lo, hi = (a, b) if a.tags.get("highway") != "motorway" else (b, a)
                    fixes = []
                    pedestrian = {"footway", "pedestrian", "path", "cycleway", "steps", "bridleway", "corridor"}
                    if hi.tags.get("highway") == "motorway" and lo.tags.get("highway") not in pedestrian:
                        fixes.append({
                            "label": "Сделать съездом", "op": "set_tags",
                            "args": {"kind": "way", "id": lo.id, "tags": {"highway": "motorway_link"}},
                        })
                    self.add(category="junction", severity="warning", title="Недопустимое соединение",
                             description=f"{msg}: {self.name(a.id)} и {self.name(b.id)} имеют общий узел.",
                             refs=[{"kind": "node", "id": nid}, {"kind": "way", "id": a.id}, {"kind": "way", "id": b.id}],
                             location=self.loc(nid), levels=["sumo"], fixes=fixes)

    def check_signals_and_stops(self) -> None:
        any_way_nodes = {r for w in self.doc.ways.values() for r in w.nodes}
        seg_idx = None
        for n in self.doc.nodes.values():
            hw = n.tags.get("highway")
            if hw == "traffic_signals" and n.id in self.road_node_ways and not signal_spot(self.doc, n.id):
                self.add(category="junction", severity="warning", title="Светофор не на перекрёстке",
                         description="Светофор стоит посреди перегона: SUMO создаст отдельный регулируемый узел "
                                     "и остановит поток без пересекающего движения.",
                         refs=[{"kind": "node", "id": n.id}], location=[n.lon, n.lat], levels=["sumo"],
                         fix={"label": "Перенести на перекрёсток", "op": "place_signal", "args": {"move_from": n.id}})
                continue
            if hw in ("traffic_signals", "crossing") and n.id not in self.road_node_ways:
                if seg_idx is None:
                    seg_idx = self._segment_index()
                p = self.proj.to_xy(n.lat, n.lon)
                near = [project_on_segment(p, self.xy(a), self.xy(b))[2]  # type: ignore[arg-type]
                        for (_w, a, b) in seg_idx.query(p[0], p[1], ATTACH_MAX_M)]
                close = bool(near) and min(near) <= ATTACH_MAX_M
                fix = {"label": "Привязать к дороге", "op": "attach_to_road", "args": {"node_id": n.id}} if close else None
                if hw == "traffic_signals":
                    self.add(category="junction", severity="warning", title="Светофор вне дороги",
                             description="Узел traffic_signals не стоит на проезжей части — SUMO его не увидит."
                                         + (f" Ближайшая дорога в {min(near):.0f} м." if close else ""),
                             refs=[{"kind": "node", "id": n.id}], location=[n.lon, n.lat], levels=["sumo"], fix=fix)
                elif close and n.id not in any_way_nodes:
                    self.add(category="junction", severity="warning", title="Переход вне проезжей части",
                             description=f"Пешеходный переход не стоит на дороге (ближайшая в {min(near):.0f} м) — "
                                         "SUMO не создаст переход.",
                             refs=[{"kind": "node", "id": n.id}], location=[n.lon, n.lat], levels=["sumo"], fix=fix)
            elif hw == "bus_stop" or n.tags.get("public_transport") == "platform":
                if n.id in any_way_nodes:
                    continue
                if seg_idx is None:
                    seg_idx = self._segment_index()
                p = self.proj.to_xy(n.lat, n.lon)
                near = [project_on_segment(p, self.xy(a), self.xy(b))[2]  # type: ignore[arg-type]
                        for (_w, a, b) in seg_idx.query(p[0], p[1], STOP_FAR_M)]
                if not near or min(near) > STOP_FAR_M:
                    self.add(category="geometry", severity="info", title="Остановка далеко от дороги",
                             description=f"{n.tags.get('name', 'Остановка')}: дальше {STOP_FAR_M:.0f} м от проезжей части.",
                             refs=[{"kind": "node", "id": n.id}], location=[n.lon, n.lat], levels=["matsim"])
