from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters import compare_adapters
from app.main import app

EXAMPLE = Path(__file__).resolve().parents[2] / "data" / "example.osm"


def test_lermontova_adapter_defaults_and_reviewed_speed_fix():
    client = TestClient(app)
    document = client.post("/api/documents", files={"file": (EXAMPLE.name, EXAMPLE.read_bytes(), "application/xml")})
    document.raise_for_status()
    base = f"/api/documents/{document.json()['doc_id']}"
    issues = client.get(f"{base}/validate").json()["issues"]
    for issue in issues:
        for fix in issue["fixes"]:
            args = fix["args"]
            if args.get("tags") == {"maxspeed": "60"} and args.get("kind") == "way":
                endpoint = f"{base}/elements/way/{args['id']}"
                detail = client.get(endpoint).json()
                if detail["tags"].get("name") == "улица Лермонтова" and "maxspeed" not in detail["tags"]:
                    break
        else:
            continue
        break
    else:
        raise AssertionError("Expected a speed fix for Lermontova")

    before = detail["adapters"]
    assert before["matsim"]["speed_kmh"] == 30
    assert before["sumo"]["speed_kmh"] == 100
    assert before["differs"]
    client.post(f"{base}/fix-preview", json=fix).raise_for_status()
    assert client.get(endpoint).json()["adapters"] == before
    client.post(f"{base}/ops", json=fix).raise_for_status()
    after = client.get(endpoint).json()["adapters"]
    assert after["matsim"]["speed_kmh"] == after["sumo"]["speed_kmh"] == 60


def test_service_road_is_missing_from_matsim_default_table():
    result = compare_adapters({"highway": "service"})
    assert result["matsim"] is None
    assert result["sumo"] == {"speed_kmh": 20, "lanes": [1, 1], "capacity": None}
    assert result["differs"]


def test_odd_total_lanes_and_capacity_are_directional():
    result = compare_adapters({"highway": "secondary", "lanes": "3", "maxspeed": "60"})
    assert result["matsim"]["lanes"] == [1.5, 1.5]
    assert result["matsim"]["capacity"] == [1500, 1500]
    assert result["sumo"]["lanes"] == [2, 1]
    assert result["differs"]


def test_russian_speed_shorthand_and_single_bidirectional_lane():
    result = compare_adapters({"highway": "secondary", "lanes": "1", "maxspeed": "RU:urban"})
    assert result["matsim"]["speed_kmh"] == 30
    assert result["sumo"]["speed_kmh"] == 60
    assert result["sumo"]["lanes"] == [1, 1]


def test_isolated_service_road_only_flags_sumo():
    from app.osm_model import OsmDocument
    from app.validation import validate

    doc = OsmDocument.parse(EXAMPLE.read_bytes())
    issues = validate(doc)["issues"]
    issue = next(issue for issue in issues if issue["refs"] == [{"kind": "way", "id": 714482565}]
                 and issue["category"] == "connectivity")
    assert issue["levels"] == ["sumo"]
