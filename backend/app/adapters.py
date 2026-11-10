"""Per-way estimates from upstream OSM readers with their default settings."""

from math import isfinite

from .norms import SPEED_SHORTHAND
from .roads import direction, parse_int

MATSIM_SOURCE = "https://matsim.org/doxygen/_osm_network_reader_8java_source.html#l00159"
SUMO_SOURCE = "https://github.com/eclipse-sumo/sumo/blob/v1_21_0/data/typemap/osmNetconvert.typ.xml"

# lanes/direction, km/h, vehicles/hour/lane (useVspAdjustments=false)
MATSIM_DEFAULTS = {
    "motorway": (2, 120, 2000), "motorway_link": (1, 80, 1500),
    "trunk": (1, 80, 2000), "trunk_link": (1, 50, 1500),
    "primary": (1, 80, 1500), "primary_link": (1, 60, 1500),
    "secondary": (1, 30, 1000), "secondary_link": (1, 30, 1000),
    "tertiary": (1, 25, 600), "tertiary_link": (1, 25, 600),
    "unclassified": (1, 15, 600), "residential": (1, 15, 600), "living_street": (1, 10, 300),
}

# numLanes, speed (m/s); the SUMO typemap does not define capacity.
SUMO_DEFAULTS = {
    "motorway": (2, 39.44), "motorway_link": (1, 22.22),
    "trunk": (2, 27.78), "trunk_link": (1, 22.22),
    "primary": (2, 27.78), "primary_link": (1, 22.22),
    "secondary": (1, 27.78), "secondary_link": (1, 22.22),
    "tertiary": (1, 22.22), "tertiary_link": (1, 22.22),
    "unclassified": (1, 13.89), "residential": (1, 13.89), "living_street": (1, 2.78),
    "service": (1, 5.56), "track": (1, 5.56), "unsurfaced": (1, 8.33),
    "raceway": (2, 83.33), "bus_guideway": (1, 27.78), "ford": (1, 2.78),
}


def _number(value: str | None) -> float | None:
    try:
        n = float(value or "")
    except ValueError:
        return None
    return n if isfinite(n) and n > 0 else None


def _lanes(tags: dict[str, str], default: int, *, matsim: bool) -> tuple[float, float]:
    parse = _number if matsim else parse_int
    total = parse(tags.get("lanes")) or 0
    forward = parse(tags.get("lanes:forward")) or 0
    backward = parse(tags.get("lanes:backward")) or 0
    if not all(0 <= count <= 10_000 for count in (total, forward, backward)):
        total = forward = backward = 0
    travel = direction(tags)
    if tags.get("highway") == "motorway_link" and "oneway" not in tags:
        travel = 1
    if travel > 0:
        return forward or total or default, 0
    if travel < 0:
        return 0, backward or total or default
    if forward and backward:
        return forward, backward
    if total > 0:
        if not forward:
            forward = total - backward if backward else (total / 2 if matsim else (total + 1) // 2)
        backward = total - forward
        minimum = 0 if matsim else 1
        return max(minimum, forward), max(minimum, backward)
    return forward or default, backward or default


def compare_adapters(tags: dict[str, str]) -> dict:
    highway = tags.get("highway", "")
    speed = _number(tags.get("maxspeed"))
    matsim = sumo = None
    if highway in MATSIM_DEFAULTS:
        default_lanes, default_speed, capacity = MATSIM_DEFAULTS[highway]
        forward, backward = _lanes(tags, default_lanes, matsim=True)
        matsim = {"speed_kmh": speed or default_speed, "lanes": [forward, backward],
                  "capacity": [forward * capacity, backward * capacity]}
    if highway in SUMO_DEFAULTS:
        default_lanes, default_speed = SUMO_DEFAULTS[highway]
        forward, backward = _lanes(tags, default_lanes, matsim=False)
        maxspeed = tags.get("maxspeed", "").strip()
        mph = _number(maxspeed.removesuffix("mph")) if maxspeed.endswith("mph") else None
        symbolic = SPEED_SHORTHAND.get(maxspeed) or {"none": 142, "no": 142, "walk": 5}.get(maxspeed)
        sumo_speed = speed or symbolic or (mph * 1.609344 if mph else default_speed * 3.6)
        sumo = {"speed_kmh": round(sumo_speed, 1),
                "lanes": [forward, backward], "capacity": None}
    differs = (matsim is None) != (sumo is None) or bool(
        matsim and sumo and (matsim["speed_kmh"] != sumo["speed_kmh"] or matsim["lanes"] != sumo["lanes"])
    )
    return {"matsim": matsim, "sumo": sumo, "differs": differs,
            "sources": {"matsim": MATSIM_SOURCE, "sumo": SUMO_SOURCE}}
