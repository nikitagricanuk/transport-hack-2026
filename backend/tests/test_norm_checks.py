"""Checks backed by Russian norms: speed limits (ПДД 10.2), turn restrictions, grades (СП 42)."""

from fastapi.testclient import TestClient

from app import main
from app.analysis import _speed
from app.edits import History, apply_op
from app.norms import max_grade, speed_kmh
from app.osm_model import Member, Relation
from app.validation import validate


def _issues(doc, title):
    return [i for i in validate(doc)["issues"] if i["title"] == title]


def test_main_road_without_maxspeed_is_a_warning_with_the_urban_limit(doc):
    issue = next(i for i in _issues(doc, "Нет ограничения скорости") if i["refs"][0]["id"] == 100)
    assert issue["severity"] == "warning" and set(issue["levels"]) == {"matsim", "sumo"}
    assert issue["fixes"][0]["args"]["tags"] == {"maxspeed": "60"} and "ПДД" in issue["fixes"][0]["label"]
    # residential roads get a quieter note with both ПДД options; service roads are left alone
    res = next(i for i in _issues(doc, "Нет ограничения скорости") if i["refs"][0]["id"] == 101)
    assert res["severity"] == "info" and [f["args"]["tags"]["maxspeed"] for f in res["fixes"]] == ["60", "20"]
    assert not any(i["refs"][0]["id"] == 103 for i in _issues(doc, "Нет ограничения скорости"))
    apply_op(doc, History(), "set_tags", {"kind": "way", "id": 100, "tags": {"maxspeed": "60"}})
    assert not any(i["refs"][0]["id"] == 100 for i in _issues(doc, "Нет ограничения скорости"))


def test_ru_urban_is_replaced_by_a_number_matsim_can_read(doc):
    apply_op(doc, History(), "set_tags", {"kind": "way", "id": 100, "tags": {"maxspeed": "RU:urban"}})
    issue = _issues(doc, "Скорость задана сокращением")[0]
    assert issue["levels"] == ["matsim"] and issue["fixes"][0]["args"]["tags"] == {"maxspeed": "60"}
    assert _speed({"highway": "primary", "maxspeed": "RU:urban"}) == 60
    assert _speed({"highway": "residential"}) == 60 and _speed({"highway": "living_street"}) == 20
    assert speed_kmh("30 mph") == 48 and speed_kmh("RU:living_street") == 20 and speed_kmh("none") is None


def test_restriction_outside_the_extract_offers_removal(doc):
    doc.relations[600] = Relation(600, [Member("way", 100, "from"), Member("node", 4, "via"),
                                        Member("way", 999, "to")], {"type": "restriction", "restriction": "no_left_turn"})
    issue = _issues(doc, "Запрет поворота не применится")[0]
    assert "999" in issue["description"] and issue["fixes"][-1]["op"] == "delete"
    apply_op(doc, History(), issue["fixes"][-1]["op"], issue["fixes"][-1]["args"])
    assert 600 not in doc.relations and not _issues(doc, "Запрет поворота не применится")


def test_restriction_with_a_wrong_via_can_be_pointed_at_the_shared_node(doc):
    # ways 100 (1-2-3-4) and 104 (4-12) meet at node 4, but the relation says node 2
    doc.relations[601] = Relation(601, [Member("way", 100, "from"), Member("node", 2, "via"),
                                        Member("way", 104, "to")], {"type": "restriction", "restriction": "no_u_turn"})
    issue = _issues(doc, "Запрет поворота не применится")[0]
    fix = issue["fixes"][0]
    assert fix["op"] == "set_restriction_via" and fix["args"] == {"relation_id": 601, "node_id": 4}
    apply_op(doc, History(), fix["op"], fix["args"])
    assert [m.ref for m in doc.relations[601].members if m.role == "via"] == [4]
    assert not _issues(doc, "Запрет поворота не применится")


def test_reviewed_sample_repairs_preview_and_log():
    client = TestClient(main.app)
    state = client.post("/api/samples/example.osm/open").raise_for_status().json()
    doc_id = state["doc_id"]
    base = f"/api/documents/{doc_id}"

    def validate_sample():
        return client.get(f"{base}/validate").raise_for_status().json()

    initial = validate_sample()
    restriction = next(i for i in initial["issues"] if i["refs"][0] == {"kind": "relation", "id": 6452614})
    repair, fallback = restriction["fixes"]
    assert repair["op"] == "replace_restriction_way" and fallback["op"] == "delete"
    before = client.get(f"{base}/export").raise_for_status().content
    preview = client.post(f"{base}/fix-preview", json=repair).raise_for_status().json()
    assert preview["counts"] == {"added": 0, "deleted": 0, "modified": 1}
    assert preview["features"][0]["properties"]["id"] == repair["args"]["way_id"]
    assert client.get(f"{base}/export").raise_for_status().content == before
    assert validate_sample() == initial
    assert restriction["description"] not in client.get(f"{base}/edit-log").text

    applied = client.post(f"{base}/ops", json={**repair, "reason": restriction["description"]}).raise_for_status().json()
    assert applied["history"]["details"][0]["reason"] == restriction["description"]
    assert not any(i["title"] == restriction["title"] and i["refs"][0] == restriction["refs"][0]
                   for i in validate_sample()["issues"])

    speed = next(i for i in initial["issues"] if i["title"] == "Нет ограничения скорости"
                 and i["refs"][0]["id"] == 35071296)
    client.post(f"{base}/ops", json={**speed["fixes"][0], "reason": speed["description"]}).raise_for_status()
    main.SESSIONS.pop(doc_id)
    restored = client.get(f"{base}").raise_for_status().json()
    assert restored["history"]["details"][0]["reason"] == speed["description"]
    log = client.get(f"{base}/edit-log").raise_for_status().text
    assert restriction["description"] in log
    assert speed["description"] in log and "ПДД РФ, п. 10.2" in log


def test_grade_limits_follow_sp42_and_smooth_z_meets_them_for_two_levels(doc):
    assert max_grade({"highway": "secondary"}) == 0.07 and max_grade({"highway": "primary_link"}) == 0.06
    h = History()
    apply_op(doc, h, "set_tags", {"kind": "way", "id": 104, "tags": {"layer": "2"}})
    res = apply_op(doc, h, "smooth_z", {"way_id": 104})
    assert res["height"] == 10.0
    titles = {i["title"] for i in validate(doc)["issues"]}
    assert "Резкий перепад высоты" not in titles and "Резкий Z-переход" not in titles
