"""FastAPI application: load .osm, edit it, validate, export."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .edits import History, OpError, apply_op, apply_ops
from .geojson import to_geojson, vertices
from .organizer import OrganizerError, credentials, upload_osm
from .osm_model import OsmDocument
from .sumo_check import find_netconvert, run_netconvert
from .validation import validate

DATA_DIR = Path(os.environ.get("UDS_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
DRAFT_DIR = DATA_DIR / "drafts"
STATIC_DIR = Path(os.environ.get("UDS_STATIC_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
MAX_UPLOAD = 200 * 1024 * 1024


@dataclass
class Session:
    id: str
    filename: str
    doc: OsmDocument
    history: History = field(default_factory=History)


SESSIONS: dict[str, Session] = {}

app = FastAPI(title="УДС-редактор", version=__version__)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(RequestValidationError)
async def validation_error(_request, exc: RequestValidationError) -> JSONResponse:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", []) if x != "body")
        msg = err.get("msg", "некорректное значение")
        parts.append(f"{loc}: {msg}" if loc else str(msg))
    return JSONResponse(status_code=400, content={"detail": "; ".join(parts) or "некорректный запрос"})


# ------------------------------------------------------------------ models
class OpRequest(BaseModel):
    op: str
    args: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


class BatchRequest(BaseModel):
    ops: list[OpRequest]
    label: str | None = None


# ----------------------------------------------------------------- helpers
def _session(doc_id: str) -> Session:
    s = SESSIONS.get(doc_id)
    if s is None:
        raise HTTPException(404, "документ не найден — загрузите .osm заново")
    return s


def _state(s: Session, include_geo: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "doc_id": s.id,
        "filename": s.filename,
        "stats": s.doc.stats(),
        "diff": s.doc.diff_summary(),
        "history": s.history.info(),
    }
    if include_geo:
        out["geo"] = to_geojson(s.doc)
    return out


def _open(data: bytes, filename: str) -> dict[str, Any]:
    try:
        doc = OsmDocument.parse(data)
    except Exception as e:  # noqa: BLE001 - shown to the user
        raise HTTPException(400, f"не удалось прочитать OSM: {e}") from e
    sid = uuid.uuid4().hex[:12]
    SESSIONS[sid] = Session(id=sid, filename=filename, doc=doc)
    _persist_draft(SESSIONS[sid])
    return _state(SESSIONS[sid])


# Document ids are generated as short hex, but restore accepts a client-supplied
# id, so the filename is restricted to this set before it is joined onto the draft dir.
_DRAFT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_LEGACY_DRAFT = ("autosave.osm", "autosave.json")


def _draft_paths(doc_id: str) -> tuple[Path, Path] | None:
    if not _DRAFT_ID.fullmatch(doc_id):
        return None
    root = DRAFT_DIR.resolve()
    osm_path = (DRAFT_DIR / f"{doc_id}.osm").resolve()
    meta_path = (DRAFT_DIR / f"{doc_id}.json").resolve()
    if osm_path.parent != root or meta_path.parent != root:
        return None
    return osm_path, meta_path


def _migrate_legacy_autosave() -> None:
    """Copy the old shared autosave to the document it names, then remove it.

    A single autosave.osm cannot be attributed to a request for a different id,
    so it is never served until it lives under that document's own filenames.
    """
    meta_path = DRAFT_DIR / "autosave.json"
    osm_path = DRAFT_DIR / "autosave.osm"
    if not meta_path.is_file() or not osm_path.is_file():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        sid = str(meta["doc_id"])
        filename = str(meta.get("filename") or "draft.osm")
    except (OSError, ValueError, KeyError, TypeError):
        return
    paths = _draft_paths(sid)
    if paths is None:
        return
    dest_osm, dest_meta = paths
    try:
        if not dest_osm.exists():
            DRAFT_DIR.mkdir(parents=True, exist_ok=True)
            dest_osm.write_bytes(osm_path.read_bytes())
            dest_meta.write_text(
                json.dumps({"doc_id": sid, "filename": filename}, ensure_ascii=False),
                encoding="utf-8",
            )
        meta_path.unlink()
        osm_path.unlink()
    except OSError:
        return


def _persist_draft(s: Session) -> None:
    """Write this document's draft. Other documents keep their own files."""
    paths = _draft_paths(s.id)
    if paths is None:
        return
    osm_path, meta_path = paths
    try:
        DRAFT_DIR.mkdir(parents=True, exist_ok=True)
        _migrate_legacy_autosave()
        osm_path.write_bytes(s.doc.to_xml(renumber_new=False))
        meta_path.write_text(
            json.dumps({"doc_id": s.id, "filename": s.filename}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        return


def _draft_ids() -> list[str]:
    _migrate_legacy_autosave()
    if not DRAFT_DIR.is_dir():
        return []
    found: list[str] = []
    for meta in DRAFT_DIR.glob("*.json"):
        if meta.name == "autosave.json":
            continue
        paths = _draft_paths(meta.stem)
        if paths is None:
            continue
        osm_path, meta_path = paths
        if osm_path.is_file() and meta_path.is_file():
            found.append(meta.stem)
    return sorted(found)


def _draft_exists(doc_id: str) -> bool:
    _migrate_legacy_autosave()
    paths = _draft_paths(doc_id)
    if paths is None:
        return False
    osm_path, meta_path = paths
    return osm_path.is_file() and meta_path.is_file()


def _materialize_draft(doc_id: str) -> Session:
    """Load the draft stored for this id. Never substitutes a different document."""
    existing = SESSIONS.get(doc_id)
    if existing is not None:
        return existing
    _migrate_legacy_autosave()
    paths = _draft_paths(doc_id)
    if paths is None:
        raise HTTPException(404, "черновик не найден")
    osm_path, meta_path = paths
    if not osm_path.is_file() or not meta_path.is_file():
        raise HTTPException(404, "черновик не найден")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        sid = str(meta["doc_id"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise HTTPException(404, "черновик повреждён") from e
    if sid != doc_id:
        raise HTTPException(404, "черновик не найден")
    try:
        doc = OsmDocument.parse(osm_path.read_bytes())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"не удалось прочитать черновик: {e}") from e
    SESSIONS[doc_id] = Session(id=doc_id, filename=str(meta.get("filename") or "draft.osm"), doc=doc)
    return SESSIONS[doc_id]


def _delete_draft_files(doc_id: str) -> None:
    paths = _draft_paths(doc_id)
    if paths is None:
        return
    for path in paths:
        if path.is_file():
            path.unlink()


def _remember(s: Session) -> dict[str, Any]:
    _persist_draft(s)
    return _state(s)


def _safe_filename(name: str) -> str:
    name = Path(name).name or "export.osm"
    name = re.sub(r"[^\w.\-]+", "_", name)
    return name if name.lower().endswith(".osm") else name + ".osm"


# ------------------------------------------------------------------ routes
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__, "netconvert": find_netconvert() is not None}


@app.get("/api/samples")
def samples() -> list[dict[str, Any]]:
    if not DATA_DIR.exists():
        return []
    return [{"name": p.name, "size": p.stat().st_size} for p in sorted(DATA_DIR.glob("*.osm"))]


@app.post("/api/samples/{name}/open")
def open_sample(name: str) -> dict[str, Any]:
    path = (DATA_DIR / Path(name).name).resolve()
    if not path.is_file() or path.parent != DATA_DIR.resolve():
        raise HTTPException(404, "файл не найден в data/")
    return _open(path.read_bytes(), path.name)


@app.post("/api/documents")
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "файл слишком большой")
    return _open(data, file.filename or "upload.osm")


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str) -> dict[str, Any]:
    s = SESSIONS.get(doc_id)
    if s is None and _draft_exists(doc_id):
        s = _materialize_draft(doc_id)
    if s is None:
        raise HTTPException(404, "документ не найден — загрузите .osm заново")
    return _state(s)


@app.get("/api/documents/{doc_id}/elements/{kind}/{eid}")
def get_element(doc_id: str, kind: Literal["node", "way", "relation"], eid: int) -> dict[str, Any]:
    s = _session(doc_id)
    el = s.doc.get(kind, eid)
    if el is None:
        raise HTTPException(404, "объект не найден")
    out: dict[str, Any] = {"kind": kind, "id": eid, "tags": el.tags, "attrs": el.attrs}
    if kind == "way":
        out["vertices"] = vertices(s.doc, eid)
    elif kind == "node":
        out["lat"], out["lon"] = el.lat, el.lon  # type: ignore[union-attr]
        out["ways"] = [w.id for w in s.doc.ways_of_node(eid)]
    return out


@app.post("/api/documents/{doc_id}/ops")
def run_op(doc_id: str, req: OpRequest) -> dict[str, Any]:
    s = _session(doc_id)
    try:
        result = apply_op(s.doc, s.history, req.op, req.args, req.label)
    except OpError as e:
        raise HTTPException(422, str(e)) from e
    return {"result": result, **_remember(s)}


@app.post("/api/documents/{doc_id}/batch")
def run_batch(doc_id: str, req: BatchRequest) -> dict[str, Any]:
    s = _session(doc_id)
    try:
        results = apply_ops(s.doc, s.history, [(o.op, o.args) for o in req.ops], req.label)
    except OpError as e:
        raise HTTPException(422, str(e)) from e
    return {"results": results, **_remember(s)}


@app.post("/api/documents/{doc_id}/undo")
def undo(doc_id: str) -> dict[str, Any]:
    s = _session(doc_id)
    s.history.undo()
    return _remember(s)


@app.post("/api/documents/{doc_id}/redo")
def redo(doc_id: str) -> dict[str, Any]:
    s = _session(doc_id)
    s.history.redo()
    return _remember(s)


@app.get("/api/draft")
def read_draft(doc_id: str | None = None) -> dict[str, Any]:
    """Restore one document.

    With ``doc_id``, only that document's draft is loaded — an unknown id is a
    404 even when other drafts exist. Without it, the sole draft is returned
    so a single-document client keeps working; several drafts are ambiguous
    and also 404 rather than handing back whichever file was written last.
    """
    if doc_id is not None:
        return _state(_materialize_draft(doc_id))
    ids = _draft_ids()
    if len(ids) != 1:
        raise HTTPException(404, "черновик не найден")
    return _state(_materialize_draft(ids[0]))


@app.delete("/api/draft")
def delete_draft(doc_id: str | None = None) -> dict[str, bool]:
    if doc_id is not None:
        _delete_draft_files(doc_id)
        return {"ok": True}
    for sid in _draft_ids():
        _delete_draft_files(sid)
    for name in _LEGACY_DRAFT:
        path = DRAFT_DIR / name
        if path.is_file():
            path.unlink()
    return {"ok": True}


@app.get("/api/documents/{doc_id}/validate")
def run_validate(doc_id: str) -> dict[str, Any]:
    return validate(_session(doc_id).doc)


@app.get("/api/documents/{doc_id}/export")
def export(doc_id: str, filename: str = Query("team.osm"), renumber: bool = True) -> Response:
    s = _session(doc_id)
    data = s.doc.to_xml(renumber_new=renumber)
    name = _safe_filename(filename)
    return Response(content=data, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/documents/{doc_id}/check/sumo")
def check_sumo(doc_id: str) -> dict[str, Any]:
    s = _session(doc_id)
    return run_netconvert(s.doc.to_xml(renumber_new=True))


@app.post("/api/documents/{doc_id}/check/organizer")
def check_organizer(doc_id: str) -> dict[str, Any]:
    s = _session(doc_id)
    try:
        token, team, base = credentials()
        return upload_osm(s.doc.to_xml(renumber_new=True), token, team, base)
    except OrganizerError as e:
        status = e.status if 400 <= e.status < 500 else 502
        raise HTTPException(status, e.message) from e


# --------------------------------------------------------- static frontend
if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        target = (STATIC_DIR / path).resolve()
        if path and target.is_file() and STATIC_DIR.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(STATIC_DIR / "index.html")
