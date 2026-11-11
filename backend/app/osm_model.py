"""Lossless in-memory model of an OSM XML document.

Design goals:
* everything that was in the source file survives a load -> save round trip:
  element order, attributes (id, version, timestamp, uid, ...), tag order,
  non-element children such as <bounds>, <note> and <meta>;
* edits are expressed on plain dataclasses, so they are easy to copy for undo;
* new elements get negative ids (JOSM convention) and are renumbered to free
  positive ids on export, because not every converter accepts negative ids.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape
from dataclasses import dataclass, field
from typing import Iterable, Literal, Union

Kind = Literal["node", "way", "relation"]
KINDS: tuple[Kind, ...] = ("node", "way", "relation")


@dataclass
class Node:
    id: int
    lat: float
    lon: float
    tags: dict[str, str] = field(default_factory=dict)
    attrs: dict[str, str] = field(default_factory=dict)
    # original textual coordinates, kept so untouched nodes are byte-stable
    raw_coords: tuple[str, str] | None = None

    def set_position(self, lat: float, lon: float) -> None:
        self.lat = lat
        self.lon = lon
        self.raw_coords = None


@dataclass
class Way:
    id: int
    nodes: list[int] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    attrs: dict[str, str] = field(default_factory=dict)

    @property
    def is_closed(self) -> bool:
        return len(self.nodes) > 2 and self.nodes[0] == self.nodes[-1]


@dataclass
class Member:
    type: Kind
    ref: int
    role: str = ""


@dataclass
class Relation:
    id: int
    members: list[Member] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    attrs: dict[str, str] = field(default_factory=dict)


Element = Union[Node, Way, Relation]


def clone(el: Element | None) -> Element | None:
    """Independent copy of an element. ~50x faster than copy.deepcopy."""
    if el is None:
        return None
    if isinstance(el, Node):
        return Node(el.id, el.lat, el.lon, dict(el.tags), dict(el.attrs), el.raw_coords)
    if isinstance(el, Way):
        return Way(el.id, list(el.nodes), dict(el.tags), dict(el.attrs))
    return Relation(el.id, [Member(m.type, m.ref, m.role) for m in el.members], dict(el.tags), dict(el.attrs))

_SKIP_NODE_ATTRS = {"id", "lat", "lon"}
_SKIP_ATTRS = {"id"}


class OsmDocument:
    def __init__(self) -> None:
        self.root_attrs: dict[str, str] = {"version": "0.6", "generator": "uds-editor"}
        self.head: list[ET.Element] = []  # <note>, <meta>, <bounds> ... before data
        self.tail: list[ET.Element] = []  # unknown elements after data
        self.nodes: dict[int, Node] = {}
        self.ways: dict[int, Way] = {}
        self.relations: dict[int, Relation] = {}
        self._next_new_id = -1
        self._original: dict[tuple[str, int], Element] = {}

    # ------------------------------------------------------------------ access
    def store(self, kind: Kind) -> dict[int, Element]:
        if kind == "node":
            return self.nodes  # type: ignore[return-value]
        if kind == "way":
            return self.ways  # type: ignore[return-value]
        if kind == "relation":
            return self.relations  # type: ignore[return-value]
        raise ValueError(f"unknown element kind: {kind}")

    def get(self, kind: Kind, eid: int) -> Element | None:
        return self.store(kind).get(eid)

    def new_id(self) -> int:
        nid = self._next_new_id
        self._next_new_id -= 1
        return nid

    def bounds(self) -> tuple[float, float, float, float] | None:
        """(minlat, minlon, maxlat, maxlon) from <bounds> or from node extent."""
        for el in self.head:
            if el.tag == "bounds":
                try:
                    return (
                        float(el.attrib["minlat"]),
                        float(el.attrib["minlon"]),
                        float(el.attrib["maxlat"]),
                        float(el.attrib["maxlon"]),
                    )
                except (KeyError, ValueError):
                    break
        if not self.nodes:
            return None
        lats = [n.lat for n in self.nodes.values()]
        lons = [n.lon for n in self.nodes.values()]
        return (min(lats), min(lons), max(lats), max(lons))

    # ------------------------------------------------------------ change diff
    def snapshot_original(self) -> None:
        self._original = {}
        for kind in KINDS:
            for eid, el in self.store(kind).items():
                self._original[(kind, eid)] = clone(el)  # type: ignore[assignment]

    def diff_summary(self) -> dict[str, dict[str, int]]:
        out = {k: {"added": 0, "modified": 0, "deleted": 0} for k in KINDS}
        for kind in KINDS:
            current = self.store(kind)
            for eid, el in current.items():
                orig = self._original.get((kind, eid))
                if orig is None:
                    out[kind]["added"] += 1
                elif orig != el:
                    out[kind]["modified"] += 1
        for (kind, eid) in self._original:
            if eid not in self.store(kind):  # type: ignore[arg-type]
                out[kind]["deleted"] += 1
        return out

    def is_changed(self) -> bool:
        return any(v for d in self.diff_summary().values() for v in d.values())

    # ----------------------------------------------------------------- parse
    @classmethod
    def parse(cls, data: bytes) -> "OsmDocument":
        root = ET.fromstring(data)
        if root.tag != "osm":
            raise ValueError("корневой элемент файла должен быть <osm>")
        doc = cls()
        doc.root_attrs = dict(root.attrib)
        seen_data = False
        min_id = 0
        for child in root:
            tag = child.tag
            if tag == "node":
                seen_data = True
                n = _parse_node(child)
                doc.nodes[n.id] = n
                min_id = min(min_id, n.id)
            elif tag == "way":
                seen_data = True
                w = _parse_way(child)
                doc.ways[w.id] = w
                min_id = min(min_id, w.id)
            elif tag == "relation":
                seen_data = True
                r = _parse_relation(child)
                doc.relations[r.id] = r
                min_id = min(min_id, r.id)
            else:
                (doc.tail if seen_data else doc.head).append(child)
        doc._next_new_id = min(-1, min_id - 1)
        doc.snapshot_original()
        return doc

    # ------------------------------------------------------------- serialize
    def export_idmap(self) -> dict[Kind, dict[int, int]]:
        """Ids that elements created in the editor (negative ids) get in the exported file."""
        idmap: dict[Kind, dict[int, int]] = {k: {} for k in KINDS}
        for kind in KINDS:
            store = self.store(kind)
            used = set(store.keys()) | {eid for (k, eid) in self._original if k == kind}
            nxt = max([i for i in used if i > 0], default=0) + 1
            for eid in store:
                if eid < 0:
                    idmap[kind][eid] = nxt
                    nxt += 1
        return idmap

    def to_xml(self, renumber_new: bool = True, generator: str | None = None,
               extra_node_tags: dict[int, dict[str, str | None]] | None = None) -> bytes:
        """Serialize. `extra_node_tags` override node tags in the output only; None drops a key."""
        extra_node_tags = extra_node_tags or {}
        idmap = self.export_idmap() if renumber_new else {k: {} for k in KINDS}

        def m(kind: Kind, eid: int) -> int:
            return idmap[kind].get(eid, eid)

        attrs = dict(self.root_attrs)
        if generator:
            attrs["generator"] = generator

        # Hand-written serializer: same output shape as ElementTree + indent,
        # but ~10x faster, which matters because drafts and exports run often.
        out: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>\n', "<osm", _attrs(attrs), ">\n"]
        for el in self.head:
            out.append(_raw_element(el))
        for n in self.nodes.values():
            if n.raw_coords:
                lat, lon = n.raw_coords
            else:
                lat, lon = f"{n.lat:.7f}", f"{n.lon:.7f}"
            out.append(f' <node id="{m("node", n.id)}" lat="{_esc(lat)}" lon="{_esc(lon)}"{_attrs(n.attrs)}')
            extra = extra_node_tags.get(n.id)
            if extra:
                merged = {**n.tags, **extra}
                _tags_xml(out, {k: v for k, v in merged.items() if v is not None}, "node")
            else:
                _tags_xml(out, n.tags, "node")
        for w in self.ways.values():
            out.append(f' <way id="{m("way", w.id)}"{_attrs(w.attrs)}')
            if not w.nodes and not w.tags:
                out.append("/>\n")
                continue
            out.append(">\n")
            out.extend(f'  <nd ref="{m("node", ref)}"/>\n' for ref in w.nodes)
            out.extend(f'  <tag k="{_esc(k)}" v="{_esc(v)}"/>\n' for k, v in w.tags.items())
            out.append(" </way>\n")
        for r in self.relations.values():
            out.append(f' <relation id="{m("relation", r.id)}"{_attrs(r.attrs)}')
            if not r.members and not r.tags:
                out.append("/>\n")
                continue
            out.append(">\n")
            out.extend(f'  <member type="{_esc(mem.type)}" ref="{m(mem.type, mem.ref)}" role="{_esc(mem.role)}"/>\n'
                       for mem in r.members)
            out.extend(f'  <tag k="{_esc(k)}" v="{_esc(v)}"/>\n' for k, v in r.tags.items())
            out.append(" </relation>\n")
        for el in self.tail:
            out.append(_raw_element(el))
        out.append("</osm>\n")
        return "".join(out).encode("utf-8")

    # --------------------------------------------------------------- helpers
    def ways_of_node(self, node_id: int) -> list[Way]:
        return [w for w in self.ways.values() if node_id in w.nodes]

    def node_way_index(self) -> dict[int, list[int]]:
        idx: dict[int, list[int]] = {}
        for w in self.ways.values():
            for ref in dict.fromkeys(w.nodes):
                idx.setdefault(ref, []).append(w.id)
        return idx

    def relations_with(self, kind: Kind, eid: int) -> Iterable[Relation]:
        for r in self.relations.values():
            if any(mem.type == kind and mem.ref == eid for mem in r.members):
                yield r

    def stats(self) -> dict[str, int]:
        return {
            "nodes": len(self.nodes),
            "ways": len(self.ways),
            "relations": len(self.relations),
            "signals": sum(1 for n in self.nodes.values() if n.tags.get("highway") == "traffic_signals"),
        }


# ---------------------------------------------------------------- xml utils
def _tags(el: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in el.findall("tag"):
        k = t.attrib.get("k")
        if k is not None:
            out[k] = t.attrib.get("v", "")
    return out


def _extra_attrs(el: ET.Element, skip: set[str]) -> dict[str, str]:
    return {k: v for k, v in el.attrib.items() if k not in skip}


def _parse_node(el: ET.Element) -> Node:
    lat_s, lon_s = el.attrib.get("lat", "0"), el.attrib.get("lon", "0")
    return Node(
        id=int(el.attrib["id"]),
        lat=float(lat_s),
        lon=float(lon_s),
        tags=_tags(el),
        attrs=_extra_attrs(el, _SKIP_NODE_ATTRS),
        raw_coords=(lat_s, lon_s),
    )


def _parse_way(el: ET.Element) -> Way:
    return Way(
        id=int(el.attrib["id"]),
        nodes=[int(nd.attrib["ref"]) for nd in el.findall("nd")],
        tags=_tags(el),
        attrs=_extra_attrs(el, _SKIP_ATTRS),
    )


def _parse_relation(el: ET.Element) -> Relation:
    members = [
        Member(type=m.attrib["type"], ref=int(m.attrib["ref"]), role=m.attrib.get("role", ""))  # type: ignore[arg-type]
        for m in el.findall("member")
    ]
    return Relation(
        id=int(el.attrib["id"]), members=members, tags=_tags(el), attrs=_extra_attrs(el, _SKIP_ATTRS)
    )


_ATTR_ENTITIES = {'"': "&quot;", "\n": "&#10;", "\r": "&#13;", "\t": "&#9;"}


def _esc(value: str) -> str:
    return _xml_escape(str(value), _ATTR_ENTITIES)


def _attrs(attrs: dict[str, str]) -> str:
    return "".join(f' {k}="{_esc(v)}"' for k, v in attrs.items())


def _tags_xml(out: list[str], tags: dict[str, str], tag: str) -> None:
    if not tags:
        out.append("/>\n")
        return
    out.append(">\n")
    out.extend(f'  <tag k="{_esc(k)}" v="{_esc(v)}"/>\n' for k, v in tags.items())
    out.append(f" </{tag}>\n")


def _raw_element(el: ET.Element) -> str:
    """Non-data children (<note>, <meta>, <bounds>, ...) written back verbatim."""
    e = copy.copy(el)
    e.tail = None
    return " " + ET.tostring(e, encoding="unicode") + "\n"
