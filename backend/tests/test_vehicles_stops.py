"""Vehicle mix with car equivalents, and the stops coverage / load analysis."""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.analysis import pcu_factor, road_rows, scenario_rows, vehicle_mix
from app.osm_model import OsmDocument
from app.stops_analysis import analyse


def test_vehicle_mix_defaults_and_legacy_fields():
    assert pcu_factor({}) == pytest.approx(1.0)
    mix = {v["key"]: v for v in vehicle_mix({"truck_share": 0.2})}
    assert mix["truck"]["share"] == pytest.approx(0.2)
    assert mix["car"]["share"] == pytest.approx(0.8)
    assert pcu_factor({"truck_share": 0.2}) == pytest.approx(1.2)
    # bicycles count for less than a car; a custom coefficient wins over the default
    assert pcu_factor({"vehicle_mix": {"bicycle": {"share": 0.5}}}) == pytest.approx(0.65)
    assert pcu_factor({"vehicle_mix": {"bicycle": {"share": 0.5, "pcu": 0.5}}}) == pytest.approx(0.75)


def _doc(extra=""):
    nodes = "".join(f'<node id="{i}" lat="52" lon="{104 + x / 68000}"/>' for i, x in [(1, 0), (2, 300)])
    road = '<way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/></way>'
    return f'<osm version="0.6">{nodes}{road}{extra}</osm>'.encode()


def test_scenario_uses_mix():
    doc = OsmDocument.parse(_doc())
    base = scenario_rows(road_rows(doc, {}, 1), doc, {})[0]["vc"]
    bikes = scenario_rows(road_rows(doc, {}, 1), doc, {"vehicle_mix": {"bicycle": {"share": 0.5}}})[0]["vc"]
    assert bikes == pytest.approx(base * 0.65)


def test_load_endpoint_mix_and_explain(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DRAFT_DIR", tmp_path)
    c = TestClient(main.app)
    did = c.post("/api/documents", files={"file": ("t.osm", _doc(), "application/xml")}).json()["doc_id"]
    r = c.post(f"/api/documents/{did}/load", json={"vehicle_mix": {"bicycle": {"share": 0.2}, "truck": {"share": 0.1, "pcu": 3}}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pcu_factor"] == pytest.approx(0.7 + 0.06 + 0.3)
    assert {v["key"] for v in body["vehicle_mix"]} >= {"car", "bicycle", "truck", "bus", "moto", "van"}
    ex = c.get(f"/api/documents/{did}/load/10/explain").json()
    names = [s["name"] for s in ex["steps"]]
    assert "Коэффициент приведения состава" in names and "Приведённая интенсивность" in names
    assert c.post(f"/api/documents/{did}/load", json={"vehicle_mix": {"bicycle": {"share": 0.7}, "truck": {"share": 0.5}}}).status_code == 400
    assert c.post(f"/api/documents/{did}/load", json={"vehicle_mix": {"plane": {"share": 0.1}}}).status_code == 400
    # the older two-field form still works and updates the same mix
    body = c.post(f"/api/documents/{did}/load", json={"truck_share": 0.2}).json()
    assert {v["key"]: v["share"] for v in body["vehicle_mix"]}["truck"] == pytest.approx(0.2)


def _house(i, x, levels=9, size=30):
    """A square residential building of size x size metres whose corner is x metres east of the origin."""
    dx, dy = size / 68000, size / 111000
    lon, lat = 104 + x / 68000, 52.001
    pts = [(lat, lon), (lat, lon + dx), (lat + dy, lon + dx), (lat + dy, lon)]
    nodes = "".join(f'<node id="{i * 10 + k}" lat="{a}" lon="{b}"/>' for k, (a, b) in enumerate(pts))
    refs = "".join(f'<nd ref="{i * 10 + k}"/>' for k in [0, 1, 2, 3, 0])
    return nodes + f'<way id="{i}">{refs}<tag k="building" v="apartments"/><tag k="building:levels" v="{levels}"/></way>'


def test_stops_coverage_gap_and_load():
    stop = '<node id="5" lat="52" lon="104"><tag k="highway" v="bus_stop"/><tag k="name" v="Центр"/></node>'
    route = ('<relation id="900"><member type="node" ref="5" role="platform"/><member type="way" ref="10" role=""/>'
             '<tag k="type" v="route"/><tag k="route" v="bus"/><tag k="ref" v="1"/><tag k="interval" v="30"/></relation>')
    xml = _doc(stop + _house(100, 50) + _house(101, 2000) + route)
    r = analyse(OsmDocument.parse(xml), {})
    s = r["summary"]
    assert s["stops"] == 1 and s["buildings"] == 2 and s["uncovered_buildings"] == 1
    assert 0 < s["coverage"] < 1
    assert r["gaps"] and r["gaps"][0]["residents"] > 0
    assert any(a["kind"] == "gap" for a in r["advice"])
    st = r["stops"][0]
    assert st["routes"] == ["1"] and st["vehicles_per_hour"] == pytest.approx(2)
    # 270 residents -> 27 boardings in the peak hour against 2 buses x 80 places
    assert st["demand_per_hour"] == round(st["residents"] * 0.1)
    assert st["load"] == pytest.approx(st["demand_per_hour"] / 160, abs=0.01)
    # a busier scenario makes the same stop crowded and produces advice
    busy = analyse(OsmDocument.parse(xml), {}, trips_per_resident=5, peak_share=0.5)
    assert busy["stops"][0]["level"] == "высокая"
    assert any(a["kind"] == "overload" for a in busy["advice"])


def test_stops_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DRAFT_DIR", tmp_path)
    c = TestClient(main.app)
    did = c.post("/api/documents", files={"file": ("t.osm", _doc(), "application/xml")}).json()["doc_id"]
    r = c.get(f"/api/documents/{did}/analysis/stops", params={"radius_m": 400})
    assert r.status_code == 200
    assert r.json()["summary"]["stops"] == 0 and r.json()["params"]["radius_m"] == 400
