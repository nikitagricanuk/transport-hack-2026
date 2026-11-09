"""Split a joined crossing apart, and move a whole way in one step."""

import math

import pytest

from app.edits import History, OpError, _proj, apply_op
from app.geo import LocalProjection, dist, project_on_segment


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


def _ll(proj, x, y):
    return proj.to_latlon(x, y)


def _road(doc, h, proj, a, b):
    pa, pb = _ll(proj, *a), _ll(proj, *b)
    return apply_op(doc, h, "add_way", {"points": [
        {"lat": pa[0], "lon": pa[1]}, {"lat": pb[0], "lon": pb[1]},
    ], "tags": {"highway": "residential"}})["id"]


def _off_way(doc, proj, nid, way_id) -> float:
    node = doc.nodes[nid]
    point = proj.to_xy(node.lat, node.lon)
    best = float("inf")
    for a, b in zip(doc.ways[way_id].nodes, doc.ways[way_id].nodes[1:]):
        if a not in doc.nodes or b not in doc.nodes:
            continue
        na, nb = doc.nodes[a], doc.nodes[b]
        _, _, gap = project_on_segment(point, proj.to_xy(na.lat, na.lon), proj.to_xy(nb.lat, nb.lon))
        best = min(best, gap)
    return best


def _spur(doc, h, proj, length, angle_deg):
    """A branch of ``length`` metres meeting a straight road at ``angle_deg``."""
    main = _road(doc, h, proj, (-80.0, 0.0), (80.0, 0.0))
    rad = math.radians(angle_deg)
    tip = _ll(proj, length * math.cos(rad), length * math.sin(rad))
    join = _ll(proj, 0.0, 0.0)
    branch = apply_op(doc, h, "add_way", {"points": [
        {"lat": tip[0], "lon": tip[1]},
        {"lat": join[0], "lon": join[1], "way_id": main},
    ], "tags": {"highway": "residential"}})["id"]
    shared = _shared(doc, main, branch)
    assert len(shared) == 1
    return main, branch, next(iter(shared))


def test_separate_shallow_branch_clears_the_snap_and_stays_split(doc):
    # 4 m along a 6° branch is only ~0.4 m off the retained road, inside the 1 m snap.
    h = History()
    proj = _proj(doc)
    main, branch, junction = _spur(doc, h, proj, 50.0, 6.0)
    original = set(doc.ways[branch].nodes)
    apply_op(doc, h, "separate_ways", {"node_id": junction})
    assert junction in doc.ways[main].nodes
    assert junction not in doc.ways[branch].nodes
    fresh = [nid for nid in doc.ways[branch].nodes if nid not in original]
    assert len(fresh) == 1
    end = fresh[0]
    assert _off_way(doc, proj, end, main) > 1.0
    end_xy = proj.to_xy(doc.nodes[end].lat, doc.nodes[end].lon)
    join_xy = proj.to_xy(doc.nodes[junction].lat, doc.nodes[junction].lon)
    assert dist(end_xy, join_xy) > 4.5
    tip = next(nid for nid in doc.ways[branch].nodes if nid != end)
    moved = doc.nodes[tip]
    apply_op(doc, h, "move_node", {"id": tip, "lat": moved.lat - 1.0 / 110_540.0, "lon": moved.lon})
    assert not _shared(doc, main, branch)
    assert end in doc.ways[branch].nodes
    assert _off_way(doc, proj, end, main) > 1.0


@pytest.mark.parametrize(("length", "angle"), [(15.0, 6.0), (2.0, 90.0)])
def test_separate_rejects_when_the_end_cannot_clear_the_snap(doc, length, angle):
    h = History()
    proj = _proj(doc)
    main, branch, junction = _spur(doc, h, proj, length, angle)
    before_ways = {wid: list(w.nodes) for wid, w in doc.ways.items()}
    before_pos = {nid: (n.lat, n.lon) for nid, n in doc.nodes.items()}
    stacked = len(h.undo_stack)
    with pytest.raises(OpError, match="ближе 1 м"):
        apply_op(doc, h, "separate_ways", {"node_id": junction})
    assert len(h.undo_stack) == stacked
    assert {wid: list(w.nodes) for wid, w in doc.ways.items()} == before_ways
    for nid, (lat, lon) in before_pos.items():
        assert doc.nodes[nid].lat == lat and doc.nodes[nid].lon == lon
    assert junction in _shared(doc, main, branch)


def test_separate_rolls_back_when_one_arm_cannot_clear_the_snap(doc):
    h = History()
    proj = _proj(doc)
    main = _road(doc, h, proj, (-80.0, 0.0), (80.0, 0.0))
    rad = math.radians(6.0)
    back, forward = 15.0, 50.0
    start = _ll(proj, -back * math.cos(rad), -back * math.sin(rad))
    end = _ll(proj, forward * math.cos(rad), forward * math.sin(rad))
    branch = apply_op(doc, h, "add_way", {"points": [
        {"lat": start[0], "lon": start[1]}, {"lat": end[0], "lon": end[1]},
    ], "tags": {"highway": "residential"}})["id"]
    shared = _shared(doc, main, branch)
    assert len(shared) == 1
    junction = next(iter(shared))
    assert junction not in (doc.ways[branch].nodes[0], doc.ways[branch].nodes[-1])
    assert abs(main) < abs(branch)
    for nid in (doc.ways[branch].nodes[0], doc.ways[branch].nodes[-1]):
        assert _off_way(doc, proj, nid, main) > 1.0
    before_ways = {wid: list(w.nodes) for wid, w in doc.ways.items()}
    stacked = len(h.undo_stack)
    with pytest.raises(OpError, match="ближе 1 м"):
        apply_op(doc, h, "separate_ways", {"node_id": junction})
    assert len(h.undo_stack) == stacked
    assert {wid: list(w.nodes) for wid, w in doc.ways.items()} == before_ways
    assert junction in _shared(doc, main, branch)


def test_move_way_joins_a_crossing_created_by_a_shared_building_corner(doc):
    h = History()
    proj = _proj(doc)
    road_c = _road(doc, h, proj, (-40.0, 0.0), (40.0, 0.0))
    south, corner_at = _ll(proj, 0.0, -30.0), _ll(proj, 0.0, -6.0)
    road_b = apply_op(doc, h, "add_way", {"points": [
        {"lat": south[0], "lon": south[1]}, {"lat": corner_at[0], "lon": corner_at[1]},
    ], "tags": {"highway": "residential"}})["id"]
    corner = doc.ways[road_b].nodes[-1]
    assert not _shared(doc, road_b, road_c)
    box = [_ll(proj, 12.0, -6.0), _ll(proj, 12.0, -18.0), _ll(proj, 0.0, -18.0)]
    building = apply_op(doc, h, "add_way", {"points": [
        {"id": corner},
        {"lat": box[0][0], "lon": box[0][1]},
        {"lat": box[1][0], "lon": box[1][1]},
        {"lat": box[2][0], "lon": box[2][1]},
        {"id": corner},
    ], "tags": {"building": "yes"}})["id"]
    before_c = list(doc.ways[road_c].nodes)
    dlat = 20.0 / 110_540.0
    apply_op(doc, h, "move_way", {"id": building, "dlat": dlat, "dlon": 0.0})
    shared = _shared(doc, road_b, road_c)
    assert len(shared) == 1
    assert corner not in shared
    assert len(doc.ways[road_c].nodes) == len(before_c) + 1
    assert corner in doc.ways[building].nodes
    h.undo()
    assert doc.ways[road_c].nodes == before_c
    assert not _shared(doc, road_b, road_c)
    node = doc.nodes[corner]
    apply_op(doc, h, "move_node", {"id": corner, "lat": node.lat + dlat, "lon": node.lon})
    assert len(_shared(doc, road_b, road_c)) == 1


def test_move_way_rejoins_a_highway_that_only_shared_a_moved_node(doc):
    h = History()
    proj = _proj(doc)
    road_c = _road(doc, h, proj, (-40.0, 0.0), (40.0, 0.0))
    start, corner_at = _ll(proj, -20.0, -20.0), _ll(proj, 0.0, -5.0)
    road_a = apply_op(doc, h, "add_way", {"points": [
        {"lat": start[0], "lon": start[1]}, {"lat": corner_at[0], "lon": corner_at[1]},
    ], "tags": {"highway": "residential"}})["id"]
    shared_node = doc.ways[road_a].nodes[-1]
    other = _ll(proj, 25.0, -5.0)
    road_b = apply_op(doc, h, "add_way", {"points": [
        {"id": shared_node}, {"lat": other[0], "lon": other[1]},
    ], "tags": {"highway": "residential"}})["id"]
    assert not _shared(doc, road_a, road_c)
    assert not _shared(doc, road_b, road_c)
    apply_op(doc, h, "move_way", {"id": road_b, "dlat": 20.0 / 110_540.0, "dlon": 0.0})
    assert not _shared(doc, road_b, road_c)
    assert len(_shared(doc, road_a, road_c)) == 1


def test_move_way_joins_when_the_moved_road_itself_crosses(doc):
    h = History()
    proj = _proj(doc)
    road_c = _road(doc, h, proj, (-8.0, 0.0), (8.0, 0.0))
    road_b = _road(doc, h, proj, (30.0, -20.0), (30.0, 20.0))
    assert not _shared(doc, road_b, road_c)
    apply_op(doc, h, "move_way", {"id": road_b, "dlat": 0.0, "dlon": -30.0 / proj.kx})
    assert len(_shared(doc, road_b, road_c)) == 1
