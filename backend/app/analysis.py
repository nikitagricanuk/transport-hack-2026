"""Static road-load analysis, network statistics and before/after changes.

Load model (a teaching tool, not a microsimulation):
* capacity = lanes x per-lane capacity of the road class (veh/h);
* volume   = what the user set for the road, otherwise a class default
             (a typical share of capacity), times a global demand multiplier;
* v/c ratio -> level of service A..F;
* travel time by the BPR function t = t0 * (1 + 0.15 * (v/c)^4).

Editing the graph (lanes, class, speed) changes capacity, editing the load
changes volume, and snapshots of both can be compared side by side.
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Any

from .geo import LocalProjection
from .norms import speed_kmh
from .osm_model import OsmDocument, Way, clone
from .roads import direction, is_drivable, parse_int

# Scenario assumptions for capacity per lane, veh/h. Not calibrated to observed traffic.
LANE_CAPACITY = {
    "motorway": 1800, "trunk": 1500, "primary": 900, "secondary": 800, "tertiary": 700,
    "unclassified": 500, "residential": 500, "living_street": 200, "service": 300, "road": 500,
    "motorway_link": 1200, "trunk_link": 1000, "primary_link": 800, "secondary_link": 700, "tertiary_link": 600,
}
# Assumed share of capacity used when traffic counts are unavailable.
DEFAULT_VC = {
    "motorway": 0.6, "trunk": 0.6, "primary": 0.55, "secondary": 0.5, "tertiary": 0.4,
    "unclassified": 0.25, "residential": 0.2, "living_street": 0.1, "service": 0.1, "road": 0.2,
}
# Fallback scenario speeds in km/h. Motorway and trunk values are modelling assumptions.
DEFAULT_SPEED = {
    "motorway": 90, "trunk": 70, "primary": 60, "secondary": 60, "tertiary": 60, "unclassified": 60,
    "residential": 60, "living_street": 20, "service": 20, "road": 60,
}
# ODM 218.2.020-2012, table 1: traffic load coefficient z.
# https://meganorm.ru/Data2/1/4293787/4293787897.htm
LOS_BOUNDS = [(0.20, "A"), (0.45, "B"), (0.70, "C"), (0.90, "D")]
LOS_SOURCE_URL = "https://meganorm.ru/Data2/1/4293787/4293787897.htm"
ANALYTICS_ASSUMPTIONS = (
    "При отсутствии данных учёта объём оценивается по классу дороги. "
    "Пропускная способность на полосу, доля спроса и запасные скорости — допущения сценария. "
    "Задержки рассчитаны по функции BPR с коэффициентами 0,15 и 4. "
    "Приведение грузовых ×2 и автобусов ×2,5, множители времени суток и погоды — допущения, не измерения. "
    "Уровень обслуживания определяется по коэффициенту загрузки из таблицы 1 ОДМ 218.2.020-2012."
)
HIGHWAY_RU = {
    "motorway": "Автомагистраль", "trunk": "Скоростная", "primary": "Магистральная", "secondary": "Районная",
    "tertiary": "Местная", "unclassified": "Прочая", "residential": "Жилая улица", "living_street": "Жилая зона",
    "service": "Проезд", "road": "Дорога",
}


def projection(doc: OsmDocument) -> LocalProjection:
    b = doc.bounds() or (0.0, 0.0, 0.0, 0.0)
    return LocalProjection((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def way_length_m(doc: OsmDocument, w: Way, proj: LocalProjection) -> float:
    pts = [proj.to_xy(doc.nodes[r].lat, doc.nodes[r].lon) for r in w.nodes if r in doc.nodes]
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def polygon_area_m2(doc: OsmDocument, w: Way, proj: LocalProjection) -> float:
    pts = [proj.to_xy(doc.nodes[r].lat, doc.nodes[r].lon) for r in w.nodes if r in doc.nodes]
    if len(pts) < 4:
        return 0.0
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:]))) / 2


def _base_class(highway: str) -> str:
    """primary_link -> primary, for the default speed and load share."""
    return highway.removesuffix("_link")


def _lanes(tags: dict[str, str]) -> int:
    n = parse_int(tags.get("lanes"))
    if n and n > 0:
        return n
    return 1 if direction(tags) != 0 else 2


def _speed(tags: dict[str, str]) -> float:
    """maxspeed in km/h (numbers, mph, RU:urban = 60, RU:living_street = 20), else the class default."""
    v = speed_kmh(tags.get("maxspeed"))
    if v:
        return float(v)
    return float(DEFAULT_SPEED.get(_base_class(tags.get("highway", "")), 60))


def los(vc: float) -> str:
    for bound, letter in LOS_BOUNDS:
        if vc < bound:
            return letter
    return "E" if vc <= 1.0 else "F"


def road_rows(doc: OsmDocument, loads: dict[int, float], multiplier: float,
              transit_pcu: dict[int, float] | None = None,
              capacity_factor: dict[int, float] | None = None) -> list[dict[str, Any]]:
    """transit_pcu: car equivalents of scheduled buses per way (not scaled by the demand
    multiplier); capacity_factor: signal green-share multiplier per way."""
    proj = projection(doc)
    transit_pcu = transit_pcu or {}
    capacity_factor = capacity_factor or {}
    rows: list[dict[str, Any]] = []
    for w in doc.ways.values():
        if not is_drivable(w.tags) or len(w.nodes) < 2:
            continue
        hw = w.tags.get("highway", "")
        lanes = _lanes(w.tags)
        cap = lanes * LANE_CAPACITY.get(hw, 500) * capacity_factor.get(w.id, 1.0)
        custom = w.id in loads
        # Demand belongs to the corridor, not to its lane count: the default comes from the
        # road as it was loaded, so adding a lane lowers v/c instead of attracting more cars.
        src = doc._original.get(("way", w.id))
        src_tags = src.tags if src is not None and is_drivable(src.tags) else w.tags
        src_hw = src_tags.get("highway", "")
        src_cap = _lanes(src_tags) * LANE_CAPACITY.get(src_hw, 500)
        base = loads[w.id] if custom else src_cap * DEFAULT_VC.get(_base_class(src_hw), 0.2)
        transit = transit_pcu.get(w.id, 0.0)
        volume = base * multiplier + transit
        vc = volume / cap if cap else 0.0
        length = way_length_m(doc, w, proj)
        speed = _speed(w.tags)
        t0 = length / (speed / 3.6) if speed else 0.0
        t = t0 * (1 + 0.15 * vc ** 4)
        rows.append({
            "id": w.id, "name": w.tags.get("name", ""), "highway": hw, "lanes": lanes,
            "length_m": round(length, 1), "speed": speed, "capacity": round(cap),
            "volume": round(volume), "base_volume": round(base), "custom": custom, "transit_pcu": round(transit),
            # a road added in the editor has no observed demand: its intensity must be entered
            "new": src is None, "needs_intensity": src is None and not custom,
            "vc": round(vc, 3), "los": los(vc), "t0_s": round(t0, 1), "t_s": round(t, 1),
        })
    return rows


def load_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_len = sum(r["length_m"] for r in rows) or 1.0
    veh_km = sum(r["volume"] * r["length_m"] / 1000 for r in rows)
    veh_h = sum(r["volume"] * r["t_s"] / 3600 for r in rows)
    veh_h0 = sum(r["volume"] * r["t0_s"] / 3600 for r in rows)
    by_los = {letter: 0 for letter in "ABCDEF"}
    for r in rows:
        by_los[r["los"]] += 1
    return {
        "roads": len(rows),
        "length_km": round(total_len / 1000, 2),
        "veh_km": round(veh_km),
        "veh_hours": round(veh_h, 1),
        "delay_hours": round(veh_h - veh_h0, 1),
        "avg_vc": round(sum(r["vc"] * r["length_m"] for r in rows) / total_len, 3),
        "congested_km": round(sum(r["length_m"] for r in rows if r["vc"] >= 0.9) / 1000, 2),
        "overloaded": sum(1 for r in rows if r["vc"] >= 1.0),
        "by_los": by_los,
    }


def make_snapshot(name: str, rows: list[dict[str, Any]], multiplier: float) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:8],
        "name": name.strip() or "Состояние",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "multiplier": multiplier,
        "summary": load_summary(rows),
        "rows": rows,
    }


def compare_snapshots(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """What changed between snapshot a (before) and b (after)."""
    ra = {r["id"]: r for r in a["rows"]}
    rb = {r["id"]: r for r in b["rows"]}
    changed: list[dict[str, Any]] = []
    for wid in sorted(set(ra) | set(rb), key=str):
        x, y = ra.get(wid), rb.get(wid)
        if x and y and (x["volume"], x["capacity"], x["lanes"]) == (y["volume"], y["capacity"], y["lanes"]):
            continue
        ref = y or x
        changed.append({
            "id": wid, "name": ref["name"], "highway": ref["highway"],
            "status": "added" if x is None else "removed" if y is None else "changed",
            "lanes_before": x["lanes"] if x else None, "lanes_after": y["lanes"] if y else None,
            "capacity_before": x["capacity"] if x else None, "capacity_after": y["capacity"] if y else None,
            "volume_before": x["volume"] if x else None, "volume_after": y["volume"] if y else None,
            "vc_before": x["vc"] if x else None, "vc_after": y["vc"] if y else None,
            "los_before": x["los"] if x else None, "los_after": y["los"] if y else None,
            "t_before": x["t_s"] if x else None, "t_after": y["t_s"] if y else None,
        })
    changed.sort(key=lambda c: -abs((c["vc_after"] or 0) - (c["vc_before"] or 0)))
    sa, sb = a["summary"], b["summary"]
    keys = ("veh_km", "veh_hours", "delay_hours", "avg_vc", "congested_km", "overloaded", "roads")
    totals = {k: {"before": sa[k], "after": sb[k], "delta": round(sb[k] - sa[k], 3)} for k in keys}
    return {
        "a": {"id": a["id"], "name": a["name"], "created": a["created"]},
        "b": {"id": b["id"], "name": b["name"], "created": b["created"]},
        "totals": totals,
        "los_before": sa["by_los"], "los_after": sb["by_los"],
        "changed": changed,
    }


# ------------------------------------------------------------ network stats
def _class_label(highway: str) -> str:
    label = HIGHWAY_RU.get(_base_class(highway), highway)
    return f"{label} (съезд)" if highway.endswith("_link") else label


def network_stats(doc: OsmDocument) -> dict[str, Any]:
    proj = projection(doc)
    by_class: dict[str, dict[str, float]] = {}
    buildings = {"count": 0, "footprint_m2": 0.0, "floor_area_m2": 0.0, "with_levels": 0}
    for w in doc.ways.values():
        if is_drivable(w.tags):
            hw = w.tags.get("highway", "road")
            length = way_length_m(doc, w, proj)
            c = by_class.setdefault(hw, {"count": 0, "length_km": 0.0, "lane_km": 0.0})
            c["count"] += 1
            c["length_km"] += length / 1000
            c["lane_km"] += length / 1000 * _lanes(w.tags)
        elif "building" in w.tags and w.is_closed:
            area = polygon_area_m2(doc, w, proj)
            levels = parse_int(w.tags.get("building:levels"))
            buildings["count"] += 1
            buildings["footprint_m2"] += area
            if levels:
                buildings["with_levels"] += 1
                buildings["floor_area_m2"] += area * levels
    classes = sorted(
        ({"highway": k, "label": _class_label(k), "count": int(v["count"]),
          "length_km": round(v["length_km"], 2), "lane_km": round(v["lane_km"], 2)} for k, v in by_class.items()),
        key=lambda r: -r["length_km"],
    )
    nodes = doc.nodes.values()
    return {
        "classes": classes,
        "road_km": round(sum(c["length_km"] for c in classes), 2),
        "lane_km": round(sum(c["lane_km"] for c in classes), 2),
        "buildings": {k: round(v) if isinstance(v, float) else v for k, v in buildings.items()},
        "signals": sum(1 for n in nodes if n.tags.get("highway") == "traffic_signals"),
        "crossings": sum(1 for n in doc.nodes.values() if n.tags.get("highway") == "crossing"),
        "stops": sum(1 for n in doc.nodes.values()
                     if n.tags.get("highway") == "bus_stop" or n.tags.get("public_transport") == "platform"),
        "bridges": sum(1 for w in doc.ways.values() if w.tags.get("bridge") not in (None, "no")),
    }


# ----------------------------------------------------------- before / after
def original_document(doc: OsmDocument) -> OsmDocument:
    """The document as it was loaded (from the snapshot kept for diffing)."""
    orig = OsmDocument()
    orig.root_attrs = dict(doc.root_attrs)
    orig.head = list(doc.head)
    for (kind, eid), el in doc._original.items():
        orig.store(kind)[eid] = clone(el)  # type: ignore[arg-type,index]
    return orig


def changes(doc: OsmDocument) -> list[dict[str, Any]]:
    """Every element that differs from the loaded file, with tag-level diffs."""
    out: list[dict[str, Any]] = []
    keys = set(doc._original) | {(k, e) for k in ("node", "way", "relation") for e in doc.store(k)}  # type: ignore[arg-type]
    for kind, eid in keys:
        before = doc._original.get((kind, eid))
        after = doc.get(kind, eid)  # type: ignore[arg-type]
        if before == after:
            continue
        status = "added" if before is None else "deleted" if after is None else "modified"
        tb = before.tags if before else {}
        ta = after.tags if after else {}
        tag_diff = [{"key": k, "before": tb.get(k), "after": ta.get(k)}
                    for k in sorted(set(tb) | set(ta)) if tb.get(k) != ta.get(k)]
        geometry = False
        if before is not None and after is not None:
            if kind == "node":
                geometry = (before.lat, before.lon) != (after.lat, after.lon)  # type: ignore[union-attr]
            elif kind == "way":
                geometry = before.nodes != after.nodes  # type: ignore[union-attr]
            else:
                geometry = before.members != after.members  # type: ignore[union-attr]
        ref = after or before
        entry: dict[str, Any] = {
            "kind": kind, "id": eid, "status": status, "name": ref.tags.get("name", ""),  # type: ignore[union-attr]
            "highway": ref.tags.get("highway", ""), "tags": tag_diff, "geometry": geometry,  # type: ignore[union-attr]
        }
        if kind == "way":
            # original line for the "before" overlay
            src = before or after
            entry["before_coords"] = _coords(doc, src, original=True) if before else None  # type: ignore[arg-type]
            entry["after_coords"] = _coords(doc, after, original=False) if after else None  # type: ignore[arg-type]
        elif kind == "node":
            entry["before_coords"] = [before.lon, before.lat] if before else None  # type: ignore[union-attr]
            entry["after_coords"] = [after.lon, after.lat] if after else None  # type: ignore[union-attr]
        out.append(entry)
    order = {"added": 0, "modified": 1, "deleted": 2}
    out.sort(key=lambda c: (order[c["status"]], c["kind"], str(c["id"])))
    return out


def _coords(doc: OsmDocument, w: Way, original: bool) -> list[list[float]]:
    pts = []
    for r in w.nodes:
        n = doc._original.get(("node", r)) if original else None
        n = n or doc.nodes.get(r)
        if n is not None:
            pts.append([n.lon, n.lat])  # type: ignore[union-attr]
    return pts


def scenario_rows(rows, doc, settings):
    """Explicit scenario assumptions. PCU coefficients are not claimed as norms."""
    from .norms import road_category
    demand = {'morning':1, 'day':.65, 'evening':1.1, 'night':.15}[settings.get('time_of_day','morning')]
    weather = {'dry':1, 'rain':.85, 'snow':.65}[settings.get('weather','dry')]
    truck = settings.get('truck_share', 0)
    bus = settings.get('bus_share', 0)
    pcu = 1 - truck - bus + 2 * truck + 2.5 * bus
    for r in rows:
        r['category'] = road_category(doc.ways[r['id']].tags)
        r['capacity'] *= weather
        r['volume'] *= demand * pcu
        r['vc'] = r['volume'] / r['capacity'] if r['capacity'] else 0
        r['los'] = los(r['vc'])
        r['t_s'] = r['t0_s'] * (1 + .15 * r['vc'] ** 4)
    return rows


def segment_chains(doc):
    """Aggregate physical edges into maximal chains between non-degree-2 nodes.

    z is the maximum along a chain; this keeps the limiting capacity visible.
    Parallel ways remain separate edges.
    """
    import networkx as nx
    g = nx.MultiGraph()
    proj = projection(doc)
    for w in doc.ways.values():
        if not is_drivable(w.tags):
            continue
        for i,(a,b) in enumerate(zip(w.nodes,w.nodes[1:])):
            if a in doc.nodes and b in doc.nodes and a != b:
                na,nb = doc.nodes[a],doc.nodes[b]
                length = math.dist(proj.to_xy(na.lat,na.lon),proj.to_xy(nb.lat,nb.lon))
                g.add_edge(a,b,key=(w.id,i),way=w.id,length=length)
    visited, out = set(), []
    for start in sorted(g.nodes, key=lambda n: g.degree(n) == 2):
        for _,n,key,e in list(g.edges(start,keys=True,data=True)):
            if key in visited:
                continue
            ways, length, end, edge = set(), 0, n, e
            while True:
                visited.add(key)
                ways.add(edge['way'])
                length += edge['length']
                if g.degree(end) != 2:
                    break
                candidates = [(v,k,d) for _,v,k,d in g.edges(end,keys=True,data=True) if k not in visited]
                if not candidates:
                    break
                end,key,edge = candidates[0]
            out.append(dict(id=len(out),from_node=start,to_node=end,ways=sorted(ways),length_m=length))
    return out


def load_segments(doc, rows, chains=None):
    by_id = {r['id']:r for r in rows}
    result = []
    for chain in segment_chains(doc) if chains is None else chains:
        members = [by_id[w] for w in chain['ways'] if w in by_id]
        if not members:
            continue
        limiting = max(members,key=lambda r:r['vc'])
        result.append({**chain,'vc':limiting['vc'],'los':limiting['los'],
                       'category':limiting['category'],
                       'name':' / '.join(dict.fromkeys(r['name'] for r in members if r['name']))})
    return result


# ------------------------------------------------------------------ explanation
# Formulas of the load model, in the order the numbers are produced (report «Методика»).
METHOD = [
    {"name": "Пропускная способность без светофора", "formula": "c = n · c_пол",
     "where": "n — число полос (тег lanes, иначе 1 на направление); c_пол — пропускная способность полосы "
              "по классу дороги, авт./ч (допущение сценария)"},
    {"name": "Пропускная способность у светофора", "formula": "c = n · s · g / C",
     "where": "s — поток насыщения полосы по классу дороги, авт./ч зелёного (HCM 1900, понижен — допущение); "
              "g — зелёный фазы этого подхода, с; C — цикл светофора, с. Подход с двумя светофорами берёт меньшее g/C"},
    {"name": "Интенсивность", "formula": "q = q_баз · k + q_ОТ",
     "where": "q_баз — данные учёта, введённые вручную, иначе оценка c_исх · (v/c)_тип по классу дороги; "
              "k — множитель спроса сценария; q_ОТ — автобусы маршрутов в приведённых авт. (×2,5)"},
    {"name": "Коэффициент загрузки", "formula": "z = v/c = q / c", "where": ""},
    {"name": "Уровень обслуживания", "formula": "A: z < 0,20; B: < 0,45; C: < 0,70; D: < 0,90; E: < 1,00; F: ≥ 1,00",
     "where": "ОДМ 218.2.020-2012, табл. 1"},
    {"name": "Время проезда", "formula": "t₀ = L / v;  t = t₀ · (1 + 0,15 · z⁴)",
     "where": "L — длина ребра, м; v — разрешённая скорость (maxspeed, иначе по классу, ПДД 10.2); "
              "функция BPR (Bureau of Public Roads, 1964)"},
]


def _num(x: float, digits: int = 0) -> str:
    s = f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")
    return s


def explain(row: dict[str, Any], doc: OsmDocument, signal: dict[str, Any] | None,
            multiplier: float) -> dict[str, Any]:
    """Every number of one road's load row with the formula and the values put into it."""
    row = {**row, "volume": round(row["volume"]), "capacity": round(row["capacity"]),
           "base_volume": round(row["base_volume"]), "vc": round(row["vc"], 3),
           "t0_s": round(row["t0_s"], 1), "t_s": round(row["t_s"], 1)}
    w = doc.ways.get(row["id"])
    tags = w.tags if w is not None else {}
    hw = row["highway"]
    n = row["lanes"]
    steps: list[dict[str, Any]] = []
    lanes_src = "тег lanes" if "lanes" in tags else "по умолчанию: 1 полоса на направление"
    steps.append({"name": "Число полос", "formula": "n", "substitution": lanes_src, "value": n, "unit": "полос"})
    if signal:
        s, g, cyc = signal["sat_flow"], signal["green_s"], signal["cycle_s"]
        cap = n * s * g / cyc
        steps.append({"name": "Светофор на подходе", "formula": "g / C",
                      "substitution": f"{g} с / {cyc} с ({'план задан вручную' if signal['custom'] else 'план по умолчанию'}, "
                                      f"фаза {'главного' if signal['group'] == 'main' else 'пересекающего'} направления, "
                                      f"узел {signal['node_id']})",
                      "value": round(g / cyc, 3), "unit": ""})
        steps.append({"name": "Пропускная способность", "formula": "c = n · s · g / C",
                      "substitution": f"{n} · {_num(s)} · {g} / {cyc}", "value": round(cap), "unit": "авт./ч",
                      "source": "s — поток насыщения по классу (HCM, допущение)"})
    else:
        per = LANE_CAPACITY.get(hw, 500)
        cap = n * per
        steps.append({"name": "Пропускная способность", "formula": "c = n · c_пол",
                      "substitution": f"{n} · {_num(per)} ({HIGHWAY_RU.get(_base_class(hw), hw)})",
                      "value": round(cap), "unit": "авт./ч", "source": "c_пол по классу дороги — допущение сценария"})
    if abs(cap - row["capacity"]) > 1:
        steps.append({"name": "Пропускная способность с учётом сценария", "formula": "c",
                      "substitution": "изменена сценарием (перекрытие, погода, время суток)",
                      "value": row["capacity"], "unit": "авт./ч"})
    if row["custom"]:
        base_src = "данные учёта, введено вручную"
    else:
        share = DEFAULT_VC.get(_base_class(hw), 0.2)
        base_src = f"нет данных учёта: оценка по классу, c_исх · {_num(share, 2)}"
    steps.append({"name": "Базовая интенсивность", "formula": "q_баз", "substitution": base_src,
                  "value": row["base_volume"], "unit": "авт./ч"})
    steps.append({"name": "Интенсивность", "formula": "q = q_баз · k + q_ОТ",
                  "substitution": f"{_num(row['base_volume'])} · {_num(multiplier, 2)} + {_num(row.get('transit_pcu', 0))}",
                  "value": row["volume"], "unit": "авт./ч"})
    steps.append({"name": "Коэффициент загрузки", "formula": "z = q / c",
                  "substitution": f"{_num(row['volume'])} / {_num(row['capacity'])}", "value": row["vc"], "unit": ""})
    steps.append({"name": "Уровень обслуживания", "formula": "по z", "substitution": "ОДМ 218.2.020-2012, табл. 1",
                  "value": row["los"], "unit": ""})
    steps.append({"name": "Время проезда без помех", "formula": "t₀ = L / v",
                  "substitution": f"{_num(row['length_m'])} м / {_num(row['speed'])} км/ч", "value": row["t0_s"], "unit": "с"})
    steps.append({"name": "Время проезда", "formula": "t = t₀ · (1 + 0,15 · z⁴)",
                  "substitution": f"{_num(row['t0_s'], 1)} · (1 + 0,15 · {_num(row['vc'], 2)}⁴)", "value": row["t_s"],
                  "unit": "с", "source": "функция BPR, 1964"})
    return {
        "id": row["id"], "name": row["name"],
        # what the collapsed card shows
        "summary": {"volume": row["volume"], "capacity": row["capacity"], "vc": row["vc"], "los": row["los"],
                    "t_s": row["t_s"], "signal": bool(signal)},
        "steps": steps,
    }
