import csv
import io
import json
import math

import pytest
from fastapi import HTTPException

from app.main import import_webots, simulation_report
from app.osm_model import OsmDocument
from app.roads import is_drivable
from test_simulation import _document, _file


def _road(doc: OsmDocument):
    """The longest drivable way of the fixture, as (way, [(lon, lat), ...])."""
    ways = [w for w in doc.ways.values() if is_drivable(w.tags) and len(w.nodes) >= 2]
    w = max(ways, key=lambda w: sum(math.dist((doc.nodes[a].lon, doc.nodes[a].lat), (doc.nodes[b].lon, doc.nodes[b].lat))
                                    for a, b in zip(w.nodes, w.nodes[1:])))
    return w, [(doc.nodes[r].lon, doc.nodes[r].lat) for r in w.nodes]


def _along(pts, share):
    """Point at `share` of the first segment and the heading along it."""
    (x0, y0), (x1, y1) = pts[0], pts[1]
    heading = math.degrees(math.atan2((x1 - x0) * math.cos(math.radians(y0)), y1 - y0)) % 360
    return x0 + (x1 - x0) * share, y0 + (y1 - y0) * share, heading


def _recording(doc):
    """car a drives along the road and leaves early; car b stands still for the whole window."""
    w, pts = _road(doc)
    lines = ["time,id,lon,lat,angle"]
    for t in range(0, 21):
        if t <= 10:
            lon, lat, h = _along(pts, t / 10)
            lines.append(f"{t},a,{lon},{lat},{h}")
        lon, lat, h = _along(pts, 0.5)
        lines.append(f"{t},b,{lon},{lat},{h}")
    return w, "\n".join(lines).encode()


def test_simulation_report_metrics_and_exports(raw):
    doc_id = _document(raw)
    way, data = _recording(OsmDocument.parse(raw))
    with pytest.raises(HTTPException) as error:
        simulation_report(doc_id, "json")
    assert error.value.status_code == 404

    result = import_webots(doc_id, _file("cars.csv", data))
    summary = result["report"]["summary"]
    assert summary["vehicles"] == 2
    assert summary["completed"] == 1           # a left before the end, b is still there
    assert summary["avg_trip_s"] == 10
    assert summary["avg_stopped_s"] >= 9       # b stood the whole time
    assert summary["co2_kg"] > 0
    assert summary["matched_share"] == 1

    rep = json.loads(simulation_report(doc_id, "json").body)
    road = next(r for r in rep["roads"] if r["id"] == way.id)
    assert road["vehicles"] == 2
    assert rep["summary"]["duration_s"] == 21     # 21 one-second frames
    assert road["intensity"] == round(2 * 3600 / 21)
    assert road["load"] == pytest.approx(road["intensity"] / road["capacity"], abs=0.002)
    assert road["los"] in "ABCDEF"
    assert road["max_queue"] == 1
    assert {t["id"]: t["completed"] for t in rep["trips"]} == {"a": "да", "b": "нет"}

    table = simulation_report(doc_id, "csv").body.decode("utf-8-sig")
    assert "Интенсивность N, авт/ч" in table and "Коэффициент загрузки Z" in table
    assert next(csv.reader(io.StringIO(table), delimiter=";"))[0] == "Отчёт о симуляции движения"
    assert simulation_report(doc_id, "xlsx").body[:2] == b"PK"
    assert simulation_report(doc_id, "docx").body[:2] == b"PK"
    assert simulation_report(doc_id, "pdf").body[:4] == b"%PDF"
