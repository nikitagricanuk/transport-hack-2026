import pytest
from fastapi.testclient import TestClient
from app.osm_model import OsmDocument
from app import routing
from app.main import SESSIONS, Session, app
from app.fix_preview import preview


def line(tags="", extra=""):
    return OsmDocument.parse(
        f'<osm version="0.6"><node id="1" lat="52" lon="104"/><node id="2" lat="52" lon="104.01"/><node id="3" lat="52" lon="104.02"/><way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="highway" v="residential"/><tag k="maxspeed" v="60"/>{tags}</way>{extra}</osm>'.encode()
    )


def request(a=1, b=3, **kw):
    return {"from": {"node_id": a}, "to": {"node_id": b}, **kw}


def test_route_and_virtual_snap():
    doc = line()
    r = routing.route(doc, request())
    assert r["found"]
    assert r["stats"]["time_s"] == pytest.approx(r["stats"]["length_m"] / (60 / 3.6))
    rg = routing.build_graph(doc, "car")
    a = routing.snap(rg, 52, 104.002, 60)
    b = routing.snap(rg, 52, 104.008, 60)
    p = routing.shortest_path(rg, a, b)
    assert p["length_m"] == pytest.approx(r["stats"]["length_m"] * 0.3)
    assert len(rg.g) == 3  # virtual nodes never enter the cached graph


def test_oneway_diagnosis():
    r = routing.route(line('<tag k="oneway" v="yes"/>'), request(3, 1))
    assert not r["found"]
    assert {f["op"] for f in r["issues"][0]["fixes"]} == {"reverse_way", "set_tags"}


@pytest.mark.parametrize(
    "tags,expected",
    [
        ({"highway": "footway"}, True),
        ({"highway": "steps"}, True),
        ({"highway": "residential", "foot": "no"}, False),
        ({"highway": "residential", "sidewalk": "no"}, False),
        ({"highway": "motorway"}, False),
        ({"highway": "cycleway", "foot": "yes"}, True),
        ({"highway": "primary", "sidewalk": "separate"}, False),
    ],
)
def test_walkable(tags, expected):
    assert routing.walkable(tags) == expected


def test_bands_partial_edges_monotonic():
    doc = line()
    out = routing.isochrone(doc, {"from": {"node_id": 1}, "step_min": 0.5, "count": 3})
    assert out["bands"][0]["road_km"] == pytest.approx(0.5)
    assert [b["road_km"] for b in out["bands"]] == sorted(b["road_km"] for b in out["bands"])
    assert out["boundary_limited"]


def test_api_readonly_and_probe():
    doc = line('<tag k="oneway" v="yes"/>')
    session = Session("route-test", "test.osm", doc)
    SESSIONS[session.id] = session
    try:
        before = doc.to_xml()
        client = TestClient(app)
        r = client.post("/api/documents/route-test/analysis/route", json=request())
        assert r.status_code == 200, r.text
        assert r.json()["read_only"] and r.json()["revision"] == 0
        out = preview(
            doc,
            "set_tags",
            {"kind": "way", "id": 10, "tags": {"oneway": None}},
            lambda d: routing.route(d, request(3, 1)),
        )
        assert out["probe"]["found"]
        assert doc.to_xml() == before
        assert session.revision == 0 and not session.history.undo_stack
    finally:
        SESSIONS.pop(session.id)
