"""FastAPI application: load .osm, edit it, validate, export."""

from __future__ import annotations

import json
import os
import re
import threading
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
from .geojson import geo_patch, to_geojson, vertices
from .organizer import OrganizerError, credentials, upload_osm
from .osm_model import OsmDocument
from .sumo_check import find_netconvert, run_netconvert
from .validation import validate

DATA_DIR = Path(os.environ.get("UDS_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
DRAFT_DIR = DATA_DIR / "drafts"
STATIC_DIR = Path(os.environ.get("UDS_STATIC_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
MAX_UPLOAD = 200 * 1024 * 1024
JOURNAL_COMPACT_AT = 1000  # journal lines before the draft base is rewritten


@dataclass
class Session:
    id: str
    filename: str
    doc: OsmDocument
    history: History = field(default_factory=History)
    # FastAPI runs sync endpoints in a thread pool: validation could read the
    # graph while an edit is half applied. Every endpoint takes this lock.
    lock: threading.RLock = field(default_factory=threading.RLock)
    journal_len: int = 0


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


def _json(obj: Any, status: int = 200) -> Response:
    """Serialize directly. FastAPI's jsonable_encoder walks the ~1 MB map payload
    and costs ~5x more than json.dumps."""
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return Response(content=body.encode("utf-8"), status_code=status, media_type="application/json")


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
    _start_draft(SESSIONS[sid], data)
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


# Drafts are a base .osm plus an append-only journal of edits ({id}.ops.jsonl).
# Rewriting the whole file after every edit took ~0.7 s on example.osm; a
# journal line takes microseconds. Restore = parse base + replay journal, which
# also brings back undo/redo history after a backend restart.
def _journal_path(doc_id: str) -> Path | None:
    paths = _draft_paths(doc_id)
    return paths[0].with_suffix(".ops.jsonl") if paths else None


def _write_draft_base(s: Session, base: bytes) -> None:
    paths = _draft_paths(s.id)
    journal = _journal_path(s.id)
    if paths is None or journal is None:
        return
    osm_path, meta_path = paths
    try:
        DRAFT_DIR.mkdir(parents=True, exist_ok=True)
        _migrate_legacy_autosave()
        osm_path.write_bytes(base)
        meta_path.write_text(
            json.dumps({"doc_id": s.id, "filename": s.filename}, ensure_ascii=False),
            encoding="utf-8",
        )
        journal.write_text("", encoding="utf-8")
        s.journal_len = 0
    except OSError:
        return


def _start_draft(s: Session, raw: bytes) -> None:
    """The uploaded bytes are the draft base; no re-serialization needed."""
    _write_draft_base(s, raw)


def _persist_draft(s: Session) -> None:
    """Rewrite the base from the current state and clear the journal."""
    _write_draft_base(s, s.doc.to_xml(renumber_new=False))


def _journal(s: Session, entry: dict[str, Any]) -> None:
    paths = _draft_paths(s.id)
    journal = _journal_path(s.id)
    if paths is None or journal is None:
        return
    if not paths[0].is_file() or not paths[1].is_file():
        _persist_draft(s)  # draft was discarded or never written: restart it from the current state
        return
    try:
        with journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        s.journal_len += 1
    except OSError:
        return
    if s.journal_len >= JOURNAL_COMPACT_AT:
        _persist_draft(s)


def _replay_journal(s: Session) -> None:
    journal = _journal_path(s.id)
    if journal is None or not journal.is_file():
        return
    count = 0
    for line in journal.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            kind = entry.get("t")
            if kind == "ops":
                apply_ops(s.doc, s.history, [(o[0], o[1]) for o in entry["ops"]], entry.get("label"))
            elif kind == "undo":
                s.history.undo()
            elif kind == "redo":
                s.history.redo()
        except (OpError, ValueError, KeyError, TypeError, IndexError):
            continue  # one bad line must not make the whole draft unreadable
        count += 1
    s.journal_len = count


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
    s = Session(id=doc_id, filename=str(meta.get("filename") or "draft.osm"), doc=doc)
    _replay_journal(s)
    SESSIONS[doc_id] = s
    return s


def _delete_draft_files(doc_id: str) -> None:
    paths = _draft_paths(doc_id)
    if paths is None:
        return
    for path in (*paths, _journal_path(doc_id)):
        if path is not None and path.is_file():
            path.unlink()


def _changed(s: Session, tx: Any, entry: dict[str, Any] | None) -> dict[str, Any]:
    """Journal the edit and answer with only the map features it touched."""
    if entry is not None:
        _journal(s, entry)
    out = _state(s, include_geo=False)
    out["patch"] = geo_patch(s.doc, tx.before, tx.after) if tx is not None else None
    return out


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
def open_sample(name: str) -> Response:
    path = (DATA_DIR / Path(name).name).resolve()
    if not path.is_file() or path.parent != DATA_DIR.resolve():
        raise HTTPException(404, "файл не найден в data/")
    return _json(_open(path.read_bytes(), path.name))


@app.post("/api/documents")
def upload(file: UploadFile = File(...)) -> Response:
    # sync on purpose: parsing takes ~0.5 s and must not block the event loop
    data = file.file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "файл слишком большой")
    return _json(_open(data, file.filename or "upload.osm"))


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str) -> Response:
    s = SESSIONS.get(doc_id)
    if s is None and _draft_exists(doc_id):
        s = _materialize_draft(doc_id)
    if s is None:
        raise HTTPException(404, "документ не найден — загрузите .osm заново")
    with s.lock:
        return _json(_state(s))


@app.get("/api/documents/{doc_id}/elements/{kind}/{eid}")
def get_element(doc_id: str, kind: Literal["node", "way", "relation"], eid: int) -> dict[str, Any]:
    s = _session(doc_id)
    with s.lock:
        el = s.doc.get(kind, eid)
        if el is None:
            raise HTTPException(404, "объект не найден")
        out: dict[str, Any] = {"kind": kind, "id": eid, "tags": dict(el.tags), "attrs": dict(el.attrs)}
        if kind == "way":
            out["vertices"] = vertices(s.doc, eid)
        elif kind == "node":
            out["lat"], out["lon"] = el.lat, el.lon  # type: ignore[union-attr]
            out["ways"] = [w.id for w in s.doc.ways_of_node(eid)]
        return out


@app.post("/api/documents/{doc_id}/ops")
def run_op(doc_id: str, req: OpRequest) -> Response:
    s = _session(doc_id)
    with s.lock:
        try:
            result = apply_op(s.doc, s.history, req.op, req.args, req.label)
        except OpError as e:
            raise HTTPException(422, str(e)) from e
        entry = {"t": "ops", "ops": [[req.op, req.args]], "label": req.label}
        return _json({"result": result, **_changed(s, s.history.last_tx, entry)})


@app.post("/api/documents/{doc_id}/batch")
def run_batch(doc_id: str, req: BatchRequest) -> Response:
    s = _session(doc_id)
    with s.lock:
        try:
            results = apply_ops(s.doc, s.history, [(o.op, o.args) for o in req.ops], req.label)
        except OpError as e:
            raise HTTPException(422, str(e)) from e
        entry = {"t": "ops", "ops": [[o.op, o.args] for o in req.ops], "label": req.label}
        return _json({"results": results, **_changed(s, s.history.last_tx, entry)})


@app.post("/api/documents/{doc_id}/undo")
def undo(doc_id: str) -> Response:
    s = _session(doc_id)
    with s.lock:
        tx = s.history.undo()
        return _json(_changed(s, tx, {"t": "undo"} if tx else None))


@app.post("/api/documents/{doc_id}/redo")
def redo(doc_id: str) -> Response:
    s = _session(doc_id)
    with s.lock:
        tx = s.history.redo()
        return _json(_changed(s, tx, {"t": "redo"} if tx else None))


@app.get("/api/draft")
def read_draft(doc_id: str | None = None) -> Response:
    """Restore one document.

    With ``doc_id``, only that document's draft is loaded — an unknown id is a
    404 even when other drafts exist. Without it, the sole draft is returned
    so a single-document client keeps working; several drafts are ambiguous
    and also 404 rather than handing back whichever file was written last.
    """
    if doc_id is not None:
        return _json(_state(_materialize_draft(doc_id)))
    ids = _draft_ids()
    if len(ids) != 1:
        raise HTTPException(404, "черновик не найден")
    return _json(_state(_materialize_draft(ids[0])))


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
def run_validate(doc_id: str) -> Response:
    s = _session(doc_id)
    with s.lock:
        return _json(validate(s.doc))


@app.get("/api/documents/{doc_id}/export")
def export(doc_id: str, filename: str = Query("team.osm"), renumber: bool = True) -> Response:
    s = _session(doc_id)
    with s.lock:
        data = s.doc.to_xml(renumber_new=renumber)
    name = _safe_filename(filename)
    return Response(content=data, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/documents/{doc_id}/check/sumo")
def check_sumo(doc_id: str) -> dict[str, Any]:
    s = _session(doc_id)
    with s.lock:
        xml = s.doc.to_xml(renumber_new=True)
    return run_netconvert(xml)


@app.post("/api/documents/{doc_id}/check/organizer")
def check_organizer(doc_id: str) -> dict[str, Any]:
    s = _session(doc_id)
    try:
        token, team, base = credentials()
        with s.lock:
            xml = s.doc.to_xml(renumber_new=True)
        return upload_osm(xml, token, team, base)
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
