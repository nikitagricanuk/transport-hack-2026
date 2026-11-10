"""Lane completeness, the two-way reverse bug, signal phases that add up, fringe edges in the preview."""

from app import fix_preview, signals
from app.analysis import road_rows
from app.edits import History, apply_op
from app.roads import reversed_tags
from app.validation import validate


def _issues(doc, title):
    return [i for i in validate(doc)["issues"] if i["title"] == title]


def test_reversing_a_two_way_road_keeps_lanes_on_their_sides():
    tags = {"highway": "primary", "lanes": "3", "lanes:forward": "2", "lanes:backward": "1",
            "turn:lanes:forward": "left|through", "sidewalk:left": "yes"}
    once = reversed_tags(tags)
    assert once["lanes:forward"] == "1" and once["lanes:backward"] == "2"
    assert once["turn:lanes:backward"] == "left|through"   # same physical side, same arrows
    assert once["sidewalk:right"] == "yes"
    assert reversed_tags(once) == tags


def test_main_road_without_lanes_offers_counts_to_confirm(doc):
    apply_op(doc, History(), "set_tags", {"kind": "way", "id": 100, "tags": {"lanes": None}})
    issue = next(i for i in _issues(doc, "Не указано число полос") if i["refs"][0]["id"] == 100)
    assert issue["severity"] == "warning"
    assert issue["fixes"][0]["args"]["tags"] == {"lanes": "2", "lanes:forward": "1", "lanes:backward": "1"}
    assert not any(i["refs"][0]["id"] == 101 for i in _issues(doc, "Не указано число полос"))  # residential


def test_odd_lanes_on_a_two_way_road_ask_for_a_split(doc):
    apply_op(doc, History(), "set_tags", {"kind": "way", "id": 100, "tags": {"lanes": "3"}})
    issue = _issues(doc, "Нечётное число полос без разбивки")[0]
    tags = [f["args"]["tags"] for f in issue["fixes"]]
    assert tags[0] == {"lanes": "3", "lanes:forward": "2", "lanes:backward": "1"}
    assert tags[1] == {"lanes": "3", "lanes:forward": "1", "lanes:backward": "2"}
    apply_op(doc, History(), issue["fixes"][0]["op"], issue["fixes"][0]["args"])
    assert not _issues(doc, "Нечётное число полос без разбивки")


def test_signal_phases_add_up_to_the_cycle(doc):
    apply_op(doc, History(), "place_signal", {"node_id": 2})
    rows = {r["id"]: r for r in road_rows(doc, {}, 1.0)}
    for cycle, share in ((90, 0.5), (100, 0.63), (45, 0.2)):
        plan = signals.plan(doc, 2, {"cycle": cycle, "main_share": share}, rows)
        total = sum(p["green_s"] + p["yellow_s"] + p["all_red_s"] for p in plan["phases"])
        assert total == cycle and all(p["yellow_s"] == 3 for p in plan["phases"])
        tags = signals.timing_tags(doc, {2: {"cycle": cycle, "main_share": share}})[2]
        assert int(tags["uds:signal:green_main"]) + int(tags["uds:signal:green_cross"]) + 2 * (3 + 2) == cycle


def test_fringe_edge_is_marked_in_the_preview(doc):
    pts = [{"id": 6}, {"id": 8}]
    p = fix_preview.preview(doc, "add_way", {"points": pts, "tags": {"highway": "residential", "uds:fringe": "yes"}})
    assert any(f["properties"]["fringe"] for f in p["features"] if f["properties"]["status"] == "added")
