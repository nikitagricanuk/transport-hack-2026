"""Convert the document into GeoJSON layers for the map."""

from __future__ import annotations

from typing import Any

from .osm_model import OsmDocument
from .roads import direction, is_drivable

_AREA_KEYS = ("building", "landuse", "leisure", "natural", "amenity", "water", "area")


def _is_area(tags: dict[str, str]) -> bool:
    if tags.get("area") == "no":
        return False
    if tags.get("area") == "yes":
        return True
    if "highway" in tags or "railway" in tags or "barrier" in tags:
        return False
    return any(k in tags for k in _AREA_KEYS)


def _layer_of_way(tags: dict[str, str], closed: bool) -> str:
    if "building" in tags and closed:
        return "building"
    if tags.get("railway") in {"tram", "light_rail", "rail", "subway"}:
        return "rail"
    if "highway" in tags:
        return "road" if is_drivable(tags) else "path"
    if closed and _is_area(tags):
        return "area"
    return "other"


def to_geojson(doc: OsmDocument) -> dict[str, Any]:
    way_feats: list[dict] = []
    area_feats: list[dict] = []
    junction_count: dict[int, int] = {}
    for w in doc.ways.values():
        if is_drivable(w.tags):
            for ref in dict.fromkeys(w.nodes):
                junction_count[ref] = junction_count.get(ref, 0) + 1

    for w in doc.ways.values():
        coords = [[doc.nodes[r].lon, doc.nodes[r].lat] for r in w.nodes if r in doc.nodes]
        if len(coords) < 2:
            continue
        layer = _layer_of_way(w.tags, w.is_closed)
        props = {
            "id": w.id,
            "kind": "way",
            "layer": layer,
            "highway": w.tags.get("highway", ""),
            "name": w.tags.get("name", ""),
            "lanes": _lanes(w.tags),
            "dir": direction(w.tags),
            "bridge": w.tags.get("bridge", "no") not in ("no", ""),
            "nodes": w.nodes,
            "tags": w.tags,
        }
        if layer in ("building", "area") and w.is_closed:
            area_feats.append({"type": "Feature", "id": _fid("way", w.id),
                               "geometry": {"type": "Polygon", "coordinates": [coords]}, "properties": props})
        else:
            way_feats.append({"type": "Feature", "id": _fid("way", w.id),
                              "geometry": {"type": "LineString", "coordinates": coords}, "properties": props})

    node_feats: list[dict] = []
    for n in doc.nodes.values():
        jc = junction_count.get(n.id, 0)
        if not n.tags and jc < 2:
            continue  # plain shape vertices are sent with ways; keep payload small
        node_feats.append({
            "type": "Feature", "id": _fid("node", n.id),
            "geometry": {"type": "Point", "coordinates": [n.lon, n.lat]},
            "properties": {"id": n.id, "kind": "node", "junction": jc >= 2, "category": _node_category(n.tags),
                           "tags": n.tags},
        })

    return {
        "ways": {"type": "FeatureCollection", "features": way_feats},
        "areas": {"type": "FeatureCollection", "features": area_feats},
        "nodes": {"type": "FeatureCollection", "features": node_feats},
        "bounds": doc.bounds(),
    }


def vertices(doc: OsmDocument, way_id: int) -> list[dict]:
    w = doc.ways.get(way_id)
    if not w:
        return []
    return [{"id": r, "lon": doc.nodes[r].lon, "lat": doc.nodes[r].lat, "tags": doc.nodes[r].tags}
            for r in w.nodes if r in doc.nodes]


def _fid(kind: str, eid: int) -> int:
    # MapLibre feature-state needs non-negative integer ids; encode kind and sign.
    base = abs(eid) * 4 + (0 if kind == "node" else 1)
    return base * 2 + (1 if eid < 0 else 0)


def _lanes(tags: dict[str, str]) -> int:
    try:
        return max(1, int(tags.get("lanes", "")))
    except ValueError:
        return 2 if direction(tags) == 0 else 1


def _node_category(tags: dict[str, str]) -> str:
    hw = tags.get("highway")
    if hw == "traffic_signals":
        return "signal"
    if hw == "crossing" or tags.get("railway") == "crossing":
        return "crossing"
    if hw == "bus_stop" or tags.get("public_transport") in {"platform", "stop_position"} or tags.get("railway") == "tram_stop":
        return "stop"
    if tags:
        return "tagged"
    return "junction"
