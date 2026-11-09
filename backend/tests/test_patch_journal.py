"""Incremental map patches, edit journal drafts and the fast XML writer."""

import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from app.edits import History, apply_op
from app.geojson import geo_patch, to_geojson
from app.main import SESSIONS, app
from app.osm_model import OsmDocument


def _merge(geo: dict, patch: dict) -> dict:
    """What the frontend does with a patch."""
    ways, nodes = set(patch["ways"]), set(patch["nodes"])
    out = {}
    for key, drop in (("ways", ways), ("areas", ways), ("nodes", nodes)):
        kept = [f for f in geo[key]["features"] if f["properties"]["id"] not in drop]
        out[key] = sorted(kept + patch["features"][key], key=lambda f: f["id"])
    return out


def _sorted(geo: dict) -> dict:
    return {k: sorted(geo[k]["features"], key=lambda f: f["id"]) for k in ("ways", "areas", "nodes")}


def test_patch_equals_full_reload_for_every_kind_of_edit(doc):
    h = History()
    edits = [
        ("move_node", {"id": 2, "lat": 52.2801, "lon": 104.2811}),        # moves 2 ways and a junction
        ("insert_node", {"way_id": 100, "lat": 52.28, "lon": 104.2815}),
        ("connect_endpoint", {"node_id": 8, "way_id": 100}),              # creates a junction
        ("set_tags", {"kind": "node", "id": 3, "tags": {"highway": "traffic_signals"}}),
        ("delete", {"kind": "way", "id": 103}),
        ("add_way", {"points": [{"id": 5}, {"lat": 52.282, "lon": 104.281}], "tags": {"highway": "residential"}}),
        ("reverse_way", {"id": 101}),
    ]
    geo = to_geojson(doc)
    for op, args in edits:
        apply_op(doc, h, op, args)
        tx = h.last_tx
        merged = _merge(geo, geo_patch(doc, tx.before, tx.after))
        full = to_geojson(doc)
        assert merged == _sorted(full), op
        geo = full
    # undo is also a patch
    tx = h.undo()
    assert _merge(geo, geo_patch(doc, tx.before, tx.after)) == _sorted(to_geojson(doc))


def test_ops_return_patch_not_full_map(raw):
    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    r = c.post(f"/api/documents/{doc_id}/ops", json={"op": "move_node", "args": {"id": 2, "lat": 52.2801, "lon": 104.2811}})
    body = r.json()
    assert "geo" not in body
    assert set(body["patch"]["ways"]) == {100, 101}
    assert 2 in body["patch"]["nodes"]
    assert body["history"]["can_undo"]
    r = c.post(f"/api/documents/{doc_id}/undo").json()
    assert set(r["patch"]["ways"]) == {100, 101}
    # nothing to redo twice: patch is null, state still returned
    c.post(f"/api/documents/{doc_id}/redo")
    r = c.post(f"/api/documents/{doc_id}/redo").json()
    assert r["patch"] is None and r["history"]["can_undo"]


def test_journal_restores_edits_and_undo_history(raw):
    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    ops = [
        {"op": "set_tags", "args": {"kind": "way", "id": 100, "tags": {"maxspeed": "40"}}},
        {"op": "add_way", "args": {"points": [{"id": 5}, {"lat": 52.282, "lon": 104.281}], "tags": {"highway": "service"}}},
        {"op": "set_tags", "args": {"kind": "way", "id": 100, "tags": {"name": "temp"}}},
    ]
    for op in ops:
        assert c.post(f"/api/documents/{doc_id}/ops", json=op).status_code == 200
    c.post(f"/api/documents/{doc_id}/undo")  # drops name=temp
    c.post(f"/api/documents/{doc_id}/batch", json={"ops": [
        {"op": "set_tags", "args": {"kind": "way", "id": 101, "tags": {"lanes": "2"}}},
        {"op": "set_tags", "args": {"kind": "way", "id": 101, "tags": {"maxspeed": "30"}}},
    ], "label": "Шаблон"})
    live = SESSIONS[doc_id].doc.to_xml()

    SESSIONS.pop(doc_id)  # backend restart
    state = c.get(f"/api/documents/{doc_id}").json()
    assert SESSIONS[doc_id].doc.to_xml() == live
    assert state["history"]["undo_label"] == "Шаблон"
    assert len(state["history"]["entries"]) == 3
    # undo history survived the restart
    c.post(f"/api/documents/{doc_id}/undo")
    tags = c.get(f"/api/documents/{doc_id}/elements/way/101").json()["tags"]
    assert "maxspeed" not in tags and "lanes" not in tags


def test_journal_compacts(raw, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "JOURNAL_COMPACT_AT", 3)
    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    for speed in ("10", "20", "30", "40"):
        c.post(f"/api/documents/{doc_id}/ops", json={"op": "set_tags", "args": {"kind": "way", "id": 100, "tags": {"maxspeed": speed}}})
    journal = main._journal_path(doc_id)
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 1
    SESSIONS.pop(doc_id)
    c.get(f"/api/documents/{doc_id}")
    assert SESSIONS[doc_id].doc.ways[100].tags["maxspeed"] == "40"


def test_fast_writer_is_valid_xml_and_escapes(doc):
    h = History()
    apply_op(doc, h, "set_tags", {"kind": "way", "id": 100, "tags": {"name": 'A & B <"C">\n\tтест'}})
    out = doc.to_xml()
    ET.fromstring(out)  # well-formed
    again = OsmDocument.parse(out)
    assert again.ways[100].tags["name"] == 'A & B <"C">\n\tтест'
    assert out.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    text = out.decode("utf-8")
    assert "<note>" in text and "<bounds" in text and "<meta" in text
