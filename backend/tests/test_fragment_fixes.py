"""Fringe-aware connectivity and the auto-fixes that make the hackathon fragment simulation-ready."""

from pathlib import Path

import pytest

from app.edits import History, apply_op
from app.osm_model import OsmDocument
from app.scenario import builtin_tasks, check_task
from app.validation import validate

FIXED = Path(__file__).resolve().parents[2] / "data" / "irkutsk_fragment_fixed.osm"


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


@pytest.mark.skipif(not FIXED.exists(), reason="fixed fragment not built")
def test_fixed_fragment_is_simulation_ready():
    doc = OsmDocument.parse(FIXED.read_bytes())
    v = validate(doc)
    assert v["summary"]["counts"]["critical"] == 0
    assert v["summary"]["components"] == 1
    assert v["summary"]["levels"]["matsim"] == "ok" and v["summary"]["levels"]["sumo"] == "ok"
    task = next(t for t in builtin_tasks(doc) if t["id"] == "fix-graph")
    assert check_task(doc, task, v, [])["done"]


@pytest.mark.skipif(not FIXED.exists(), reason="fixed fragment not bundled")
def test_kurchatova_closure_has_a_real_detour():
    """Demo case: closing ул. Академика Курчатова reroutes its whole volume."""
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("f.osm", FIXED.read_bytes(), "application/xml")}).json()["doc_id"]
    closure = c.post(f"/api/documents/{doc_id}/scenario/closures",
                     json={"way_id": 80347185, "closed": True}).json()["closures"][0]
    assert closure["found"] and not closure["partial"] and closure["moved"] > 0 and len(closure["ways"]) >= 3
