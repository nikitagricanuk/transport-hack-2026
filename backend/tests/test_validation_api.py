from fastapi.testclient import TestClient

from app.edits import History, apply_op
from app.main import app
from app.validation import validate


def _titles(res):
    return [i["title"] for i in res["issues"]]


def test_detects_known_problems(doc):
    res = validate(doc)
    titles = _titles(res)
    assert "Разорванный перекрёсток" in titles
    assert "Изолированное ребро" in titles
    assert "Мост без layer" in titles
    assert "Светофор вне дороги" in titles
    assert res["summary"]["levels"]["matsim"] == "fail"
    assert res["summary"]["components"] == 3


def test_fixes_resolve_issues(doc):
    h = History()
    for _ in range(5):
        res = validate(doc)
        fixable = [i for i in res["issues"]
                   if i["fix"] and i["title"] in ("Разорванный перекрёсток", "Мост без layer", "Изолированное ребро")]
        if not fixable:
            break
        fix = fixable[0]["fix"]
        apply_op(doc, h, fix["op"], fix["args"])
    res = validate(doc)
    titles = _titles(res)
    assert "Разорванный перекрёсток" not in titles
    assert "Мост без layer" not in titles
    assert res["summary"]["components"] == 1
    assert 8 not in doc.nodes or any(8 in w.nodes for w in doc.ways.values() if w.id == 100)


def test_api_flow(raw):
    c = TestClient(app)
    r = c.post("/api/documents", files={"file": ("example.osm", raw, "application/xml")})
    assert r.status_code == 200, r.text
    state = r.json()
    doc_id = state["doc_id"]
    assert state["stats"]["ways"] == 6
    assert len(state["geo"]["ways"]["features"]) == 5  # building goes to areas
    assert len(state["geo"]["areas"]["features"]) == 1

    r = c.post(f"/api/documents/{doc_id}/ops", json={"op": "set_lanes", "args": {"way_id": 100, "forward": 2, "backward": 2}})
    assert r.status_code == 200 and r.json()["history"]["can_undo"]

    r = c.post(f"/api/documents/{doc_id}/ops", json={"op": "split_way", "args": {"way_id": 100, "node_id": 1}})
    assert r.status_code == 422

    assert c.get(f"/api/documents/{doc_id}/validate").json()["summary"]["counts"]["critical"] >= 1

    r = c.get(f"/api/documents/{doc_id}/export", params={"filename": "team1"})
    assert r.status_code == 200
    assert 'filename="team1.osm"' in r.headers["content-disposition"]
    assert b'k="lanes:forward" v="2"' in r.content

    r = c.post(f"/api/documents/{doc_id}/undo")
    assert not r.json()["history"]["can_undo"]
    assert c.get(f"/api/documents/{doc_id}/elements/way/100").json()["tags"]["lanes"] == "2"
