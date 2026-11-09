"""Split a joined crossing apart, and move a whole way in one step."""

import pytest

from app.edits import History, OpError, apply_op
from app.geo import LocalProjection, dist


def _shared(doc, a, b):
    return set(doc.ways[a].nodes) & set(doc.ways[b].nodes)


def _xy(doc, nid):
    n = doc.nodes[nid]
    return LocalProjection(52.28, 104.28).to_xy(n.lat, n.lon)


def test_separate_crossing_opens_a_gap_and_undo_restores_it(doc):
    # node 2 is the interior crossing of way 100 (east-west) and way 101 (north-south)
    assert 2 in _shared(doc, 100, 101)
    before_100 = list(doc.ways[100].nodes)
    before_101 = list(doc.ways[101].nodes)
    h = History()
    res = apply_op(doc, h, "separate_ways", {"node_id": 2})
    assert res["id"] == 2
    assert 2 in doc.ways[100].nodes
    assert doc.ways[100].nodes == before_100
    assert 2 not in doc.ways[101].nodes
    pieces = [wid for wid in {101, *res["detached"]} if wid in doc.ways]
    assert len(pieces) == 2
    original = set(before_101)
    for wid in pieces:
        assert 2 not in doc.ways[wid].nodes
        assert not _shared(doc, 100, wid)
        fresh = [nid for nid in doc.ways[wid].nodes if nid not in original]
        assert len(fresh) == 1
        # the new end stops short of the kept road, outside the 1 m snap
        assert dist(_xy(doc, fresh[0]), _xy(doc, 2)) == pytest.approx(4.0, abs=0.2)
    # a later nudge of the new end must not glue the roads back together
    end_id = next(nid for nid in doc.ways[101].nodes if nid not in original)
    end = doc.nodes[end_id]
    apply_op(doc, h, "move_node", {"id": end_id, "lat": end.lat + 0.00002, "lon": end.lon})
    assert 2 not in doc.ways[101].nodes
    assert not _shared(doc, 100, 101)
    h.undo()
    h.undo()
    assert doc.ways[100].nodes == before_100
    assert doc.ways[101].nodes == before_101
    assert 2 in _shared(doc, 100, 101)


def test_separate_pulls_a_stem_off_the_road_it_was_joined_to(doc):
    h = History()
    res = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.2810, "lon": 104.2805},
        {"lat": 52.2800, "lon": 104.2805, "way_id": 100},
    ], "tags": {"highway": "residential"}})
    stem = res["id"]
    shared = _shared(doc, 100, stem)
    assert len(shared) == 1
    nid = next(iter(shared))
    assert doc.ways[stem].nodes[-1] == nid
    apply_op(doc, h, "separate_ways", {"node_id": nid})
    assert not _shared(doc, 100, stem)
    assert nid in doc.ways[100].nodes
    assert nid not in doc.ways[stem].nodes
    assert len(doc.ways[stem].nodes) == 2
    assert dist(_xy(doc, doc.ways[stem].nodes[-1]), _xy(doc, nid)) == pytest.approx(4.0, abs=0.3)


def test_separate_refuses_a_node_that_joins_nothing(doc):
    h = History()
    with pytest.raises(OpError, match="не соединяет"):
        apply_op(doc, h, "separate_ways", {"node_id": 1})
    assert h.undo_stack == []


def test_move_way_shifts_every_corner_of_a_building(doc):
    h = History()
    origin = apply_op(doc, h, "add_node", {"lat": 52.2840, "lon": 104.2860})["id"]
    wid = apply_op(doc, h, "add_way", {"points": [
        {"id": origin},
        {"lat": 52.2840, "lon": 104.2864},
        {"lat": 52.2842, "lon": 104.2864},
        {"lat": 52.2842, "lon": 104.2860},
        {"id": origin},
    ], "tags": {"building": "yes"}})["id"]
    ids = list(doc.ways[wid].nodes)
    assert ids[0] == ids[-1]
    assert len(set(ids)) == 4
    before = {nid: (doc.nodes[nid].lat, doc.nodes[nid].lon) for nid in set(ids)}
    res = apply_op(doc, h, "move_way", {"id": wid, "dlat": 0.001, "dlon": -0.002})
    assert res["nodes"] == 4
    for nid, (lat, lon) in before.items():
        assert doc.nodes[nid].lat == pytest.approx(lat + 0.001)
        assert doc.nodes[nid].lon == pytest.approx(lon - 0.002)
    assert doc.ways[wid].nodes[0] == doc.ways[wid].nodes[-1]
    h.undo()
    for nid, (lat, lon) in before.items():
        assert doc.nodes[nid].lat == pytest.approx(lat)
        assert doc.nodes[nid].lon == pytest.approx(lon)


def test_move_way_also_moves_a_node_shared_with_another_way(doc):
    h = History()
    wid = apply_op(doc, h, "add_way", {"points": [
        {"id": 1},
        {"lat": 52.2804, "lon": 104.2800},
        {"lat": 52.2804, "lon": 104.2804},
        {"lat": 52.2800, "lon": 104.2804},
        {"id": 1},
    ], "tags": {"building": "yes"}})["id"]
    other = doc.nodes[2]
    lat2, lon2 = other.lat, other.lon
    apply_op(doc, h, "move_way", {"id": wid, "dlat": 0.0003, "dlon": 0.0004})
    assert doc.nodes[1].lat == pytest.approx(52.28 + 0.0003)
    assert doc.nodes[1].lon == pytest.approx(104.28 + 0.0004)
    # the rest of the road stays where it was
    assert doc.nodes[2].lat == pytest.approx(lat2)
    assert doc.nodes[2].lon == pytest.approx(lon2)
    assert doc.ways[100].nodes[0] == 1


def test_move_way_rejects_an_empty_shift(doc):
    h = History()
    with pytest.raises(OpError, match="слишком мал"):
        apply_op(doc, h, "move_way", {"id": 100, "dlat": 0, "dlon": 0})
    assert doc.nodes[1].lat == pytest.approx(52.28)
