"""Stateless Streamable HTTP MCP and a same-app, public-client OAuth server.

The editor is a shared workspace without accounts. Consent authorizes that shared
workspace; this module deliberately does not pretend to authenticate a person.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import inspect
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.routing import APIRoute

SCOPE = "workspace:edit"
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    """SQLite keeps grants across restarts and makes code/refresh use atomic."""
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path, timeout=10) as db:
            db.execute("CREATE TABLE IF NOT EXISTS oauth (kind TEXT, key TEXT, value TEXT, expires REAL, PRIMARY KEY(kind,key))")
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM oauth WHERE expires < ?", (time.time(),))
            yield db

    def put(self, kind, key, value, ttl):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO oauth VALUES (?,?,?,?)", (kind, key, json.dumps(value), time.time() + ttl))

    def get(self, kind, key, consume=False):
        with self.connect() as db:
            row = db.execute("SELECT value FROM oauth WHERE kind=? AND key=?", (kind, key)).fetchone()
            if row and consume:
                db.execute("DELETE FROM oauth WHERE kind=? AND key=?", (kind, key))
            return json.loads(row[0]) if row else None


def install_mcp(app, data_dir):
    store = Store(lambda: Path(os.environ.get("UDS_OAUTH_DB", data_dir / "oauth.sqlite3")))
    app.state.oauth_store = store

    def base(request):
        # Set an explicit public URL behind a reverse proxy; do not derive public
        # token audiences from untrusted forwarded headers.
        return os.environ.get("UDS_PUBLIC_URL", str(request.base_url).rstrip("/")).rstrip("/")

    def resource(request):
        return base(request) + "/mcp"

    def error(code, description, status=400):
        return JSONResponse({"error": code, "error_description": description}, status_code=status,
                            headers={"Cache-Control": "no-store"})

    def page(title, content, status=200, redirect_origin=""):
        form_action = "'self'" + (" " + redirect_origin if redirect_origin else "")
        return HTMLResponse(f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · УДС</title>
<style>body{{font:16px system-ui;background:#edf2f6;color:#152638;margin:0;padding:8vh 20px}}
main{{max-width:540px;margin:auto;background:white;padding:32px;border-radius:16px;box-shadow:0 8px 40px #14263818}}
h1{{font-size:26px}}code{{overflow-wrap:anywhere}}button,a.button{{padding:12px 20px;border:0;border-radius:8px;background:#1666ad;color:white;cursor:pointer;font:inherit}}button[value=deny]{{background:#e5ebf0;color:#152638}}p{{line-height:1.6}}</style>
<main><small>РЕДАКТОР УДС · MCP</small><h1>{title}</h1>{content}</main></html>''', status_code=status,
                            headers={"Cache-Control": "no-store", "Content-Security-Policy": f"default-src 'none'; style-src 'unsafe-inline'; form-action {form_action}; frame-ancestors 'none'; base-uri 'none'", "X-Content-Type-Options": "nosniff"})

    @app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
    @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    async def protected_metadata(request: Request):
        return {"resource": resource(request), "authorization_servers": [base(request)],
                "scopes_supported": [SCOPE], "bearer_methods_supported": ["header"], "resource_name": "Редактор УДС"}

    @app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    async def authorization_metadata(request: Request):
        origin = base(request)
        return {"issuer": origin, "authorization_endpoint": origin + "/oauth/authorize",
                "token_endpoint": origin + "/oauth/token", "registration_endpoint": origin + "/oauth/register",
                "revocation_endpoint": origin + "/oauth/revoke", "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"], "scopes_supported": [SCOPE],
                "token_endpoint_auth_methods_supported": ["none"], "code_challenge_methods_supported": ["S256"]}

    @app.post("/oauth/register", include_in_schema=False)
    async def register(request: Request):
        try:
            body = await request.json()
            uris = body["redirect_uris"]
            if not isinstance(uris, list) or not 1 <= len(uris) <= 10:
                raise ValueError()
            for uri in uris:
                parsed = urlsplit(uri)
                if (not parsed.hostname or parsed.username or parsed.password or parsed.fragment
                        or not (parsed.scheme == "https" or
                                (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}))):
                    raise ValueError()
            if body.get("token_endpoint_auth_method", "none") != "none":
                return error("invalid_client_metadata", "Only public clients (none) are supported")
            if body.get("grant_types", ["authorization_code", "refresh_token"]) != ["authorization_code", "refresh_token"] and not (
                isinstance(body.get("grant_types"), list) and "authorization_code" in body["grant_types"]
                and set(body["grant_types"]) <= {"authorization_code", "refresh_token"}
            ):
                raise ValueError()
            if body.get("response_types", ["code"]) != ["code"]:
                raise ValueError()
            client = {"client_id": secrets.token_urlsafe(24), "client_name": str(body.get("client_name", "AI client"))[:100],
                      "redirect_uris": uris, "token_endpoint_auth_method": "none",
                      "grant_types": body.get("grant_types", ["authorization_code", "refresh_token"]), "response_types": ["code"]}
            store.put("client", client["client_id"], client, 365 * 86400)
            return JSONResponse(client, status_code=201, headers={"Cache-Control": "no-store"})
        except (ValueError, KeyError, TypeError, AttributeError):
            return error("invalid_client_metadata", "Provide valid HTTPS redirect_uris (HTTP allowed for loopback)")

    @app.get("/oauth/authorize", include_in_schema=False)
    async def authorize(request: Request):
        params = dict(request.query_params)
        client = store.get("client", params.get("client_id", ""))
        if not client or params.get("redirect_uri") not in client["redirect_uris"]:
            return error("invalid_request", "Unknown client or unregistered redirect URI")
        if (params.get("response_type") != "code" or params.get("code_challenge_method") != "S256"
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.get("code_challenge", ""))):
            return error("invalid_request", "Authorization code with S256 PKCE is required")
        if params.get("resource") != resource(request):
            return error("invalid_target", "resource must be " + resource(request))
        if params.get("scope", SCOPE) != SCOPE:
            return error("invalid_scope", "Supported scope: " + SCOPE)
        ticket = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        store.put("consent", digest(ticket), {"params": params, "csrf": digest(csrf)}, 600)
        redirect = urlsplit(params["redirect_uri"])
        redirect_origin = redirect.scheme + "://" + quote(redirect.netloc, safe=":[]-.")
        response = page("Connect your AI assistant", f'''<p><strong>{html.escape(client['client_name'])}</strong> is requesting access to this shared workspace.</p>
<p>Allow it to read, create, edit, export and delete documents, run analyses, and change scenarios. Changes appear live in the editor and supported edits can be undone.</p>
<p>This MVP has no user accounts. Approval grants access to <strong>all shared documents</strong> in this app. Approve only a client you trust.</p>
<p>Return address: <code>{html.escape(params['redirect_uri'])}</code></p>
<form method="post" action="/oauth/authorize"><input type="hidden" name="ticket" value="{ticket}"><input type="hidden" name="csrf" value="{csrf}">
<button name="decision" value="allow">Allow access</button> <button name="decision" value="deny">Cancel</button></form>''', redirect_origin=redirect_origin)
        response.set_cookie("uds_oauth_consent", csrf, max_age=600, httponly=True, secure=base(request).startswith("https:"), samesite="lax", path="/oauth/authorize")
        return response

    @app.post("/oauth/authorize", include_in_schema=False)
    async def consent(request: Request):
        form = await request.form()
        ticket, csrf = str(form.get("ticket", "")), str(form.get("csrf", ""))
        saved = store.get("consent", digest(ticket))
        if (not saved or not csrf or not hmac.compare_digest(saved["csrf"], digest(csrf))
                or not hmac.compare_digest(request.cookies.get("uds_oauth_consent", ""), csrf)):
            return error("invalid_request", "Expired or invalid consent; reconnect your client")
        if not store.get("consent", digest(ticket), consume=True):
            return error("invalid_request", "Consent already used")
        params = saved["params"]
        result = {}
        if form.get("decision") == "allow":
            code = secrets.token_urlsafe(32)
            store.put("code", digest(code), params, 300)
            result["code"] = code
        else:
            result["error"] = "access_denied"
        if "state" in params:
            result["state"] = params["state"]
        target = params["redirect_uri"] + ("&" if "?" in params["redirect_uri"] else "?") + urlencode(result)
        response = RedirectResponse(target, status_code=303, headers={"Cache-Control": "no-store"})
        response.delete_cookie("uds_oauth_consent", path="/oauth/authorize")
        return response

    @app.post("/oauth/token", include_in_schema=False)
    async def token(request: Request):
        form = await request.form()
        client_id = str(form.get("client_id", ""))
        if not store.get("client", client_id):
            return error("invalid_client", "Unknown client", 401)
        if request.headers.get("authorization") or form.get("client_secret"):
            return error("invalid_client", "Public clients do not use client secrets", 401)
        grant = form.get("grant_type")
        if grant == "authorization_code":
            key = digest(str(form.get("code", "")))
            saved = store.get("code", key)
            verifier = str(form.get("code_verifier", ""))
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            valid = (saved and saved["client_id"] == client_id and saved["redirect_uri"] == form.get("redirect_uri")
                     and re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
                     and hmac.compare_digest(saved["code_challenge"], challenge))
        elif grant == "refresh_token":
            key = digest(str(form.get("refresh_token", "")))
            saved = store.get("refresh", key)
            valid = saved and saved["client_id"] == client_id
        else:
            return error("unsupported_grant_type", "Use authorization_code or refresh_token")
        if not valid:
            return error("invalid_grant", "Invalid, expired or already used grant")
        if form.get("resource", saved["resource"]) != saved["resource"] or saved["resource"] != resource(request):
            return error("invalid_target", "Token resource does not match the grant")
        if form.get("scope", SCOPE) != SCOPE:
            return error("invalid_scope", "Scope cannot be changed")
        if not store.get("code" if grant == "authorization_code" else "refresh", key, consume=True):
            return error("invalid_grant", "Grant already used")
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        value = {"client_id": client_id, "resource": saved["resource"], "scope": SCOPE}
        store.put("access", digest(access), value, ACCESS_TTL)
        store.put("refresh", digest(refresh), value, REFRESH_TTL)
        return JSONResponse({"access_token": access, "refresh_token": refresh, "token_type": "Bearer",
                             "expires_in": ACCESS_TTL, "scope": SCOPE},
                            headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    @app.post("/oauth/revoke", include_in_schema=False)
    async def revoke(request: Request):
        form = await request.form()
        key = digest(str(form.get("token", "")))
        for kind in ("access", "refresh"):
            store.get(kind, key, consume=True)
        return Response(status_code=200, headers={"Cache-Control": "no-store"})

    def api_routes():
        return [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/")]

    tools = [
        {"name": "list_documents", "description": "List open and saved shared documents. Use doc_id for API calls.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "api_schema", "description": "Discover every editor API endpoint and edit operation with argument signatures. Optionally filter paths by substring. Read before calling editor_api.", "inputSchema": {"type": "object", "properties": {"path_filter": {"type": "string"}}, "additionalProperties": False}},
        {"name": "editor_api", "description": "Call any documented editor API using its local path, method, query and JSON body. Supports all editing, batching, undo/redo, validation, scenarios, signals, transit, analysis and exports. Uses normal app persistence and live collaboration. No external URLs. Binary exports return base64; XML exports return text.", "inputSchema": {"type": "object", "properties": {"method": {"type": "string", "enum": ["GET", "POST", "DELETE"]}, "path": {"type": "string"}, "query": {"type": "object"}, "body": {"type": "object"}, "files": {"type": "object", "description": "Multipart uploads: map field names to objects with filename and base64 strings. Omit body when uploading."}}, "required": ["method", "path"], "additionalProperties": False}},
        {"name": "import_osm", "description": "Create a document from OSM XML text. For bundled files use POST /api/samples/{name}/open instead.", "inputSchema": {"type": "object", "properties": {"filename": {"type": "string"}, "xml": {"type": "string"}}, "required": ["xml"], "additionalProperties": False}},
    ]

    async def call_tool(name, args, client):
        from . import main
        from .edits import OPS
        if name == "list_documents":
            ids = sorted(set(main.SESSIONS) | set(main._draft_ids()))
            return [{"doc_id": sid, "filename": main._session(sid).filename} for sid in ids]
        if name == "api_schema":
            schema = app.openapi()
            paths = {p: v for p, v in schema["paths"].items() if p.startswith("/api/") and args.get("path_filter", "") in p}
            return {"paths": paths, "components": schema.get("components", {}), "edit_operations": {
                op: {"description": label, "arguments": str(inspect.signature(fn)).replace("(doc, tx, ", "(")}
                for op, (fn, label) in OPS.items()}}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp.internal") as api:
            headers = {"x-user-name": quote("AI: " + client["client_name"]), "x-client-id": "mcp-" + client["client_id"]}
            if name == "import_osm":
                response = await api.post("/api/documents", files={"file": (args.get("filename", "ai.osm"), args["xml"].encode(), "application/xml")}, headers=headers)
            elif name == "editor_api":
                path, method = args["path"], args["method"]
                if (not path.startswith("/api/") or any(c in path for c in "%?#\\")
                        or any(p in {".", ".."} for p in path.split("/"))
                        or not any(r.path_regex.fullmatch(path) and method in r.methods for r in api_routes())):
                    raise ValueError("Use a documented local /api/ path and method")
                files = None
                if args.get("files"):
                    if args.get("body"):
                        raise ValueError("Use files or a JSON body, not both")
                    files = {}
                    for field, upload in args["files"].items():
                        if not isinstance(upload, dict) or not isinstance(upload.get("base64"), str):
                            raise ValueError("Each upload needs filename and base64 strings")
                        files[field] = (str(upload.get("filename", field)), base64.b64decode(upload["base64"], validate=True))
                response = await api.request(method, path, params=args.get("query"), json=args.get("body"), files=files, headers=headers)
            else:
                raise ValueError("Unknown tool")
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            body = response.json()
        elif content_type.startswith("text/") or "xml" in content_type:
            body = response.text
        else:
            body = {"content_type": content_type, "base64": base64.b64encode(response.content).decode()}
        if response.is_error:
            raise ValueError(f"HTTP {response.status_code}: {body}")
        return body

    @app.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
    async def mcp(request: Request):
        origin = request.headers.get("origin")
        if origin and origin != base(request):
            return Response(status_code=403)
        auth = request.headers.get("authorization", "")
        saved = store.get("access", digest(auth[7:])) if auth.startswith("Bearer ") else None
        if not saved or saved["resource"] != resource(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers={
                "WWW-Authenticate": f'Bearer resource_metadata="{base(request)}/.well-known/oauth-protected-resource/mcp", scope="{SCOPE}"', "Cache-Control": "no-store"})
        if request.method != "POST":
            # No unsolicited server messages and no server-side MCP sessions.
            return Response(status_code=405, headers={"Allow": "POST"})
        accept = request.headers.get("accept", "")
        if "application/json" not in accept or "text/event-stream" not in accept:
            return Response(status_code=406)
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return Response(status_code=415)
        version = request.headers.get("mcp-protocol-version")
        if version and version not in VERSIONS:
            return Response(status_code=400)
        def rpc_error(id, code, message):
            return JSONResponse({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})
        try:
            message = await request.json()
        except ValueError:
            return rpc_error(None, -32700, "Parse error")
        if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
                or not isinstance(message.get("method"), str)
                or ("id" in message and (isinstance(message["id"], bool) or not isinstance(message["id"], (str, int))))):
            return rpc_error(None, -32600, "Invalid request")
        id, method, params = message.get("id"), message["method"], message.get("params", {})
        if not isinstance(params, dict):
            return rpc_error(id, -32602, "params must be an object")
        if "id" not in message:
            return Response(status_code=202)
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion") if params.get("protocolVersion") in VERSIONS else VERSIONS[0],
                      "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "uds-editor", "version": main_version()},
                      "instructions": "Shared transport editor. Discover documents with list_documents, then api_schema. Use batch for atomic edits. All edits autosave and broadcast to the app. Never invent edit-operation arguments."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tools}
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments", {})
            tool = next((t for t in tools if t["name"] == name), None)
            if not tool or not isinstance(args, dict):
                return rpc_error(id, -32602, "Unknown tool or invalid arguments")
            schema = tool["inputSchema"]
            if (set(args) - set(schema["properties"]) or set(schema.get("required", [])) - set(args)
                    or any((schema["properties"][k]["type"] == "string" and not isinstance(v, str))
                           or (schema["properties"][k]["type"] == "object" and not isinstance(v, dict))
                           or ("enum" in schema["properties"][k] and v not in schema["properties"][k]["enum"])
                           for k, v in args.items())):
                return rpc_error(id, -32602, "Invalid tool arguments")
            try:
                client = store.get("client", saved["client_id"])
                if client is None:
                    raise ValueError("Client registration expired; reconnect")
                output = await call_tool(name, args, client)
                result = {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}], "isError": False}
            except (ValueError, TypeError, KeyError, binascii.Error, HTTPException) as exc:
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        else:
            return rpc_error(id, -32601, "Method not found")
        return JSONResponse({"jsonrpc": "2.0", "id": id, "result": result}, headers={"Cache-Control": "no-store"})

    @app.get("/mcp/connect", include_in_schema=False)
    async def connect_page(request: Request):
        return page("Connect via MCP", f'''<p>Add a custom connector in Claude or another Streamable HTTP MCP client:</p>
<p><code>{html.escape(resource(request))}</code></p><p>Choose OAuth and approve access here in the app. No API key or client secret is needed. Clients register automatically.</p>
<p>Access lasts one hour; refresh grants last 30 days. This app is a shared workspace without user accounts.</p><a class="button" href="/">Back to editor</a>''')


def main_version():
    from . import __version__
    return __version__
