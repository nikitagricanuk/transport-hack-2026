"""Road class rules, crossings, validator alternatives, entrance exceptions."""

import pytest

from app.edits import History, OpError, apply_op
from app.geo import segment_intersection
from app.osm_model import Node, OsmDocument, Way
from app.roads import can_connect, is_access_way
from app.validation import validate


def _draw(doc, points, tags):
    return apply_op(doc, History(), "add_way", {
        "points": [{"lat": lat, "lon": lon} for lat, lon in points],
        "tags": tags,
    })


def _shared(doc, a, b):
    return set(doc.ways[a].nodes) & set(doc.ways[b].nodes)


def _issues_touching(doc, way_id):
    return [i for i in validate(doc)["issues"] if any(r["kind"] == "way" and r["id"] == way_id for r in i["refs"])]


# ---------------------------------------------------------------- geometry
def test_segment_intersection_and_parallel():
    hit = segment_intersection((0, 0), (10, 0), (5, -4), (5, 6))
    assert hit is not None
    t, u = hit
    assert t == pytest.approx(0.5) and u == pytest.approx(0.4)
    assert segment_intersection((0, 0), (1, 0), (0, 1), (1, 1)) is None
    assert segment_intersection((0, 0), (1, 0), (2, -1), (2, 1)) is None


def test_can_connect_matrix():
    assert can_connect({"highway": "footway"}, {"highway": "residential"}) is None
    assert can_connect({"highway": "pedestrian"}, {"highway": "residential"})
    assert can_connect({"highway": "primary"}, {"highway": "pedestrian"})
    assert can_connect({"highway": "service"}, {"highway": "primary"}) is None
    assert can_connect({"highway": "motorway"}, {"highway": "motorway_link"}) is None
    assert can_connect({"highway": "motorway"}, {"highway": "motorway"}) is None
    foot = can_connect({"highway": "footway"}, {"highway": "motorway"})
    assert foot and "не соединяется" in foot
    assert can_connect({"highway": "cycleway"}, {"highway": "trunk"})
    ramp = can_connect({"highway": "residential"}, {"highway": "motorway"})
    assert ramp and "motorway_link" in ramp
    assert can_connect({"highway": "primary_link"}, {"highway": "motorway"})
    assert is_access_way({"highway": "service", "service": "driveway"})
    assert is_access_way({"highway": "footway", "footway": "sidewalk"})
    assert not is_access_way({"highway": "service"})
    assert not is_access_way({"highway": "footway"})


# ---------------------------------------------------------------- crossings
def test_drawing_across_a_road_inserts_a_shared_node(doc):
    # crosses way 100 (lat 52.28) between nodes 1 (lon 104.28) and 2 (lon 104.281)
    before = list(doc.ways[100].nodes)
    res = _draw(doc, [(52.2795, 104.2805), (52.2805, 104.2805)], {"highway": "residential"})
    shared = _shared(doc, 100, res["id"])
    assert len(shared) == 1
    node = next(iter(shared))
    assert doc.ways[res["id"]].nodes[1] == node
    assert doc.ways[100].nodes.index(node) == 1
    assert doc.ways[100].nodes[0] == before[0] and node not in before


def test_crossing_is_one_undo_step(doc):
    h = History()
    before = list(doc.ways[100].nodes)
    res = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.2795, "lon": 104.2805}, {"lat": 52.2805, "lon": 104.2805},
    ], "tags": {"highway": "tertiary"}})
    assert _shared(doc, 100, res["id"])
    h.undo()
    assert doc.ways[100].nodes == before
    assert res["id"] not in doc.ways


def test_bridge_and_layer_do_not_join(doc):
    before = list(doc.ways[100].nodes)
    over = _draw(doc, [(52.2795, 104.2805), (52.2805, 104.2805)],
                 {"highway": "primary", "bridge": "yes", "layer": "1"})
    assert not _shared(doc, 100, over["id"])
    assert doc.ways[100].nodes == before
    tunnel = _draw(doc, [(52.2796, 104.2807), (52.2804, 104.2807)],
                   {"highway": "primary", "tunnel": "yes"})
    assert not _shared(doc, 100, tunnel["id"])
    raised = _draw(doc, [(52.2797, 104.2802), (52.2803, 104.2802)],
                   {"highway": "primary", "layer": "2"})
    assert not _shared(doc, 100, raised["id"])


def test_same_bridge_deck_does_join(doc):
    h = History()
    lower = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.2830, "lon": 104.2840}, {"lat": 52.2830, "lon": 104.2860},
    ], "tags": {"highway": "primary", "bridge": "yes", "layer": "1"}})["id"]
    upper = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.2826, "lon": 104.2850}, {"lat": 52.2834, "lon": 104.2850},
    ], "tags": {"highway": "secondary", "bridge": "yes", "layer": "1"}})["id"]
    assert len(_shared(doc, lower, upper)) == 1


def test_dragging_a_vertex_across_another_road_splits_both(doc):
    h = History()
    horizontal = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.2830, "lon": 104.2840}, {"lat": 52.2830, "lon": 104.2860},
    ], "tags": {"highway": "residential"}})["id"]
    vertical = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.2824, "lon": 104.2850}, {"lat": 52.2828, "lon": 104.2850},
    ], "tags": {"highway": "residential"}})["id"]
    assert not _shared(doc, horizontal, vertical)
    before = list(doc.ways[horizontal].nodes)
    north = doc.ways[vertical].nodes[-1]
    apply_op(doc, h, "move_node", {"id": north, "lat": 52.2836, "lon": 104.2850})
    assert len(_shared(doc, horizontal, vertical)) == 1
    assert len(doc.ways[horizontal].nodes) == len(before) + 1
    h.undo()
    assert doc.ways[horizontal].nodes == before


def test_incompatible_overlay_does_not_join_and_warns(doc):
    h = History()
    motorway = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.2830, "lon": 104.2840}, {"lat": 52.2830, "lon": 104.2860},
    ], "tags": {"highway": "motorway"}})["id"]
    before = list(doc.ways[motorway].nodes)
    foot = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.2826, "lon": 104.2850}, {"lat": 52.2834, "lon": 104.2850},
    ], "tags": {"highway": "footway"}})
    assert not _shared(doc, motorway, foot["id"])
    assert doc.ways[motorway].nodes == before
    assert foot["warnings"] and "не соединяется" in foot["warnings"][0]
    link = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.2826, "lon": 104.2846}, {"lat": 52.2834, "lon": 104.2846},
    ], "tags": {"highway": "motorway_link"}})
    assert _shared(doc, motorway, link["id"])
    assert "warnings" not in link


def test_explicit_snap_of_a_footway_onto_a_motorway_is_blocked(doc):
    h = History()
    motorway = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.2830, "lon": 104.2840}, {"lat": 52.2830, "lon": 104.2860},
    ], "tags": {"highway": "motorway"}})["id"]
    end = doc.ways[motorway].nodes[0]
    n_ways = len(doc.ways)
    with pytest.raises(OpError, match="не соединяется"):
        apply_op(doc, h, "add_way", {"points": [
            {"id": end}, {"lat": 52.2834, "lon": 104.2840},
        ], "tags": {"highway": "footway"}})
    assert len(doc.ways) == n_ways
    # a driveway must not be merged into the motorway either
    driveway_node = apply_op(doc, h, "add_node", {"lat": 52.2832, "lon": 104.2842})["id"]
    apply_op(doc, h, "add_way", {"points": [
        {"id": driveway_node}, {"lat": 52.2832, "lon": 104.2836},
    ], "tags": {"highway": "service", "service": "driveway"}})
    with pytest.raises(OpError, match="motorway_link"):
        apply_op(doc, h, "merge_nodes", {"keep_id": end, "remove_id": driveway_node})
    with pytest.raises(OpError, match="motorway_link"):
        apply_op(doc, h, "connect_endpoint", {"node_id": driveway_node, "way_id": motorway})
    # a link road may
    link_node = apply_op(doc, h, "add_node", {"lat": 52.2831, "lon": 104.2855})["id"]
    link_way = apply_op(doc, h, "add_way", {"points": [
        {"id": link_node}, {"lat": 52.2834, "lon": 104.2855},
    ], "tags": {"highway": "motorway_link"}})["id"]
    joined = apply_op(doc, h, "connect_endpoint", {"node_id": link_node, "way_id": motorway})["id"]
    assert joined in doc.ways[motorway].nodes and joined in doc.ways[link_way].nodes


# ---------------------------------------------------------------- validator
def _trap_doc():
    xml = """<?xml version="1.0"?><osm version="0.6">
      <node id="1" lat="52.2800" lon="104.2800"/>
      <node id="2" lat="52.2805" lon="104.2800"/>
      <node id="3" lat="52.2810" lon="104.2800"/>
      <node id="4" lat="52.2810" lon="104.28025"/>
      <node id="5" lat="52.2813" lon="104.28025"/>
      <way id="1"><nd ref="1"/><nd ref="2"/>
        <tag k="highway" v="residential"/></way>
      <way id="2"><nd ref="2"/><nd ref="3"/>
        <tag k="highway" v="residential"/><tag k="oneway" v="yes"/></way>
      <way id="3"><nd ref="4"/><nd ref="5"/>
        <tag k="highway" v="residential"/></way>
    </osm>"""
    return OsmDocument.parse(xml.encode())


def test_oneway_trap_offers_three_alternatives():
    doc = _trap_doc()
    traps = [i for i in validate(doc)["issues"] if "ловуш" in i["title"].lower()]
    assert traps
    labels = [f["label"] for f in traps[0]["fixes"]]
    assert labels[:2] == ["Сделать двусторонним", "Развернуть направление"]
    assert any(f["op"] == "connect_endpoint" for f in traps[0]["fixes"])
    assert traps[0]["fix"] == traps[0]["fixes"][0]
    # applying "make two-way" clears this trap
    fix = traps[0]["fixes"][0]
    apply_op(doc, History(), fix["op"], fix["args"])
    again = [i for i in validate(doc)["issues"] if "ловуш" in i["title"].lower()]
    assert not again


def test_isolated_segment_offers_connect_and_delete(doc):
    isol = [i for i in validate(doc)["issues"] if i["title"] == "Изолированное ребро"]
    by_way = {}
    for issue in isol:
        wid = next(r["id"] for r in issue["refs"] if r["kind"] == "way")
        by_way[wid] = [f["op"] for f in issue["fixes"]]
    # way 102 ends a few metres from the primary; way 103 sits hundreds of metres away
    assert by_way[102][0] == "connect_endpoint" and "delete" in by_way[102]
    assert by_way[103] == ["delete"]
    assert isol[0]["fix"]["op"] == isol[0]["fixes"][0]["op"]


def test_driveway_and_sidewalk_at_an_entrance_are_not_connectivity_errors(doc):
    # Door on the line of way 101, ~33 m north of the primary. The street end of
    # the driveway is ~6 m from the primary and ~28 m from the door.
    doc.nodes[50] = Node(50, 52.28030, 104.281, {"entrance": "yes"})
    doc.nodes[51] = Node(51, 52.28005, 104.281, {})
    doc.nodes[52] = Node(52, 52.28030, 104.2807, {})
    doc.nodes[53] = Node(53, 52.28010, 104.2807, {})
    doc.nodes[54] = Node(54, 52.28030, 104.2812, {})
    doc.ways[400] = Way(400, [52, 50, 53, 52], {"building": "yes"})
    doc.ways[401] = Way(401, [50, 51], {"highway": "service", "service": "driveway"})
    doc.ways[402] = Way(402, [50, 54], {"highway": "footway", "footway": "sidewalk"})
    # a driveway in a field, no entrance nearby, must still be reported
    doc.nodes[60] = Node(60, 52.2845, 104.2870, {})
    doc.nodes[61] = Node(61, 52.2847, 104.2870, {})
    doc.ways[403] = Way(403, [60, 61], {"highway": "service", "service": "driveway"})

    issues = validate(doc)["issues"]

    def touching(way_id, categories):
        return [i for i in issues if i["category"] in categories
                and any(r["kind"] == "way" and r["id"] == way_id for r in i["refs"])]

    driveway = touching(401, {"connectivity", "junction", "direction"})
    # the door itself is not a broken junction; the street end still is
    # (the gap issue points at the road being joined, not at the driveway way)
    assert not any(i["title"].startswith("Изолирован") for i in driveway)
    assert not any(i["category"] in {"connectivity", "junction", "direction"}
                   and any(r["kind"] == "node" and r["id"] == 50 for r in i["refs"]) for i in issues)
    assert any(i["title"] == "Разорванный перекрёсток" and any(r["id"] == 51 for r in i["refs"]) for i in issues)
    # sidewalks are not part of the car graph, and the entrance exception keeps them out
    assert not touching(402, {"connectivity", "junction", "direction"})
    field = touching(403, {"connectivity"})
    assert any(i["title"] == "Изолированное ребро" for i in field)


def test_crossing_near_a_motorway_junction_does_not_adopt_that_node(doc):
    """A link may cross a motorway, but a residential must not snap onto that junction."""
    h = History()
    motorway = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.2830, "lon": 104.2840}, {"lat": 52.2830, "lon": 104.2860},
    ], "tags": {"highway": "motorway"}})["id"]
    link = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.2825, "lon": 104.2850}, {"lat": 52.2835, "lon": 104.2850},
    ], "tags": {"highway": "motorway_link"}})["id"]
    junction = _shared(doc, motorway, link)
    assert len(junction) == 1
    # 0.6 m north of the junction: inside the 0.8 m snap, still on the link.
    residential = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.2830054, "lon": 104.2847}, {"lat": 52.2830054, "lon": 104.2853},
    ], "tags": {"highway": "residential"}})["id"]
    assert not (set(doc.ways[residential].nodes) & set(doc.ways[motorway].nodes))
    assert not (set(doc.ways[residential].nodes) & junction)
    assert not _shared(doc, residential, link)


def test_trap_connect_ignores_the_road_that_already_contains_the_node():
    doc = OsmDocument.parse(b"""<?xml version="1.0"?><osm version="0.6">
      <node id="1" lat="52.2800" lon="104.2800"/>
      <node id="2" lat="52.2804" lon="104.2800"/>
      <node id="7" lat="52.2804" lon="104.2796"/>
      <node id="3" lat="52.2808" lon="104.2800"/>
      <node id="4" lat="52.2812" lon="104.2800"/>
      <node id="5" lat="52.2808" lon="104.2802"/>
      <node id="6" lat="52.2810" lon="104.2802"/>
      <way id="1"><nd ref="1"/><nd ref="2"/><nd ref="7"/><tag k="highway" v="residential"/></way>
      <way id="2"><nd ref="2"/><nd ref="3"/>
        <tag k="highway" v="residential"/><tag k="oneway" v="yes"/></way>
      <way id="3"><nd ref="3"/><nd ref="4"/><tag k="highway" v="residential"/></way>
      <way id="4"><nd ref="5"/><nd ref="6"/><tag k="highway" v="residential"/></way>
    </osm>""")
    traps = [i for i in validate(doc)["issues"] if "ловуш" in i["title"].lower()]
    assert traps
    connect = next(f for f in traps[0]["fixes"] if f["op"] == "connect_endpoint")
    assert connect["args"]["node_id"] == 3
    assert connect["args"]["way_id"] == 4
    assert not (set(doc.ways[2].nodes) & set(doc.ways[4].nodes))
    apply_op(doc, History(), connect["op"], connect["args"])
    # connecting must actually tie the trap to way 4, not no-op on the road it already touches
    assert set(doc.ways[2].nodes) & set(doc.ways[4].nodes)


def test_gap_connect_fix_does_not_target_a_motorway(doc):
    h = History()
    apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.2860, "lon": 104.2860}, {"lat": 52.2860, "lon": 104.2880},
    ], "tags": {"highway": "motorway"}})
    service = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.28610, "lon": 104.2872}, {"lat": 52.28610, "lon": 104.2878},
    ], "tags": {"highway": "service"}})["id"]
    # south end is ~4.4 m from the motorway and ~6.6 m from the service road
    near_both = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.28604, "lon": 104.2875}, {"lat": 52.28620, "lon": 104.2875},
    ], "tags": {"highway": "residential"}})["id"]
    only_motorway = apply_op(doc, h, "add_way", {"points": [
        {"lat": 52.28604, "lon": 104.2863}, {"lat": 52.28620, "lon": 104.2863},
    ], "tags": {"highway": "residential"}})["id"]
    issues = validate(doc)["issues"]

    def gap(nid):
        return next(i for i in issues if i["title"] == "Разорванный перекрёсток"
                    and any(r["kind"] == "node" and r["id"] == nid for r in i["refs"]))

    south = doc.ways[near_both].nodes[0]
    chosen = gap(south)
    connects = [f for f in chosen["fixes"] if f["op"] == "connect_endpoint"]
    assert len(connects) == 1 and connects[0]["args"]["way_id"] == service
    apply_op(doc, h, connects[0]["op"], connects[0]["args"])
    far = doc.ways[only_motorway].nodes[0]
    assert not any(f["op"] == "connect_endpoint" for f in gap(far)["fixes"])


def test_oneway_driveway_into_an_entrance_is_not_a_trap(doc):
    doc.nodes[70] = Node(70, 52.2804, 104.280, {"entrance": "main"})
    # node 1 is the west end of the primary, two-way. The spur runs north, one-way.
    doc.ways[410] = Way(410, [1, 70], {"highway": "service", "service": "driveway", "oneway": "yes"})
    issues = validate(doc)["issues"]
    flagged = [i for i in issues if i["category"] in {"connectivity", "direction"}
               and any(r["kind"] == "node" and r["id"] == 70 for r in i["refs"])]
    assert flagged == []


def test_pedestrian_street_does_not_auto_join_motor_road(doc):
    before = list(doc.ways[100].nodes)
    result = _draw(doc, [(52.2795, 104.2805), (52.2805, 104.2805)], {"highway": "pedestrian"})
    assert not _shared(doc, 100, result["id"])
    assert doc.ways[100].nodes == before
