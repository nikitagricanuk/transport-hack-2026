"""Russian road norms the checks rely on, in one place with their sources.

* Speeds — ПДД РФ, п. 10.2: in a settlement at most 60 km/h, in residential
  zones and courtyards at most 20 km/h. ``RU:urban`` / ``RU:living_street``
  are the OSM shorthands for exactly these limits; MatSim's OsmNetworkReader
  reads only numbers, so the shorthands are turned into numbers.
* Grades — СП 42.13330.2016 «Градостроительство. Планировка и застройка
  городских и сельских поселений», section 11, table of design parameters of
  streets and roads: the largest longitudinal grade, ‰. Mapping of OSM classes
  to the street categories of the СП is ours (motorway/trunk — магистральные
  дороги, primary — магистральные улицы общегородского значения, secondary —
  районного значения, the rest — улицы и проезды местного значения).
  Assumption, not verified: whether Irkutsk's climate subregion reduces these
  limits further; we use the table values as they are.
"""

from __future__ import annotations

from .roads import parse_int

URBAN_KMH = 60          # ПДД 10.2: населённый пункт
LIVING_KMH = 20         # ПДД 10.2: жилые зоны и дворовые территории
SPEED_SHORTHAND = {     # OSM implicit limits for Russia, as numbers
    "RU:urban": URBAN_KMH, "RU:living_street": LIVING_KMH, "RU:rural": 90, "RU:motorway": 110,
}
SPEED_SOURCE = "ПДД РФ, п. 10.2"

# Largest longitudinal grade, ‰ → as a fraction (СП 42.13330.2016, section 11).
MAX_GRADE_BY_CLASS = {
    "motorway": 0.050, "trunk": 0.050, "primary": 0.060, "secondary": 0.070,
    "tertiary": 0.080, "unclassified": 0.080, "residential": 0.080, "living_street": 0.080, "service": 0.080,
}
MAX_GRADE_DEFAULT = 0.080
GRADE_SOURCE = "СП 42.13330.2016, разд. 11"


def max_grade(tags: dict[str, str]) -> float:
    """Allowed longitudinal grade for this road class (links take their parent class)."""
    base = tags.get("highway", "").removesuffix("_link")
    return MAX_GRADE_BY_CLASS.get(base, MAX_GRADE_DEFAULT)


def speed_kmh(value: str | None) -> int | None:
    """maxspeed as km/h: numbers, «50 mph», and the RU:* shorthands; None if unknown."""
    if not value:
        return None
    value = value.strip()
    if value in SPEED_SHORTHAND:
        return SPEED_SHORTHAND[value]
    parts = value.split()
    n = parse_int(parts[0])
    if n is None or n <= 0:
        return None
    if len(parts) > 1 and parts[1].lower() == "mph":
        return round(n * 1.609)
    return n


def default_limit(highway: str) -> int:
    """Limit that applies without a sign: 20 in courtyards and residential zones, 60 elsewhere in town."""
    base = highway.removesuffix("_link")
    return LIVING_KMH if base in ("living_street", "service") else URBAN_KMH

# Scenario correspondence, not a normative conversion from OSM to engineering class.
# Classification vocabulary: ГОСТ Р 52398-2005; urban streets: СП 42.13330.2016, table 11.1.
OSM_CATEGORY = {'motorway':'IА','trunk':'IБ','primary':'II','secondary':'III','tertiary':'IV',
                'unclassified':'V','residential':'V','living_street':'V','service':'V','road':'V',
                'footway':'V','path':'V','pedestrian':'V','steps':'V','cycleway':'V','track':'V'}
ROAD_CATEGORIES = ('IА','IБ','IВ','II','III','IV','V')


def road_category(tags):
    override = tags.get('uds:category')
    return override if override in ROAD_CATEGORIES else OSM_CATEGORY.get(tags.get('highway','').removesuffix('_link'),'V')
