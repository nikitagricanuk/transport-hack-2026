"""OAuth and MCP integration, exercising the actual editor mutation pipeline."""
import base64
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.mcp_server import digest

RESOURCE = "http://testserver/mcp"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("UDS_OAUTH_DB", str(tmp_path / "oauth.sqlite3"))
    monkeypatch.delenv("UDS_PUBLIC_URL", raising=False)
    with TestClient(app) as c:
        yield c


def register(client, **kwargs):
    response = client.post("/oauth/register", json={"redirect_uris": [REDIRECT], "client_name": "Клод", **kwargs})
    assert response.status_code == 201, response.text
    return response.json()["client_id"]


def authorize(client, client_id, decision="allow", **kwargs):
    response = client.get("/oauth/authorize", params={"client_id": client_id, "redirect_uri": REDIRECT,
        "response_type": "code", "code_challenge": CHALLENGE, "code_challenge_method": "S256",
        "resource": RESOURCE, "state": "opaque-state", **kwargs})
    assert response.status_code == 200, response.text
    assert "form-action 'self' https://claude.ai" in response.headers["content-security-policy"]
    fields = {k: re.search(f'name="{k}" value="([^"]+)"', response.text)[1] for k in ("ticket", "csrf")}
    return client.post("/oauth/authorize", data={**fields, "decision": decision}, follow_redirects=False)


def exchange(client, client_id, code, **kwargs):
    return client.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": client_id,
        "code": code, "code_verifier": VERIFIER, "redirect_uri": REDIRECT, "resource": RESOURCE, **kwargs})


def credentials(client):
    client_id = register(client)
    response = authorize(client, client_id)
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["state"] == ["opaque-state"]
    code = query["code"][0]
    response = exchange(client, client_id, code)
    assert response.status_code == 200, response.text
    return client_id, response.json(), code


def rpc(client, tokens, method, params=None, **kwargs):
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"Authorization": "Bearer " + tokens["access_token"],
                 "Accept": "application/json, text/event-stream", **kwargs})


def tool(client, tokens, name, **args):
    return rpc(client, tokens, "tools/call", {"name": name, "arguments": args}).json()["result"]


def test_discovery_and_public_clients_only(client):
    response = client.post("/mcp")
    assert response.status_code == 401
    assert "oauth-protected-resource/mcp" in response.headers["www-authenticate"]
    metadata = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert metadata["resource"] == RESOURCE
    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["token_endpoint_auth_methods_supported"] == ["none"]
    for body in ({"redirect_uris": ["http://evil.example/cb"]}, {"redirect_uris": [REDIRECT + "#fragment"]},
                 {"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "client_secret_basic"}):
        assert client.post("/oauth/register", json=body).status_code == 400
    assert client.post("/mcp", headers={"X-API-Key": "anything"}).status_code == 401
    assert client.get("/mcp/connect").status_code == 200


def test_code_pkce_binding_refresh_and_revocation(client):
    client_id, tokens, code = credentials(client)
    assert exchange(client, client_id, code).json()["error"] == "invalid_grant"
    assert tokens["expires_in"] == 3600
    assert rpc(client, tokens, "ping").json()["result"] == {}
    data = {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": tokens["refresh_token"]}
    assert client.post("/oauth/token", data={**data, "resource": "https://evil.example/mcp"}).json()["error"] == "invalid_target"
    other = register(client)
    assert client.post("/oauth/token", data={**data, "client_id": other}).json()["error"] == "invalid_grant"
    refreshed = client.post("/oauth/token", data=data)
    assert refreshed.status_code == 200
    assert client.post("/oauth/token", data=data).json()["error"] == "invalid_grant"
    new_tokens = refreshed.json()
    assert client.post("/oauth/revoke", data={"token": new_tokens["access_token"]}).status_code == 200
    assert rpc(client, new_tokens, "ping").status_code == 401
    # Expired tokens are rejected by persistent storage, also after process restart.
    app.state.oauth_store.put("access", digest(tokens["access_token"]), {}, -1)
    assert rpc(client, tokens, "ping").status_code == 401


def test_consent_redirects_and_csrf(client):
    client_id = register(client)
    response = client.get("/oauth/authorize", params={"client_id": client_id, "redirect_uri": "https://evil.example"})
    assert response.status_code == 400 and "location" not in response.headers
    response = client.post("/oauth/authorize", data={"ticket": "fake", "csrf": "fake", "decision": "allow"})
    assert response.status_code == 400
    response = authorize(client, client_id, decision="deny")
    assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == ["access_denied"]
    response = authorize(client, client_id)
    code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
    assert exchange(client, client_id, code, code_verifier="x" * 64).json()["error"] == "invalid_grant"
    assert exchange(client, client_id, code, redirect_uri="https://evil.example").json()["error"] == "invalid_grant"
    assert exchange(client, client_id, code).status_code == 200


def test_mcp_edit_undo_export_and_persistence(client, raw):
    _, tokens, _ = credentials(client)
    initialized = rpc(client, tokens, "initialize", {"protocolVersion": "2025-06-18"}).json()["result"]
    assert initialized["protocolVersion"] == "2025-06-18"
    assert "tools" in initialized["capabilities"]
    names = {t["name"] for t in rpc(client, tokens, "tools/list").json()["result"]["tools"]}
    assert {"editor_api", "api_schema", "list_documents", "import_osm"} <= names
    result = tool(client, tokens, "import_osm", xml=raw.decode(), filename="test.osm")
    assert not result["isError"], result
    import json
    doc_id = json.loads(result["content"][0]["text"])["doc_id"]
    path = f"/api/documents/{doc_id}"
    listed = json.loads(tool(client, tokens, "list_documents")["content"][0]["text"])
    assert doc_id not in {r["doc_id"] for r in listed}  # private import stays off the lobby
    assert all(r["doc_id"].startswith("sample-") for r in listed)
    schema = json.loads(tool(client, tokens, "api_schema", path_filter="/ops")["content"][0]["text"])
    assert "/api/documents/{doc_id}/ops" in schema["paths"] and "set_tags" in schema["edit_operations"]
    doc = client.get(path).json()
    way_id = doc["geo"]["ways"]["features"][0]["properties"]["id"]
    result = tool(client, tokens, "editor_api", method="POST", path=path + "/ops",
        body={"op": "set_tags", "args": {"kind": "way", "id": way_id, "tags": {"mcp:test": "yes"}}})
    assert not result["isError"], result
    assert b'mcp:test' in client.get(path + "/export").content
    exported = tool(client, tokens, "editor_api", method="GET", path=path + "/export")
    assert "mcp:test" in exported["content"][0]["text"]
    from app.main import SESSIONS
    SESSIONS.pop(doc_id)
    assert b'mcp:test' in client.get(path + "/export").content
    result = tool(client, tokens, "editor_api", method="POST", path=path + "/undo")
    assert not result["isError"]
    assert b'mcp:test' not in client.get(path + "/export").content
    for bad in ("https://evil.example/api/health", "/api/../oauth/token", "/api/%2e%2e/oauth/token", "/api/health?x=1"):
        assert tool(client, tokens, "editor_api", method="GET", path=bad)["isError"]
    assert rpc(client, tokens, "ping", Origin="https://evil.example").status_code == 403
    assert rpc(client, tokens, "ping", **{"mcp-protocol-version": "invalid"}).status_code == 400
    assert rpc(client, tokens, "unknown").json()["error"]["code"] == -32601
    notification = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Authorization": "Bearer " + tokens["access_token"], "Accept": "application/json, text/event-stream"})
    assert notification.status_code == 202


def test_csrf_cookie_and_consent_replay(client):
    client_id = register(client)
    response = client.get("/oauth/authorize", params={"client_id": client_id, "redirect_uri": REDIRECT,
        "response_type": "code", "code_challenge": CHALLENGE, "code_challenge_method": "S256", "resource": RESOURCE})
    fields = {k: re.search(f'name="{k}" value="([^"]+)"', response.text)[1] for k in ("ticket", "csrf")}
    csrf = client.cookies.get("uds_oauth_consent")
    client.cookies.clear()
    assert client.post("/oauth/authorize", data={**fields, "decision": "allow"}).status_code == 400
    client.cookies.set("uds_oauth_consent", csrf)
    approved = client.post("/oauth/authorize", data={**fields, "decision": "allow"}, follow_redirects=False)
    assert approved.status_code == 303
    assert client.post("/oauth/authorize", data={**fields, "decision": "allow"}).status_code == 400


def test_multipart_upload_and_atomic_batch(client, raw):
    import json
    from app.main import SESSIONS
    from app.mcp_server import Store
    _, tokens, _ = credentials(client)
    # A fresh store object reads grants from disk, rather than a process cache.
    reopened = Store(app.state.oauth_store.path)
    assert reopened.get("access", digest(tokens["access_token"]))["resource"] == RESOURCE
    imported = tool(client, tokens, "editor_api", method="POST", path="/api/documents",
        files={"file": {"filename": "network.osm", "base64": base64.b64encode(raw).decode()}})
    assert not imported["isError"], imported
    doc_id = json.loads(imported["content"][0]["text"])["doc_id"]
    path = f"/api/documents/{doc_id}"
    way_id = next(iter(SESSIONS[doc_id].doc.ways))
    ops = [{"op": "set_tags", "args": {"kind": "way", "id": way_id, "tags": {"mcp:batch": "yes"}}},
           {"op": "unknown", "args": {}}]
    before = client.get(path + "/export").content
    result = tool(client, tokens, "editor_api", method="POST", path=path + "/batch", body={"ops": ops})
    assert result["isError"]
    assert client.get(path + "/export").content == before
    result = tool(client, tokens, "editor_api", method="POST", path=path + "/batch", body={"ops": ops[:1]})
    assert not result["isError"]
    assert SESSIONS[doc_id].history.last_tx.author == "AI: Клод"
    assert SESSIONS[doc_id].revision == 1
    assert tool(client, tokens, "editor_api", method="POST", path="/api/documents",
                files={"file": {"base64": "invalid%%"}})["isError"]
