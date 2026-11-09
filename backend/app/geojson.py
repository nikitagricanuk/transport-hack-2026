"""Convert the document into GeoJSON layers for the map."""

from __future__ import annotations

from typing import Any

from .osm_model import Node, OsmDocument, Way
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


def _junction_counts(doc: OsmDocument) -> dict[int, int]:
    counts: dict[int, int] = {}
    for w in doc.ways.values():
        if is_drivable(w.tags):
            for ref in dict.fromkeys(w.nodes):
                counts[ref] = counts.get(ref, 0) + 1
    return counts


def way_feature(doc: OsmDocument, w: Way) -> tuple[str, dict] | None:
    """("ways" | "areas", feature) for one way, or None when it has no geometry."""
    coords = [[doc.nodes[r].lon, doc.nodes[r].lat] for r in w.nodes if r in doc.nodes]
    if len(coords) < 2:
        return None
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
        return "areas", {"type": "Feature", "id": _fid("way", w.id),
                         "geometry": {"type": "Polygon", "coordinates": [coords]}, "properties": props}
    return "ways", {"type": "Feature", "id": _fid("way", w.id),
                    "geometry": {"type": "LineString", "coordinates": coords}, "properties": props}


def node_feature(n: Node, junction_count: int) -> dict | None:
    if not n.tags and junction_count < 2:
        return None  # plain shape vertices are sent with ways; keep payload small
    return {
        "type": "Feature", "id": _fid("node", n.id),
        "geometry": {"type": "Point", "coordinates": [n.lon, n.lat]},
        "properties": {"id": n.id, "kind": "node", "junction": junction_count >= 2,
                       "category": _node_category(n.tags), "tags": n.tags},
    }


def to_geojson(doc: OsmDocument) -> dict[str, Any]:
    out: dict[str, list[dict]] = {"ways": [], "areas": []}
    for w in doc.ways.values():
        f = way_feature(doc, w)
        if f:
            out[f[0]].append(f[1])
    jc = _junction_counts(doc)
    node_feats = [f for n in doc.nodes.values() if (f := node_feature(n, jc.get(n.id, 0)))]
    return {
        "ways": {"type": "FeatureCollection", "features": out["ways"]},
        "areas": {"type": "FeatureCollection", "features": out["areas"]},
        "nodes": {"type": "FeatureCollection", "features": node_feats},
        "bounds": doc.bounds(),
    }


def geo_patch(doc: OsmDocument, before: dict, after: dict) -> dict[str, Any]:
    """Map features that changed in one transaction.

    ``before``/``after`` are a transaction's snapshots keyed by (kind, id). The
    client drops every feature listed in ``ways``/``nodes`` and adds the
    returned ones, instead of reloading the whole map after each edit.
    """
    way_ids: set[int] = set()
    node_ids: set[int] = set()
    for states in (before, after):
        for (kind, eid), el in states.items():
            if kind == "way":
                way_ids.add(eid)
                if el is not None:
                    node_ids.update(el.nodes)  # junction status of old and new vertices
            elif kind == "node":
                node_ids.add(eid)
    if node_ids:
        # a moved node changes the geometry of every way that uses it
        for w in doc.ways.values():
            if w.id not in way_ids and not node_ids.isdisjoint(w.nodes):
                way_ids.add(w.id)
    feats: dict[str, list[dict]] = {"ways": [], "areas": [], "nodes": []}
    for wid in way_ids:
        w = doc.ways.get(wid)
        f = way_feature(doc, w) if w else None
        if f:
            feats[f[0]].append(f[1])
    jc = _junction_counts(doc) if node_ids else {}
    for nid in node_ids:
        n = doc.nodes.get(nid)
        f = node_feature(n, jc.get(nid, 0)) if n else None
        if f:
            feats["nodes"].append(f)
    return {"ways": sorted(way_ids), "nodes": sorted(node_ids), "features": feats}


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
