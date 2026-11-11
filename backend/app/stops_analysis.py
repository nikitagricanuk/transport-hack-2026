"""Public transport stops: coverage of housing, gaps, districts, stop load and advice.

Everything here is an estimate from the OSM file itself, with the assumptions listed in
the response:

* residents of a building = floor area (footprint x levels) / 30 m2 per person;
* a building is covered when a stop is within the walking radius in a straight line
  (SP 42.13330.2016, 11.24: at most 500 m to the nearest stop);
* platforms and stop positions closer than 60 m are one stop;
* residents use the nearest stop; peak-hour boardings = residents x PT trips per
  resident per day x share of the peak hour;
* offered capacity = sum over routes of vehicles/h x nominal vehicle capacity.
"""

from __future__ import annotations

import math
from typing import Any

from .geo import GridIndex, LocalProjection
from .osm_model import OsmDocument
from .roads import is_drivable
from . import transit

STOP_MERGE_M = 60.0
GAP_CELL_M = 300.0
SPACING_MIN_M = 300.0   # below the 400-600 m of SP 42 with a margin
SPACING_MAX_M = 800.0
VEHICLE_CAPACITY = {"bus": 80, "trolleybus": 85, "tram": 130, "minibus": 20, "share_taxi": 20}
SOURCES = [
    {"label": "СП 42.13330.2016, п. 11.24: пешеходный подход до остановки не более 500 м; расстояние между остановками 400–600 м",
     "url": "https://docs.cntd.ru/document/456054209"},
]


def _is_stop(tags: dict[str, str]) -> bool:
    return (tags.get("highway") == "bus_stop" or tags.get("public_transport") in {"platform", "stop_position"}
            or tags.get("railway") == "tram_stop")


def _point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _level(ratio: float) -> str:
    if ratio < 0.3:
        return "низкая"
    if ratio < 0.6:
        return "средняя"
    return "высокая"


LEVEL_COLOUR = {"низкая": "#16a34a", "средняя": "#eab308", "высокая": "#dc2626", "нет маршрутов": "#64748b"}


def analyse(doc: OsmDocument, overrides: dict[int, float], radius_m: float = 500.0,
            trips_per_resident: float = 1.0, peak_share: float = 0.1) -> dict[str, Any]:
    if not doc.nodes:
        return {"stops": [], "summary": {}, "gaps": [], "districts": [], "advice": [], "features": {"type": "FeatureCollection", "features": []}}
    lats = [n.lat for n in doc.nodes.values()]
    lons = [n.lon for n in doc.nodes.values()]
    proj = LocalProjection((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)

    # ---- stops, merged into stop points
    raw = [n for n in doc.nodes.values() if _is_stop(n.tags)]
    clusters: list[dict[str, Any]] = []
    grid = GridIndex(STOP_MERGE_M)
    for n in sorted(raw, key=lambda n: (n.tags.get("public_transport") == "stop_position", n.id)):
        x, y = proj.to_xy(n.lat, n.lon)
        near = [c for c in grid.query(x, y, STOP_MERGE_M) if math.hypot(c["x"] - x, c["y"] - y) <= STOP_MERGE_M]
        if near:
            c = near[0]
            c["nodes"].append(n.id)
            c["name"] = c["name"] or n.tags.get("name", "")
            continue
        c = {"id": n.id, "nodes": [n.id], "name": n.tags.get("name", ""), "x": x, "y": y, "coord": [n.lon, n.lat]}
        clusters.append(c)
        grid.insert_point(x, y, c)
    by_node = {nid: c for c in clusters for nid in c["nodes"]}

    # routes serving each stop point
    routes = [r for r in doc.relations.values() if transit._is_route(r)]
    for c in clusters:
        c["routes"] = {}
    for r in routes:
        freq = transit.route_frequency(r, overrides)
        info = {"id": r.id, "ref": r.tags.get("ref") or r.tags.get("name", "")[:8] or str(r.id),
                "mode": r.tags["route"], "frequency": freq}
        for m in r.members:
            if m.type == "node" and m.ref in by_node:
                by_node[m.ref]["routes"][r.id] = info

    # ---- housing
    buildings = []
    for w in doc.ways.values():
        b = w.tags.get("building")
        if b not in transit.RESIDENTIAL or not w.is_closed:
            continue
        pts = [proj.to_xy(doc.nodes[r].lat, doc.nodes[r].lon) for r in w.nodes if r in doc.nodes]
        if len(pts) < 4:
            continue
        area = abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:]))) / 2
        try:
            levels = int(w.tags.get("building:levels", ""))
        except ValueError:
            levels = transit.DEFAULT_LEVELS.get(b, 3)
        residents = area * max(levels, 1) / transit.M2_PER_PERSON
        if residents < 1:
            continue
        cx = sum(p[0] for p in pts[:-1]) / (len(pts) - 1)
        cy = sum(p[1] for p in pts[:-1]) / (len(pts) - 1)
        buildings.append({"id": w.id, "x": cx, "y": cy, "residents": residents, "ring": pts})

    stop_grid = GridIndex(max(radius_m, 50.0))
    for c in clusters:
        stop_grid.insert_point(c["x"], c["y"], c)
        c["residents"] = 0.0
    covered_res = 0.0
    uncovered: list[dict[str, Any]] = []
    for b in buildings:
        best, best_d = None, math.inf
        for c in stop_grid.query(b["x"], b["y"], radius_m):
            d = math.hypot(c["x"] - b["x"], c["y"] - b["y"])
            if d < best_d:
                best, best_d = c, d
        if best is not None and best_d <= radius_m:
            best["residents"] += b["residents"]
            covered_res += b["residents"]
            b["covered"] = True
        else:
            b["covered"] = False
            uncovered.append(b)
    total_res = sum(b["residents"] for b in buildings)

    # ---- stop load
    stops_out = []
    for c in clusters:
        rs = list(c["routes"].values())
        per_hour = sum(r["frequency"] for r in rs)
        offered = sum(r["frequency"] * VEHICLE_CAPACITY.get(r["mode"], 80) for r in rs)
        demand = c["residents"] * trips_per_resident * peak_share
        ratio = demand / offered if offered else 0.0
        level = _level(ratio) if rs else "нет маршрутов"
        stops_out.append({
            "id": c["id"], "nodes": c["nodes"], "name": c["name"], "coord": c["coord"],
            "routes": sorted({r["ref"] for r in rs}, key=transit._natural),
            "vehicles_per_hour": round(per_hour, 1), "capacity_per_hour": round(offered),
            "residents": round(c["residents"]), "demand_per_hour": round(demand), "load": round(ratio, 2), "level": level,
        })
    stops_out.sort(key=lambda s: (-s["load"], -s["residents"]))

    # ---- gaps: uncovered housing grouped by 300 m cells
    cells: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for b in uncovered:
        cells.setdefault((int(b["x"] // GAP_CELL_M), int(b["y"] // GAP_CELL_M)), []).append(b)
    road_grid = GridIndex(100.0)
    for w in doc.ways.values():
        if not is_drivable(w.tags):
            continue
        for a, bb in zip(w.nodes, w.nodes[1:]):
            if a in doc.nodes and bb in doc.nodes:
                x0, y0 = proj.to_xy(doc.nodes[a].lat, doc.nodes[a].lon)
                x1, y1 = proj.to_xy(doc.nodes[bb].lat, doc.nodes[bb].lon)
                road_grid.insert_box(x0, y0, x1, y1, (w, x0, y0, x1, y1))
    gaps = []
    for group in cells.values():
        res = sum(b["residents"] for b in group)
        gx = sum(b["x"] * b["residents"] for b in group) / res
        gy = sum(b["y"] * b["residents"] for b in group) / res
        nearest_stop = min((math.hypot(c["x"] - gx, c["y"] - gy) for c in clusters), default=None)
        road, road_d, foot = None, math.inf, (gx, gy)
        for w, x0, y0, x1, y1 in road_grid.query(gx, gy, 400.0):
            dx, dy = x1 - x0, y1 - y0
            t = max(0.0, min(1.0, ((gx - x0) * dx + (gy - y0) * dy) / ((dx * dx + dy * dy) or 1e-9)))
            px, py = x0 + t * dx, y0 + t * dy
            d = math.hypot(px - gx, py - gy)
            if d < road_d:
                road, road_d, foot = w, d, (px, py)
        lat, lon = proj.to_latlon(*foot)
        gaps.append({
            "residents": round(res), "buildings": len(group), "coord": [lon, lat],
            "nearest_stop_m": round(nearest_stop) if nearest_stop is not None else None,
            "road_id": road.id if road else None,
            "road_name": (road.tags.get("name") or road.tags.get("highway", "")) if road else "",
        })
    gaps.sort(key=lambda g: -g["residents"])

    # ---- districts: residential landuse and place polygons
    districts = []
    for w in doc.ways.values():
        if not w.is_closed or not (w.tags.get("landuse") == "residential"
                                   or w.tags.get("place") in {"suburb", "quarter", "neighbourhood"}):
            continue
        poly = [proj.to_xy(doc.nodes[r].lat, doc.nodes[r].lon) for r in w.nodes if r in doc.nodes]
        if len(poly) < 4:
            continue
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        inside = [b for b in buildings if min(xs) <= b["x"] <= max(xs) and min(ys) <= b["y"] <= max(ys)
                  and _point_in_poly(b["x"], b["y"], poly)]
        res = sum(b["residents"] for b in inside)
        if res < 50:
            continue
        unc = sum(b["residents"] for b in inside if not b["covered"])
        districts.append({"id": w.id, "name": w.tags.get("name") or f"Жилая территория #{w.id}",
                          "residents": round(res), "uncovered": round(unc),
                          "coverage": round(1 - unc / res, 3), "buildings": len(inside)})
    districts.sort(key=lambda d: (-d["uncovered"], d["coverage"]))

    # ---- spacing along routes
    spacing = []
    seen_pairs: set[tuple[int, int]] = set()
    for r in routes:
        seq: list[dict[str, Any]] = []
        for m in r.members:
            if m.type == "node" and m.ref in by_node and transit._is_stop_role(m.role):
                c = by_node[m.ref]
                if not seq or seq[-1] is not c:
                    seq.append(c)
        ref = r.tags.get("ref") or str(r.id)
        for a, b in zip(seq, seq[1:]):
            key = (min(a["id"], b["id"]), max(a["id"], b["id"]))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            d = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
            if d < SPACING_MIN_M or d > SPACING_MAX_M:
                mid = proj.to_latlon((a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2)
                spacing.append({"route": ref, "a": a["name"] or f"#{a['id']}", "b": b["name"] or f"#{b['id']}",
                                "a_id": a["id"], "b_id": b["id"], "distance_m": round(d),
                                "kind": "close" if d < SPACING_MIN_M else "far", "coord": [mid[1], mid[0]]})

    # ---- advice
    advice: list[dict[str, Any]] = []
    for g in gaps[:5]:
        if g["residents"] < 30:
            break
        where = f"у «{g['road_name']}»" if g["road_name"] else "в этой зоне"
        far = f"; ближайшая остановка в {g['nearest_stop_m']} м" if g["nearest_stop_m"] is not None else ""
        advice.append({"kind": "gap", "severity": "warning",
                       "title": f"≈{g['residents']} жителей дальше {round(radius_m)} м от остановки",
                       "text": f"{g['buildings']} жилых зданий без остановки рядом{far}. Добавить остановку {where} "
                               f"или продлить маршрут.", "coord": g["coord"], "way_id": g["road_id"]})
    for s in stops_out:
        if s["level"] == "высокая":
            advice.append({"kind": "overload", "severity": "warning",
                           "title": f"Остановка {s['name'] or '#' + str(s['id'])} перегружена",
                           "text": f"Спрос ≈{s['demand_per_hour']} пасс./ч при провозной ≈{s['capacity_per_hour']} мест/ч "
                                   f"({', '.join(s['routes'])}). Увеличить частоту маршрутов или добавить маршрут.",
                           "coord": s["coord"], "node_id": s["id"]})
    for s in stops_out:
        if s["level"] == "нет маршрутов" and s["residents"] >= 100:
            advice.append({"kind": "no_routes", "severity": "info",
                           "title": f"Остановка {s['name'] or '#' + str(s['id'])} без маршрутов в файле",
                           "text": f"Рядом ≈{s['residents']} жителей, но через остановку не проходит ни один маршрут (relation route). "
                                   "Проверить маршруты или включить остановку в маршрут.",
                           "coord": s["coord"], "node_id": s["id"]})
    for sp in spacing[:8]:
        if sp["kind"] == "close":
            text = f"Маршрут {sp['route']}: {sp['a']} → {sp['b']} — {sp['distance_m']} м. Норма 400–600 м: остановки можно объединить."
        else:
            text = f"Маршрут {sp['route']}: {sp['a']} → {sp['b']} — {sp['distance_m']} м. Больше 600 м: стоит добавить промежуточную остановку."
        advice.append({"kind": "spacing", "severity": "info", "title": "Расстояние между остановками", "text": text,
                       "coord": sp["coord"], "node_id": sp["a_id"]})
    for d in districts[:3]:
        if d["uncovered"] >= 50:
            advice.append({"kind": "district", "severity": "info",
                           "title": f"{d['name']}: покрыто {round(d['coverage'] * 100)} %",
                           "text": f"≈{d['uncovered']} из {d['residents']} жителей дальше {round(radius_m)} м от остановки.",
                           "way_id": d["id"]})

    # ---- map
    features: list[dict[str, Any]] = []
    for b in uncovered:
        ring = [[*reversed(proj.to_latlon(x, y))] for x, y in b["ring"]]
        features.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                         "properties": {"set": "uncovered", "colour": "#dc2626"}})
    for s in stops_out:
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": s["coord"]},
                         "properties": {"colour": LEVEL_COLOUR[s["level"]],
                                        "label": f"{round(s['load'] * 100)}%" if s["level"] == "высокая" else ""}})
    for g in gaps[:5]:
        if g["residents"] >= 30:
            features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": g["coord"]},
                             "properties": {"colour": "#7c3aed", "label": f"+{g['residents']} жит."}})

    summary = {
        "stops": len(clusters), "stop_nodes": len(raw), "routes": len(routes),
        "residents": round(total_res), "covered": round(covered_res), "uncovered": round(total_res - covered_res),
        "coverage": round(covered_res / total_res, 3) if total_res else None,
        "buildings": len(buildings), "uncovered_buildings": len(uncovered),
        "by_level": {lv: sum(1 for s in stops_out if s["level"] == lv) for lv in LEVEL_COLOUR},
    }
    return {
        "summary": summary, "stops": stops_out, "gaps": gaps[:20], "districts": districts[:20],
        "spacing": spacing[:30], "advice": advice, "features": {"type": "FeatureCollection", "features": features},
        "params": {"radius_m": radius_m, "trips_per_resident": trips_per_resident, "peak_share": peak_share},
        "assumptions": [
            "Жители — оценка по площади жилых зданий: площадь × этажность / 30 м² на человека (не ИД5).",
            f"Покрытие — по прямой до ближайшей остановки, радиус {round(radius_m)} м; по сети пути длиннее.",
            "Платформы и места остановки ближе 60 м считаются одной остановкой.",
            f"Спрос в час пик = жители × {trips_per_resident:g} поездки на ОТ в сутки × доля часа пик {peak_share:g}; "
            "к ближайшей остановке. Места приложения труда не учтены.",
            "Провозная способность = Σ машин/ч × вместимость: автобус 80, троллейбус 85, трамвай 130, маршрутка 20 — допущения.",
            "Загруженность: спрос / провозная < 0,3 низкая, < 0,6 средняя, иначе высокая (машины приходят частично заполненными).",
        ],
        "sources": SOURCES,
    }
