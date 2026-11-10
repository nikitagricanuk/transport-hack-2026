"""Readable SUMO and organiser check results."""

from app.check_report import interpret_netconvert, interpret_organizer, organizer_error_text


def test_netconvert_warnings_are_grouped_in_russian_with_osm_refs():
    warnings = [
        "Warning: Environment variable SUMO_HOME is not set, using built in type maps.",
        "Warning: Lane '142025799_0' is not connected from any incoming edge at junction '1554362590'.",
        "Warning: Found sharp turn with radius 8.98 at the end of edge '-227484856'.",
        "Warning: Found sharp turn with radius 6.27 at the start of edge '-1482925131#2'.",
        "Warning: 25 total messages of type: Found sharp turn with radius % at the % of edge '%'.",
        "Warning: Something nobody has seen before.",
    ]
    r = interpret_netconvert(["Error: No nodes loaded."], warnings)
    groups = {g["key"]: g for g in r["groups"]}
    assert r["groups"][0]["key"] == "error" and r["groups"][0]["severity"] == "critical"
    lane = groups["lane_unreachable"]
    assert lane["severity"] == "warning" and lane["title"] == "Полоса без въезда на перекрёстке"
    assert {"kind": "way", "id": 142025799} in lane["refs"] and {"kind": "node", "id": 1554362590} in lane["refs"]
    sharp = groups["sharp_turn"]
    assert sharp["count"] == 25 and {"kind": "way", "id": 1482925131} in sharp["refs"]
    assert groups["other"]["count"] == 1
    assert not any("SUMO_HOME" in e for g in r["groups"] for e in g["examples"])
    assert r["counts"] == {"critical": 1, "warning": 1, "info": 26}


def test_organizer_reply_becomes_labelled_fields():
    fields = interpret_organizer('{"run_id": "20261110-094928", "status": "queued", "ok": true}')
    assert {"label": "Номер прогона", "value": "20261110-094928"} in fields
    assert {"label": "Статус", "value": "в очереди"} in fields
    assert {"label": "ok", "value": "да"} in fields
    assert interpret_organizer("Файл принят") == [{"label": "Сообщение", "value": "Файл принят"}]
    text = organizer_error_text(401, '{"detail": "invalid token"}')
    assert "токен" in text and "invalid token" in text and "{" not in text
