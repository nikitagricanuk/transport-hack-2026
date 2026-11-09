"""Editing operations with undo/redo.

Every operation runs inside a Transaction. The transaction records a deep copy
of each element *before* it is first touched, and the state *after* the
operation, so undo/redo is just restoring snapshots: it works for any
operation without writing an inverse by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .geo import LocalProjection, dist, project_on_segment
from .osm_model import Element, Kind, Member, Node, OsmDocument, Relation, Way, clone
from .roads import direction, lane_tags, reversed_tags


class OpError(ValueError):
    """Operation cannot be applied; the message is shown to the user."""


Key = tuple[str, int]


@dataclass
class Transaction:
    doc: OsmDocument
    label: str
    before: dict[Key, Element | None] = field(default_factory=dict)
    after: dict[Key, Element | None] = field(default_factory=dict)

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
                   tags: dict | None, snap_m: float = 1.0) -> int:
    """Put a node on the nearest segment of a way; reuse a vertex if very close."""
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
        if dist(q, proj.to_xy(n.lat, n.lon)) <= snap_m:
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
    return {"id": n.id}


def op_add_node(doc, tx, lat: float, lon: float, tags: dict | None = None):
    return {"id": _create_node(doc, tx, lat, lon, tags).id}


def op_insert_node(doc, tx, way_id: int, lat: float, lon: float, tags: dict | None = None):
    return {"id": _insert_on_way(doc, tx, int(way_id), float(lat), float(lon), tags)}


def op_add_way(doc, tx, points: list[dict], tags: dict | None = None):
    """points: [{"id": node_id} | {"lat":..,"lon":..} | {"lat":..,"lon":..,"way_id":..}]"""
    if len(points) < 2:
        raise OpError("ребро должно содержать минимум две точки")
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
    wid = doc.new_id()
    tx.touch("way", wid)
    doc.ways[wid] = Way(id=wid, nodes=refs, tags=_clean_tags(tags or {"highway": "residential"}))
    return {"id": wid}


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
    _merge_nodes(doc, tx, int(keep_id), int(remove_id))
    return {"id": int(keep_id)}


def op_connect_endpoint(doc, tx, node_id: int, way_id: int):
    """Attach a dangling node to the nearest point of another way."""
    n = _node(doc, int(node_id))
    target = _insert_on_way(doc, tx, int(way_id), n.lat, n.lon, None, snap_m=3.0)
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


OPS: dict[str, tuple[Callable[..., dict], str]] = {
    "set_tags": (op_set_tags, "Изменены теги"),
    "set_lanes": (op_set_lanes, "Изменены полосы"),
    "move_node": (op_move_node, "Перемещён узел"),
    "add_node": (op_add_node, "Добавлен узел"),
    "insert_node": (op_insert_node, "Вставлен узел в ребро"),
    "add_way": (op_add_way, "Добавлено ребро"),
    "delete": (op_delete, "Удалён объект"),
    "split_way": (op_split_way, "Ребро разделено"),
    "merge_nodes": (op_merge_nodes, "Узлы объединены"),
    "connect_endpoint": (op_connect_endpoint, "Перекрёсток соединён"),
    "reverse_way": (op_reverse_way, "Ребро развёрнуто"),
}


def apply_ops(doc: OsmDocument, history: History, ops: list[tuple[str, dict]], label: str | None = None) -> list[dict]:
    """Apply several operations as ONE undo step (e.g. a road template)."""
    if not ops:
        return []
    for op, _ in ops:
        if op not in OPS:
            raise OpError(f"неизвестная операция: {op}")
    tx = Transaction(doc, label or OPS[ops[0][0]][1])
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


def apply_op(doc: OsmDocument, history: History, op: str, args: dict, label: str | None = None) -> dict:
    return apply_ops(doc, history, [(op, args)], label)[0]
