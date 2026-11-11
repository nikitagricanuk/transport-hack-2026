import gzip
import io
import json

import pytest
from fastapi import HTTPException, UploadFile

from app.main import import_matsim, import_webots, simulate_sumo, upload
from app.sumo_check import find_sumo_binary

NETWORK = b"""<network>
<nodes><node id="1" x="104.28" y="52.28"/><node id="2" x="104.29" y="52.28"/></nodes>
<links><link id="east" from="1" to="2" modes="car"/></links>
</network>"""

EVENTS = b"""<events>
<event time="100" type="vehicle enters traffic" vehicle="a" networkMode="car" link="east" relativePosition="0"/>
<event time="104" type="left link" vehicle="a" link="east"/>
<event time="104" type="vehicle leaves traffic" vehicle="a" link="east"/>
</events>"""


def _file(name, data):
    return UploadFile(io.BytesIO(data), filename=name)


def _document(raw):
    return json.loads(upload(_file("sample.osm", raw)).body)["doc_id"]


def test_replays_a_window_from_gzipped_matsim_outputs(raw):
    response = import_matsim(_document(raw), _file("network.xml.gz", gzip.compress(NETWORK)),
                             _file("events.xml.gz", gzip.compress(EVENTS)), "EPSG:4326", 102)
    first, last = response["frames"]
    assert [first["time"], last["time"]] == [0, 1]
    car, lon, lat, heading = first["vehicles"][0]
    assert car == "a"
    assert [lon, lat, heading] == pytest.approx([104.285, 52.28, 90])
    assert last["vehicles"][0][1] > lon


def test_replays_webots_poses_and_rejects_invalid_coordinates(raw):
    doc_id = _document(raw)
    header = b"time,id,lon,lat,angle\n"
    recording = header + b"10,one,104.28,52.28,359\n10.5,one,104.2801,52.28,1\n"
    response = import_webots(doc_id, _file("poses.csv", recording))
    assert response["frames"][1] == {"time": 0.5, "vehicles": [("one", 104.2801, 52.28, 1)]}
    with pytest.raises(HTTPException) as error:
        import_webots(doc_id, _file("bad.csv", header + b"0,a,104,100,0\n"))
    assert error.value.status_code == 422


def test_sumo_reports_missing_runtime(raw, monkeypatch):
    monkeypatch.setattr("app.simulation.find_sumo_binary", lambda _name: None)
    with pytest.raises(HTTPException) as error:
        simulate_sumo(_document(raw), 2)
    assert error.value.status_code == 503
    assert "SUMO" in error.value.detail


@pytest.mark.skipif(not all(find_sumo_binary(n) for n in ("sumo", "netconvert")), reason="SUMO not installed")
def test_sumo_runs_on_uploaded_road_and_returns_geographic_motion():
    doc_id = _document(b"""<osm version="0.6">
    <node id="1" lon="104.28" lat="52.28"/><node id="2" lon="104.29" lat="52.28"/>
    <way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/></way>
    </osm>""")
    response = simulate_sumo(doc_id, 2)
    positions = [car[1] for frame in response["frames"] for car in frame["vehicles"] if car[0] == "car-1"]
    assert all(104.279 < lon < 104.291 for lon in positions)
    assert len(set(positions)) > 2


def test_fleet_parsing():
    from app.simulation import parse_fleet
    assert parse_fleet(None, 7) == {"car": 7}
    assert parse_fleet("car:5,bus:2,tram:0", 0) == {"car": 5, "bus": 2}
    for bad in ("plane:3", "car:x", "car:500", "car:0", "car:200,bus:200"):
        with pytest.raises(Exception):
            parse_fleet(bad, 0)


@pytest.mark.skipif(not all(find_sumo_binary(n) for n in ("sumo", "netconvert")), reason="SUMO not installed")
def test_sumo_runs_each_selected_kind_on_roads_it_may_use():
    doc_id = _document(b"""<osm version="0.6">
    <node id="1" lon="104.28" lat="52.28"/><node id="2" lon="104.29" lat="52.28"/>
    <node id="3" lon="104.28" lat="52.281"/><node id="4" lon="104.29" lat="52.281"/>
    <way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/></way>
    <way id="11"><nd ref="3"/><nd ref="4"/><tag k="railway" v="tram"/></way>
    </osm>""")
    response = simulate_sumo(doc_id, 1, "car:2,bus:1,tram:1")
    kinds = {car[4] for frame in response["frames"] for car in frame["vehicles"]}
    assert kinds == {"car", "bus", "tram"}
    assert {f["kind"]: f["placed"] for f in response["fleet"]} == {"car": 2, "bus": 1, "tram": 1}
    # the tram stays on its track, north of the road
    assert all(car[2] > 52.2805 for frame in response["frames"] for car in frame["vehicles"] if car[4] == "tram")


def test_ru_speed_limits_are_written_for_sumo():
    from xml.etree import ElementTree as ET
    from app.simulation import apply_ru_speeds
    osm = b"""<osm version="0.6"><node id="1" lon="104" lat="52"/><node id="2" lon="104.01" lat="52"/>
    <way id="1"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/></way>
    <way id="2"><nd ref="1"/><nd ref="2"/><tag k="highway" v="living_street"/></way>
    <way id="3"><nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/><tag k="maxspeed" v="RU:urban"/></way>
    <way id="4"><nd ref="1"/><nd ref="2"/><tag k="highway" v="motorway"/></way></osm>"""
    root = ET.fromstring(apply_ru_speeds(osm))
    speeds = {w.get("id"): next(t.get("v") for t in w.findall("tag") if t.get("k") == "maxspeed") for w in root.iter("way")}
    assert speeds == {"1": "60", "2": "20", "3": "60", "4": "110"}


@pytest.mark.skipif(not all(find_sumo_binary(n) for n in ("sumo", "netconvert")), reason="SUMO not installed")
def test_signal_runs_the_editor_program_and_report_splits_kinds_and_phases():
    from fastapi.testclient import TestClient
    from app import main
    c = TestClient(main.app)
    osm = b"""<osm version="0.6">
    <node id="1" lon="104.270" lat="52.280"/><node id="2" lon="104.280" lat="52.280"><tag k="highway" v="traffic_signals"/></node>
    <node id="3" lon="104.290" lat="52.280"/><node id="4" lon="104.280" lat="52.274"/><node id="5" lon="104.280" lat="52.286"/>
    <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="highway" v="primary"/><tag k="name" v="Main"/></way>
    <way id="11"><nd ref="4"/><nd ref="2"/><nd ref="5"/><tag k="highway" v="residential"/><tag k="name" v="Cross"/></way>
    </osm>"""
    did = c.post("/api/documents", files={"file": ("t.osm", osm, "application/xml")}).json()["doc_id"]
    prog = [{"name": "Главное", "green_s": 30, "moves": ["main"]},
            {"name": "Стрелка", "green_s": 6, "moves": ["main_left"]},
            {"name": "Второстепенное", "green_s": 20, "moves": ["cross"]}]
    assert c.post(f"/api/documents/{did}/signals/2", json={"program": prog}).status_code == 200
    r = c.post(f"/api/documents/{did}/simulation/sumo", params={"vehicles": 20, "fleet": "car:20,bus:4"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["signals"] and body["signals"][0]["applied"]
    assert body["rules"]
    rep = body["report"]
    assert {k["kind"] for k in rep["kinds"]} == {"car", "bus"}
    sg = rep["signals"][0]
    assert sg["cycle_s"] == 30 + 6 + 20 + 3 * 5 and [p["name"] for p in sg["phases"]] == ["Главное", "Стрелка", "Второстепенное"]
    assert set(sg["phases"][0]["passed"]) == {"car", "bus"}
    only_bus = c.get(f"/api/documents/{did}/simulation/report.json", params={"kinds": "bus"}).json()
    assert [k["kind"] for k in only_bus["kinds"]] == ["bus"] and only_bus["summary"]["vehicles"] <= 4
    assert c.get(f"/api/documents/{did}/simulation/report.json", params={"kinds": "plane"}).status_code == 400
