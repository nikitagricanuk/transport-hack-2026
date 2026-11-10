"""Dry run of a suggested fix: what it would add, remove or change, without keeping it.

The issue panel never applies a fix straight away. It first asks for this
preview, draws it on the map and shows how much road a deletion removes, and
only the user's "Apply" click sends the real operation (which then lands in
the undo history and the edit log with the author and time).
"""

from __future__ import annotations

import math
from typing import Any

from .edits import History, Transaction, apply_op
from .osm_model import KINDS, Node, OsmDocument, Relation, Way
from .roads import is_drivable


def _way_coords(doc: OsmDocument, way: Way) -> list[list[float]]:
    out = []
    for ref in way.nodes:
        n = doc.nodes.get(ref)
        if n is not None:
            out.append([n.lon, n.lat])
    return out


def _haversine_m(a: list[float], b: list[float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _length_m(coords: list[list[float]]) -> float:
    return sum(_haversine_m(a, b) for a, b in zip(coords, coords[1:]))


def _is_road(way: Way) -> bool:
    return "highway" in way.tags


def _features(doc: OsmDocument, states: dict, status_of: dict, which: str) -> tuple[list[dict], float]:
    """GeoJSON features for one side of a transaction (`which` = before/after)."""
    feats: list[dict] = []
    road_m = 0.0
    for (kind, eid), el in states.items():
        status = status_of[(kind, eid)]
        if el is None or (which == "before" and status != "deleted") or (which == "after" and status == "deleted"):
            continue
        props: dict[str, Any] = {"kind": kind, "id": eid, "status": status, "name": el.tags.get("name", ""),
                                 # a fictitious edge at the extract border, not a real road
                                 "fringe": el.tags.get("uds:fringe") == "yes"}
        if isinstance(el, Way):
            coords = _way_coords(doc, el)
            if len(coords) < 2:
                continue
            if _is_road(el):
                props["length_m"] = round(_length_m(coords), 1)
                props["drivable"] = is_drivable(el.tags)
                if status != "modified":
                    road_m += props["length_m"]
            feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                          "properties": props})
        elif isinstance(el, Node) and (el.tags or status != "modified"):
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [el.lon, el.lat]},
                          "properties": props})
    return feats, road_m


def _restore_order(doc: OsmDocument, tx: Transaction, orders: dict[str, list[int]]) -> None:
    """Put re-inserted elements back in place: undo appends them at the end of the
    store, which would reorder the exported file after a mere preview."""
    for kind in {k for (k, _eid), el in tx.after.items() if el is None}:
        store = doc.store(kind)
        order = [eid for eid in orders[kind] if eid in store]
        if len(order) == len(store):
            items = [(eid, store[eid]) for eid in order]
            store.clear()
            store.update(items)


def _restriction_features(doc: OsmDocument, tx: Transaction) -> list[dict]:
    """Highlight the replacement member without counting it as a new road or node."""
    features = []
    for key, new in tx.after.items():
        old = tx.before[key]
        if not isinstance(old, Relation) or not isinstance(new, Relation):
            continue
        if not new.tags.get("type", "").startswith("restriction"):
            continue
        for member in new.members:
            if member in old.members:
                continue
            element = doc.get(member.type, member.ref)
            if isinstance(element, Node):
                geometry = {"type": "Point", "coordinates": [element.lon, element.lat]}
            elif isinstance(element, Way):
                coordinates = _way_coords(doc, element)
                if len(coordinates) < 2:
                    continue
                geometry = {"type": "LineString", "coordinates": coordinates}
            else:
                continue
            features.append({"type": "Feature", "geometry": geometry,
                             "properties": {"kind": member.type, "id": member.ref, "status": "modified",
                                            "role": member.role}})
    return features


def preview(doc: OsmDocument, op: str, args: dict, probe=None) -> dict[str, Any]:
    """Apply `op` on a throwaway history, describe the change, then undo it.

    Raises OpError like the real operation, so the panel can show why a fix
    cannot be applied before the user commits to it.
    """
    scratch = History()
    # keep the new-id counter: the draft journal replays only real edits, and the
    # ids it hands out on replay must match the ones the live session used
    next_id = doc._next_new_id
    orders = {kind: list(doc.store(kind)) for kind in KINDS}
    try:
        apply_op(doc, scratch, op, args)
    except Exception:
        doc._next_new_id = next_id
        raise
    tx: Transaction | None = scratch.last_tx
    if tx is None:
        doc._next_new_id = next_id
        return {"features": [], "counts": {"added": 0, "deleted": 0, "modified": 0},
                "added_road_m": 0.0, "removed_road_m": 0.0, "empty": True}
    status_of = {}
    for key in tx.before:
        old, new = tx.before[key], tx.after.get(key)
        status_of[key] = "added" if old is None else "deleted" if new is None else "modified"
    try:
        # geometry of what exists after the fix, read before rolling it back
        after_feats, added_m = _features(doc, tx.after, status_of, "after")
        after_feats.extend(_restriction_features(doc, tx))
        probe_result = probe(doc) if probe else None
    finally:
        tx.undo()
        doc._next_new_id = next_id
        _restore_order(doc, tx, orders)
    before_feats, removed_m = _features(doc, tx.before, status_of, "before")
    counts = {"added": 0, "deleted": 0, "modified": 0}
    for st in status_of.values():
        counts[st] += 1
    return {
        "features": before_feats + after_feats,
        "counts": counts,
        "added_road_m": round(added_m, 1),
        "removed_road_m": round(removed_m, 1),
        "empty": tx.is_empty,
        "probe": probe_result,
    }
