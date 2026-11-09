"""FastAPI application: load .osm, edit it, validate, export."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .edits import History, OpError, apply_op, apply_ops
from .geojson import to_geojson, vertices
from .osm_model import OsmDocument
from .sumo_check import find_netconvert, run_netconvert
from .validation import validate

DATA_DIR = Path(os.environ.get("UDS_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
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
    return _state(SESSIONS[sid])


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
    return _state(_session(doc_id))


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
    return {"result": result, **_state(s)}


@app.post("/api/documents/{doc_id}/batch")
def run_batch(doc_id: str, req: BatchRequest) -> dict[str, Any]:
    s = _session(doc_id)
    try:
        results = apply_ops(s.doc, s.history, [(o.op, o.args) for o in req.ops], req.label)
    except OpError as e:
        raise HTTPException(422, str(e)) from e
    return {"results": results, **_state(s)}


@app.post("/api/documents/{doc_id}/undo")
def undo(doc_id: str) -> dict[str, Any]:
    s = _session(doc_id)
    s.history.undo()
    return _state(s)


@app.post("/api/documents/{doc_id}/redo")
def redo(doc_id: str) -> dict[str, Any]:
    s = _session(doc_id)
    s.history.redo()
    return _state(s)


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


# --------------------------------------------------------- static frontend
if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        target = (STATIC_DIR / path).resolve()
        if path and target.is_file() and STATIC_DIR.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(STATIC_DIR / "index.html")
