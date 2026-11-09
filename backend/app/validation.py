"""Graph checks that predict failures in MatSim / SUMO / Webots."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import networkx as nx

from .geo import GridIndex, LocalProjection, dist, project_on_segment
from .osm_model import OsmDocument
from .roads import ONEWAY_VALID, default_turns, direction, is_drivable, parse_int
from .edits import ATTACH_MAX_M, way_level

Severity = Literal["critical", "warning", "info"]
Category = Literal["connectivity", "direction", "junction", "lanes", "geometry", "data"]

GAP_M = 8.0          # dangling end closer than this to another road = broken junction
CLOSE_JUNCTION_M = 2.0
BOUNDARY_M = 40.0    # dead ends near the extract border are expected
STOP_FAR_M = 25.0
MAX_GRADE = 0.12    # steeper than 12 % between two nodes is a Z jump


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
    fix: dict[str, Any] | None = None    # {"label", "op", "args"}
    levels: list[str] = field(default_factory=list)  # affected: matsim/sumo/webots


def validate(doc: OsmDocument) -> dict[str, Any]:
    v = _Validator(doc)
    v.run()
    issues = sorted(v.issues, key=lambda i: {"critical": 0, "warning": 1, "info": 2}[i.severity])
    return {
        "issues": [asdict(i) for i in issues],
        "summary": _summary(issues, v),
    }


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
        return min(abs(p[0] - x0), abs(p[0] - x1), abs(p[1] - y0), abs(p[1] - y1)) < BOUNDARY_M

    def name(self, wid: int) -> str:
        w = self.doc.ways.get(wid)
        if not w:
            return f"way/{wid}"
        return w.tags.get("name") or f"{w.tags.get('highway', 'ребро')} way/{wid}"

    def add(self, **kw: Any) -> None:
        kw.setdefault("id", f"{kw['category']}-{len(self.issues)}")
        self.issues.append(Issue(**kw))

    # ------------------------------------------------------------------ run
    def run(self) -> None:
        self.check_data()
        self.check_connectivity()
        self.check_strong_connectivity()
        self.check_gaps()
        self.check_close_junctions()
        self.check_directions()
        self.check_lanes()
        self.check_geometry()
        self.check_elevation()
        self.check_signals_and_stops()

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
                         fix={"label": "oneway=yes", "op": "set_tags",
                              "args": {"kind": "way", "id": w.id, "tags": {"oneway": "yes"}}})

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
            first = next(iter(comp))
            single = len(ways) == 1
            self.add(category="connectivity", severity="critical",
                     title="Изолированное ребро" if single else "Изолированный фрагмент сети",
                     description=(f"{self.name(ways[0])} не связано с основной сетью" if single
                                  else f"{len(ways)} рёбер не связаны с основной сетью")
                                 + " — MatSim не построит маршрут, SUMO отбросит фрагмент.",
                     refs=[{"kind": "way", "id": w} for w in ways[:20]], location=self.loc(first),
                     levels=["matsim", "sumo"],
                     fix={"label": "Удалить", "op": "delete", "args": {"kind": "way", "id": ways[0]}} if single else None)

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
            p = self.xy(nid)
            if p is None:
                continue
            best = None
            for (owid, a, b) in seg_idx.query(p[0], p[1], GAP_M):
                if owid == w.id:
                    continue
                pa, pb = self.xy(a), self.xy(b)
                _, _, d = project_on_segment(p, pa, pb)  # type: ignore[arg-type]
                if d <= GAP_M and (best is None or d < best[0]):
                    best = (d, owid)
            if best is None:
                continue
            d, owid = best
            self.add(category="junction", severity="warning", title="Разорванный перекрёсток",
                     description=f"{self.name(w.id)} заканчивается в {d:.1f} м от {self.name(owid)} "
                                 "без общего узла.",
                     refs=[{"kind": "node", "id": nid}, {"kind": "way", "id": owid}], location=self.loc(nid),
                     levels=["matsim", "sumo"],
                     fix={"label": "Соединить", "op": "connect_endpoint", "args": {"node_id": nid, "way_id": owid}})

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
        sccs = [set(c) for c in nx.strongly_connected_components(dg)]
        sccs.sort(key=len, reverse=True)
        self.strong_component_count = len(sccs)
        self.outside_strong = sum(len(c) for c in sccs[1:])
        if len(sccs) == 1:
            return
        cond = nx.condensation(dg, sccs)
        reachable = nx.descendants(cond, 0)
        reaches_main = nx.ancestors(cond, 0)
        groups: dict[str, list[set[int]]] = {"trap": [], "unreachable": [], "island": []}
        for i, comp in enumerate(sccs[1:], start=1):
            # Separate undirected pieces are already reported as isolated fragments.
            if self.main_nodes and not (comp & self.main_nodes):
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
        self.add(category="connectivity", severity=severity, title=title,
                 description=f"{len(nodes)} {noun}: {why}. MatSim не построит обратный маршрут, "
                             "в SUMO машины останутся в этом кармане.",
                 refs=[{"kind": "node", "id": ordered[0]}] + [{"kind": "way", "id": w} for w in sorted(ways)[:12]],
                 location=locs[0] if locs else None, locations=locs[:800] or None,
                 levels=["matsim", "sumo"])

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
        for nid in dg.nodes:
            ind, outd = dg.in_degree(nid), dg.out_degree(nid)
            if (ind == 0) == (outd == 0):
                continue
            wid = self.road_node_ways.get(nid, [None])[0]
            if wid is None or direction(self.doc.ways[wid].tags) == 0 and len(self.road_node_ways[nid]) == 1:
                continue  # plain two-way dead end: fine for simulation
            boundary = self.near_boundary(nid)
            kind = "Тупик: въезд без выезда" if outd == 0 else "Узел без въезда"
            self.add(category="direction", severity="info" if boundary else "critical", title=kind,
                     description=("Узел на границе выгрузки — проверьте, что поток уходит за пределы участка."
                                  if boundary else
                                  "Односторонние рёбра сходятся так, что машины застрянут (SUMO) или маршрут "
                                  "не построится (MatSim)."),
                     refs=[{"kind": "node", "id": nid}] + [{"kind": "way", "id": w} for w in self.road_node_ways.get(nid, [])],
                     location=self.loc(nid), levels=["matsim", "sumo"],
                     fix={"label": "Сделать двусторонним", "op": "set_tags",
                          "args": {"kind": "way", "id": wid, "tags": {"oneway": None}}})

    def check_lanes(self) -> None:
        for w in self.road_ways:
            t = w.tags
            lanes = parse_int(t.get("lanes"))
            fw, bw = parse_int(t.get("lanes:forward")), parse_int(t.get("lanes:backward"))
            loc = self.loc(w.nodes[len(w.nodes) // 2])
            ref = [{"kind": "way", "id": w.id}]
            if "lanes" in t and lanes is None:
                self.add(category="lanes", severity="warning", title="lanes не число",
                         description=f"lanes={t['lanes']} на {self.name(w.id)}.", refs=ref, location=loc,
                         levels=["sumo"])
            if lanes is not None and fw is not None and bw is not None and fw + bw != lanes and direction(t) == 0:
                self.add(category="lanes", severity="warning", title="lanes ≠ forward + backward",
                         description=f"lanes={lanes}, а lanes:forward + lanes:backward = {fw + bw}.",
                         refs=ref, location=loc, levels=["sumo"],
                         fix={"label": f"lanes={fw + bw}", "op": "set_tags",
                              "args": {"kind": "way", "id": w.id, "tags": {"lanes": str(fw + bw)}}})
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
                         fix={"label": "layer=1", "op": "set_tags",
                              "args": {"kind": "way", "id": w.id, "tags": {"layer": "1"}}})
            if w.tags.get("tunnel") not in (None, "no") and "layer" not in w.tags and w.nodes:
                self.add(category="geometry", severity="info", title="Тоннель без layer",
                         description=f"{self.name(w.id)}: tunnel без layer.", refs=[{"kind": "way", "id": w.id}],
                         location=self.loc(w.nodes[0]), levels=["webots"],
                         fix={"label": "layer=-1", "op": "set_tags",
                              "args": {"kind": "way", "id": w.id, "tags": {"layer": "-1"}}})

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
                if run < 0.5 or rise / run <= MAX_GRADE or w.id in reported:
                    continue
                reported.add(w.id)
                bridge = next((o for o in (self.doc.ways.get(x) for x in self.road_node_ways.get(a, []) +
                                           self.road_node_ways.get(b, []))
                               if o is not None and way_level(o.tags) != 0), None)
                self.add(category="geometry", severity="warning", title="Резкий перепад высоты",
                         description=f"{self.name(w.id)}: уклон {rise / run:.0%} на {run:.0f} м "
                                     f"(допустимо до {MAX_GRADE:.0%}).",
                         refs=[{"kind": "way", "id": w.id}], location=self.loc(a), levels=["webots"],
                         fix={"label": "Плавный Z", "op": "smooth_z", "args": {"way_id": bridge.id}} if bridge else None)

    def check_signals_and_stops(self) -> None:
        any_way_nodes = {r for w in self.doc.ways.values() for r in w.nodes}
        seg_idx = None
        for n in self.doc.nodes.values():
            hw = n.tags.get("highway")
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
