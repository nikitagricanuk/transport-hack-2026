"""Road-domain knowledge: which ways carry cars, directions, lanes, turns."""

from __future__ import annotations

DRIVABLE = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "living_street", "service", "road",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}

ONEWAY_FORWARD = {"yes", "true", "1"}
ONEWAY_BACKWARD = {"-1", "reverse"}
ONEWAY_VALID = ONEWAY_FORWARD | ONEWAY_BACKWARD | {"no", "false", "0", "reversible", "alternating"}


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


def lane_tags(forward: int, backward: int, oneway: str, auto_turns: bool = True) -> dict[str, str | None]:
    """Tag patch that sets lanes consistently. None values delete tags.

    oneway: "no" | "yes" | "-1"
    """
    patch: dict[str, str | None] = {
        "lanes": None, "lanes:forward": None, "lanes:backward": None,
        "turn:lanes": None, "turn:lanes:forward": None, "turn:lanes:backward": None,
    }
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


def reversed_tags(tags: dict[str, str]) -> dict[str, str]:
    """Tags of a way after its node order is reversed."""
    out: dict[str, str] = {}
    for k, v in tags.items():
        nk = k
        if ":forward" in k:
            nk = k.replace(":forward", ":backward")
        elif ":backward" in k:
            nk = k.replace(":backward", ":forward")
        out[nk] = v
    ow = out.get("oneway")
    if ow in ONEWAY_FORWARD:
        out["oneway"] = "-1"
    elif ow in ONEWAY_BACKWARD:
        out["oneway"] = "yes"
    return out
