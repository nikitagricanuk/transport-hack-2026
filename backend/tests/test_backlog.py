import math
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from app import main, routing, pt_routing
from app.analysis import road_rows, scenario_rows, load_segments
from app.edits import History, apply_op
from app.geojson import _node_category
from app.osm_model import OsmDocument
from app.validation import validate


def xml(nodes, ways, relations=""):
    ns = "".join(f'<node id="{i}" lat="52" lon="{104 + x / 68000}"/>' for i, x in nodes)
    return OsmDocument.parse(f'<osm version="0.6">{ns}{ways}{relations}</osm>'.encode())


def way(i, refs, tags=""):
    return (
        f'<way id="{i}">'
        + "".join(f'<nd ref="{n}"/>' for n in refs)
        + '<tag k="highway" v="residential"/>'
        + tags
        + "</way>"
    )


def test_segment_rewrites_route_and_undo():
    rel = '<relation id="30"><member type="way" ref="10" role=""/><tag k="type" v="route"/><tag k="route" v="bus"/></relation>'
    doc = xml([(1, 0), (2, 100), (3, 200), (4, 300)], way(10, [1, 2, 3, 4]), rel)
    before = doc.to_xml()
    h = History()
    result = apply_op(
        doc, h, "edit_segment", {"way_id": 10, "start": {"id": 2}, "end": {"id": 3}, "tags": {"lanes": "3"}}
    )
    assert len(doc.ways) == 3
    assert len(doc.relations[30].members) == 3
    assert doc.ways[result["id"]].nodes == [2, 3]
    assert [w.tags.get("lanes") for w in doc.ways.values()].count("3") == 1
    assert len(h.undo_stack) == 1
    h.undo()
    assert doc.to_xml() == before


def test_group_move_shared_vertex_once():
    doc = xml([(1, 0), (2, 100), (3, 200)], way(10, [1, 2]) + way(11, [2, 3]))
    lon = doc.nodes[2].lon
    h = History()
    apply_op(
        doc,
        h,
        "move_selection",
        {"refs": [{"kind": "way", "id": 10}, {"kind": "way", "id": 11}], "dlat": 0.001, "dlon": 0.001},
    )
    assert doc.nodes[2].lon == pytest.approx(lon + 0.001)
    h.undo()
    assert doc.nodes[2].lon == lon


def test_pedestrian_checks_only_info():
    doc = xml([(1, 0), (2, 100)], way(10, [1, 2], '<tag k="sidewalk" v="no"/>'))
    issues = [i for i in validate(doc)["issues"] if i["category"] == "pedestrian"]
    assert issues and all(i["severity"] == "info" and not i["levels"] for i in issues)
    assert issues[0]["fixes"][0]["args"]["tags"] == {"foot": "no"}
    assert not routing.walkable(doc.ways[10].tags)


def test_catchment_partial_and_intersection():
    doc = xml([(1, 0), (2, 1000)], way(10, [1, 2], '<tag k="highway" v="footway"/>'))
    out = routing.catchment(doc, {"set_a": [1], "set_b": [2], "limit_m": 500, "buffer_m": 40})
    features = out["polygons"]["features"]
    assert out["network"]["walk_km_reached_a"] == pytest.approx(0.5)
    area = {f["properties"]["set"]: f["properties"]["area_km2"] for f in features}
    assert area["A"] == pytest.approx((2 * 40 * 500 + math.pi * 40**2) / 1e6, rel=0.01)
    assert area["AB"] <= min(area["A"], area["B"])


def test_turn_restriction_and_skipped():
    # 1 -> 2 -> 3 forbidden, alternate 2 -> 4 -> 3
    doc = xml(
        [(1, 0), (2, 100), (3, 200), (4, 300)],
        way(10, [1, 2]) + way(11, [2, 3]) + way(12, [2, 4, 3]),
        '<relation id="30"><member type="way" ref="10" role="from"/><member type="node" ref="2" role="via"/><member type="way" ref="11" role="to"/><tag k="type" v="restriction"/><tag k="restriction" v="no_left_turn"/></relation>'
        '<relation id="31"><member type="way" ref="10" role="via"/><tag k="type" v="restriction"/></relation>',
    )
    out = routing.route(doc, {"from": {"node_id": 1}, "to": {"node_id": 3}})
    assert out["found"] and 12 in [w["id"] for w in out["stats"]["ways"]]
    assert out["stats"]["restrictions_skipped"] == [31]
    assert out["stats"]["restrictions_applied"] == 1


def test_load_assumptions_and_segments():
    doc = xml([(1, 0), (2, 100), (3, 200), (4, 300)], way(10, [1, 2]) + way(11, [2, 3]) + way(12, [3, 4]))
    rows = scenario_rows(road_rows(doc, {}, 1), doc, {})
    baseline = rows[0]["vc"]
    changed = scenario_rows(road_rows(doc, {}, 1), doc, {"truck_share": 0.2})
    assert changed[0]["vc"] == pytest.approx(baseline * 1.2)
    rain = scenario_rows(road_rows(doc, {}, 1), doc, {"weather": "rain"})
    assert rain[0]["capacity"] == pytest.approx(rows[0]["capacity"] * 0.85)
    assert len(load_segments(doc, rows)) == 1


def test_load_cache_and_signal_seconds(raw, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DRAFT_DIR", tmp_path)
    c = TestClient(main.app)
    did = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    with patch("app.scenario.detours", wraps=main.scenario.detours) as det:
        c.get(f"/api/documents/{did}/load")
        c.get(f"/api/documents/{did}/load")
        c.post(f"/api/documents/{did}/load", json={"multiplier": 1.2})
        assert det.call_count == 1
    c.post(f"/api/documents/{did}/ops", json={"op": "place_signal", "args": {"node_id": 2}})
    c.post(
        f"/api/documents/{did}/ops",
        json={"op": "set_tags", "args": {"kind": "node", "id": 2, "tags": {"uds:signal:arrow": "left"}}},
    )
    r = c.post(f"/api/documents/{did}/signals/2", json={"green_main_s": 40, "green_cross_s": 25})
    assert r.status_code == 200, r.text
    assert r.json()["cycle"] == 75
    exported = c.get(f"/api/documents/{did}/export").content
    doc = OsmDocument.parse(exported)
    assert doc.nodes[2].tags["uds:signal:arrow"] == "left"
    cfg = main.signal_plans.timing_from_tags(doc)[2]
    p = main.signal_plans.plan(doc, 2, cfg, {})
    assert [phase["green_s"] for phase in p["phases"]] == [40, 25]


def test_tram_signal_roundtrip():
    doc = xml([(1, 0), (2, 100)], '<way id="10"><nd ref="1"/><nd ref="2"/><tag k="railway" v="tram"/></way>')
    apply_op(doc, History(), "place_signal", {"node_id": 1})
    assert _node_category(doc.nodes[1].tags) == "tram_signal"
    assert OsmDocument.parse(doc.to_xml()).nodes[1].tags["uds:signal:tram"] == "yes"


def test_pt_wait_and_ride_and_transfer_limit():
    # Routes share platform 2. No physical walking bridge between platforms.
    ns = [(1, 0), (2, 1000), (3, 2000)]
    ws = way(10, [1, 2, 3], '<tag k="highway" v="footway"/>')

    def rel(rid, stops):
        return (
            f'<relation id="{rid}"><member type="way" ref="10" role=""/>'
            + "".join(f'<member type="node" ref="{n}" role="platform"/>' for n in stops)
            + '<tag k="type" v="route"/><tag k="route" v="bus"/><tag k="interval" v="10"/></relation>'
        )

    doc = xml(ns, ws, rel(30, [1, 2]) + rel(31, [2, 3]))
    req = {"from": {"node_id": 1}, "walk_kmh": 4.5, "pt_kmh": {"bus": 20}, "wait_factor": 0.5}
    rg, walk, pair, rs, issues = pt_routing.transit_graph(doc, req)
    g, start = pair
    first = pt_routing.costs(g, start, 10000, 1)
    second = pt_routing.costs(g, start, 10000, 2)
    assert second[("stop", 3)][0] < first[("stop", 3)][0]
    # One boarding adds 300 seconds waiting, independent of the ride length.
    assert second[("ride", 30, 1)][1]["wait_s"] == pytest.approx(300)
    assert second[("ride", 30, 1)][1]["ride_s"] > 0
    out = pt_routing.isochrone(doc, {**req, "bands_min": [5, 10, 15]})
    assert out["bands"][-1]["with_transfer"]["area_km2"] >= out["bands"][-1]["no_transfer"]["area_km2"]


def test_pt_response_json_and_read_only():
    import json
    doc = xml([(1,0),(2,1000),(3,2000)],way(10,[1,2,3],'<tag k="highway" v="footway"/>'),
        '<relation id="30"><member type="way" ref="10" role=""/><member type="node" ref="1" role="platform"/><member type="node" ref="3" role="platform"/><tag k="type" v="route"/><tag k="route" v="bus"/></relation>')
    session=main.Session('pt-test','t.osm',doc)
    main.SESSIONS[session.id]=session
    before=doc.to_xml()
    try:
        r=TestClient(main.app).post('/api/documents/pt-test/analysis/pt-isochrone',json={'from':{'node_id':1},'bands_min':[5,10,15]})
        assert r.status_code==200,r.text
        json.dumps(r.json(),allow_nan=False)
        assert doc.to_xml()==before and session.revision==0 and not session.history.undo_stack
    finally:
        main.SESSIONS.pop(session.id)


def test_multiple_detours_and_choice():
    from app.scenario import apply_closures
    coords={1:(52,104),2:(52,104.003),3:(52.001,104),4:(52.001,104.003),5:(51.999,104),6:(51.999,104.003)}
    ns=''.join(f'<node id="{n}" lat="{lat}" lon="{lon}"/>' for n,(lat,lon) in coords.items())
    ws=way(10,[1,2])+way(11,[1,3,4,2])+way(12,[1,5,6,2])
    doc=OsmDocument.parse(f'<osm version="0.6">{ns}{ws}</osm>'.encode())
    first=road_rows(doc,{},1)
    info=apply_closures(doc,first,{10})
    assert len(info[10]['alternatives'])>=2
    second=road_rows(doc,{},1)
    alternate=apply_closures(doc,second,{10},{10:1})
    assert info[10]['ways']!=alternate[10]['ways']
    assert {r['id'] for r in first if r.get('detour')}!={r['id'] for r in second if r.get('detour')}


def test_load_geometry_cache_survives_volume_change(raw,tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DRAFT_DIR',tmp_path)
    c=TestClient(main.app)
    did=c.post('/api/documents',files={'file':('t.osm',raw,'application/xml')}).json()['doc_id']
    with patch('app.analysis.segment_chains',wraps=main.__dict__.get('segment_chains',__import__('app.analysis',fromlist=['segment_chains']).segment_chains)) as chains:
        c.get(f'/api/documents/{did}/load')
        c.post(f'/api/documents/{did}/load',json={'way_id':100,'volume':500})
        assert chains.call_count==1


def test_sidewalk_check_covers_middle_of_long_segment():
    # The sidewalk lies >40m from both road vertices, so vertex-only indexing misses it.
    doc=xml([(1,0),(2,1000),(3,180),(4,820)],way(10,[1,2],'<tag k="highway" v="primary"/>')+
        way(11,[3,4],'<tag k="highway" v="footway"/><tag k="footway" v="sidewalk"/>'))
    issues=validate(doc)['issues']
    assert any(i['category']=='pedestrian' and i['title'].startswith('Тротуар нарисован отдельно') for i in issues)
