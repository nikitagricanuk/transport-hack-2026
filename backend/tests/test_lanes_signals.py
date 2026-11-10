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


def test_split_into_two_carriageways_keeps_junctions_and_moves_each_direction_to_its_centre(doc):
    from app.geo import dist
    from app.edits import _proj

    doc.ways[100].tags.update({"lanes": "4", "turn:lanes:forward": "left|through", "maxspeed:backward": "40"})
    res = apply_op(doc, History(), "split_carriageways", {"way_id": 100})
    fwd, back = doc.ways[100], doc.ways[res["new_id"]]
    # shared: both ends and node 2, where way 101 joins; node 3 became one point per carriageway
    assert fwd.nodes[0] == 1 and fwd.nodes[1] == 2 and fwd.nodes[-1] == 4
    assert back.nodes[0] == 4 and back.nodes[2:] == [2, 1] and back.nodes[1] < 0 and 3 not in doc.nodes
    assert fwd.tags["oneway"] == back.tags["oneway"] == "yes"
    assert fwd.tags["lanes"] == "2" and back.tags["lanes"] == "2"
    assert fwd.tags["turn:lanes"] == "left|through" and "turn:lanes:forward" not in fwd.tags
    assert back.tags["maxspeed"] == "40" and "maxspeed:backward" not in back.tags
    # each carriageway is half its lanes (2 × 3.5 m / 2) plus half the median away from the old axis
    proj = _proj(doc)
    xy = lambda n: proj.to_xy(doc.nodes[n].lat, doc.nodes[n].lon)  # noqa: E731
    gap = dist(xy(fwd.nodes[2]), xy(back.nodes[1]))
    assert abs(gap - (2 * 3.5 / 2 + 0.5) * 2) < 0.2
    assert res["offset_m"] == [4.0, 4.0]
    # the route that used the road now runs on both carriageways
    assert {m.ref for m in doc.relations[500].members if m.type == "way"} >= {100, res["new_id"]}


def test_split_carriageways_refuses_one_way_and_restricted_roads(doc):
    import pytest

    from app.edits import OpError
    from app.osm_model import Member, Relation

    apply_op(doc, History(), "set_tags", {"kind": "way", "id": 101, "tags": {"oneway": "yes"}})
    with pytest.raises(OpError):
        apply_op(doc, History(), "split_carriageways", {"way_id": 101})
    # a restriction through the middle of the road (node 2) cannot be carried over
    doc.relations[701] = Relation(701, [Member("way", 100, "from"), Member("node", 2, "via"), Member("way", 101, "to")],
                                  {"type": "restriction", "restriction": "no_left_turn"})
    with pytest.raises(OpError):
        apply_op(doc, History(), "split_carriageways", {"way_id": 100})


def test_split_carriageways_carries_end_restrictions_to_the_right_carriageway(doc):
    from app.osm_model import Member, Relation

    # from way 100 at its last node 4 onto 104, and a U-turn on 100 at its first node 1
    doc.relations[700] = Relation(700, [Member("way", 100, "from"), Member("node", 4, "via"), Member("way", 104, "to")],
                                  {"type": "restriction", "restriction": "no_right_turn"})
    doc.relations[702] = Relation(702, [Member("way", 100, "from"), Member("node", 1, "via"), Member("way", 100, "to")],
                                  {"type": "restriction", "restriction": "no_u_turn"})
    res = apply_op(doc, History(), "split_carriageways", {"way_id": 100})
    back = res["new_id"]
    roles = lambda rid: {m.role: m.ref for m in doc.relations[rid].members if m.type == "way"}  # noqa: E731
    assert roles(700) == {"from": 100, "to": 104}            # forward carriageway arrives at node 4
    assert roles(702) == {"from": back, "to": 100}           # backward arrives at node 1, forward leaves it
    assert not [i for i in validate(doc)["issues"] if i["title"] == "Запрет поворота не применится"]
