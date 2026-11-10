"""Public transport from OSM route relations: routes, stops served, route geometry.

A route is a relation type=route with route=bus|trolleybus|tram|minibus|share_taxi.
Members with a stop*/platform* role are stops, the rest are the ways it drives.
Frequency (vehicles/h) comes from the route's `interval` tag when present,
otherwise a default for the mode; the user can override it, and buses then
add their passenger-car equivalent to the road load model.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .geo import LocalProjection
from .osm_model import OsmDocument, Relation

MODES = {"bus": "Автобус", "trolleybus": "Троллейбус", "tram": "Трамвай", "minibus": "Маршрутка", "share_taxi": "Маршрутка"}
DEFAULT_FREQ = {"bus": 6.0, "trolleybus": 6.0, "tram": 6.0, "minibus": 10.0, "share_taxi": 10.0}  # vehicles/h
PCU = {"bus": 2.5, "trolleybus": 2.5, "tram": 0.0, "minibus": 1.5, "share_taxi": 1.5}  # trams do not use lanes
CATCHMENT_M = 400.0      # legacy radial building estimate; not the network accessibility norm
M2_PER_PERSON = 30.0     # living floor area per resident
DEFAULT_LEVELS = {"apartments": 9, "residential": 5, "house": 1, "detached": 1, "dormitory": 5}
RESIDENTIAL = set(DEFAULT_LEVELS) | {"yes"}


def _is_route(r: Relation) -> bool:
    return r.tags.get("type") == "route" and r.tags.get("route") in MODES


def _interval_minutes(value: str | None) -> float | None:
    if not value:
        return None
    m = re.match(r"^\s*(?:(\d+):)?(\d+)(?::(\d+))?\s*$", value)
    if not m:
        return None
    h, mins, secs = m.groups()
    if h is not None and secs is not None:  # hh:mm:ss
        total = int(h) * 60 + int(mins) + int(secs) / 60
    elif h is not None:                     # hh:mm
        total = int(h) * 60 + int(mins)
    else:
        total = float(mins)
    return total if total > 0 else None


def _is_stop_role(role: str) -> bool:
    return role.startswith("stop") or role.startswith("platform")


def route_frequency(r: Relation, overrides: dict[int, float]) -> float:
    if r.id in overrides:
        return overrides[r.id]
    interval = _interval_minutes(r.tags.get("interval"))
    return round(60.0 / interval, 1) if interval else DEFAULT_FREQ[r.tags["route"]]


def _summary(r: Relation, overrides: dict[int, float]) -> dict[str, Any]:
    stops = [m.ref for m in r.members if m.type == "node" and _is_stop_role(m.role)]
    return {
        "id": r.id,
        "ref": r.tags.get("ref") or r.tags.get("name", "")[:8] or str(r.id),
        "name": r.tags.get("name", ""),
        "mode": r.tags["route"],
        "mode_label": MODES[r.tags["route"]],
        "from": r.tags.get("from", ""),
        "to": r.tags.get("to", ""),
        "colour": r.tags.get("colour", ""),
        "stops": len(stops),
        "frequency": route_frequency(r, overrides),
        "custom_frequency": r.id in overrides,
    }


def routes(doc: OsmDocument, overrides: dict[int, float]) -> list[dict[str, Any]]:
    out = [_summary(r, overrides) for r in doc.relations.values() if _is_route(r)]
    out.sort(key=lambda x: (x["mode"], _natural(x["ref"])))
    return out


def _natural(ref: str) -> tuple:
    return tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", ref))


def stop_info(doc: OsmDocument, node_id: int, overrides: dict[int, float]) -> dict[str, Any]:
    """Routes serving a stop, vehicles per hour and an estimate of residents nearby."""
    n = doc.nodes.get(node_id)
    if n is None:
        raise KeyError(node_id)
    serving = [
        _summary(r, overrides) for r in doc.relations.values()
        if _is_route(r) and any(m.type == "node" and m.ref == node_id for m in r.members)
    ]
    serving.sort(key=lambda x: (x["mode"], _natural(x["ref"])))
    per_hour = round(sum(r["frequency"] for r in serving), 1)
    residents = _residents_near(doc, n.lat, n.lon)
    return {
        "id": node_id,
        "name": n.tags.get("name", ""),
        "routes": serving,
        "vehicles_per_hour": per_hour,
        "residents_400m": residents,
        "load_label": _load_label(per_hour, residents),
    }


def _load_label(per_hour: float, residents: int) -> str:
    """Rough crowding: people within walking distance per vehicle that stops here."""
    if per_hour <= 0:
        return "нет маршрутов"
    per_vehicle = residents / per_hour
    if per_vehicle < 40:
        return "низкая"
    if per_vehicle < 120:
        return "средняя"
    return "высокая"


def _residents_near(doc: OsmDocument, lat: float, lon: float) -> int:
    proj = LocalProjection(lat, lon)
    floor = 0.0
    for w in doc.ways.values():
        b = w.tags.get("building")
        if b not in RESIDENTIAL or not w.is_closed:
            continue
        pts = [proj.to_xy(doc.nodes[r].lat, doc.nodes[r].lon) for r in w.nodes if r in doc.nodes]
        if len(pts) < 4:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        if math.hypot(cx, cy) > CATCHMENT_M:
            continue
        area = abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:]))) / 2
        try:
            levels = int(w.tags.get("building:levels", ""))
        except ValueError:
            levels = DEFAULT_LEVELS.get(b, 3)
        floor += area * max(levels, 1)
    return int(floor / M2_PER_PERSON)


def route_geometry(doc: OsmDocument, route_id: int, overrides: dict[int, float]) -> dict[str, Any]:
    r = doc.relations.get(route_id)
    if r is None or not _is_route(r):
        raise KeyError(route_id)
    lines = []
    for m in r.members:
        if m.type != "way" or _is_stop_role(m.role):
            continue
        w = doc.ways.get(m.ref)
        if w is None:
            continue
        coords = [[doc.nodes[x].lon, doc.nodes[x].lat] for x in w.nodes if x in doc.nodes]
        if len(coords) >= 2:
            lines.append(coords)
    stops = []
    for m in r.members:
        if m.type == "node" and _is_stop_role(m.role) and m.ref in doc.nodes:
            n = doc.nodes[m.ref]
            stops.append({"id": n.id, "name": n.tags.get("name", ""), "coord": [n.lon, n.lat]})
    return {**_summary(r, overrides), "lines": lines, "stop_list": stops}


def bus_pcu_by_way(doc: OsmDocument, overrides: dict[int, float]) -> dict[int, float]:
    """Passenger-car equivalents per hour that scheduled vehicles add to each way."""
    out: dict[int, float] = {}
    for r in doc.relations.values():
        if not _is_route(r):
            continue
        pcu = PCU[r.tags["route"]] * route_frequency(r, overrides)
        if pcu <= 0:
            continue
        for m in r.members:
            if m.type == "way" and not _is_stop_role(m.role):
                out[m.ref] = out.get(m.ref, 0.0) + pcu
    return out
