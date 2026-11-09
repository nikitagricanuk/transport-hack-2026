"""OD matrix, closures with detours, tasks, PDF and object reports, edit authorship."""

from urllib.parse import quote

from fastapi.testclient import TestClient

from app import scenario
from app.analysis import road_rows
from app.edits import History, apply_op
from app.main import SESSIONS, app


def _open(c, raw):
    return c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]


def test_od_matrix_rows_follow_residents(doc):
    od = scenario.od_matrix(doc, grid=2)
    assert od["zones"] and len(od["trips"]) == len(od["zones"]) == len(od["shares"])
    for z, row, share in zip(od["zones"], od["trips"], od["shares"]):
        assert len(row) == len(od["zones"])
        assert abs(sum(row) - round(z["residents"] * scenario.PEAK_TRIP_RATE)) <= len(row)
        assert abs(sum(share) - 100) < 1 or sum(share) == 0


def test_closure_moves_traffic_onto_the_detour(doc):
    h = History()
    # a loop 1-2-5-1 so the new road has an alternative
    loop = apply_op(doc, h, "add_way", {"points": [{"id": 1}, {"id": 5}], "tags": {"highway": "residential"}})["id"]
    rows = road_rows(doc, {}, 1.0)
    base = {r["id"]: r["volume"] for r in rows}
    info = scenario.apply_closures(doc, rows, {loop})
    assert info[loop]["found"] and set(info[loop]["ways"]) == {100, 101}
    after = {r["id"]: r for r in rows}
    assert after[loop]["volume"] == 0 and after[loop]["closed"]
    assert after[100]["volume"] > base[100] and after[100]["detour"]


def test_closure_api_and_tasks(raw):
    c = TestClient(app)
    doc_id = _open(c, raw)
    state = c.post(f"/api/documents/{doc_id}/scenario/closures", json={"way_id": 101, "closed": True}).json()
    closure = state["closures"][0]
    assert closure["id"] == 101 and closure["found"] is False  # a dead-end street: no detour
    assert next(r for r in state["rows"] if r["id"] == 101)["volume"] == 0
    reopened = c.post(f"/api/documents/{doc_id}/scenario/closures", json={"way_id": 101, "closed": False}).json()
    assert reopened["closures"] == []

    tasks = c.get(f"/api/documents/{doc_id}/tasks").json()["tasks"]
    assert [t["id"] for t in tasks][:3] == ["fix-graph", "no-jams", "sumo-ready"]
    res = c.post(f"/api/documents/{doc_id}/tasks/fix-graph/check").json()
    assert res["total"] == 3 and res["passed"] < 3 and not res["done"]

    made = c.post(f"/api/documents/{doc_id}/tasks", json={"title": "Расширить", "criteria": [
        {"type": "lanes_min", "way_id": 100, "value": 4, "label": "4 полосы на 100"},
        {"type": "tag", "kind": "way", "id": 100, "key": "maxspeed", "value": "50"},
    ]}).json()["tasks"]
    tid = made[-1]["id"]
    assert c.post(f"/api/documents/{doc_id}/tasks/{tid}/check").json()["passed"] == 0
    c.post(f"/api/documents/{doc_id}/batch", json={"ops": [
        {"op": "set_lanes", "args": {"way_id": 100, "forward": 2, "backward": 2}},
        {"op": "set_tags", "args": {"kind": "way", "id": 100, "tags": {"maxspeed": "50"}}},
    ]})
    done = c.post(f"/api/documents/{doc_id}/tasks/{tid}/check").json()
    assert done["passed"] == 2 and done["done"]
    assert c.post(f"/api/documents/{doc_id}/tasks", json={"title": "пусто", "criteria": []}).status_code == 400
    assert c.delete(f"/api/documents/{doc_id}/tasks/{tid}").json()["tasks"][-1]["id"] != tid
    assert c.get(f"/api/documents/{doc_id}/od", params={"grid": 2}).json()["grid"] == 2


def test_pdf_and_object_reports(raw):
    c = TestClient(app)
    doc_id = _open(c, raw)
    r = c.get(f"/api/documents/{doc_id}/report.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:4] == b"%PDF"
    for kind, eid in (("way", 100), ("node", 11)):
        x = c.get(f"/api/documents/{doc_id}/objects/{kind}/{eid}/report.xlsx")
        p = c.get(f"/api/documents/{doc_id}/objects/{kind}/{eid}/report.pdf")
        assert x.status_code == 200 and x.content[:2] == b"PK"
        assert p.status_code == 200 and p.content[:4] == b"%PDF"
    assert c.get(f"/api/documents/{doc_id}/objects/way/999/report.pdf").status_code == 404


def test_history_records_author_and_elements_and_survives_restart(raw):
    c = TestClient(app)
    doc_id = _open(c, raw)
    c.post(f"/api/documents/{doc_id}/ops", headers={"X-User-Name": quote("Аня")},
           json={"op": "set_tags", "args": {"kind": "way", "id": 100, "tags": {"maxspeed": "40"}}})
    entry = c.get(f"/api/documents/{doc_id}").json()["history"]["details"][0]
    assert entry["author"] == "Аня" and entry["at"] > 0
    assert entry["refs"] == [{"kind": "way", "id": 100, "status": "modified", "name": "улица Карла Маркса"}]
    SESSIONS.pop(doc_id)
    again = c.get(f"/api/documents/{doc_id}").json()["history"]["details"][0]
    assert again["author"] == "Аня" and again["at"] == entry["at"]
