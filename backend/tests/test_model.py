from app.edits import History, OpError, apply_op
from app.osm_model import OsmDocument

import pytest


def _equal_docs(a: OsmDocument, b: OsmDocument) -> None:
    assert a.root_attrs == b.root_attrs
    assert [e.tag for e in a.head] == [e.tag for e in b.head]
    assert list(a.nodes) == list(b.nodes)
    assert list(a.ways) == list(b.ways)
    for nid, n in a.nodes.items():
        m = b.nodes[nid]
        assert (n.lat, n.lon, n.tags, n.attrs) == (m.lat, m.lon, m.tags, m.attrs)
    for wid, w in a.ways.items():
        assert (w.nodes, list(w.tags.items()), w.attrs) == (b.ways[wid].nodes, list(b.ways[wid].tags.items()), b.ways[wid].attrs)
    assert a.relations == b.relations


def test_roundtrip_is_lossless(doc, raw):
    out = doc.to_xml()
    again = OsmDocument.parse(out)
    _equal_docs(doc, again)
    text = out.decode("utf-8")
    assert 'lat="52.28"' in text, "untouched coordinates keep their original text"
    assert 'version="3"' in text and 'timestamp="2024-01-01T00:00:00Z"' in text
    assert "<bounds" in text and "<note>" in text and "<meta" in text
    assert "улица Карла Маркса" in text
    assert not doc.is_changed()


def test_tag_order_preserved(doc):
    again = OsmDocument.parse(doc.to_xml())
    assert list(again.ways[100].tags) == ["name", "highway", "lanes"]


def test_set_tags_undo_redo(doc):
    h = History()
    apply_op(doc, h, "set_tags", {"kind": "way", "id": 100, "tags": {"lanes": "4", "maxspeed": "60"}})
    assert doc.ways[100].tags["lanes"] == "4"
    assert doc.diff_summary()["way"]["modified"] == 1
    h.undo()
    assert doc.ways[100].tags["lanes"] == "2" and "maxspeed" not in doc.ways[100].tags
    assert not doc.is_changed()
    h.redo()
    assert doc.ways[100].tags["maxspeed"] == "60"


def test_set_lanes_is_consistent(doc):
    h = History()
    apply_op(doc, h, "set_lanes", {"way_id": 100, "forward": 2, "backward": 2, "oneway": "no"})
    t = doc.ways[100].tags
    assert t["lanes"] == "4" and t["lanes:forward"] == "2" and t["lanes:backward"] == "2"
    assert t["turn:lanes:forward"] == "left;through|through;right"
    apply_op(doc, h, "set_lanes", {"way_id": 100, "forward": 3, "oneway": "yes"})
    t = doc.ways[100].tags
    assert t["oneway"] == "yes" and t["lanes"] == "3" and "lanes:backward" not in t
    assert t["turn:lanes"].count("|") == 2


def test_add_way_and_new_ids_are_renumbered(doc):
    h = History()
    res = apply_op(doc, h, "add_way", {"points": [{"id": 5}, {"lat": 52.282, "lon": 104.281}],
                                        "tags": {"highway": "residential"}})
    assert res["id"] < 0
    out = OsmDocument.parse(doc.to_xml(renumber_new=True))
    assert all(i > 0 for i in out.nodes) and all(i > 0 for i in out.ways)
    new_way = list(out.ways.values())[-1]
    assert new_way.nodes[0] == 5 and new_way.nodes[1] in out.nodes
    assert doc.diff_summary()["way"]["added"] == 1


def test_insert_split_merge(doc):
    h = History()
    nid = apply_op(doc, h, "insert_node", {"way_id": 100, "lat": 52.28001, "lon": 104.2815})["id"]
    assert doc.ways[100].nodes == [1, 2, nid, 3, 4]
    res = apply_op(doc, h, "split_way", {"way_id": 100, "node_id": nid})
    assert doc.ways[100].nodes == [1, 2, nid]
    assert doc.ways[res["new_id"]].nodes == [nid, 3, 4]
    assert doc.ways[res["new_id"]].tags == doc.ways[100].tags
    rel_ways = [m.ref for m in doc.relations[500].members if m.type == "way"]
    assert rel_ways == [100, res["new_id"]]
    apply_op(doc, h, "merge_nodes", {"keep_id": 3, "remove_id": nid})
    assert nid not in doc.nodes and doc.ways[100].nodes == [1, 2, 3]
    for _ in range(3):
        h.undo()
    assert doc.ways[100].nodes == [1, 2, 3, 4] and not doc.is_changed()


def test_delete_node_removes_degenerate_way(doc):
    h = History()
    apply_op(doc, h, "delete", {"kind": "node", "id": 9})
    assert 103 not in doc.ways and 10 not in doc.nodes
    h.undo()
    assert 103 in doc.ways and 9 in doc.nodes and 10 in doc.nodes


def test_reverse_way_swaps_directional_tags(doc):
    h = History()
    apply_op(doc, h, "set_tags", {"kind": "way", "id": 101, "tags": {"oneway": "yes", "lanes:forward": "2"}})
    apply_op(doc, h, "reverse_way", {"id": 101})
    t = doc.ways[101].tags
    assert doc.ways[101].nodes == [6, 2, 5]
    assert t["oneway"] == "-1" and t["lanes:backward"] == "2" and "lanes:forward" not in t


def test_bad_op_is_rolled_back(doc):
    h = History()
    with pytest.raises(OpError):
        apply_op(doc, h, "split_way", {"way_id": 100, "node_id": 1})
    with pytest.raises(OpError):
        apply_op(doc, h, "no_such_op", {})
    assert not doc.is_changed() and not h.undo_stack
