"""Smooth Z, load model with snapshots, before/after changes, reports."""

import io

import pytest
from fastapi.testclient import TestClient

from app.analysis import network_stats, road_rows
from app.edits import History, apply_op
from app.main import SESSIONS, app
from app.validation import validate


def _titles(doc):
    return [i["title"] for i in validate(doc)["issues"]]


def test_smooth_z_ramps_down_from_the_bridge(doc):
    h = History()
    apply_op(doc, h, "set_tags", {"kind": "way", "id": 104, "tags": {"layer": "1"}})
    assert "Резкий Z-переход" in _titles(doc)
    res = apply_op(doc, h, "smooth_z", {"way_id": 104})
    assert res["height"] == 5.0
    assert doc.nodes[4].tags["ele"] == "5.0" and doc.nodes[12].tags["ele"] == "5.0"
    # way 100 approaches node 4 with 68 m segments: the 60 m ramp ends inside 3-4
    nodes = doc.ways[100].nodes
    assert nodes[:3] == [1, 2, 3] and nodes[-1] == 4 and len(nodes) == 5
    assert doc.nodes[nodes[3]].tags["ele"] == "0.0"
    titles = _titles(doc)
    assert "Резкий Z-переход" not in titles and "Резкий перепад высоты" not in titles
    h.undo()
    assert doc.ways[100].nodes == [1, 2, 3, 4] and "ele" not in doc.nodes[4].tags


def test_steep_grade_is_reported_with_a_fix(doc):
    h = History()
    apply_op(doc, h, "set_tags", {"kind": "way", "id": 104, "tags": {"layer": "1"}})
    apply_op(doc, h, "set_tags", {"kind": "node", "id": 4, "tags": {"ele": "20"}})
    issue = next(i for i in validate(doc)["issues"] if i["title"] == "Резкий перепад высоты")
    assert issue["fix"] == {"label": "Плавный Z", "op": "smooth_z", "args": {"way_id": 104}}


def test_smooth_z_needs_a_level(doc):
    from app.edits import OpError

    with pytest.raises(OpError):
        apply_op(doc, History(), "smooth_z", {"way_id": 101})


def test_load_rows_follow_lanes_and_overrides(doc):
    rows = {r["id"]: r for r in road_rows(doc, {}, 1.0)}
    assert rows[100]["lanes"] == 2 and rows[100]["capacity"] == 1800  # primary: 2 x 900
    assert rows[100]["los"] in "ABCDEF" and rows[100]["t_s"] >= rows[100]["t0_s"]
    rows = {r["id"]: r for r in road_rows(doc, {100: 2700}, 1.0)}
    assert rows[100]["vc"] == 1.5 and rows[100]["los"] == "F" and rows[100]["custom"]
    # an extra lane adds capacity, not demand: default volume stays, v/c drops
    before = {r["id"]: r for r in road_rows(doc, {}, 1.0)}[100]
    apply_op(doc, History(), "set_lanes", {"way_id": 100, "forward": 2, "backward": 2})
    after = {r["id"]: r for r in road_rows(doc, {}, 1.0)}[100]
    assert after["volume"] == before["volume"] and after["capacity"] == 2 * before["capacity"]
    assert after["vc"] == before["vc"] / 2
    stats = network_stats(doc)
    assert stats["road_km"] > 0 and stats["buildings"]["count"] == 1 and stats["bridges"] == 1


def test_snapshots_compare_load_and_graph_changes(raw):
    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    base = c.get(f"/api/documents/{doc_id}/load").json()
    assert base["multiplier"] == 1.0 and base["summary"]["roads"] == 5
    c.post(f"/api/documents/{doc_id}/load", json={"way_id": 100, "volume": 1700})
    a = c.post(f"/api/documents/{doc_id}/load/snapshots", json={"name": "До"}).json()["snapshots"][-1]["id"]
    c.post(f"/api/documents/{doc_id}/ops", json={"op": "set_lanes", "args": {"way_id": 100, "forward": 2, "backward": 2}})
    c.post(f"/api/documents/{doc_id}/load", json={"multiplier": 1.2})
    state = c.post(f"/api/documents/{doc_id}/load/snapshots", json={"name": "После"}).json()
    b = state["snapshots"][-1]["id"]
    assert "rows" not in state["snapshots"][0]

    cmp_ = c.get(f"/api/documents/{doc_id}/load/compare", params={"a": a, "b": b}).json()
    row = next(r for r in cmp_["changed"] if r["id"] == 100)
    assert (row["lanes_before"], row["lanes_after"]) == (2, 4)
    assert (row["capacity_before"], row["capacity_after"]) == (1800, 3600)
    assert row["volume_after"] == 2040 and row["vc_after"] < row["vc_before"]
    assert cmp_["totals"]["roads"]["delta"] == 0
    # compare against the live state as well
    assert c.get(f"/api/documents/{doc_id}/load/compare", params={"a": a}).status_code == 200
    assert c.get(f"/api/documents/{doc_id}/load/compare", params={"a": "nope"}).status_code == 404

    # scenario survives a backend restart together with the draft
    SESSIONS.pop(doc_id)
    again = c.get(f"/api/documents/{doc_id}/load").json()  # restored from the draft on demand
    assert again["multiplier"] == 1.2 and len(again["snapshots"]) == 2

    assert c.post(f"/api/documents/{doc_id}/load", json={"way_id": 999999, "volume": 5}).status_code == 404
    reset = c.post(f"/api/documents/{doc_id}/load", json={"reset_all": True}).json()
    assert reset["multiplier"] == 1.0 and not any(r["custom"] for r in reset["rows"])
    left = c.delete(f"/api/documents/{doc_id}/load/snapshots/{a}").json()
    assert [snap["id"] for snap in left["snapshots"]] == [b]


def test_changes_list_tag_diffs_and_validation_before_after(raw):
    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    assert c.get(f"/api/documents/{doc_id}/changes").json()["changes"] == []
    c.post(f"/api/documents/{doc_id}/ops", json={"op": "set_tags", "args": {"kind": "way", "id": 100, "tags": {"maxspeed": "50"}}})
    c.post(f"/api/documents/{doc_id}/ops", json={"op": "connect_endpoint", "args": {"node_id": 8, "way_id": 100}})
    body = c.get(f"/api/documents/{doc_id}/changes").json()
    way = next(ch for ch in body["changes"] if ch["kind"] == "way" and ch["id"] == 100)
    assert way["status"] == "modified" and way["geometry"]
    assert {"key": "maxspeed", "before": None, "after": "50"} in way["tags"]
    assert len(way["before_coords"]) == 4 and len(way["after_coords"]) == 5
    assert any(ch["status"] == "deleted" and ch["id"] == 8 for ch in body["changes"])
    assert body["before"]["components"] == 3 and body["after"]["components"] == 2


def test_reports_contain_numbers_and_charts(raw):
    openpyxl = pytest.importorskip("openpyxl")
    pytest.importorskip("docx")
    pytest.importorskip("matplotlib")
    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    c.post(f"/api/documents/{doc_id}/load/snapshots", json={"name": "A"})
    c.post(f"/api/documents/{doc_id}/ops", json={"op": "set_lanes", "args": {"way_id": 100, "forward": 2, "backward": 2}})
    c.post(f"/api/documents/{doc_id}/load/snapshots", json={"name": "B"})

    data = c.get(f"/api/documents/{doc_id}/report").json()
    assert data["comparison"]["a"]["name"] == "A" and "rows" not in data["load"]
    assert sum(r["critical"] for r in data["validation"]["matrix"]) >= 1

    r = c.get(f"/api/documents/{doc_id}/report.xlsx")
    assert r.status_code == 200 and 'report_t.xlsx' in r.headers["content-disposition"]
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Сводка", "Дороги", "Проблемы", "Нагрузка", "Сравнение", "Изменения"]
    load_ids = [row[0].value for row in wb["Нагрузка"].iter_rows(min_col=1, max_col=1) if row[0].value is not None]
    assert load_ids[0] == "ID" and len(load_ids) == 6  # header + 5 roads
    assert len(wb["Дороги"]._charts) == 1 and len(wb["Сравнение"]._charts) == 2

    r = c.get(f"/api/documents/{doc_id}/report.docx")
    assert r.status_code == 200
    import docx

    d = docx.Document(io.BytesIO(r.content))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "Сравнение состояний" in text and len(d.inline_shapes) >= 3


def test_signal_off_road_is_attached_with_its_tags(doc):
    from app.edits import OpError

    h = History()
    sid = apply_op(doc, h, "add_node", {"lat": 52.2801, "lon": 104.2815,
                                         "tags": {"highway": "traffic_signals", "traffic_signals:direction": "forward"}})["id"]
    issue = next(i for i in validate(doc)["issues"]
                 if i["title"] == "Светофор вне дороги" and i["refs"][0]["id"] == sid)
    assert issue["fix"]["op"] == "attach_to_road"
    res = apply_op(doc, h, issue["fix"]["op"], issue["fix"]["args"])
    assert res["way_id"] == 100 and res["distance_m"] < 12
    node = doc.nodes[res["id"]]
    assert node.tags["highway"] == "traffic_signals" and node.tags["traffic_signals:direction"] == "forward"
    assert res["id"] in doc.ways[100].nodes and abs(node.lat - 52.28) < 1e-6
    assert not any(i["title"] == "Светофор вне дороги" and i["refs"][0]["id"] == res["id"] for i in validate(doc)["issues"])
    with pytest.raises(OpError):
        apply_op(doc, h, "attach_to_road", {"node_id": res["id"]})
    far = apply_op(doc, h, "add_node", {"lat": 52.2835, "lon": 104.2850, "tags": {"highway": "traffic_signals"}})["id"]
    with pytest.raises(OpError):
        apply_op(doc, h, "attach_to_road", {"node_id": far})
