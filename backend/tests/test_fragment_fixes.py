"""Fringe-aware connectivity and the auto-fixes that make the hackathon fragment simulation-ready."""

from pathlib import Path

import pytest

from app.edits import History, OpError, apply_op
from app.osm_model import OsmDocument
from app.validation import validate

EXAMPLE = Path(__file__).resolve().parents[2] / "data" / "example.osm"


def _osm(nodes: dict[int, tuple[float, float]], ways: list[tuple[int, list[int], dict[str, str]]]) -> OsmDocument:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="test">',
             '<bounds minlat="52.000" minlon="104.000" maxlat="52.010" maxlon="104.010"/>']
    for nid, (lat, lon) in nodes.items():
        parts.append(f'<node id="{nid}" version="1" lat="{lat}" lon="{lon}"/>')
    for wid, refs, tags in ways:
        parts.append(f'<way id="{wid}" version="1">')
        parts += [f'<nd ref="{r}"/>' for r in refs]
        parts += [f'<tag k="{k}" v="{v}"/>' for k, v in tags.items()]
        parts.append("</way>")
    parts.append("</osm>")
    return OsmDocument.parse("\n".join(parts).encode())


def _divided_road_leaving_extract() -> OsmDocument:
    """Two-way street A–B inside, a divided primary running off the east edge."""
    return _osm(
        {1: (52.005, 104.005), 2: (52.005, 104.007), 3: (52.0050, 104.012), 4: (52.0054, 104.012),
         5: (52.0054, 104.007)},
        [(10, [1, 2], {"highway": "residential"}),
         (11, [2, 3], {"highway": "primary", "oneway": "yes"}),   # leaves the extract
         (12, [4, 5], {"highway": "primary", "oneway": "yes"}),   # comes back in
         (13, [5, 1], {"highway": "residential"})],
    )


def _critical(v):
    return [i for i in v["issues"] if i["severity"] == "critical"]


def test_dead_ends_outside_bounds_are_fringe_not_critical():
    doc = _divided_road_leaving_extract()
    v = validate(doc)
    assert not _critical(v)
    ends = [i for i in v["issues"] if i["category"] == "direction"]
    assert ends and all(i["severity"] == "info" for i in ends)


def test_fringe_turnaround_fix_closes_the_divided_road():
    doc = _divided_road_leaving_extract()
    v = validate(doc)
    fix = next(f for i in v["issues"] for f in (i.get("fixes") or []) if f["label"].startswith("Граничный разворот"))
    assert fix["op"] == "add_way" and fix["args"]["tags"]["uds:fringe"] == "yes"
    assert [p["id"] for p in fix["args"]["points"]] == [3, 4]   # from the sink to the source
    apply_op(doc, History(), fix["op"], fix["args"])
    after = validate(doc)
    assert not [i for i in after["issues"] if i["category"] == "direction"]
    assert after["summary"]["levels"]["matsim"] == "ok"


def test_interior_dead_end_stays_critical():
    doc = _osm(
        {1: (52.005, 104.003), 2: (52.005, 104.005), 3: (52.006, 104.005)},
        [(10, [1, 2], {"highway": "residential"}), (11, [2, 3], {"highway": "residential", "oneway": "yes"})],
    )
    v = validate(doc)
    assert any(i["category"] == "direction" and i["severity"] == "critical" for i in v["issues"])


def _with_fragment() -> OsmDocument:
    return _osm(
        {1: (52.005, 104.003), 2: (52.005, 104.006), 3: (52.004, 104.006), 4: (52.004, 104.003),
         # two-way service fragment ~17 m north of the main street
         20: (52.00515, 104.004), 21: (52.0058, 104.004), 22: (52.0058, 104.005)},
        [(10, [1, 2, 3, 4, 1], {"highway": "residential"}),
         (30, [20, 21], {"highway": "service"}), (31, [21, 22], {"highway": "service"})],
    )


def test_multi_way_fragment_gets_connect_and_delete_fixes():
    doc = _with_fragment()
    v = validate(doc)
    frag = next(i for i in v["issues"] if i["category"] == "connectivity" and i["severity"] == "critical")
    labels = [f["label"] for f in frag["fixes"]]
    assert labels[0].startswith("Присоединить") and "Удалить фрагмент" in labels
    link = frag["fixes"][0]
    apply_op(doc, History(), link["op"], link["args"])
    assert validate(doc)["summary"]["components"] == 1


def test_delete_ways_removes_fragment_in_one_undo_step():
    doc = _with_fragment()
    h = History()
    apply_op(doc, h, "delete_ways", {"ids": [30, 31]})
    assert 30 not in doc.ways and 31 not in doc.ways and 21 not in doc.nodes
    h.undo()
    assert 30 in doc.ways and 31 in doc.ways and 21 in doc.nodes


def test_connect_endpoint_keeps_a_short_neighbour_way():
    """The steps way 1–2 must survive when the road ending at 2 is connected to the street at 1."""
    doc = _osm(
        {1: (52.005, 104.004), 9: (52.005, 104.002), 8: (52.005, 104.006),
         2: (52.00502, 104.004), 3: (52.0056, 104.004)},
        [(10, [9, 1, 8], {"highway": "residential"}),
         (11, [1, 2], {"highway": "steps"}),
         (12, [2, 3], {"highway": "service"})],
    )
    apply_op(doc, History(), "connect_endpoint", {"node_id": 2, "way_id": 10})
    assert 11 in doc.ways and len(set(doc.ways[11].nodes)) == 2


def test_spur_joined_at_its_other_end_is_not_a_gap():
    doc = _osm(
        {1: (52.005, 104.003), 2: (52.005, 104.004), 3: (52.005, 104.005), 4: (52.00502, 104.00402)},
        [(10, [1, 2, 3], {"highway": "service"}), (11, [2, 4], {"highway": "service"})],
    )
    assert not [i for i in validate(doc)["issues"] if i["title"] == "Разорванный перекрёсток"]


def _open_example_via_api():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("example.osm", EXAMPLE.read_bytes(), "application/xml")}).json()["doc_id"]
    return c, doc_id


@pytest.mark.skipif(not EXAMPLE.exists(), reason="example.osm not present")
def test_opening_the_raw_fragment_changes_nothing_and_offers_fixes():
    """Fixes are offered, never applied on load: the user reviews and clicks each one."""
    c, doc_id = _open_example_via_api()
    v = c.get(f"/api/documents/{doc_id}/validate").json()
    assert v["summary"]["counts"]["critical"] > 0  # the task fragment is broken on purpose
    for issue in v["issues"]:
        if issue["severity"] != "info":
            assert issue.get("fixes"), issue["title"]
    exported = c.get(f"/api/documents/{doc_id}/export", params={"renumber": False}).content
    assert OsmDocument.parse(exported).stats() == OsmDocument.parse(EXAMPLE.read_bytes()).stats()


@pytest.mark.skipif(not EXAMPLE.exists(), reason="example.osm not present")
def test_offered_fixes_can_make_the_fragment_simulation_ready():
    """Test helper only: apply one offered fix per issue, reconnecting before deleting.

    In the product every one of these steps is a user's choice; this only proves
    that the offered options are enough to reach a simulation-ready graph.
    """
    doc = OsmDocument.parse(EXAMPLE.read_bytes())
    h = History()
    tried: set[tuple] = set()

    def key(i):  # issue ids are positional, so identify an issue by what it points at
        return i["title"], tuple(sorted((r["kind"], r["id"]) for r in i["refs"]))

    for _ in range(60):
        todo = [i for i in validate(doc)["issues"]
                if i["severity"] == "critical" and i.get("fixes") and key(i) not in tried]
        if not todo:
            break
        issue = todo[0]
        tried.add(key(issue))
        for fix in sorted(issue["fixes"], key=lambda f: f["op"] in ("delete", "delete_ways")):
            try:
                apply_op(doc, h, fix["op"], fix["args"])
                break
            except OpError:
                continue
    v = validate(doc)
    assert v["summary"]["counts"]["critical"] == 0
    assert "fail" not in v["summary"]["levels"].values()


@pytest.mark.skipif(not EXAMPLE.exists(), reason="example.osm not present")
def test_kurchatova_closure_has_a_real_detour():
    """Demo case: closing ул. Академика Курчатова reroutes its whole volume."""
    c, doc_id = _open_example_via_api()
    closure = c.post(f"/api/documents/{doc_id}/scenario/closures",
                     json={"way_id": 80347185, "closed": True}).json()["closures"][0]
    assert closure["found"] and not closure["partial"] and closure["moved"] > 0 and len(closure["ways"]) >= 3


@pytest.mark.skipif(not EXAMPLE.exists(), reason="example.osm not present")
def test_fix_preview_shows_the_change_and_leaves_the_document_alone():
    c, doc_id = _open_example_via_api()
    before = c.get(f"/api/documents/{doc_id}/export", params={"renumber": False}).content
    issues = [i for i in c.get(f"/api/documents/{doc_id}/validate").json()["issues"] if i["severity"] == "critical"]
    seen_delete = seen_add = False
    for issue in issues:
        for fix in issue["fixes"]:
            r = c.post(f"/api/documents/{doc_id}/fix-preview", json={"op": fix["op"], "args": fix["args"]})
            if r.status_code == 422:
                continue
            p = r.json()
            assert p["features"], fix["label"]
            if fix["op"] in ("delete", "delete_ways"):
                seen_delete = True
                assert p["removed_road_m"] > 0 and p["counts"]["deleted"] > 0
            if fix["op"] == "add_way":
                seen_add = True
                assert p["added_road_m"] > 0 and any(f["properties"]["status"] == "added" for f in p["features"])
    assert seen_delete and seen_add
    assert c.get(f"/api/documents/{doc_id}/export", params={"renumber": False}).content == before
    assert c.get(f"/api/documents/{doc_id}").json()["history"]["can_undo"] is False
    # a real apply afterwards gets the same new id it would have without previews
    fix = next(f for i in issues for f in i["fixes"] if f["op"] == "add_way")
    res = c.post(f"/api/documents/{doc_id}/ops", json={"op": fix["op"], "args": fix["args"]}).json()
    fresh = apply_op(OsmDocument.parse(EXAMPLE.read_bytes()), History(), fix["op"], fix["args"])
    assert res["result"]["id"] == fresh["id"]


@pytest.mark.skipif(not EXAMPLE.exists(), reason="example.osm not present")
def test_edit_log_lists_each_applied_fix_with_author_and_time():
    c, doc_id = _open_example_via_api()
    issue = next(i for i in c.get(f"/api/documents/{doc_id}/validate").json()["issues"]
                 if any(f["op"] == "add_way" for f in i["fixes"]))
    fix = next(f for f in issue["fixes"] if f["op"] == "add_way")
    c.post(f"/api/documents/{doc_id}/ops", json={"op": fix["op"], "args": fix["args"], "label": fix["label"]},
           headers={"X-User-Name": "Reviewer"})
    log = c.get(f"/api/documents/{doc_id}/edit-log").text
    assert "Правок: 1." in log and "| 1 |" in log and "Reviewer" in log and fix["label"].split(" (")[0] in log
