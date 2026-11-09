"""Regression tests for the independent QA pass."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.edits import History, apply_op
from app.main import SESSIONS, app
from app.organizer import OrganizerError, credentials, upload_osm
from app.osm_model import Member, OsmDocument, Relation
from app.roads import reversed_tags, traffic_node_order
from app.validation import validate

EXAMPLE = Path(__file__).resolve().parents[2] / "data" / "example.osm"


def _way_refs(rel):
    return [(m.ref, m.role) for m in rel.members if m.type == "way"]


def test_split_restriction_keeps_one_from_member(doc):
    doc.relations[900] = Relation(
        id=900,
        members=[
            Member("way", 100, "from"),
            Member("node", 4, "via"),
            Member("way", 104, "to"),
        ],
        tags={"type": "restriction", "restriction": "no_left_turn"},
    )
    h = History()
    new_id = apply_op(doc, h, "split_way", {"way_id": 100, "node_id": 2})["new_id"]
    from_members = [m for m in doc.relations[900].members if m.role == "from"]
    to_members = [m for m in doc.relations[900].members if m.role == "to"]
    assert len(from_members) == 1 and from_members[0].ref == new_id
    assert len(to_members) == 1 and to_members[0].ref == 104
    assert [m.ref for m in doc.relations[900].members if m.type == "node"] == [4]


def test_split_at_via_keeps_the_approaching_piece(doc):
    doc.relations[901] = Relation(
        id=901,
        members=[
            Member("way", 100, "from"),
            Member("node", 3, "via"),
            Member("way", 104, "to"),
        ],
        tags={"type": "restriction", "restriction": "no_straight_on"},
    )
    h = History()
    new_id = apply_op(doc, h, "split_way", {"way_id": 100, "node_id": 3})["new_id"]
    from_members = [m for m in doc.relations[901].members if m.role == "from"]
    assert len(from_members) == 1 and from_members[0].ref == 100
    assert from_members[0].ref != new_id
    assert doc.ways[100].nodes[-1] == 3


def test_split_route_backward_inserts_the_second_half_first(doc):
    doc.relations[902] = Relation(
        id=902,
        members=[Member("way", 104, ""), Member("way", 100, "backward")],
        tags={"type": "route", "route": "bus"},
    )
    doc.relations[903] = Relation(
        id=903,
        members=[Member("way", 104, ""), Member("way", 100, "")],
        tags={"type": "route"},
    )
    h = History()
    new_id = apply_op(doc, h, "split_way", {"way_id": 100, "node_id": 2})["new_id"]
    assert _way_refs(doc.relations[902]) == [(104, ""), (new_id, "backward"), (100, "backward")]
    # way 104 touches the far end of way 100, so an empty role is also travelled backwards
    assert _way_refs(doc.relations[903]) == [(104, ""), (new_id, ""), (100, "")]
    h.undo()
    assert _way_refs(doc.relations[902]) == [(104, ""), (100, "backward")]


def test_reverse_changes_geographic_traffic_including_oneway_minus_one(doc):
    h = History()
    apply_op(doc, h, "set_tags", {"kind": "way", "id": 101, "tags": {
        "oneway": "-1", "lanes:backward": "2", "turn:lanes:backward": "left|through",
        "incline": "up", "cycleway:left": "lane",
    }})
    before = traffic_node_order(doc.ways[101].nodes, doc.ways[101].tags)
    doc.relations[910] = Relation(
        id=910,
        members=[Member("way", 101, "forward")],
        tags={"type": "route"},
    )
    apply_op(doc, h, "reverse_way", {"id": 101})
    assert doc.relations[910].members[0].role == "backward"
    after = traffic_node_order(doc.ways[101].nodes, doc.ways[101].tags)
    assert before == [6, 2, 5]
    assert after == list(reversed(before))
    t = doc.ways[101].tags
    assert t["oneway"] == "-1"
    assert t["lanes:backward"] == "2" and "lanes:forward" not in t
    assert t["turn:lanes:backward"] == "through|right"
    assert t["incline"] == "down"
    assert t["cycleway:right"] == "lane" and "cycleway:left" not in t
    apply_op(doc, h, "reverse_way", {"id": 101})
    assert doc.relations[910].members[0].role == "forward"
    restored = doc.ways[101].tags
    assert doc.ways[101].nodes == [5, 2, 6]
    assert restored["turn:lanes:backward"] == "left|through"
    assert restored["incline"] == "up" and restored["cycleway:left"] == "lane"


def test_reverse_swaps_route_roles_and_roundtrips_tags():
    tags = {
        "oneway": "yes",
        "lanes:forward": "2",
        "turn:lanes:forward": "left|through",
        "sidewalk": "left",
        "incline": "10%",
        "change:lanes:forward": "yes|no",
    }
    once = reversed_tags(tags)
    assert once["oneway"] == "yes"
    assert once["lanes:forward"] == "2"
    assert once["turn:lanes:forward"] == "through|right"
    assert once["sidewalk"] == "right"
    assert once["incline"] == "-10%"
    assert once["change:lanes:forward"] == "no|yes"
    assert reversed_tags(once) == tags


def test_scc_reports_traps_and_respects_oneway_and_access():
    xml = """<?xml version="1.0"?><osm version="0.6">
      <node id="1" lat="52.28" lon="104.28"/>
      <node id="2" lat="52.281" lon="104.281"/>
      <node id="3" lat="52.282" lon="104.282"/>
      <node id="4" lat="52.281" lon="104.283"/>
      <way id="1"><nd ref="1"/><nd ref="2"/>
        <tag k="highway" v="residential"/></way>
      <way id="2"><nd ref="3"/><nd ref="2"/>
        <tag k="highway" v="residential"/><tag k="oneway" v="-1"/></way>
      <way id="3"><nd ref="3"/><nd ref="2"/>
        <tag k="highway" v="service"/><tag k="access" v="no"/></way>
      <way id="4"><nd ref="2"/><nd ref="4"/><nd ref="4"/>
        <tag k="highway" v="residential"/><tag k="oneway" v="yes"/>
        <tag k="layer" v="ground"/></way>
    </osm>"""
    # way 2 is oneway=-1, nodes 3→2 in storage so traffic runs 2→3: node 3 is a trap.
    # way 3 would let traffic return, but access=no keeps it out of the drivable graph.
    # way 4 duplicates a coordinate (zero-length) and has a non-integer layer.
    doc = OsmDocument.parse(xml.encode())
    res = validate(doc)
    traps = [i for i in res["issues"] if "ловуш" in i["title"].lower()]
    assert traps, [i["title"] for i in res["issues"]]
    trap_nodes = {r["id"] for i in traps for r in i["refs"] if r["kind"] == "node"}
    assert 3 in trap_nodes or any(3 in {r["id"] for r in i["refs"]} for i in traps)
    assert any(len(i.get("locations") or []) >= 1 for i in traps)
    assert res["summary"]["outside_strong"] >= 1
    assert res["summary"]["levels"]["webots"] == "risk"
    assert "не запускался" in res["summary"]["level_notes"]["webots"]
    titles = [i["title"] for i in res["issues"]]
    assert "Нулевая длина сегмента" in titles
    assert "layer не целое число" in titles


def test_webots_ok_is_a_real_geometry_check():
    xml = """<?xml version="1.0"?><osm version="0.6">
      <node id="1" lat="52.28" lon="104.28"/>
      <node id="2" lat="52.281" lon="104.281"/>
      <way id="1"><nd ref="1"/><nd ref="2"/>
        <tag k="highway" v="residential"/><tag k="bridge" v="yes"/><tag k="layer" v="1"/></way>
    </osm>"""
    res = validate(OsmDocument.parse(xml.encode()))
    assert res["summary"]["levels"]["webots"] == "ok"
    assert res["summary"]["outside_strong"] == 0
    note = res["summary"]["level_notes"]["webots"]
    assert "layer" in note and "не запускался" in note


def test_example_osm_roundtrip_and_directed_traps():
    raw = EXAMPLE.read_bytes()
    doc = OsmDocument.parse(raw)
    again = OsmDocument.parse(doc.to_xml())
    assert list(doc.nodes) == list(again.nodes)
    assert list(doc.ways) == list(again.ways)
    assert list(doc.relations) == list(again.relations)
    for nid, n in doc.nodes.items():
        m = again.nodes[nid]
        assert (n.lat, n.lon, n.tags, n.attrs, n.raw_coords) == (m.lat, m.lon, m.tags, m.attrs, m.raw_coords)
    for wid, w in doc.ways.items():
        m = again.ways[wid]
        assert (w.nodes, list(w.tags.items()), w.attrs) == (m.nodes, list(m.tags.items()), m.attrs)
    res = validate(doc)
    assert res["summary"]["outside_strong"] == 318
    traps = [i for i in res["issues"] if "ловуш" in i["title"].lower()]
    assert traps and any(i.get("locations") for i in traps)
    assert "не запускался" in res["summary"]["level_notes"]["webots"]


def test_malformed_input_is_4xx_not_500(raw):
    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    r = c.post(f"/api/documents/{doc_id}/ops", json={"op": "move_node", "args": {"id": 1, "lat": "abc", "lon": 1}})
    assert 400 <= r.status_code < 500, r.text
    assert isinstance(r.json()["detail"], str) and "abc" in r.json()["detail"]
    r = c.post(f"/api/documents/{doc_id}/ops", json={"op": "set_tags", "args": {"kind": "nope", "id": 1, "tags": {"a": "b"}}})
    assert 400 <= r.status_code < 500
    assert isinstance(r.json()["detail"], str)
    r = c.post(f"/api/documents/{doc_id}/ops", json={"args": {}})
    assert r.status_code == 400
    assert isinstance(r.json()["detail"], str)
    # the failed move did not stick
    el = c.get(f"/api/documents/{doc_id}/elements/node/1").json()
    assert el["lat"] == 52.28


def test_draft_survives_dropping_the_memory_session(raw):
    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    r = c.post(f"/api/documents/{doc_id}/ops", json={"op": "set_tags", "args": {"kind": "way", "id": 100, "tags": {"maxspeed": "40"}}})
    assert r.status_code == 200
    SESSIONS.pop(doc_id)
    restored = c.get("/api/draft")
    assert restored.status_code == 200
    assert restored.json()["doc_id"] == doc_id
    tags = c.get(f"/api/documents/{doc_id}/elements/way/100").json()["tags"]
    assert tags["maxspeed"] == "40"
    assert c.delete("/api/draft").status_code == 200
    assert c.get("/api/draft").status_code == 404


def test_organizer_requires_token_and_team(monkeypatch):
    monkeypatch.delenv("ORGANIZER_TOKEN", raising=False)
    monkeypatch.delenv("TEAM_NAME", raising=False)
    with pytest.raises(OrganizerError) as exc:
        credentials()
    assert exc.value.status == 400 and "ORGANIZER_TOKEN" in exc.value.message
    monkeypatch.setenv("ORGANIZER_TOKEN", "secret")
    with pytest.raises(OrganizerError) as exc:
        credentials()
    assert "TEAM_NAME" in exc.value.message


def test_organizer_posts_team_file(monkeypatch, raw):
    monkeypatch.setenv("ORGANIZER_TOKEN", "secret-token")
    monkeypatch.setenv("TEAM_NAME", "irkutsk")
    captured = {}

    class Resp:
        status = 200

        def read(self):
            return b"accepted"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_open(req, timeout=120):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = req.data
        return Resp()

    monkeypatch.setattr("app.organizer._open", fake_open)
    token, team, base = credentials()
    result = upload_osm(b"<osm/>", token, team, base)
    assert result["ok"] and result["filename"] == "irkutsk.osm"
    assert captured["url"].endswith("/api/submit")
    assert captured["auth"] == "Bearer secret-token"
    assert b'filename="irkutsk.osm"' in captured["body"]
    assert b"<osm/>" in captured["body"]

    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    monkeypatch.delenv("ORGANIZER_TOKEN", raising=False)
    r = c.post(f"/api/documents/{doc_id}/check/organizer")
    assert r.status_code == 400
    assert "ORGANIZER_TOKEN" in r.json()["detail"]
