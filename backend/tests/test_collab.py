"""Live collaboration: presence relay and edit broadcast."""

from fastapi.testclient import TestClient

from app.main import app


def _receive(ws, kind):
    while True:
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg


def test_edits_and_cursors_reach_other_editors(raw):
    c = TestClient(app)
    doc_id = c.post("/api/documents", files={"file": ("t.osm", raw, "application/xml")}).json()["doc_id"]
    with c.websocket_connect(f"/api/documents/{doc_id}/ws?client=alice&name=Аня") as a:
        hello_a = a.receive_json()
        assert hello_a["type"] == "hello" and hello_a["peers"] == [] and hello_a["rev"] == 0
        with c.websocket_connect(f"/api/documents/{doc_id}/ws?client=bob&name=Миша") as b:
            hello_b = b.receive_json()
            assert [p["name"] for p in hello_b["peers"]] == ["Аня"]
            assert _receive(a, "join")["peer"]["name"] == "Миша"

            # Alice moves her cursor and selects a road: Bob sees it
            a.send_json({"type": "presence", "cursor": [104.281, 52.28], "selection": {"kind": "way", "id": 100}})
            seen = _receive(b, "presence")["peer"]
            assert seen["id"] == "alice" and seen["cursor"] == [104.281, 52.28] and seen["selection"]["id"] == 100

            # Alice edits over HTTP: Bob gets the patch with the next revision, Alice does not
            r = c.post(f"/api/documents/{doc_id}/ops", headers={"X-Client-Id": "alice"},
                       json={"op": "set_tags", "args": {"kind": "way", "id": 100, "tags": {"maxspeed": "40"}}})
            assert r.json()["rev"] == 1
            patch = _receive(b, "patch")
            assert patch["rev"] == 1 and patch["by"] == "alice" and patch["patch"]["ways"] == [100]
            assert patch["patch"]["features"]["ways"][0]["properties"]["tags"]["maxspeed"] == "40"

            # undo by Bob reaches Alice as revision 2
            c.post(f"/api/documents/{doc_id}/undo", headers={"X-Client-Id": "bob"})
            assert _receive(a, "patch")["rev"] == 2

            # a rejected edit does not bump the revision
            c.post(f"/api/documents/{doc_id}/ops", headers={"X-Client-Id": "bob"},
                   json={"op": "split_way", "args": {"way_id": 100, "node_id": 1}})
            assert c.get(f"/api/documents/{doc_id}").json()["rev"] == 2

            c.post(f"/api/documents/{doc_id}/load", headers={"X-Client-Id": "bob"}, json={"multiplier": 1.5})
            assert _receive(a, "load")["type"] == "load"
            assert {p["name"] for p in c.get(f"/api/documents/{doc_id}/peers").json()["peers"]} == {"Аня", "Миша"}
        assert _receive(a, "leave")["id"] == "bob"


def test_unknown_document_closes_the_socket():
    c = TestClient(app)
    try:
        with c.websocket_connect("/api/documents/nope/ws") as ws:
            ws.receive_json()
        raise AssertionError("socket should have been closed")
    except Exception as e:  # starlette raises WebSocketDisconnect with code 4404
        assert getattr(e, "code", None) == 4404
