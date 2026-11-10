"""«Править всё»: only single, non-geometric fixes are offered together; the rest stays with the user."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import fix_all
from app.main import app

EXAMPLE = Path(__file__).resolve().parents[2] / "data" / "example.osm"


def _issue(fixes, severity="warning"):
    return {"id": "x", "title": "t", "severity": severity, "description": "", "refs": [], "location": None,
            "fixes": fixes}


def test_safe_and_disputed_are_told_apart():
    tag = {"label": "maxspeed=60", "op": "set_tags", "args": {"kind": "way", "id": 1, "tags": {"maxspeed": "60"}}}
    rel = {"label": "Удалить отношение", "op": "delete", "args": {"kind": "relation", "id": 5}}
    way = {"label": "Удалить", "op": "delete", "args": {"kind": "way", "id": 1}}
    join = {"label": "Соединить", "op": "connect_endpoint", "args": {}}
    fringe = {"label": "Граничный разворот", "op": "add_way", "args": {"tags": {"uds:fringe": "yes"}}}
    plan = fix_all.plan({"issues": [_issue([tag]), _issue([tag]), _issue([rel]), _issue([way]), _issue([join]),
                                     _issue([fringe]), _issue([tag, way]), _issue([])]})
    assert [s["op"] for s in plan["safe"]] == ["set_tags", "delete"]   # the duplicate tag fix is listed once
    reasons = [d["reason"] for d in plan["disputed"]]
    assert any("удаляет" in r for r in reasons) and any("геометрию" in r for r in reasons)
    assert any("фиктивное" in r for r in reasons) and any("варианта" in r for r in reasons)
    assert any("ручная" in r for r in reasons)


@pytest.mark.skipif(not EXAMPLE.exists(), reason="example.osm not present")
def test_plan_applies_nothing_and_the_batch_is_one_undo_step():
    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("e.osm", EXAMPLE.read_bytes(), "application/xml")}).json()["doc_id"]
    plan = c.get(f"/api/documents/{doc_id}/fix-all").json()
    assert plan["safe"] and plan["disputed"]
    assert c.get(f"/api/documents/{doc_id}").json()["history"]["can_undo"] is False
    ops = [{"op": s["op"], "args": s["args"]} for s in plan["safe"]]
    r = c.post(f"/api/documents/{doc_id}/batch", json={"ops": ops, "label": f"Править всё: {len(ops)} исправлений"})
    assert r.status_code == 200
    hist = c.get(f"/api/documents/{doc_id}").json()["history"]
    assert len(hist["entries"]) == 1 and hist["entries"][0].startswith("Править всё")
    assert not c.get(f"/api/documents/{doc_id}/fix-all").json()["safe"]   # nothing safe left


def test_dismissed_issue_is_hidden_from_counts_and_fix_all_and_can_come_back(raw):
    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    v = c.get(f"/api/documents/{doc_id}/validate").json()
    issue = next(i for i in v["issues"] if i["severity"] != "info")
    before = v["summary"]["counts"][issue["severity"]]
    after = c.post(f"/api/documents/{doc_id}/issues/dismiss", json={"key": issue["key"]}).json()
    hidden = next(i for i in after["issues"] if i["key"] == issue["key"])
    assert hidden["dismissed"] and after["summary"]["dismissed"] == 1
    assert after["summary"]["counts"][issue["severity"]] == before - 1
    assert c.get(f"/api/documents/{doc_id}/validate").json()["summary"]["dismissed"] == 1
    plan = c.get(f"/api/documents/{doc_id}/fix-all").json()
    assert all(p["issue_id"] != issue["id"] for p in plan["safe"] + plan["disputed"])
    back = c.post(f"/api/documents/{doc_id}/issues/dismiss", json={"key": issue["key"], "dismissed": False}).json()
    assert back["summary"]["dismissed"] == 0 and back["summary"]["counts"][issue["severity"]] == before
