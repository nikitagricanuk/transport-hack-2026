from pathlib import Path

from fastapi.testclient import TestClient

from app.access import PERMISSIONS, permission_allowed, road_permissions
from app.edits import History, apply_op
from app.geojson import to_geojson
from app.main import app
from app.osm_model import OsmDocument

_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <node id="1" lat="52.28" lon="104.28"/>
  <node id="2" lat="52.281" lon="104.281"/>
  <way id="10">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="residential"/>
    <tag k="foot" v="no"/>
    <tag k="bicycle" v="designated"/>
    <tag k="horse" v="yes"/>
  </way>
</osm>
"""


def test_defaults_follow_the_highway_class():
    motorway = road_permissions({"highway": "motorway"})
    assert motorway["foot"] is False
    assert motorway["bicycle"] is False
    assert motorway["horse"] is False
    assert motorway["motorcycle"] is True
    assert motorway["hgv"] is True
    assert motorway["psv"] is True
    assert motorway["moped"] is False
    assert motorway["wheelchair"] is False
    assert permission_allowed({"highway": "motorway_link"}, "foot") is False
    assert permission_allowed({"highway": "motorway_link"}, "motorcycle") is True

    street = road_permissions({"highway": "residential"})
    assert all(street.values())

    footway = road_permissions({"highway": "footway"})
    assert footway["foot"] is True and footway["wheelchair"] is True
    assert footway["bicycle"] is False and footway["horse"] is False and footway["hgv"] is False

    assert road_permissions({}) == {key: False for key in PERMISSIONS}
    assert permission_allowed({"highway": "corridor"}, "foot") is True


def test_specific_tag_wins_over_access_and_default():
    tags = {"highway": "residential", "access": "no", "foot": "yes", "bicycle": "use_sidepath"}
    assert permission_allowed(tags, "foot") is True
    assert permission_allowed(tags, "bicycle") is False
    assert permission_allowed(tags, "horse") is False
    assert permission_allowed({"highway": "service", "access": "destination"}, "hgv") is True
    assert permission_allowed({"highway": "residential", "access": "private"}, "psv") is False
    # an unknown token is not guessed; the class default remains, and the tag is left alone
    assert permission_allowed({"highway": "residential", "foot": "sometimes"}, "foot") is True


def test_permissions_round_trip_through_valid_osm():
    doc = OsmDocument.parse(_XML)
    flags = road_permissions(doc.ways[10].tags)
    assert flags["foot"] is False and flags["bicycle"] is True and flags["horse"] is True
    assert flags["moped"] is True
    assert b'k="bicycle" v="designated"' in doc.to_xml()

    apply_op(doc, History(), "set_tags", {"kind": "way", "id": 10, "tags": {"horse": "no", "moped": "no"}})
    out = doc.to_xml()
    again = OsmDocument.parse(out)
    tags = again.ways[10].tags
    assert list(tags) == ["highway", "foot", "bicycle", "horse", "moped"]
    assert tags["horse"] == "no" and tags["moped"] == "no"
    assert tags["bicycle"] == "designated" and tags["foot"] == "no"
    restored = road_permissions(tags)
    assert restored["horse"] is False and restored["moped"] is False
    assert restored["bicycle"] is True and restored["foot"] is False
    assert b"<osm" in out and b"</osm>" in out


def test_geojson_and_export_keep_permission_tags():
    client = TestClient(app)
    opened = client.post("/api/documents", files={"file": ("t.osm", _XML, "application/xml")})
    assert opened.status_code == 200, opened.text
    body = opened.json()
    props = body["geo"]["ways"]["features"][0]["properties"]
    assert list(props["permissions"]) == list(PERMISSIONS)
    assert props["permissions"]["foot"] is False
    assert props["permissions"]["bicycle"] is True

    doc_id = body["doc_id"]
    edited = client.post(
        f"/api/documents/{doc_id}/ops",
        json={"op": "set_tags", "args": {"kind": "way", "id": 10, "tags": {"hgv": "no"}}, "label": "Грузовики"},
    )
    assert edited.status_code == 200, edited.text
    patched = edited.json()["patch"]["features"]["ways"][0]["properties"]["permissions"]
    assert patched["hgv"] is False and patched["bicycle"] is True

    exported = client.get(f"/api/documents/{doc_id}/export").content
    saved = OsmDocument.parse(exported)
    assert saved.ways[10].tags["hgv"] == "no"
    assert saved.ways[10].tags["bicycle"] == "designated"
    assert road_permissions(saved.ways[10].tags)["hgv"] is False

    # the same flags the map receives
    assert to_geojson(saved)["ways"]["features"][0]["properties"]["permissions"]["foot"] is False


def test_frontend_permission_keys_match_the_model():
    text = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "utils" / "osm.ts").read_text(encoding="utf-8")
    for key in PERMISSIONS:
        assert f"key: '{key}'" in text
