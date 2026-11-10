"""Editing operations with undo/redo.

Every operation runs inside a Transaction. The transaction records a deep copy
of each element *before* it is first touched, and the state *after* the
operation, so undo/redo is just restoring snapshots: it works for any
operation without writing an inverse by hand.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .geo import GridIndex, LocalProjection, dist, project_on_segment, segment_intersection
from .osm_model import Element, Kind, Member, Node, OsmDocument, Relation, Way, clone
from .norms import lane_width, max_grade
from .roads import can_connect, direction, is_drivable, lane_tags, parse_int, reversed_tags


class OpError(ValueError):
    """Operation cannot be applied; the message is shown to the user."""


Key = tuple[str, int]


@dataclass
class Transaction:
    doc: OsmDocument
    label: str
    before: dict[Key, Element | None] = field(default_factory=dict)
    after: dict[Key, Element | None] = field(default_factory=dict)
    author: str | None = None   # who made the edit (collaborator name)
    at: float = 0.0             # unix time of the edit
    reason: str | None = None

    def summary(self) -> dict[str, Any]:
        """Journal entry for the history panel: what, who, when and which elements."""
        refs = []
        for (kind, eid) in self.before:
            old, new = self.before[(kind, eid)], self.after.get((kind, eid))
            status = "added" if old is None else "deleted" if new is None else "modified"
            ref = new or old
            refs.append({"kind": kind, "id": eid, "status": status,
                         "name": ref.tags.get("name", "") if ref is not None else ""})
        return {"label": self.label, "author": self.author, "at": self.at, "refs": refs[:30], "count": len(refs),
                "reason": self.reason}

    def touch(self, kind: Kind, eid: int) -> None:
        key = (kind, eid)
        if key not in self.before:
            cur = self.doc.get(kind, eid)
            self.before[key] = clone(cur)

    def commit(self) -> None:
        for (kind, eid) in self.before:
            cur = self.doc.get(kind, eid)  # type: ignore[arg-type]
            self.after[(kind, eid)] = clone(cur)

    def _restore(self, states: dict[Key, Element | None]) -> None:
        for (kind, eid), el in states.items():
            store = self.doc.store(kind)  # type: ignore[arg-type]
            if el is None:
                store.pop(eid, None)
            else:
                store[eid] = clone(el)  # type: ignore[assignment]

    def undo(self) -> None:
        self._restore(self.before)

    def redo(self) -> None:
        self._restore(self.after)

    @property
    def is_empty(self) -> bool:
        return all(self.before[k] == self.after.get(k) for k in self.before)


class History:
    def __init__(self, limit: int = 500) -> None:
        self.undo_stack: list[Transaction] = []
        self.redo_stack: list[Transaction] = []
        self.limit = limit
        # the transaction applied, undone or redone last; tells callers what changed
        self.last_tx: Transaction | None = None

    def push(self, tx: Transaction) -> None:
        self.undo_stack.append(tx)
        if len(self.undo_stack) > self.limit:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self) -> Transaction | None:
        if not self.undo_stack:
            return None
        tx = self.undo_stack.pop()
        tx.undo()
        self.redo_stack.append(tx)
        return tx

    def redo(self) -> Transaction | None:
        if not self.redo_stack:
            return None
        tx = self.redo_stack.pop()
        tx.redo()
        self.undo_stack.append(tx)
        return tx

    def info(self) -> dict[str, Any]:
        return {
            "can_undo": bool(self.undo_stack),
            "can_redo": bool(self.redo_stack),
            "undo_label": self.undo_stack[-1].label if self.undo_stack else None,
            "redo_label": self.redo_stack[-1].label if self.redo_stack else None,
            "entries": [tx.label for tx in reversed(self.undo_stack[-50:])],
            "details": [tx.summary() for tx in reversed(self.undo_stack[-50:])],
        }


# =================================================================== helpers
def _node(doc: OsmDocument, nid: int) -> Node:
    n = doc.nodes.get(nid)
    if n is None:
        raise OpError(f"узел {nid} не найден")
    return n


def _way(doc: OsmDocument, wid: int) -> Way:
    w = doc.ways.get(wid)
    if w is None:
        raise OpError(f"ребро {wid} не найдено")
    return w


def _proj(doc: OsmDocument) -> LocalProjection:
    b = doc.bounds() or (0.0, 0.0, 0.0, 0.0)
    return LocalProjection((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _clean_tags(tags: dict[str, Any]) -> dict[str, str]:
    out = {}
    for k, v in tags.items():
        k = str(k).strip()
        if not k or v is None:
            continue
        out[k] = str(v).strip()
    return out


def _create_node(doc: OsmDocument, tx: Transaction, lat: float, lon: float, tags: dict | None = None) -> Node:
    nid = doc.new_id()
    tx.touch("node", nid)
    n = Node(id=nid, lat=float(lat), lon=float(lon), tags=_clean_tags(tags or {}))
    doc.nodes[nid] = n
    return n


def _dedupe_consecutive(refs: list[int]) -> list[int]:
    out: list[int] = []
    for r in refs:
        if not out or out[-1] != r:
            out.append(r)
    return out


def _remove_from_relations(doc: OsmDocument, tx: Transaction, kind: Kind, eid: int) -> None:
    for rel in list(doc.relations_with(kind, eid)):
        tx.touch("relation", rel.id)
        rel.members = [m for m in rel.members if not (m.type == kind and m.ref == eid)]


def _delete_way(doc: OsmDocument, tx: Transaction, wid: int, drop_orphans: bool = True) -> None:
    w = _way(doc, wid)
    tx.touch("way", wid)
    del doc.ways[wid]
    _remove_from_relations(doc, tx, "way", wid)
    if drop_orphans:
        still_used = {r for other in doc.ways.values() for r in other.nodes}
        in_rel = {m.ref for r in doc.relations.values() for m in r.members if m.type == "node"}
        for ref in set(w.nodes):
            n = doc.nodes.get(ref)
            if n is not None and ref not in still_used and ref not in in_rel and not n.tags:
                tx.touch("node", ref)
                del doc.nodes[ref]


def _delete_node(doc: OsmDocument, tx: Transaction, nid: int) -> None:
    _node(doc, nid)
    for w in doc.ways_of_node(nid):
        tx.touch("way", w.id)
        w.nodes = _dedupe_consecutive([r for r in w.nodes if r != nid])
        if len(set(w.nodes)) < 2:
            _delete_way(doc, tx, w.id, drop_orphans=True)
    _remove_from_relations(doc, tx, "node", nid)
    if nid in doc.nodes:
        tx.touch("node", nid)
        del doc.nodes[nid]


def _insert_on_way(doc: OsmDocument, tx: Transaction, wid: int, lat: float, lon: float,
                   tags: dict | None, snap_m: float = 1.0, avoid: set[int] | frozenset[int] = frozenset()) -> int:
    """Put a node on the nearest segment of a way; reuse a vertex if very close (unless in ``avoid``)."""
    w = _way(doc, wid)
    if len(w.nodes) < 2:
        raise OpError("у ребра меньше двух узлов")
    proj = _proj(doc)
    p = proj.to_xy(lat, lon)
    best = None
    for i in range(len(w.nodes) - 1):
        a, b = _node(doc, w.nodes[i]), _node(doc, w.nodes[i + 1])
        q, t, d = project_on_segment(p, proj.to_xy(a.lat, a.lon), proj.to_xy(b.lat, b.lon))
        if best is None or d < best[0]:
            best = (d, i, q, t)
    assert best is not None
    _, i, q, _t = best
    for ref in (w.nodes[i], w.nodes[i + 1]):
        n = doc.nodes[ref]
        if ref not in avoid and dist(q, proj.to_xy(n.lat, n.lon)) <= snap_m:
            if tags:
                tx.touch("node", ref)
                n.tags.update(_clean_tags(tags))
            return ref
    qlat, qlon = proj.to_latlon(*q)
    n = _create_node(doc, tx, qlat, qlon, tags)
    tx.touch("way", wid)
    w.nodes.insert(i + 1, n.id)
    return n.id


def _merge_nodes(doc: OsmDocument, tx: Transaction, keep_id: int, remove_id: int) -> None:
    if keep_id == remove_id:
        raise OpError("нужно выбрать два разных узла")
    keep, rem = _node(doc, keep_id), _node(doc, remove_id)
    tx.touch("node", keep_id)
    for k, v in rem.tags.items():
        keep.tags.setdefault(k, v)
    for w in doc.ways_of_node(remove_id):
        tx.touch("way", w.id)
        w.nodes = _dedupe_consecutive([keep_id if r == remove_id else r for r in w.nodes])
    for rel in list(doc.relations_with("node", remove_id)):
        tx.touch("relation", rel.id)
        for m in rel.members:
            if m.type == "node" and m.ref == remove_id:
                m.ref = keep_id
    tx.touch("node", remove_id)
    del doc.nodes[remove_id]
    for w in list(doc.ways.values()):
        if keep_id in w.nodes and len(set(w.nodes)) < 2:
            _delete_way(doc, tx, w.id)


# ================================================================ operations
def op_set_tags(doc, tx, kind: Kind, id: int, tags: dict, replace: bool = False):
    el = doc.get(kind, int(id))
    if el is None:
        raise OpError(f"{kind} {id} не найден")
    tx.touch(kind, el.id)
    if replace:
        el.tags = _clean_tags(tags)
    else:
        for k, v in tags.items():
            if v is None or v == "":
                el.tags.pop(k, None)
            else:
                el.tags[str(k).strip()] = str(v).strip()
    return {"id": el.id}


def op_set_lanes(doc, tx, way_id: int, forward: int, backward: int = 0,
                 oneway: str = "no", auto_turns: bool = False):
    return op_set_tags(doc, tx, "way", way_id, lane_tags(int(forward), int(backward), oneway, auto_turns))


def op_move_node(doc, tx, id: int, lat: float, lon: float):
    n = _node(doc, int(id))
    tx.touch("node", n.id)
    n.set_position(float(lat), float(lon))
    # Dragging a vertex across another road is an overlay: split both at the crossing.
    warnings = _join_crossings(doc, tx, [w.id for w in doc.ways_of_node(n.id)])
    return _with_warnings({"id": n.id}, warnings)


def op_move_way(doc, tx, id: int, dlat: float, dlon: float):
    """Shift every node of a way by the same delta, so a building or a road moves as one object.

    Nodes shared with another way move too: in OSM the corner belongs to every way that uses it.
    Every highway that uses a moved node is checked for a new same-grade crossing.
    """
    w = _way(doc, int(id))
    dlat, dlon = float(dlat), float(dlon)
    if dlat != dlat or dlon != dlon or abs(dlat) == float("inf") or abs(dlon) == float("inf"):
        raise OpError("сдвиг должен быть конечным числом")
    if abs(dlat) < 1e-12 and abs(dlon) < 1e-12:
        raise OpError("сдвиг слишком мал")
    moved = 0
    seen: set[int] = set()
    moved_ids: list[int] = []
    for nid in w.nodes:
        if nid in seen:
            continue
        seen.add(nid)
        n = doc.nodes.get(nid)
        if n is None:
            continue
        tx.touch("node", n.id)
        n.set_position(n.lat + dlat, n.lon + dlon)
        moved += 1
        moved_ids.append(n.id)
    if moved == 0:
        raise OpError("у объекта нет узлов")
    # A shared node moves every way that uses it. Crossing detection has to see
    # those highways too: dragging a building corner can make the attached road
    # cross a third road, and that crossing is not on the building itself.
    warnings = _join_crossings(doc, tx, _highway_ids_touching(doc, moved_ids))
    return _with_warnings({"id": w.id, "nodes": moved}, warnings)


def op_add_node(doc, tx, lat: float, lon: float, tags: dict | None = None):
    return {"id": _create_node(doc, tx, lat, lon, tags).id}


def op_insert_node(doc, tx, way_id: int, lat: float, lon: float, tags: dict | None = None):
    return {"id": _insert_on_way(doc, tx, int(way_id), float(lat), float(lon), tags)}


def op_add_way(doc, tx, points: list[dict], tags: dict | None = None, closed: bool = False):
    """points: [{"id": node_id} | {"lat":..,"lon":..} | {"lat":..,"lon":..,"way_id":..}]

    closed: the road returns to its first point (a loop drawn back onto where it started).
    """
    if len(points) < 2:
        raise OpError("ребро должно содержать минимум две точки")
    new_tags = _clean_tags(tags or {"highway": "residential"})
    # Snapping to an existing node or way is an explicit connection: block it
    # before any geometry is created. A free-drawn line that merely crosses an
    # incompatible road is kept and reported by ``_join_crossings`` instead.
    for p in points:
        if p.get("id") is not None:
            _assert_compatible_with(doc, new_tags, _link_ways(doc, int(p["id"])))
        elif p.get("way_id") is not None:
            _assert_compatible_with(doc, new_tags, [_way(doc, int(p["way_id"]))])
    refs: list[int] = []
    for p in points:
        if p.get("id") is not None:
            refs.append(_node(doc, int(p["id"])).id)
        elif p.get("way_id") is not None:
            refs.append(_insert_on_way(doc, tx, int(p["way_id"]), float(p["lat"]), float(p["lon"]), None))
        else:
            refs.append(_create_node(doc, tx, float(p["lat"]), float(p["lon"])).id)
    refs = _dedupe_consecutive(refs)
    if len(set(refs)) < 2:
        raise OpError("ребро должно соединять минимум два разных узла")
    if closed:
        if len(set(refs)) < 3:
            raise OpError("замкнутое ребро должно содержать минимум три разных узла")
        if refs[-1] != refs[0]:
            refs.append(refs[0])
    wid = doc.new_id()
    tx.touch("way", wid)
    doc.ways[wid] = Way(id=wid, nodes=refs, tags=new_tags)
    warnings = _join_crossings(doc, tx, [wid])
    return _with_warnings({"id": wid}, warnings)


def op_delete(doc, tx, kind: Kind, id: int):
    eid = int(id)
    if kind == "node":
        _delete_node(doc, tx, eid)
    elif kind == "way":
        _delete_way(doc, tx, eid)
    elif kind == "relation":
        if eid not in doc.relations:
            raise OpError(f"отношение {eid} не найдено")
        _remove_from_relations(doc, tx, "relation", eid)
        tx.touch("relation", eid)
        del doc.relations[eid]
    else:
        raise OpError(f"неизвестный тип {kind}")
    return {"id": eid}


_BACKWARD_ROLES = {"backward", "reverse"}


def _is_restriction(tags: dict[str, str]) -> bool:
    kind = tags.get("type", "").lower()
    return kind == "restriction" or kind.startswith("restriction:") or kind == "connectivity"


def _via_node_ids(doc: OsmDocument, rel: Relation) -> set[int]:
    nodes: set[int] = set()
    for m in rel.members:
        if m.role != "via":
            continue
        if m.type == "node":
            nodes.add(m.ref)
        elif m.type == "way":
            w = doc.ways.get(m.ref)
            if w is not None and w.nodes:
                nodes.add(w.nodes[0])
                nodes.add(w.nodes[-1])
    return nodes


def _touches_end(nodes: list[int] | None, nid: int) -> bool:
    return bool(nodes) and (nodes[0] == nid or nodes[-1] == nid)


def _piece_for_role(role: str, old: Way, new: Way, via: set[int], tags: dict[str, str]) -> Way:
    """The single from/to piece that still touches the via node.

    SUMO drops a restriction that has two ``from`` or two ``to`` members, which
    is what a blind copy of both pieces produces.
    """
    hits = [w for w in (old, new) if w.nodes and (w.nodes[0] in via or w.nodes[-1] in via)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) != 2:
        return old
    # The split node is the via. Keep the piece that approaches it (from) or leaves it (to).
    backward = direction(tags) < 0
    if role == "from":
        if backward:
            return new if new.nodes[0] in via else old
        return old if old.nodes[-1] in via else new
    if backward:
        return old if old.nodes[-1] in via else new
    return new if new.nodes[0] in via else old


def _role_endpoints(doc: OsmDocument, rel: Relation, role: str) -> set[int]:
    nodes: set[int] = set()
    for member in rel.members:
        if member.role != role:
            continue
        if member.type == "node":
            nodes.add(member.ref)
        elif member.type == "way":
            way = doc.ways.get(member.ref)
            if way is not None and way.nodes:
                nodes.add(way.nodes[0])
                nodes.add(way.nodes[-1])
    return nodes


def _other_via_edges(doc: OsmDocument, rel: Relation, skip_id: int) -> list[tuple[int, int]]:
    edges: list[tuple[int, int]] = []
    for member in rel.members:
        if member.role != "via" or member.type != "way" or member.ref == skip_id:
            continue
        way = doc.ways.get(member.ref)
        if way is not None and len(way.nodes) >= 2:
            edges.append((way.nodes[0], way.nodes[-1]))
    return edges


def _endpoints_linked(sources: set[int], targets: set[int], edges: list[tuple[int, int]]) -> bool:
    """True when some source node reaches some target along via-way endpoints."""
    if not sources or not targets:
        return False
    if sources & targets:
        return True
    adjacent: dict[int, set[int]] = {}
    for left, right in edges:
        adjacent.setdefault(left, set()).add(right)
        adjacent.setdefault(right, set()).add(left)
    seen = set(sources)
    stack = list(sources)
    while stack:
        node = stack.pop()
        for nxt in adjacent.get(node, ()):
            if nxt in seen:
                continue
            if nxt in targets:
                return True
            seen.add(nxt)
            stack.append(nxt)
    return False


def _restriction_via_backward(doc: OsmDocument, rel: Relation, old: Way, new: Way) -> bool:
    """True when the via way is travelled from its last node toward its first.

    Restriction members are not a route. ``from``, ``to``, and ``via`` may be
    stored in any order, so the direction is the path from the ``from`` role
    to the ``to`` role through connected via endpoints. ``old`` is the first
    half of the original node order and ``new`` is the second.
    """
    if len(old.nodes) < 2 or len(new.nodes) < 2:
        return False
    start, end = old.nodes[0], new.nodes[-1]
    edges = _other_via_edges(doc, rel, old.id)
    from_nodes = _role_endpoints(doc, rel, "from")
    enters_start = _endpoints_linked(from_nodes, {start}, edges)
    enters_end = _endpoints_linked(from_nodes, {end}, edges)
    if enters_end and not enters_start:
        return True
    if enters_start and not enters_end:
        return False
    to_nodes = _role_endpoints(doc, rel, "to")
    leaves_start = _endpoints_linked({start}, to_nodes, edges)
    leaves_end = _endpoints_linked({end}, to_nodes, edges)
    if leaves_start and not leaves_end:
        return True
    if leaves_end and not leaves_start:
        return False
    return False


def _neighbour_nodes(doc: OsmDocument, members: list[Member], index: int, step: int, old_id: int) -> list[int] | None:
    j = index + step
    while 0 <= j < len(members):
        m = members[j]
        if m.type == "way" and m.ref != old_id:
            w = doc.ways.get(m.ref)
            if w is not None:
                return w.nodes
        j += step
    return None


def _traverse_backward(doc: OsmDocument, members: list[Member], index: int, old: Way, new: Way, role: str) -> bool:
    """True when the relation travels this way against its node order.

    The second half is then the first piece the route reaches, so it has to be
    inserted before the original member. Appending it always walks the route
    backwards along that way.
    """
    if role in _BACKWARD_ROLES:
        return True
    if role == "forward":
        return False
    prev = _neighbour_nodes(doc, members, index, -1, old.id)
    if prev is not None:
        at_start = _touches_end(prev, old.nodes[0])
        at_end = _touches_end(prev, new.nodes[-1])
        if at_end and not at_start:
            return True
        if at_start and not at_end:
            return False
    nxt = _neighbour_nodes(doc, members, index, 1, old.id)
    if nxt is not None and _touches_end(nxt, old.nodes[0]) and not _touches_end(nxt, new.nodes[-1]):
        return True
    return False


def _ordered_pieces(backward: bool, old: Way, new: Way, role: str) -> list[Member]:
    ids = (new.id, old.id) if backward else (old.id, new.id)
    return [Member("way", wid, role) for wid in ids]


def _rewrite_split_members(doc: OsmDocument, rel: Relation, old: Way, new: Way) -> list[Member]:
    out: list[Member] = []
    if _is_restriction(rel.tags):
        via = _via_node_ids(doc, rel)
        for m in rel.members:
            if not (m.type == "way" and m.ref == old.id):
                out.append(m)
                continue
            if m.role in {"from", "to"}:
                chosen = _piece_for_role(m.role, old, new, via, old.tags)
                out.append(Member("way", chosen.id, m.role))
            elif m.role == "via":
                backward = _restriction_via_backward(doc, rel, old, new)
                out.extend(_ordered_pieces(backward, old, new, m.role))
            else:
                # Unknown role: still one member, so converters do not see a duplicated from/to.
                chosen = _piece_for_role("from", old, new, via, old.tags) if via else old
                out.append(Member("way", chosen.id, m.role))
        return out
    for i, m in enumerate(rel.members):
        if m.type == "way" and m.ref == old.id:
            backward = _traverse_backward(doc, rel.members, i, old, new, m.role)
            out.extend(_ordered_pieces(backward, old, new, m.role))
        else:
            out.append(m)
    return out


# Preferred pull along the detached road, used when that point already sits
# outside the endpoint snap of the road that stays. A shallower meeting needs
# a longer pull. The pull never takes more than 40% of the segment; if that
# still cannot clear the snap, the split is refused.
_SEPARATE_GAP_M = 4.0
_SEPARATE_STEP_M = 0.05
_SEPARATE_BLOCKED = (
    "нельзя разъединить: конец дороги останется ближе 1 м "
    "к оставшейся и снова соединится при сдвиге узла"
)


def _highway_ways_at(doc: OsmDocument, nid: int) -> list[Way]:
    return [w for w in doc.ways_of_node(nid) if "highway" in w.tags and w.tags.get("area") != "yes"]


def _endpoint_only(way: Way, nid: int) -> bool:
    last = len(way.nodes) - 1
    indexes = [i for i, ref in enumerate(way.nodes) if ref == nid]
    return bool(indexes) and all(i == 0 or i == last for i in indexes)


def _split_way_at(doc: OsmDocument, tx: Transaction, way: Way, idx: int) -> Way:
    if idx <= 0 or idx >= len(way.nodes) - 1:
        raise OpError("разделять можно только по внутреннему узлу")
    tx.touch("way", way.id)
    second = way.nodes[idx:]
    way.nodes = way.nodes[: idx + 1]
    new_id = doc.new_id()
    tx.touch("way", new_id)
    new_way = Way(id=new_id, nodes=second, tags=dict(way.tags))
    doc.ways[new_id] = new_way
    for rel in list(doc.relations_with("way", way.id)):
        tx.touch("relation", rel.id)
        rel.members = _rewrite_split_members(doc, rel, way, new_way)
    return new_way


def _segments_of(doc: OsmDocument, proj: LocalProjection, way: Way):
    pts = []
    for nid in way.nodes:
        n = doc.nodes.get(nid)
        pts.append(None if n is None else proj.to_xy(n.lat, n.lon))
    out = []
    for a, b in zip(pts, pts[1:]):
        if a is None or b is None or dist(a, b) < 0.05:
            continue
        out.append((a, b))
    return out


def _distance_to_segments(point: tuple[float, float], segments) -> float:
    best = float("inf")
    for a, b in segments:
        _, _, d = project_on_segment(point, a, b)
        if d < best:
            best = d
    return best


def _highway_ids_touching(doc: OsmDocument, node_ids: list[int]) -> list[int]:
    """Every highway line that references one of ``node_ids``, in first-seen order."""
    out: list[int] = []
    seen: set[int] = set()
    for nid in node_ids:
        for way in _highway_ways_at(doc, nid):
            if way.id in seen:
                continue
            seen.add(way.id)
            out.append(way.id)
    return out


def _separation_step(origin: tuple[float, float], toward: tuple[float, float], segments) -> float | None:
    """Distance to slide from ``origin`` toward ``toward`` so the point clears the endpoint snap.

    ``None`` when no point in the allowed range (at most 40% of the segment) is
    further than the snap tolerance from ``segments``. Distance to the original
    junction is not enough: a branch that meets the retained road at a shallow
    angle is still inside the snap after a short slide along itself.
    """
    length = dist(origin, toward)
    if length < 0.2:
        return None
    max_step = length * 0.4
    ux = (toward[0] - origin[0]) / length
    uy = (toward[1] - origin[1]) / length
    need = _ENDPOINT_SNAP_M + 0.02

    def clearance(step: float) -> float:
        return _distance_to_segments((origin[0] + ux * step, origin[1] + uy * step), segments)

    preferred = min(_SEPARATE_GAP_M, max_step)
    if clearance(preferred) > need:
        return preferred
    # The preferred gap can sit inside the snap, while a shorter slide clears a
    # bend or a longer one clears a shallow angle. Take the first that does.
    count = int(max_step / _SEPARATE_STEP_M)
    for i in range(1, count + 1):
        step = min(max_step, i * _SEPARATE_STEP_M)
        if clearance(step) > need:
            return step
    if clearance(max_step) > need:
        return max_step
    return None


def _detach_endpoint(doc: OsmDocument, tx: Transaction, way: Way, nid: int, proj: LocalProjection, retained) -> int:
    nodes = way.nodes
    if nodes[0] == nid:
        neighbor = next((ref for ref in nodes[1:] if ref != nid), None)
    else:
        neighbor = next((ref for ref in reversed(nodes[:-1]) if ref != nid), None)
    if neighbor is None:
        raise OpError("некуда отодвинуть конец дороги")
    a, b = doc.nodes.get(nid), doc.nodes.get(neighbor)
    if a is None or b is None:
        raise OpError("некуда отодвинуть конец дороги")
    pa, pb = proj.to_xy(a.lat, a.lon), proj.to_xy(b.lat, b.lon)
    length = dist(pa, pb)
    if length < 0.2:
        raise OpError("сегмент слишком короткий, чтобы разъединить дороги")
    step = _separation_step(pa, pb, retained)
    if step is None:
        raise OpError(_SEPARATE_BLOCKED)
    point = proj.to_latlon(
        pa[0] + (pb[0] - pa[0]) / length * step,
        pa[1] + (pb[1] - pa[1]) / length * step,
    )
    # The stored lat/lon is what the next edit measures. Refuse if that round trip
    # falls back inside the snap.
    if _distance_to_segments(proj.to_xy(point[0], point[1]), retained) <= _ENDPOINT_SNAP_M:
        raise OpError(_SEPARATE_BLOCKED)
    new = _create_node(doc, tx, point[0], point[1])
    tx.touch("way", way.id)
    way.nodes = _dedupe_consecutive([new.id if ref == nid else ref for ref in way.nodes])
    if len(set(way.nodes)) < 2:
        raise OpError("после разъединения ребро стало короче двух узлов")
    return new.id


def op_separate_ways(doc, tx, node_id: int):
    """Undo a shared connection: every road but one leaves the node.

    The through road (the node sits in its middle) keeps the point. A spur that
    only ends here is pulled back along itself. A road that crossed through the
    node is split and both new ends stop short. Each new end has to land outside
    the endpoint snap of the road that stays, measured against that road's
    geometry. A shallow angle or a short segment cannot always do that; the
    split is then refused so the next vertex edit does not join the roads again.
    Positive ids are preferred over ways created in this session.
    """
    nid = int(node_id)
    _node(doc, nid)
    ways = _highway_ways_at(doc, nid)
    if len(ways) < 2:
        raise OpError("узел не соединяет несколько дорог")
    kept = min(ways, key=lambda w: (_endpoint_only(w, nid), w.id < 0, abs(w.id)))
    pending = [w for w in ways if w.id != kept.id]
    proj = _proj(doc)
    retained = _segments_of(doc, proj, kept)
    created: list[int] = []
    detached: list[int] = []
    seen: set[int] = set()
    steps = 0
    while pending:
        steps += 1
        if steps > 32:
            raise OpError("не удалось разъединить дороги")
        way = pending.pop()
        if way.id in seen or way.id not in doc.ways or nid not in way.nodes:
            continue
        if not _endpoint_only(way, nid):
            interior = next(i for i, ref in enumerate(way.nodes) if ref == nid and 0 < i < len(way.nodes) - 1)
            other = _split_way_at(doc, tx, way, interior)
            pending.append(way)
            pending.append(other)
            continue
        seen.add(way.id)
        created.append(_detach_endpoint(doc, tx, way, nid, proj, retained))
        detached.append(way.id)
    return {"id": nid, "detached": detached, "nodes": created}


def op_split_way(doc, tx, way_id: int, node_id: int):
    w = _way(doc, int(way_id))
    nid = int(node_id)
    if nid not in w.nodes:
        raise OpError("узел не принадлежит ребру")
    idx = w.nodes.index(nid)
    if idx == 0 or idx == len(w.nodes) - 1:
        raise OpError("разделять можно только по внутреннему узлу")
    tx.touch("way", w.id)
    first, second = w.nodes[: idx + 1], w.nodes[idx:]
    w.nodes = first
    new_id = doc.new_id()
    tx.touch("way", new_id)
    new_way = Way(id=new_id, nodes=second, tags=dict(w.tags))
    doc.ways[new_id] = new_way
    for rel in list(doc.relations_with("way", w.id)):
        tx.touch("relation", rel.id)
        rel.members = _rewrite_split_members(doc, rel, w, new_way)
    return {"id": w.id, "new_id": new_id}


def op_merge_nodes(doc, tx, keep_id: int, remove_id: int):
    keep_id, remove_id = int(keep_id), int(remove_id)
    _assert_node_link(doc, keep_id, remove_id)
    _merge_nodes(doc, tx, keep_id, remove_id)
    return {"id": keep_id}


def op_connect_endpoint(doc, tx, node_id: int, way_id: int):
    """Attach a dangling node to the nearest point of another way."""
    n = _node(doc, int(node_id))
    target_way = _way(doc, int(way_id))
    for w in _link_ways(doc, n.id):
        if w.id == target_way.id:
            continue
        msg = can_connect(w.tags, target_way.tags)
        if msg:
            raise OpError(msg)
    # Snapping onto the node's own neighbour would collapse the short way between
    # them (a 2-node stub or steps) and delete it; insert a fresh vertex instead.
    neighbours = {r for w in doc.ways_of_node(n.id) for a, b in zip(w.nodes, w.nodes[1:])
                  for r in (a, b) if n.id in (a, b) and r != n.id}
    target = _insert_on_way(doc, tx, target_way.id, n.lat, n.lon, None, snap_m=3.0, avoid=neighbours)
    if target != n.id:
        _merge_nodes(doc, tx, target, n.id)
    return {"id": target}


def op_reverse_way(doc, tx, id: int):
    w = _way(doc, int(id))
    tx.touch("way", w.id)
    w.nodes = list(reversed(w.nodes))
    w.tags = reversed_tags(w.tags)
    for rel in list(doc.relations_with("way", w.id)):
        if not any(m.type == "way" and m.ref == w.id and m.role in {"forward", "backward"} for m in rel.members):
            continue
        tx.touch("relation", rel.id)
        for m in rel.members:
            if m.type == "way" and m.ref == w.id:
                if m.role == "forward":
                    m.role = "backward"
                elif m.role == "backward":
                    m.role = "forward"
    return {"id": w.id}


ATTACH_MAX_M = 30.0


def op_delete_ways(doc, tx, ids: list[int]):
    """Delete several ways in one undo step (e.g. an isolated fragment)."""
    removed = []
    for wid in ids:
        if int(wid) in doc.ways:
            _delete_way(doc, tx, int(wid))
            removed.append(int(wid))
    if not removed:
        raise OpError("рёбра не найдены")
    return {"ids": removed}


def op_attach_to_road(doc, tx, node_id: int, way_id: int | None = None, max_m: float = ATTACH_MAX_M):
    """Move a free-standing node (signal, crossing, stop line) onto the nearest road and
    make it part of that way, so SUMO sees it on the edge. Tags are kept."""
    n = _node(doc, int(node_id))
    if any(is_drivable(w.tags) for w in doc.ways_of_node(n.id)):
        raise OpError("узел уже стоит на дороге")
    proj = _proj(doc)
    p = proj.to_xy(n.lat, n.lon)
    candidates = [_way(doc, int(way_id))] if way_id is not None else [
        w for w in doc.ways.values() if is_drivable(w.tags) and len(w.nodes) >= 2]
    best: tuple[float, int] | None = None
    for w in candidates:
        for a, b in zip(w.nodes, w.nodes[1:]):
            if a not in doc.nodes or b not in doc.nodes:
                continue
            na, nb = doc.nodes[a], doc.nodes[b]
            _, _, d = project_on_segment(p, proj.to_xy(na.lat, na.lon), proj.to_xy(nb.lat, nb.lon))
            if best is None or d < best[0]:
                best = (d, w.id)
    if best is None or best[0] > float(max_m):
        raise OpError(f"рядом нет дороги (ближе {float(max_m):.0f} м)")
    target = _insert_on_way(doc, tx, best[1], n.lat, n.lon, None, snap_m=1.5)
    if target != n.id:
        _merge_nodes(doc, tx, target, n.id)  # keeps the node's tags on the road vertex
    return {"id": target, "way_id": best[1], "distance_m": round(best[0], 1)}


SIGNAL_SNAP_M = 30.0
SIGNAL_MOVE_M = 120.0


def signal_spot(doc: OsmDocument, nid: int) -> str | None:
    """'junction' or 'crossing' when a traffic signal may stand on this node, else None."""
    n = doc.nodes.get(nid)
    if n is None:
        return None
    ways = [w for w in doc.ways_of_node(nid) if is_drivable(w.tags)]
    if not ways:
        return None
    if n.tags.get("highway") == "crossing" or "crossing" in n.tags:
        return "crossing"
    arms = 0
    for w in ways:
        for i, ref in enumerate(w.nodes):
            if ref == nid:
                arms += (i > 0) + (i < len(w.nodes) - 1)
    return "junction" if arms >= 3 or len(ways) >= 2 else None


def op_place_signal(doc, tx, node_id: int | None = None, way_id: int | None = None,
                    lat: float | None = None, lon: float | None = None, move_from: int | None = None,
                    snap_m: float = SIGNAL_SNAP_M):
    """Put a traffic signal only where one belongs: a road junction or a crossing.

    A click near such a node (on the clicked road, within ``snap_m``) snaps to it.
    A signal on a crossing turns it into a signalised crossing. ``move_from``
    moves an existing signal tag from a misplaced node to the nearest valid spot.
    """
    tram = doc.ways.get(int(way_id)) if way_id is not None else None
    if tram is None and node_id is not None:
        linked = list(doc.ways_of_node(int(node_id)))
        if linked and all(w.tags.get('railway') == 'tram' for w in linked):
            tram = linked[0]
    if tram is not None and tram.tags.get('railway') == 'tram':
        target = int(node_id) if node_id is not None else _insert_on_way(doc, tx, tram.id, float(lat), float(lon), None)
        op_set_tags(doc, tx, kind='node', id=target, tags={'railway':'signal', 'railway:signal:direction':'forward', 'uds:signal:tram':'yes'})
        return {'id': target}
    proj = _proj(doc)
    target: int | None = None
    if node_id is not None and signal_spot(doc, int(node_id)):
        target = int(node_id)
    else:
        src = int(move_from) if move_from is not None else (int(node_id) if node_id is not None else None)
        if src is not None:
            n = _node(doc, src)
            lat, lon = n.lat, n.lon
            cand = {r for w in doc.ways_of_node(src) if is_drivable(w.tags) for r in w.nodes}
        elif way_id is not None and lat is not None and lon is not None:
            cand = set(_way(doc, int(way_id)).nodes)
        else:
            raise OpError("укажите узел или точку на дороге")
        p = proj.to_xy(float(lat), float(lon))  # type: ignore[arg-type]
        if move_from is not None:
            snap_m = max(float(snap_m), SIGNAL_MOVE_M)  # a misplaced signal may sit mid-block
        best = None
        for r in cand:
            if r == src or r not in doc.nodes or not signal_spot(doc, r):
                continue
            d = dist(p, proj.to_xy(doc.nodes[r].lat, doc.nodes[r].lon))
            if d <= float(snap_m) and (best is None or d < best[0]):
                best = (d, r)
        if best is None:
            raise OpError("светофор ставится только на перекрёстке или пешеходном переходе — "
                          f"рядом (до {float(snap_m):.0f} м) их нет")
        target = best[1]
    if move_from is not None:
        old = _node(doc, int(move_from))
        if old.tags.get("highway") == "traffic_signals":
            tx.touch("node", old.id)
            old.tags.pop("highway", None)
            old.tags.pop("traffic_signals", None)
            old.tags.pop("traffic_signals:direction", None)
    t = doc.nodes[target]
    tx.touch("node", target)
    if signal_spot(doc, target) == "crossing":
        t.tags["highway"] = "crossing"
        t.tags["crossing"] = "traffic_signals"
    else:
        t.tags["highway"] = "traffic_signals"
    return {"id": target, "spot": signal_spot(doc, target)}


LEVEL_HEIGHT_M = 5.0  # height of one OSM layer step
GRADE_MARGIN = 0.95   # ramps a bit gentler than the limit, so rounding never trips the check


def way_level(tags: dict[str, str]) -> int:
    layer = parse_int(tags.get("layer"))
    if layer is not None:
        return layer
    if tags.get("bridge") not in (None, "no"):
        return 1
    if tags.get("tunnel") not in (None, "no"):
        return -1
    return 0


def same_grade(a: dict[str, str], b: dict[str, str]) -> bool:
    """True when two ways may meet in plan: same level and the same bridge/tunnel flags.

    ``layer=1`` does not meet a ground road, a bridge does not meet a road that
    is not a bridge, and a tunnel does not meet either. Two bridges on the same
    layer do meet.
    """
    def raised(tags: dict[str, str], key: str) -> bool:
        return tags.get(key) not in (None, "no")

    return (
        way_level(a) == way_level(b)
        and raised(a, "bridge") == raised(b, "bridge")
        and raised(a, "tunnel") == raised(b, "tunnel")
    )


# Endpoint placed on another carriageway (a click a pixel off the line).
# Interior vertices only snap when they are essentially on the line, so a
# parallel carriageway a metre away is left alone.
_ENDPOINT_SNAP_M = 1.0
_VERTEX_ON_LINE_M = 0.35


def _link_ways(doc: OsmDocument, nid: int) -> list[Way]:
    return [w for w in doc.ways_of_node(nid) if "highway" in w.tags and w.tags.get("area") != "yes"]


def _assert_compatible_with(doc: OsmDocument, tags: dict[str, str], others: list[Way]) -> None:
    for w in others:
        if w.tags.get("area") == "yes" or "highway" not in w.tags:
            continue
        msg = can_connect(tags, w.tags)
        if msg:
            raise OpError(msg)


def _assert_node_link(doc: OsmDocument, a: int, b: int) -> None:
    """Merging these nodes must not tie incompatible highway classes together."""
    for wa in _link_ways(doc, a):
        for wb in _link_ways(doc, b):
            if wa.id == wb.id:
                continue
            msg = can_connect(wa.tags, wb.tags)
            if msg:
                raise OpError(msg)


def _with_warnings(result: dict, warnings: list[str]) -> dict:
    if warnings:
        result = {**result, "warnings": warnings}
    return result


def _join_crossings(doc: OsmDocument, tx: Transaction, way_ids: list[int]) -> list[str]:
    """Insert a shared node where ``way_ids`` cross another highway at the same grade.

    A bridge, tunnel or different ``layer`` is left unsplit. Classes that
    ``can_connect`` rejects are also left unsplit; the returned strings are
    warnings for the UI. The roads themselves stay.
    """
    warnings: list[str] = []
    warned: set[tuple[int, int]] = set()
    ids = [
        wid for wid in dict.fromkeys(int(w) for w in way_ids)
        if (w := doc.ways.get(int(wid))) is not None and "highway" in w.tags
        and w.tags.get("area") != "yes" and len(w.nodes) >= 2
    ]
    if not ids:
        return warnings
    proj = _proj(doc)

    def xy_of(nid: int) -> tuple[float, float] | None:
        n = doc.nodes.get(nid)
        return None if n is None else proj.to_xy(n.lat, n.lon)

    segments: list[tuple] = []
    idx = GridIndex(cell=40.0)
    for w in doc.ways.values():
        if "highway" not in w.tags or w.tags.get("area") == "yes" or len(w.nodes) < 2:
            continue
        pts = [xy_of(nid) for nid in w.nodes]
        for i in range(len(w.nodes) - 1):
            pa, pb = pts[i], pts[i + 1]
            if pa is None or pb is None or dist(pa, pb) < 0.05:
                continue
            item = (w.id, i, pa, pb)
            segments.append(item)
            idx.insert_box(pa[0], pa[1], pb[0], pb[1], item)

    def skip(a: Way, b: Way) -> bool:
        if not same_grade(a.tags, b.tags):
            return True
        msg = can_connect(a.tags, b.tags)
        if not msg:
            return False
        key = (min(a.id, b.id), max(a.id, b.id))
        if key not in warned:
            warned.add(key)
            warnings.append(msg)
        return True

    # (lat, lon, way a, way b, prefer existing node or None)
    jobs: list[tuple[float, float, int, int, int | None]] = []
    seen: set[tuple] = set()

    def add_job(x: float, y: float, wa: int, wb: int, prefer: int | None) -> None:
        key = (min(wa, wb), max(wa, wb), round(x * 2), round(y * 2))
        if key in seen:
            return
        seen.add(key)
        lat, lon = proj.to_latlon(x, y)
        jobs.append((lat, lon, wa, wb, prefer))

    for wid in ids:
        w = doc.ways[wid]
        ends = {w.nodes[0], w.nodes[-1]}
        for nid in dict.fromkeys(w.nodes):
            p = xy_of(nid)
            if p is None:
                continue
            tol = _ENDPOINT_SNAP_M if nid in ends else _VERTEX_ON_LINE_M
            for ow, _i, pa, pb in idx.query(p[0], p[1], tol):
                if ow == wid or ow not in doc.ways:
                    continue
                other = doc.ways[ow]
                q, _t, d = project_on_segment(p, pa, pb)
                if d > tol or nid in other.nodes or skip(w, other):
                    continue
                add_job(q[0], q[1], wid, ow, nid)

    for wid in ids:
        w = doc.ways[wid]
        for sw, i, pa, pb in segments:
            if sw != wid:
                continue
            mid = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
            for ow, j, qa, qb in idx.query(mid[0], mid[1], dist(pa, pb) / 2 + _ENDPOINT_SNAP_M):
                if ow == wid or ow not in doc.ways:
                    continue
                other = doc.ways[ow]
                hit = segment_intersection(pa, pb, qa, qb)
                if hit is None:
                    continue
                t, u = hit
                # Both hits hugging endpoints means the ways already meet, or the
                # vertex pass above will snap them. Interior crossings fall through.
                if min(t, 1 - t) * dist(pa, pb) < 0.3 and min(u, 1 - u) * dist(qa, qb) < 0.3:
                    continue
                if skip(w, other):
                    continue
                x = pa[0] + (pb[0] - pa[0]) * t
                y = pa[1] + (pb[1] - pa[1]) * t
                prefer = None
                for nid, origin in (
                    (w.nodes[i], pa), (w.nodes[min(i + 1, len(w.nodes) - 1)], pb),
                    (other.nodes[j], qa), (other.nodes[min(j + 1, len(other.nodes) - 1)], qb),
                ):
                    if nid in doc.nodes and dist((x, y), origin) <= 0.5:
                        prefer = nid
                        break
                add_job(x, y, wid, ow, prefer)

    alias: dict[int, int] = {}

    def resolve(nid: int) -> int:
        seen_ids: set[int] = set()
        while nid in alias and nid not in seen_ids:
            seen_ids.add(nid)
            nid = alias[nid]
        return nid

    def blocked_merge(keep_id: int, remove_id: int) -> bool:
        """True when merging would tie a highway to one it must not meet.

        Snapping to a vertex is not enough: that vertex may already sit on a
        motorway while the segment we crossed is only its link.
        """
        blocked = False
        for a in _link_ways(doc, keep_id):
            for b in _link_ways(doc, remove_id):
                if a.id == b.id:
                    continue
                msg = can_connect(a.tags, b.tags)
                if not msg:
                    continue
                blocked = True
                key = (min(a.id, b.id), max(a.id, b.id))
                if key not in warned:
                    warned.add(key)
                    warnings.append(msg)
        return blocked

    def thread(wid: int, nid: int) -> int:
        nid = resolve(nid)
        w = doc.ways.get(wid)
        if w is None or nid not in doc.nodes or nid in w.nodes:
            return nid
        n = doc.nodes[nid]
        placed = _insert_on_way(doc, tx, wid, n.lat, n.lon, None, snap_m=0.8)
        if placed != nid and placed in doc.nodes and nid in doc.nodes:
            if blocked_merge(placed, nid):
                return nid  # the snap target is shared with a forbidden road
            _merge_nodes(doc, tx, placed, nid)
            alias[nid] = placed
            return placed
        return placed if placed in doc.nodes else nid

    for lat, lon, wa, wb, prefer in jobs:
        if wa not in doc.ways or wb not in doc.ways:
            continue
        prefer_id = resolve(prefer) if prefer is not None else None
        if prefer_id is not None and prefer_id not in doc.nodes:
            prefer_id = None
        nid = _insert_on_way(doc, tx, wa, lat, lon, None, snap_m=0.8)
        if prefer_id is not None and prefer_id != nid and prefer_id in doc.nodes and nid in doc.nodes:
            if not blocked_merge(nid, prefer_id):
                _merge_nodes(doc, tx, nid, prefer_id)
                alias[prefer_id] = nid
        if nid not in doc.nodes:
            nid = resolve(nid)
        if nid in doc.nodes:
            thread(wb, nid)
    return warnings


def _set_ele(doc: OsmDocument, tx: Transaction, nid: int, value: float) -> None:
    n = doc.nodes[nid]
    # centimetres: on a 2 m segment a 5 cm rounding step alone is a 25 ‰ grade
    text = f"{value:.2f}"
    if text.endswith("0"):
        text = text[:-1]   # 5.00 → 5.0, 3.20 → 3.2
    if n.tags.get("ele") != text:
        tx.touch("node", nid)
        n.tags["ele"] = text


def op_smooth_z(doc, tx, way_id: int, ramp_m: float | None = None, level_height: float = LEVEL_HEIGHT_M):
    """Smooth Z: bridge/tunnel nodes get ele = layer x level_height and the
    approaches descend linearly to 0 on each side, so 3D models do not jump
    from ground to deck in one segment.

    Ramp length L = height / allowed grade (СП 42.13330.2016, see ``norms.max_grade``;
    the strictest class among the approaches): 5 m at 70 ‰ ≈ 75 m, 10 m ≈ 150 m.
    The ramp is not limited to the first approach way: every ground node within
    L metres along the road network from the bridge ends gets
    ele = height · (1 − d / L), d = network distance. Any segment then rises at most
    height / L, wherever the ramp crosses into the next way, and a node reached
    by two ramps keeps the higher one. ``ramp_m`` forces L. Heights are relative
    to the ground.
    """
    import heapq

    w = _way(doc, int(way_id))
    level = way_level(w.tags)
    if level == 0:
        raise OpError("у ребра нет уровня: сначала задайте мост или тоннель (layer)")
    if ramp_m is not None and float(ramp_m) <= 0:
        raise OpError("длина съезда должна быть больше нуля")
    height = level * float(level_height)
    for nid in dict.fromkeys(w.nodes):
        if nid in doc.nodes:
            _set_ele(doc, tx, nid, height)
    proj = _proj(doc)
    index = doc.node_way_index()

    def xy(nid: int) -> tuple[float, float]:
        n = doc.nodes[nid]
        return proj.to_xy(n.lat, n.lon)

    def ground(way: Way) -> bool:
        return way.id != w.id and "highway" in way.tags and way_level(way.tags) == 0

    def elevated(nid: int) -> bool:  # on some bridge or tunnel (this one or another)
        return any(way_level(doc.ways[x].tags) != 0 for x in index.get(nid, []) if x in doc.ways)

    ends = [e for e in dict.fromkeys((w.nodes[0], w.nodes[-1])) if e in doc.nodes]
    approaches = [doc.ways[x] for e in ends for x in index.get(e, []) if x in doc.ways and ground(doc.ways[x])]
    if ramp_m is not None:
        ramp = float(ramp_m)
    else:
        grade = min((max_grade(a.tags) for a in approaches), default=max_grade(w.tags))
        ramp = abs(height) / (grade * GRADE_MARGIN)

    def spread(length: float) -> tuple[dict[int, float], float]:
        """Dijkstra over ground roads from the bridge ends up to `length`; also the
        strictest allowed grade among the roads the ramp reaches."""
        best: dict[int, float] = {e: 0.0 for e in ends}
        strictest = 1.0
        heap = [(0.0, e) for e in ends]
        while heap:
            d, a = heapq.heappop(heap)
            if d > best.get(a, float("inf")) or d >= length:
                continue
            for x in index.get(a, []):
                way = doc.ways.get(x)
                if way is None or not ground(way):
                    continue
                for i, ref in enumerate(way.nodes):
                    if ref != a:
                        continue
                    for j in (i - 1, i + 1):
                        if not 0 <= j < len(way.nodes):
                            continue
                        b = way.nodes[j]
                        if b not in doc.nodes or b in ends:
                            continue
                        strictest = min(strictest, max_grade(way.tags))
                        nd = d + dist(xy(a), xy(b))
                        if nd < best.get(b, float("inf")):
                            best[b] = nd
                            if not elevated(b):   # another bridge keeps its own height and stops the ramp
                                heapq.heappush(heap, (nd, b))
        return best, strictest

    best, strictest = spread(ramp)
    for _ in range(3):   # the ramp ran onto a stricter road class: lengthen it and spread again
        if ramp_m is not None or abs(height) / ramp <= strictest * GRADE_MARGIN + 1e-9:
            break
        ramp = abs(height) / (strictest * GRADE_MARGIN)
        best, strictest = spread(ramp)
    for nid, d in best.items():
        if nid in ends or d >= ramp or elevated(nid):
            continue
        value = height * (1 - d / ramp)
        old = doc.nodes[nid].tags.get("ele")
        try:
            prev = float(old) if old is not None else None
        except ValueError:
            prev = None
        if prev is not None and prev * value > 0 and abs(prev) >= abs(value):
            continue   # already on a higher ramp of a neighbouring bridge
        _set_ele(doc, tx, nid, value)
    return {"id": w.id, "height": height, "ramps": len(approaches), "ramp_m": round(ramp, 1)}


def op_set_restriction_via(doc, tx, relation_id: int, node_id: int):
    """Point a turn restriction at the node where its from and to ways actually meet."""
    rel = doc.relations.get(int(relation_id))
    if rel is None:
        raise OpError(f"отношение {relation_id} не найдено")
    nid = int(node_id)
    if nid not in doc.nodes:
        raise OpError(f"узел {nid} не найден")
    for role in ("from", "to"):
        member = next((m for m in rel.members if m.role == role and m.type == "way"), None)
        w = doc.ways.get(member.ref) if member else None
        if w is None or len(w.nodes) < 2 or nid not in (w.nodes[0], w.nodes[-1]):
            raise OpError(f"узел {nid} не на конце ребра {role}")
    tx.touch("relation", rel.id)
    rel.members = [m for m in rel.members if m.role != "via"] + [Member("node", nid, "via")]
    return {"id": rel.id}


def restriction_way_matches(way: Way | None, role: str, via: int) -> bool:
    if way is None or len(way.nodes) < 2 or not is_drivable(way.tags):
        return False
    first, last = way.nodes[0], way.nodes[-1]
    start, end = (first, last) if role == "to" else (last, first)
    flow = direction(way.tags)
    return (start == via and flow >= 0) or (end == via and flow <= 0)


def op_replace_restriction_way(doc, tx, relation_id: int, role: str, old_id: int | None, way_id: int, via: int):
    """Replace one from/to way, rechecking the reviewed junction on apply."""
    rel = doc.relations.get(relation_id)
    if role not in ("from", "to") or rel is None or not rel.tags.get("type", "").startswith("restriction"):
        raise OpError("отношение запрета поворота не найдено")
    other_role = "to" if role == "from" else "from"
    members = [m for m in rel.members if m.role == role]
    others = [m for m in rel.members if m.role == other_role and m.type == "way"]
    vias = [m for m in rel.members if m.role == "via"]
    expected = [] if old_id is None else [Member("way", old_id, role)]
    if members != expected or len(others) != 1 or vias != [Member("node", via, "via")] or via not in doc.nodes:
        raise OpError("участники запрета изменились — проверьте запрет заново")
    if (way_id == others[0].ref or not restriction_way_matches(doc.ways.get(way_id), role, via)
            or not restriction_way_matches(doc.ways.get(others[0].ref), other_role, via)):
        raise OpError("рёбра не образуют поворот через узел via")
    tx.touch("relation", rel.id)
    replacement = Member("way", way_id, role)
    if members:
        rel.members[rel.members.index(members[0])] = replacement
    else:
        rel.members.append(replacement)
    return {"id": rel.id}


def op_move_selection(doc, tx, refs: list[dict], dlat: float, dlon: float):
    """Move shared vertices exactly once in one undo transaction."""
    ids = set()
    for ref in refs:
        if ref['kind'] == 'node':
            ids.add(int(ref['id']))
        elif ref['kind'] == 'way':
            ids.update(_way(doc, int(ref['id'])).nodes)
        else:
            raise OpError('выберите узлы или рёбра')
    for nid in ids:
        n = _node(doc, nid)
        if not math.isfinite(float(dlat)) or not math.isfinite(float(dlon)):
            raise OpError('координаты должны быть конечными')
        tx.touch('node',nid)
        n.set_position(n.lat+float(dlat),n.lon+float(dlon))
    _join_crossings(doc,tx,_highway_ids_touching(doc,ids))
    return {'nodes': len(ids)}


def op_edit_segment(doc, tx, way_id: int, start: dict, end: dict, tags: dict | None = None, attributes: list[dict] | None = None):
    """Materialise two projected endpoints, split with relation rewriting, tag middle."""
    way = _way(doc, int(way_id))
    if way.is_closed:
        raise OpError('выберите незамкнутое ребро')
    a = int(start['id']) if 'id' in start else _insert_on_way(doc, tx, way.id, float(start['lat']), float(start['lon']), None)
    b = int(end['id']) if 'id' in end else _insert_on_way(doc, tx, way.id, float(end['lat']), float(end['lon']), None)
    if a not in way.nodes or b not in way.nodes or a == b:
        raise OpError('выберите два различных конца участка на ребре')
    i, j = sorted([way.nodes.index(a), way.nodes.index(b)])
    a, b = way.nodes[i], way.nodes[j]
    middle = way.id
    if i > 0:
        middle = op_split_way(doc, tx, way.id, a)['new_id']
    if b != doc.ways[middle].nodes[-1]:
        op_split_way(doc, tx, middle, b)
    if tags:
        op_set_tags(doc, tx, kind='way', id=middle, tags=tags)
    for attribute in attributes or []:
        args = dict(attribute.get('args', {}))
        args.pop('way_id',None)
        args.pop('id',None)
        if attribute.get('op') == 'set_lanes':
            op_set_lanes(doc,tx,way_id=middle,**args)
        elif attribute.get('op') == 'smooth_z':
            op_smooth_z(doc,tx,way_id=middle)
        else:
            raise OpError('для участка поддерживаются теги, полосы и плавный Z')
    return {'id': middle}


def _direction_tags(tags: dict[str, str], side: str) -> dict[str, str]:
    """Tags of one carriageway: ``*:forward`` (or ``*:backward``) become plain keys,
    the other direction's keys are dropped, the road becomes one-way."""
    other = "backward" if side == "forward" else "forward"
    out: dict[str, str] = {}
    for k, v in tags.items():
        if f":{other}" in k or k in ("oneway",):
            continue
        out[k.replace(f":{side}", "")] = v
    for k, v in tags.items():   # direction-specific values win over the shared ones
        if f":{side}" in k:
            out[k.replace(f":{side}", "")] = v
    out["oneway"] = "yes"
    return out


def op_split_carriageways(doc, tx, way_id: int, median_m: float = 1.0):
    """Turn a two-way road into two one-way carriageways (as OSM maps divided roads).

    Each carriageway sits at the centre of its own direction: offset from the old
    axis by half its lanes (lane width by class, СП 42.13330.2016) plus half the
    median. The ends and every node where another road joins stay shared, so the
    network stays connected there; in between, new roads can be attached to either
    direction. The old way keeps its id as the forward carriageway; the backward
    one is a new way drawn in its own direction of travel.
    """
    w = _way(doc, int(way_id))
    if not is_drivable(w.tags):
        raise OpError("разделить можно только проезжую дорогу")
    if direction(w.tags) != 0:
        raise OpError("дорога уже односторонняя")
    if len(w.nodes) < 3:
        raise OpError("у дороги нет промежуточных точек: сначала добавьте узел посередине")
    first, last = w.nodes[0], w.nodes[-1]
    restrictions = [rel for rel in doc.relations_with("way", w.id) if _is_restriction(rel.tags)]
    for rel in restrictions:
        if not _via_node_ids(doc, rel) & {first, last}:
            raise OpError(f"запрет поворота {rel.id} проходит через середину дороги: сначала исправьте или удалите его")
    t = w.tags
    total = parse_int(t.get("lanes"))
    fw, bw = parse_int(t.get("lanes:forward")), parse_int(t.get("lanes:backward"))
    if fw is None and bw is None:
        fw = bw = max(1, (total or 2) // 2)
    elif fw is None:
        fw = max(1, (total or bw + 1) - bw)
    elif bw is None:
        bw = max(1, (total or fw + 1) - fw)
    lw = lane_width(t)
    off_f = fw * lw / 2 + float(median_m) / 2
    off_b = bw * lw / 2 + float(median_m) / 2

    index = doc.node_way_index()
    proj = _proj(doc)
    pts = [proj.to_xy(doc.nodes[n].lat, doc.nodes[n].lon) for n in w.nodes]

    def xy(nid: int) -> tuple[float, float]:
        n = doc.nodes[nid]
        return proj.to_xy(n.lat, n.lon)

    def normal(i: int) -> tuple[float, float]:   # right-hand unit normal at vertex i
        a, b = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        return dy / length, -dx / length

    def others(nid: int) -> list[Way]:
        return [doc.ways[x] for x in index.get(nid, []) if x != w.id and x in doc.ways and "highway" in doc.ways[x].tags]

    def collinear(i: int, other: Way) -> bool:
        """`other` goes on from this end of the road in roughly the same direction (within ~35°)."""
        if i not in (0, len(w.nodes) - 1) or w.nodes[i] not in (other.nodes[0], other.nodes[-1]):
            return False
        k = other.nodes.index(w.nodes[i])
        nb = other.nodes[k + 1] if k == 0 and len(other.nodes) > 1 else other.nodes[k - 1] if k > 0 else None
        if nb is None or nb not in doc.nodes:
            return False
        inner = pts[1] if i == 0 else pts[-2]
        ax, ay = pts[i][0] - inner[0], pts[i][1] - inner[1]          # along the road, outwards
        bx, by = xy(nb)[0] - pts[i][0], xy(nb)[1] - pts[i][1]        # into the other way
        la, lb = (ax * ax + ay * ay) ** 0.5 or 1.0, (bx * bx + by * by) ** 0.5 or 1.0
        return (ax * bx + ay * by) / (la * lb) > 0.82                  # within ~35°

    # a stop or another non-restriction relation member keeps its node; turn restrictions are
    # carried over to the new junction nodes below
    pinned = {m.ref for rel in doc.relations.values() if not _is_restriction(rel.tags)
              for m in rel.members if m.type == "node"}
    junctions = {i for i, n in enumerate(w.nodes) if others(n) or i in (0, len(w.nodes) - 1)}
    # Shared points stay only where the road merges back into an undivided street, at a
    # dead end, or where a relation (a turn restriction's via, a stop) pins the node.
    # Everywhere else each carriageway gets its own junction on the crossing road.
    merge = {i for i in junctions
             if not others(w.nodes[i]) or w.nodes[i] in pinned
             or any(direction(o.tags) == 0 and collinear(i, o) for o in others(w.nodes[i]))}

    right: list[int] = []
    left: list[int] = []
    split_at: dict[int, tuple[int, int]] = {}   # crossing node → (its right, left copies)
    for i, nid in enumerate(w.nodes):
        if i in merge:
            right.append(nid)
            left.append(nid)
            continue
        nx, ny = normal(i)
        node_tags = doc.nodes[nid].tags
        made: list[int] = []
        for side, off, out in ((1, off_f, right), (-1, off_b, left)):
            px, py = pts[i][0] + side * nx * off, pts[i][1] + side * ny * off
            if i in junctions:
                # put the junction on the crossing road itself where it reaches that side
                for o in others(nid):
                    k = o.nodes.index(nid)
                    for j in (k - 1, k + 1):
                        if 0 <= j < len(o.nodes) and o.nodes[j] in doc.nodes:
                            vx, vy = xy(o.nodes[j])[0] - pts[i][0], xy(o.nodes[j])[1] - pts[i][1]
                            lat_off = vx * nx + vy * ny
                            if lat_off * side > 0 and abs(lat_off) > off * 1.1:
                                frac = side * off / lat_off
                                px, py = pts[i][0] + vx * frac, pts[i][1] + vy * frac
                                break
                    else:
                        continue
                    break
            lat, lon = proj.to_latlon(px, py)
            node = _create_node(doc, tx, lat, lon, dict(node_tags)).id
            out.append(node)
            made.append(node)
        if i in junctions:
            split_at[nid] = (made[0], made[1])
    # every road that met the old axis now meets the carriageway on its side, and a road
    # that crosses or ends there runs on across the median to the far carriageway
    normals = {w.nodes[i]: normal(i) for i in range(len(w.nodes))}
    for nid, (r_node, l_node) in split_at.items():
        nx, ny = normals[nid]
        jx, jy = pts[w.nodes.index(nid)]

        def side_node(other_nid: int) -> int:
            ox, oy = xy(other_nid)
            return r_node if (ox - jx) * nx + (oy - jy) * ny >= 0 else l_node

        i_end = w.nodes.index(nid)
        for o in others(nid):
            tx.touch("way", o.id)
            if direction(o.tags) != 0 and collinear(i_end, o):
                # the street goes on as an existing one-way carriageway: join only the new
                # carriageway of the same direction. Traffic flowing into this node continues
                # on the carriageway that leaves it, and the other way round.
                d = direction(o.tags)
                flows_in = (o.nodes[-1] == nid) if d > 0 else (o.nodes[0] == nid)
                at_start = i_end == 0
                target = (r_node if at_start else l_node) if flows_in else (l_node if at_start else r_node)
                o.nodes = [target if ref == nid else ref for ref in o.nodes]
                continue
            new: list[int] = []
            for k, ref in enumerate(o.nodes):
                if ref != nid:
                    new.append(ref)
                    continue
                prev = o.nodes[k - 1] if k > 0 else None
                nxt = o.nodes[k + 1] if k + 1 < len(o.nodes) else None
                if prev is not None and nxt is not None:
                    a, b = side_node(prev), side_node(nxt)
                    new.extend([a] if a == b else [a, b])
                elif prev is not None:          # the road ends here: carry it across the median
                    near = side_node(prev)
                    new.extend([near, l_node if near == r_node else r_node])
                elif nxt is not None:
                    near = side_node(nxt)
                    new.extend([l_node if near == r_node else r_node, near])
            o.nodes = _dedupe_consecutive(new)
    tx.touch("way", w.id)
    old_inner = [n for i, n in enumerate(w.nodes) if i not in merge]
    base_tags = dict(t)
    w.nodes = right
    w.tags = _direction_tags(base_tags, "forward")
    w.tags["lanes"] = str(fw)
    bid = doc.new_id()
    tx.touch("way", bid)
    back_tags = _direction_tags(base_tags, "backward")
    back_tags["lanes"] = str(bw)
    doc.ways[bid] = Way(id=bid, nodes=list(reversed(left)), tags=back_tags)
    # turn restrictions: «from» is the carriageway arriving at the via node, «to» the one
    # leaving it. The forward carriageway ends at the old last node and starts at the first,
    # the backward one the other way round.
    removed: list[int] = []
    for rel in restrictions:
        via = _via_node_ids(doc, rel)
        end = last if last in via else first
        if end in split_at:
            # the end was split into a junction per carriageway: «from» must arrive at, and «to»
            # leave from, the same new node; if they no longer meet, the turn is impossible now
            r_node, l_node = split_at[end]
            at_start = end == first
            arrive_at = (bid, l_node) if at_start else (w.id, r_node)
            leave_at = (w.id, r_node) if at_start else (bid, l_node)
            spots: dict[str, tuple[int, int]] = {}
            for m in rel.members:
                if m.type != "way" or m.role not in ("from", "to"):
                    continue
                if m.ref == w.id:
                    spots[m.role] = arrive_at if m.role == "from" else leave_at
                    continue
                on = [n for n in (r_node, l_node) if m.ref in doc.ways and n in doc.ways[m.ref].nodes]
                if len(on) != 1:
                    raise OpError(f"запрет поворота {rel.id} на пересечении неоднозначен после разделения: "
                                  "сначала исправьте или удалите его")
                spots[m.role] = (m.ref, on[0])
            tx.touch("relation", rel.id)
            if spots.get("from", (0, 0))[1] != spots.get("to", (0, -1))[1]:
                del doc.relations[rel.id]   # the two ways no longer meet: nothing left to forbid
                removed.append(rel.id)
                continue
            node = spots["from"][1]
            rel.members = [Member("way", spots[m.role][0], m.role) if m.type == "way" and m.role in spots
                           else Member("node", node, "via") if m.role == "via" else m
                           for m in rel.members]
            continue
        arrive, leave = (w.id, bid) if last in via else (bid, w.id)
        tx.touch("relation", rel.id)
        rel.members = [Member("way", arrive if m.role == "from" else leave, m.role)
                       if m.type == "way" and m.ref == w.id and m.role in ("from", "to") else m
                       for m in rel.members]
    # routes and other relations that used the road now use both carriageways
    for rel in doc.relations_with("way", w.id):
        if _is_restriction(rel.tags):
            continue
        tx.touch("relation", rel.id)
        members: list[Member] = []
        for m in rel.members:
            members.append(m)
            if m.type == "way" and m.ref == w.id:
                members.append(Member("way", bid, m.role))
        rel.members = members
    # the old inner points are no longer on any road: drop them unless something else uses them
    still_used = {r for x in doc.ways.values() for r in x.nodes}
    for nid in old_inner:
        if nid not in still_used and not any(True for _ in doc.relations_with("node", nid)):
            tx.touch("node", nid)
            del doc.nodes[nid]
    return {"id": w.id, "new_id": bid, "forward_lanes": fw, "backward_lanes": bw,
            "offset_m": [round(off_f, 2), round(off_b, 2)], "removed_restrictions": removed}


def op_merge_ways(doc, tx, way_id: int, other_id: int):
    """Join two roads that meet end to end into one way (the reverse of «Разделить ребро»).

    The first way keeps its id and its tags; a two-way second road is reversed if needed
    so the line runs on, a one-way pair must already run in the same direction. Tags that
    differ are reported (the first way's values win). Routes that used the second road
    now use the merged one.
    """
    w, o = _way(doc, int(way_id)), _way(doc, int(other_id))
    if w.id == o.id:
        raise OpError("выберите другое ребро")
    if w.tags.get("highway") != o.tags.get("highway"):
        raise OpError("объединяются только рёбра одного класса дороги")
    if w.nodes[0] == w.nodes[-1] or o.nodes[0] == o.nodes[-1]:
        raise OpError("замкнутое ребро объединить нельзя")
    shared = {w.nodes[0], w.nodes[-1]} & {o.nodes[0], o.nodes[-1]}
    if not shared:
        raise OpError("рёбра не соединены концами")
    joint = next(iter(shared))
    for rel in doc.relations_with("way", o.id):
        if _is_restriction(rel.tags) and joint in _via_node_ids(doc, rel):
            raise OpError(f"в узле соединения действует запрет поворота {rel.id}: сначала удалите его")
    dw, do = direction(w.tags), direction(o.tags)
    if (dw != 0) != (do != 0):
        raise OpError("одно ребро одностороннее, другое двустороннее — объединить нельзя")
    a = list(w.nodes) if w.nodes[-1] == joint else list(reversed(w.nodes))
    b = list(o.nodes) if o.nodes[0] == joint else list(reversed(o.nodes))
    if dw != 0:
        # one-way: the line must keep the travel direction of both parts
        fwd_w = (a == w.nodes) == (dw > 0)
        fwd_o = (b == o.nodes) == (do > 0)
        if fwd_w != fwd_o:
            raise OpError("односторонние рёбра направлены навстречу друг другу")
    reversed_w = a != w.nodes
    tags = reversed_tags(w.tags) if reversed_w and dw == 0 else dict(w.tags)
    other_tags = reversed_tags(o.tags) if b != o.nodes and do == 0 else dict(o.tags)
    differ = sorted(k for k in set(tags) | set(other_tags) if tags.get(k) != other_tags.get(k))
    tx.touch("way", w.id)
    w.nodes = a + b[1:]
    if reversed_w and dw != 0:
        # keep the stored order the way traffic goes
        w.nodes = list(reversed(w.nodes))
    w.tags = tags
    for rel in list(doc.relations_with("way", o.id)):
        tx.touch("relation", rel.id)
        members: list[Member] = []
        for m in rel.members:
            nm = Member("way", w.id, m.role) if m.type == "way" and m.ref == o.id else m
            if members and nm.type == "way" and members[-1].type == "way" and members[-1].ref == nm.ref:
                continue
            members.append(nm)
        rel.members = members
    tx.touch("way", o.id)
    del doc.ways[o.id]
    warnings = [f"теги различались, оставлены значения первого ребра: {', '.join(differ)}"] if differ else []
    return _with_warnings({"id": w.id}, warnings)


OPS: dict[str, tuple[Callable[..., dict], str]] = {
    "merge_ways": (op_merge_ways, "Рёбра объединены"),
    "move_selection": (op_move_selection, "Группа сдвинута"),
    "edit_segment": (op_edit_segment, "Атрибуты участка изменены"),
    "split_carriageways": (op_split_carriageways, "Дорога разделена на две проезжие части"),
    "replace_restriction_way": (op_replace_restriction_way, "Исправлено ребро запрета поворота"),
    "set_restriction_via": (op_set_restriction_via, "Исправлен узел запрета поворота"),
    "smooth_z": (op_smooth_z, "Плавный Z-переход"),
    "attach_to_road": (op_attach_to_road, "Узел привязан к дороге"),
    "delete_ways": (op_delete_ways, "Удалён фрагмент сети"),
    "place_signal": (op_place_signal, "Светофор установлен"),
    "set_tags": (op_set_tags, "Изменены теги"),
    "set_lanes": (op_set_lanes, "Изменены полосы"),
    "move_node": (op_move_node, "Перемещён узел"),
    "move_way": (op_move_way, "Объект перемещён"),
    "separate_ways": (op_separate_ways, "Дороги разъединены"),
    "add_node": (op_add_node, "Добавлен узел"),
    "insert_node": (op_insert_node, "Вставлен узел в ребро"),
    "add_way": (op_add_way, "Добавлено ребро"),
    "delete": (op_delete, "Удалён объект"),
    "split_way": (op_split_way, "Ребро разделено"),
    "merge_nodes": (op_merge_nodes, "Узлы объединены"),
    "connect_endpoint": (op_connect_endpoint, "Перекрёсток соединён"),
    "reverse_way": (op_reverse_way, "Ребро развёрнуто"),
}


def apply_ops(doc: OsmDocument, history: History, ops: list[tuple[str, dict]], label: str | None = None,
              author: str | None = None, at: float | None = None, reason: str | None = None) -> list[dict]:
    """Apply several operations as ONE undo step (e.g. a road template)."""
    if not ops:
        return []
    for op, _ in ops:
        if op not in OPS:
            raise OpError(f"неизвестная операция: {op}")
    tx = Transaction(doc, label or OPS[ops[0][0]][1], author=author, at=at if at is not None else time.time(),
                     reason=reason)
    results: list[dict] = []
    try:
        for op, args in ops:
            results.append(OPS[op][0](doc, tx, **args) or {})
    except OpError:
        tx.undo()
        raise
    except (TypeError, ValueError, KeyError) as e:
        tx.undo()
        raise OpError(f"неверные параметры операции: {e}") from e
    except Exception:
        tx.undo()
        raise
    tx.commit()
    history.last_tx = tx
    if not tx.is_empty:
        history.push(tx)
    return results


def apply_op(doc: OsmDocument, history: History, op: str, args: dict, label: str | None = None,
             author: str | None = None, at: float | None = None, reason: str | None = None) -> dict:
    return apply_ops(doc, history, [(op, args)], label, author, at, reason)[0]
