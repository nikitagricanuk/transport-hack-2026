"""Mid-road joins, splitting two-point roads, signal programs with arrows, report labels, OD zones."""

from fastapi.testclient import TestClient

from app import report, scenario, signals
from app.edits import History, apply_op
from app.main import app
from app.osm_model import OsmDocument


def _doc(nodes, ways):
    ns = "".join(f'<node id="{i}" lat="{52 + y / 111000}" lon="{104 + x / 68000}"/>' for i, x, y in nodes)
    ws = "".join(f'<way id="{i}">' + "".join(f'<nd ref="{n}"/>' for n in refs)
                 + f'<tag k="highway" v="{hw}"/></way>' for i, refs, hw in ways)
    return OsmDocument.parse(f'<osm version="0.6">{ns}{ws}</osm>'.encode())


def test_new_road_attached_to_the_middle_splits_the_existing_one():
    doc = _doc([(1, 0, 0), (2, 200, 0), (3, 100, 80)], [(10, [1, 2], "residential")])
    h = History()
    mid = {"lat": 52.0, "lon": 104 + 100 / 68000, "way_id": 10}
    res = apply_op(doc, h, "add_way", {"points": [{"id": 3}, mid], "tags": {"highway": "residential"}})
    new = doc.ways[res["id"]]
    junction = new.nodes[-1]
    # the old road now ends at the junction and a new segment starts there: a real node of degree 3
    assert doc.ways[10].nodes[-1] == junction
    assert len(res["split"]) == 1 and doc.ways[res["split"][0]].nodes[0] == junction
    assert sum(junction in (w.nodes[0], w.nodes[-1]) for w in doc.ways.values()) == 3
    # «Разъединить» pulls the branch away and keeps the road whole on both sides
    apply_op(doc, h, "separate_ways", {"node_id": junction})
    assert junction in doc.ways[10].nodes and junction in doc.ways[res["split"][0]].nodes
    assert junction not in doc.ways[res["id"]].nodes


def test_two_point_road_splits_into_carriageways():
    doc = _doc([(1, 0, 0), (2, 200, 0)], [(10, [1, 2], "secondary")])
    res = apply_op(doc, History(), "split_carriageways", {"way_id": 10})
    assert res["id"] == 10
    assert doc.ways[10].tags["oneway"] == "yes"
    assert sum(1 for w in doc.ways.values() if w.tags.get("highway") == "secondary") == 2


def test_arrow_phase_adds_capacity_and_round_trips_through_tags():
    prog = [{"name": "Главное", "green_s": 40, "moves": ["main"]},
            {"name": "Стрелка", "green_s": 10, "moves": ["cross_left"]},
            {"name": "Пересекающее", "green_s": 30, "moves": ["cross", "main_right"]}]
    assert signals.program_cycle(prog) == 80 + 3 * signals.INTERGREEN_S
    assert signals.effective_green(prog, "cross") == 30 + 10 * signals.TURN_SHARE["left"]
    assert signals.effective_green(prog, "main") == 40 + 30 * signals.TURN_SHARE["right"]
    tags = "40:main;10:cross_left;30:cross,main_right"
    back = signals._parse_program(tags)
    assert [p["moves"] for p in back] == [p["moves"] for p in prog]


def test_signal_program_api(raw):
    c = TestClient(app)
    did = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    c.post(f"/api/documents/{did}/ops", json={"op": "place_signal", "args": {"node_id": 2}})
    before = c.get(f"/api/documents/{did}/signals/2").json()
    prog = [{"name": "Главное", "green_s": 40, "moves": ["main"]},
            {"name": "Налево", "green_s": 10, "moves": ["main_left"]},
            {"name": "Пересекающее", "green_s": 30, "moves": ["cross"]}]
    r = c.post(f"/api/documents/{did}/signals/2", json={"program": prog})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["program"] and len(plan["phases"]) == 3 and plan["cycle"] == 95
    assert plan["effective_green"]["main"] == 41.5 and before["cycle"] == 90
    assert b'uds:signal:program' in c.get(f"/api/documents/{did}/export").content
    bad = [{"green_s": 40, "moves": ["main"]}, {"green_s": 10, "moves": ["main_left"]}]
    assert c.post(f"/api/documents/{did}/signals/2", json={"program": bad}).status_code == 400


def test_report_labels_are_russian_with_the_osm_value():
    assert report.highway_label("secondary") == "Магистральная улица районного значения (secondary)"
    assert report.kind_label("way", {"highway": "secondary"}) == "Дорога (way)"
    assert report.kind_label("node", {}) == "Узел (node)"


def test_od_zone_names_are_real_compass_words():
    assert scenario._zone_name(0, 0, 3) == "Юго-запад" and scenario._zone_name(1, 1, 3) == "Центр"
    assert scenario._zone_name(1, 1, 2) == "Северо-восток"
    assert scenario._zone_name(0, 3, 4) == "Зона А1"


def test_moving_an_attached_building_leaves_its_neighbour_in_place():
    # two houses with a common wall (nodes 2 and 3)
    ns = [(1, 0, 0), (2, 10, 0), (3, 10, 10), (4, 0, 10), (5, 20, 0), (6, 20, 10)]
    doc = _doc(ns, [])
    from app.osm_model import Way
    doc.ways[100] = Way(id=100, nodes=[1, 2, 3, 4, 1], tags={"building": "yes"})
    doc.ways[101] = Way(id=101, nodes=[2, 5, 6, 3, 2], tags={"building": "yes"})
    before = [(doc.nodes[n].lat, doc.nodes[n].lon) for n in doc.ways[101].nodes]
    apply_op(doc, History(), "move_way", {"id": 100, "dlat": 0.0001, "dlon": 0.0})
    assert [(doc.nodes[n].lat, doc.nodes[n].lon) for n in doc.ways[101].nodes] == before
    assert not set(doc.ways[100].nodes) & set(doc.ways[101].nodes)
