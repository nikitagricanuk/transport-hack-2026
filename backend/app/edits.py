"""Editing operations with undo/redo.

Every operation runs inside a Transaction. The transaction records a deep copy
of each element *before* it is first touched, and the state *after* the
operation, so undo/redo is just restoring snapshots: it works for any
operation without writing an inverse by hand.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable

from .geo import LocalProjection, dist, project_on_segment
from .osm_model import Element, Kind, Member, Node, OsmDocument, Way
from .roads import lane_tags, reversed_tags


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
            self.before[key] = copy.deepcopy(cur)

    def commit(self) -> None:
        for (kind, eid) in self.before:
            cur = self.doc.get(kind, eid)  # type: ignore[arg-type]
            self.after[(kind, eid)] = copy.deepcopy(cur)

    def _restore(self, states: dict[Key, Element | None]) -> None:
        for (kind, eid), el in states.items():
            store = self.doc.store(kind)  # type: ignore[arg-type]
            if el is None:
                store.pop(eid, None)
            else:
                store[eid] = copy.deepcopy(el)

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
                 oneway: str = "no", auto_turns: bool = True):
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
    doc.ways[new_id] = Way(id=new_id, nodes=second, tags=dict(w.tags))
    for rel in list(doc.relations_with("way", w.id)):
        tx.touch("relation", rel.id)
        out: list[Member] = []
        for m in rel.members:
            out.append(m)
            if m.type == "way" and m.ref == w.id:
                out.append(Member("way", new_id, m.role))
        rel.members = out
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
    except TypeError as e:
        tx.undo()
        raise OpError(f"неверные параметры операции: {e}") from e
    except Exception:
        tx.undo()
        raise
    tx.commit()
    if not tx.is_empty:
        history.push(tx)
    return results


def apply_op(doc: OsmDocument, history: History, op: str, args: dict, label: str | None = None) -> dict:
    return apply_ops(doc, history, [(op, args)], label)[0]
