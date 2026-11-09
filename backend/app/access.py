"""Boolean access flags for a way, stored as OSM access tags.

A checkbox is true when the mode may use the way. The specific tag wins
(``foot=designated`` stays allowed and is not rewritten until the user edits
it). Otherwise ``access=*`` applies, and otherwise the usual default for that
``highway`` class. Editing a checkbox writes ``yes`` or ``no`` on that tag.
"""

from __future__ import annotations

PERMISSIONS: tuple[str, ...] = (
    "foot",
    "bicycle",
    "horse",
    "motorcycle",
    "hgv",
    "psv",
    "moped",
    "wheelchair",
)

# Values that mean the mode may be here, including conditional access.
_ALLOWED = frozenset({
    "yes", "designated", "permissive", "official", "public",
    "destination", "customers", "delivery", "agricultural", "forestry",
    "discouraged", "permit", "residents",
})
# Values that keep the mode off this way. ``use_sidepath`` / ``separate``
# mean the traffic belongs on a parallel way, not on this one.
_DENIED = frozenset({"no", "private", "use_sidepath", "separate", "dismount"})

_OPEN: dict[str, bool] = {key: True for key in PERMISSIONS}


def _profile(**overrides: bool) -> dict[str, bool]:
    out = dict(_OPEN)
    out.update(overrides)
    return out


# International OSM defaults, with ``*_link`` using the same class as the road
# it joins. Unknown highway values stay open so a normal street is not shown
# as closed; a way with no highway is closed until a tag says otherwise.
_BY_HIGHWAY: dict[str, dict[str, bool]] = {
    "motorway": _profile(foot=False, bicycle=False, horse=False, moped=False, wheelchair=False),
    "trunk": _profile(foot=False, bicycle=False, horse=False, moped=False, wheelchair=False),
    "primary": _profile(),
    "secondary": _profile(),
    "tertiary": _profile(),
    "unclassified": _profile(),
    "residential": _profile(),
    "living_street": _profile(),
    "service": _profile(),
    "road": _profile(),
    "pedestrian": _profile(bicycle=False, horse=False, motorcycle=False, hgv=False, psv=False, moped=False),
    "footway": _profile(bicycle=False, horse=False, motorcycle=False, hgv=False, psv=False, moped=False),
    "steps": _profile(
        bicycle=False, horse=False, motorcycle=False, hgv=False, psv=False, moped=False, wheelchair=False,
    ),
    "cycleway": _profile(horse=False, motorcycle=False, hgv=False, psv=False, moped=False),
    "bridleway": _profile(bicycle=False, motorcycle=False, hgv=False, psv=False, moped=False, wheelchair=False),
    "path": _profile(motorcycle=False, hgv=False, psv=False, moped=False, wheelchair=False),
    "track": _profile(psv=False, wheelchair=False),
}


def _flag(value: str | None) -> bool | None:
    if value is None:
        return None
    token = value.strip().lower()
    if token in _ALLOWED:
        return True
    if token in _DENIED:
        return False
    return None


def _defaults(highway: str) -> dict[str, bool]:
    base = highway.removesuffix("_link")
    found = _BY_HIGHWAY.get(base)
    if found is not None:
        return found
    if not highway:
        return {key: False for key in PERMISSIONS}
    return _OPEN


def permission_allowed(tags: dict[str, str], key: str) -> bool:
    """Whether ``key`` (an OSM access tag) is allowed on this way."""
    specific = _flag(tags.get(key))
    if specific is not None:
        return specific
    general = _flag(tags.get("access"))
    if general is not None:
        return general
    return _defaults(tags.get("highway", "")).get(key, False)


def road_permissions(tags: dict[str, str]) -> dict[str, bool]:
    return {key: permission_allowed(tags, key) for key in PERMISSIONS}
