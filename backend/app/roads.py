"""Road-domain knowledge: which ways carry cars, directions, lanes, turns."""

from __future__ import annotations

import re

DRIVABLE = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "living_street", "service", "road",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}

ONEWAY_FORWARD = {"yes", "true", "1"}
ONEWAY_BACKWARD = {"-1", "reverse"}
ONEWAY_VALID = ONEWAY_FORWARD | ONEWAY_BACKWARD | {"no", "false", "0", "reversible", "alternating"}


# Pedestrian and cycle ways must not be tied into a controlled-access road.
_NONMOTOR = frozenset({
    "footway", "pedestrian", "path", "cycleway", "steps", "bridleway", "corridor",
})
# motorway_link is the only ramp that may touch a motorway; trunk_link is not.
_FAST = frozenset({"motorway", "motorway_link", "trunk", "trunk_link"})


def can_connect(a: dict[str, str], b: dict[str, str]) -> str | None:
    """``None`` when two highways may share a node, otherwise a Russian reason.

    A footway (and the other walking/cycling classes) must not merge into a
    motorway or trunk. A motorway itself only connects to another motorway or
    to ``motorway_link`` — a primary, a residential street or a driveway has to
    meet the motorway through a link road. Same class, and every ordinary
    urban pair (footway × residential, service × primary), is allowed.
    """
    ha = a.get("highway") or ""
    hb = b.get("highway") or ""
    if not ha or not hb or ha == hb:
        return None
    pair = {ha, hb}
    if (pair & _NONMOTOR) and (pair & _FAST):
        return "Пешеходная или велосипедная дорога не соединяется с магистралью"
    if "motorway" in pair and not pair <= {"motorway", "motorway_link"}:
        return "Автомагистраль соединяется только через съезд (motorway_link)"
    return None


def is_access_way(tags: dict[str, str]) -> bool:
    """Driveway or sidewalk: a local access link that may end at a building door.

    ``service=driveway`` and ``footway=sidewalk`` are the OSM tags for the spur
    that stops at an entrance. A generic footway or service road is not.
    """
    if tags.get("service") == "driveway":
        return True
    return tags.get("footway") == "sidewalk"


def is_drivable(tags: dict[str, str]) -> bool:
    if tags.get("highway") not in DRIVABLE:
        return False
    if tags.get("access") in {"no", "private"} and tags.get("motor_vehicle") not in {"yes", "designated"}:
        return False
    if tags.get("area") == "yes":
        return False
    return True


def direction(tags: dict[str, str]) -> int:
    """+1 forward only, -1 backward only, 0 both ways."""
    ow = tags.get("oneway")
    if ow in ONEWAY_FORWARD:
        return 1
    if ow in ONEWAY_BACKWARD:
        return -1
    if ow is None and (
        tags.get("junction") in {"roundabout", "circular"} or tags.get("highway") == "motorway"
    ):
        return 1
    return 0


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def default_turns(n: int) -> list[str]:
    """Reasonable default turn:lanes for n lanes (right-hand traffic)."""
    if n <= 0:
        return []
    if n == 1:
        return ["left;through;right"]
    out = []
    for i in range(n):
        if i == 0:
            out.append("left;through")
        elif i == n - 1:
            out.append("through;right")
        else:
            out.append("through")
    return out


def lane_tags(forward: int, backward: int, oneway: str, auto_turns: bool = False) -> dict[str, str | None]:
    """Tag patch that sets lanes consistently. None values delete tags.

    oneway: "no" | "yes" | "-1"

    Turn lanes are left untouched unless ``auto_turns`` is set. Inventing
    ``turn:lanes`` marks lanes that were not in the source and SUMO then
    treats those invented maneuvers as real.
    """
    patch: dict[str, str | None] = {
        "lanes": None, "lanes:forward": None, "lanes:backward": None,
    }
    if auto_turns:
        patch.update({
            "turn:lanes": None, "turn:lanes:forward": None, "turn:lanes:backward": None,
        })
    forward = max(1, forward)
    if oneway in ("yes", "-1"):
        patch["oneway"] = oneway
        patch["lanes"] = str(forward)
        if auto_turns:
            patch["turn:lanes"] = "|".join(default_turns(forward))
    else:
        backward = max(1, backward)
        patch["oneway"] = None
        patch["lanes"] = str(forward + backward)
        patch["lanes:forward"] = str(forward)
        patch["lanes:backward"] = str(backward)
        if auto_turns:
            patch["turn:lanes:forward"] = "|".join(default_turns(forward))
            patch["turn:lanes:backward"] = "|".join(default_turns(backward))
    return patch


_TURN_SWAP = {
    "left": "right", "right": "left",
    "slight_left": "slight_right", "slight_right": "slight_left",
    "sharp_left": "sharp_right", "sharp_right": "sharp_left",
    "merge_to_left": "merge_to_right", "merge_to_right": "merge_to_left",
}
_SIDE_WORD = re.compile(r"(?<![A-Za-z0-9])(left|right)(?![A-Za-z0-9])")
_SIDE_VALUE_KEYS = ("sidewalk", "cycleway", "shoulder", "placement", "parking:lane", "busway")


def _swap_side_word(value: str) -> str:
    return _SIDE_WORD.sub(lambda m: "right" if m.group(1) == "left" else "left", value)


def _mirror_turns(value: str) -> str:
    """Lane order is left-to-right in the way direction; reversing the way flips it."""
    lanes = []
    for lane in reversed(value.split("|")):
        parts = [_TURN_SWAP.get(p.strip(), p.strip()) for p in lane.split(";")]
        lanes.append(";".join(parts))
    return "|".join(lanes)


def _flip_incline(value: str) -> str:
    v = value.strip()
    if v == "up":
        return "down"
    if v == "down":
        return "up"
    m = re.fullmatch(r"([+-]?)(\d+(?:\.\d+)?)(%?)", v)
    if not m:
        return value
    sign, num, pct = m.groups()
    return f"{'' if sign == '-' else '-'}{num}{pct}"


def _side_value_key(key: str) -> bool:
    return any(key == prefix or key.startswith(prefix + ":") for prefix in _SIDE_VALUE_KEYS)


def reversed_tags(tags: dict[str, str]) -> dict[str, str]:
    """Tags after the way's nodes are reversed, so traffic follows the new geometry.

    ``oneway`` is intentionally not flipped between ``yes`` and ``-1``. Reversing
    the nodes already turns the way around; flipping the tag as well cancels
    that and the vehicles keep going the same way on the ground. ``:forward`` /
    ``:backward`` keys stay put for the same reason: they describe the new way
    direction, which is the direction traffic now uses.

    Values that are relative to the way direction are rewritten so the road
    itself does not move: turn maneuvers are mirrored, ``left``/``right`` sides
    swap, and ``incline`` flips.
    """
    out: dict[str, str] = {}
    for key, value in tags.items():
        nk = _swap_side_word(key)
        nv = value
        if "turn" in nk and "lane" in nk:
            nv = _mirror_turns(value)
        elif "lanes" in nk and "|" in value:
            nv = "|".join(reversed(value.split("|")))
        elif nk == "incline" or nk.startswith("incline:"):
            nv = _flip_incline(value)
        elif _side_value_key(nk) and _SIDE_WORD.search(value):
            nv = _swap_side_word(value)
        out[nk] = nv
    return out


def traffic_node_order(nodes: list[int], tags: dict[str, str]) -> list[int] | None:
    """Node ids in the direction vehicles may travel, or None when both ways are open."""
    d = direction(tags)
    if d > 0:
        return list(nodes)
    if d < 0:
        return list(reversed(nodes))
    return None
