"""Signal placement rules and timing, transit routes and their load."""

import pytest
from fastapi.testclient import TestClient

from app import signals, transit
from app.analysis import road_rows
from app.edits import History, OpError, apply_op
from app.main import app
from app.osm_model import Member, Relation
from app.validation import validate


def test_signal_snaps_to_a_junction_and_refuses_mid_block(doc):
    h = History()
    res = apply_op(doc, h, "place_signal", {"way_id": 100, "lat": 52.28, "lon": 104.28092})  # ~5 m from node 2
    assert res == {"id": 2, "spot": "junction"} and doc.nodes[2].tags["highway"] == "traffic_signals"
    with pytest.raises(OpError):  # 37 m from the nearest junction
        apply_op(doc, h, "place_signal", {"way_id": 100, "lat": 52.28, "lon": 104.28155})
    # a signal on a crossing makes it a signalised crossing
    apply_op(doc, h, "set_tags", {"kind": "node", "id": 3, "tags": {"highway": "crossing", "crossing": "marked"}})
    res = apply_op(doc, h, "place_signal", {"node_id": 3})
    assert res["spot"] == "crossing" and doc.nodes[3].tags == {"highway": "crossing", "crossing": "traffic_signals"}


def test_misplaced_signal_is_reported_and_moved(doc):
    h = History()
    apply_op(doc, h, "set_tags", {"kind": "node", "id": 3, "tags": {"highway": "traffic_signals"}})
    issue = next(i for i in validate(doc)["issues"] if i["title"] == "Светофор не на перекрёстке")
    res = apply_op(doc, h, issue["fix"]["op"], issue["fix"]["args"])
    assert res["id"] in (2, 4) and "highway" not in doc.nodes[3].tags
    assert doc.nodes[res["id"]].tags["highway"] == "traffic_signals"
    assert not any(i["title"] == "Светофор не на перекрёстке" for i in validate(doc)["issues"])


def test_signal_plan_groups_arms_and_green_share_changes_capacity(doc):
    arms = signals.approaches(doc, 2)
    groups = {(a["way_id"], a["group"]) for a in arms}
    assert groups == {(100, "main"), (101, "cross")} and len(arms) == 4
    rows = {r["id"]: r for r in road_rows(doc, {}, 1.0)}
    plan = signals.plan(doc, 2, None, rows)
    assert plan["cycle"] == 90 and [p["green_s"] for p in plan["phases"]] == [40, 40]
    assert 40 <= plan["webster"]["cycle"] <= 150 and 0.2 <= plan["webster"]["main_share"] <= 0.8
    # capacity of an approach to a signal: c = lanes · s(class) · g/C
    doc.nodes[2].tags["highway"] = "traffic_signals"
    even = {r["id"]: r for r in road_rows(doc, {}, 1.0, capacity_factor=signals.capacity_factors(doc, {}))}
    assert even[100]["capacity"] == round(2 * signals.sat_flow("primary") * 40 / 90)   # default plan, even split
    f = signals.capacity_factors(doc, {2: {"main_share": 0.7, "cycle": 90}})
    boosted = {r["id"]: r for r in road_rows(doc, {}, 1.0, capacity_factor=f)}
    assert boosted[100]["capacity"] == round(2 * signals.sat_flow("primary") * 56 / 90)
    assert boosted[100]["capacity"] > even[100]["capacity"] and boosted[101]["capacity"] < even[101]["capacity"]
    # a longer cycle with the same split loses less time per hour: more capacity
    longer = signals.capacity_factors(doc, {2: {"main_share": 0.5, "cycle": 120}})
    assert longer[100] > signals.capacity_factors(doc, {})[100]


def _add_bus_route(doc):
    doc.relations[600] = Relation(600, [Member("way", 100, ""), Member("node", 11, "stop"), Member("way", 101, "")],
                                  {"type": "route", "route": "bus", "ref": "7", "interval": "10", "name": "Автобус 7"})


def test_routes_stops_and_bus_load(doc):
    _add_bus_route(doc)
    rs = transit.routes(doc, {})
    assert [(r["ref"], r["frequency"], r["stops"]) for r in rs] == [("7", 6.0, 1)]
    stop = transit.stop_info(doc, 11, {})
    assert [r["ref"] for r in stop["routes"]] == ["7"] and stop["vehicles_per_hour"] == 6.0
    geo = transit.route_geometry(doc, 600, {})
    assert len(geo["lines"]) == 2 and geo["stop_list"][0]["id"] == 11
    pcu = transit.bus_pcu_by_way(doc, {600: 12})
    assert pcu == {100: 30.0, 101: 30.0}
    base = {r["id"]: r for r in road_rows(doc, {}, 1.0)}
    with_bus = {r["id"]: r for r in road_rows(doc, {}, 1.0, transit_pcu=pcu)}
    assert with_bus[100]["volume"] == base[100]["volume"] + 30 and with_bus[100]["transit_pcu"] == 30


def test_transit_and_signal_api(raw):
    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    assert c.get(f"/api/documents/{doc_id}/transit/routes").json() == {"routes": []}
    assert c.get(f"/api/documents/{doc_id}/transit/stops/11").json()["routes"] == []
    assert c.get(f"/api/documents/{doc_id}/signals/2").status_code == 404  # no signal yet
    c.post(f"/api/documents/{doc_id}/ops", json={"op": "place_signal", "args": {"node_id": 2}})
    plan = c.get(f"/api/documents/{doc_id}/signals/2").json()
    assert plan["custom"] is False and {a["group"] for a in plan["approaches"]} == {"main", "cross"}
    cap_before = next(r for r in c.get(f"/api/documents/{doc_id}/load").json()["rows"] if r["id"] == 100)["capacity"]
    plan = c.post(f"/api/documents/{doc_id}/signals/2", json={"main_share": 0.7, "cycle": 100}).json()
    assert plan["custom"] and plan["cycle"] == 100 and plan["main_share"] == 0.7
    cap_after = next(r for r in c.get(f"/api/documents/{doc_id}/load").json()["rows"] if r["id"] == 100)["capacity"]
    assert cap_after > cap_before
    opt = c.post(f"/api/documents/{doc_id}/signals/2", json={"optimize": True}).json()
    assert opt["cycle"] == opt["webster"]["cycle"]
    assert c.post(f"/api/documents/{doc_id}/signals/2", json={"main_share": 0.95}).status_code == 400
    reset = c.post(f"/api/documents/{doc_id}/signals/2", json={"reset": True}).json()
    assert reset["custom"] is False


def test_signal_timing_is_exported_as_tags_and_restored_on_import(raw):
    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    c.post(f"/api/documents/{doc_id}/ops", json={"op": "place_signal", "args": {"node_id": 2}})
    c.post(f"/api/documents/{doc_id}/signals/2", json={"main_share": 0.6, "cycle": 100})
    xml = c.get(f"/api/documents/{doc_id}/export").content
    for tag in ('k="uds:signal:cycle" v="100"', 'k="uds:signal:main_share" v="0.60"',
                'k="uds:signal:green_main" v="54"', 'k="uds:signal:green_cross" v="36"'):
        assert tag.encode() in xml
    # re-import restores the plan
    doc2 = c.post("/api/documents", files={"file": ("t2.osm", xml, "application/xml")}).json()["doc_id"]
    plan = c.get(f"/api/documents/{doc2}/signals/2").json()
    assert plan["custom"] and plan["cycle"] == 100 and plan["main_share"] == 0.6
    # a reset drops the stale tags from the next export
    c.post(f"/api/documents/{doc2}/signals/2", json={"reset": True})
    assert b"uds:signal:" not in c.get(f"/api/documents/{doc2}/export").content


def test_explain_shows_formulas_and_signal_capacity(raw):
    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    plain = c.get(f"/api/documents/{doc_id}/load/100/explain").json()
    assert [s["formula"] for s in plain["steps"]][1] == "c = n · c_пол"
    assert plain["summary"]["signal"] is False
    c.post(f"/api/documents/{doc_id}/ops", json={"op": "place_signal", "args": {"node_id": 2}})
    c.post(f"/api/documents/{doc_id}/signals/2", json={"main_share": 0.6, "cycle": 100})
    ex = c.get(f"/api/documents/{doc_id}/load/100/explain").json()
    cap = next(s for s in ex["steps"] if s["formula"] == "c = n · s · g / C")
    g = round((100 - 10) * 0.6)
    assert cap["value"] == round(2 * signals.sat_flow("primary") * g / 100)
    assert ex["summary"]["signal"] and ex["summary"]["capacity"] == cap["value"]
    assert any(s["formula"].startswith("t = t₀") for s in ex["steps"])
    assert c.get(f"/api/documents/{doc_id}/load/105/explain").status_code == 404   # a building
