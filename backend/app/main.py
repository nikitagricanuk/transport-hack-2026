"""FastAPI application: load .osm, edit it, validate, export."""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Header, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, report
from .collab import HUB, Peer, clean_presence
from . import fix_preview
from . import signals as signal_plans
from . import scenario, transit
from urllib.parse import unquote
from .adapters import compare_adapters
from .analysis import (
    changes as list_changes,
    compare_snapshots,
    load_summary,
    make_snapshot,
    network_stats,
    original_document,
    road_rows,
)
from .edits import History, OpError, apply_op, apply_ops
from .geojson import geo_patch, to_geojson, vertices
from .organizer import OrganizerError, credentials, upload_osm
from .osm_model import OsmDocument
from .sumo_check import find_netconvert, run_netconvert
from .simulation import FILE_LIMIT, SimulationError, read_matsim, read_webots, run_sumo, unpack_recording
from .validation import validate

DATA_DIR = Path(os.environ.get("UDS_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
SAMPLES_DIR = Path(os.environ.get("UDS_SAMPLES_DIR", DATA_DIR))
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
    revision: int = 0  # bumped on every edit; clients apply broadcast patches in this order
    # load scenario: per-way volume overrides (veh/h), a demand multiplier and fixed snapshots
    loads: dict[int, float] = field(default_factory=dict)
    multiplier: float = 1.0
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    # vehicles/h per transit route (overrides of the OSM interval) and signal timing per node
    transit_freq: dict[int, float] = field(default_factory=dict)
    signal_timing: dict[int, dict[str, Any]] = field(default_factory=dict)
    closures: set[int] = field(default_factory=set)        # roads closed by an accident or works
    tasks: list[dict[str, Any]] = field(default_factory=list)  # user-made Packet Tracer style tasks
    original_validation: dict[str, Any] | None = None


SESSIONS: dict[str, Session] = {}

app = FastAPI(title="Редактор УДС", version=__version__)
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
    reason: str | None = None


class BatchRequest(BaseModel):
    ops: list[OpRequest]
    label: str | None = None


class LoadRequest(BaseModel):
    way_id: int | None = None
    volume: float | None = Field(default=None, ge=0, le=20000)  # veh/h; null resets the way to its default
    multiplier: float | None = Field(default=None, gt=0, le=5)
    reset_all: bool = False


class SnapshotRequest(BaseModel):
    name: str = Field(default="", max_length=80)


# ----------------------------------------------------------------- helpers
def _session(doc_id: str) -> Session:
    """The open document, restored from its draft if the backend was restarted.

    Without the restore every request after a redeploy answered 404, and the
    browser saved that 88-byte error as the exported .osm.
    """
    s = SESSIONS.get(doc_id)
    if s is None and _draft_exists(doc_id):
        s = _materialize_draft(doc_id)
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
        "rev": s.revision,
    }
    if include_geo:
        out["geo"] = to_geojson(s.doc)
    return out


def _export_xml(s: Session, renumber: bool = True) -> bytes:
    """The .osm that leaves the editor: the graph plus signal timing as uds:signal:* tags."""
    timing = {nid: cfg for nid, cfg in s.signal_timing.items() if nid in s.doc.nodes}
    return s.doc.to_xml(renumber_new=renumber, extra_node_tags=signal_plans.timing_tags(s.doc, timing))


def _open(data: bytes, filename: str) -> dict[str, Any]:
    try:
        doc = OsmDocument.parse(data)
    except Exception as e:  # noqa: BLE001 - shown to the user
        raise HTTPException(400, f"не удалось прочитать OSM: {e}") from e
    sid = uuid.uuid4().hex[:12]
    SESSIONS[sid] = Session(id=sid, filename=filename, doc=doc,
                            signal_timing=signal_plans.timing_from_tags(doc))
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


def _analysis_path(doc_id: str) -> Path | None:
    paths = _draft_paths(doc_id)
    return paths[0].with_suffix(".analysis.json") if paths else None


def _save_analysis(s: Session) -> None:
    path = _analysis_path(s.id)
    if path is None:
        return
    data = {"loads": {str(k): v for k, v in s.loads.items()}, "multiplier": s.multiplier, "snapshots": s.snapshots,
            "transit_freq": {str(k): v for k, v in s.transit_freq.items()},
            "signal_timing": {str(k): v for k, v in s.signal_timing.items()},
            "closures": sorted(s.closures), "tasks": s.tasks}
    try:
        DRAFT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return


def _load_analysis(s: Session) -> None:
    path = _analysis_path(s.id)
    if path is None or not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        s.loads = {int(k): float(v) for k, v in data.get("loads", {}).items()}
        s.multiplier = float(data.get("multiplier", 1.0))
        s.snapshots = list(data.get("snapshots", []))
        s.transit_freq = {int(k): float(v) for k, v in data.get("transit_freq", {}).items()}
        s.signal_timing = {int(k): dict(v) for k, v in data.get("signal_timing", {}).items()}
        s.closures = {int(x) for x in data.get("closures", [])}
        s.tasks = list(data.get("tasks", []))
    except (OSError, ValueError, TypeError, AttributeError):
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
                apply_ops(s.doc, s.history, [(o[0], o[1]) for o in entry["ops"]], entry.get("label"),
                          entry.get("author"), entry.get("at"), entry.get("reason"))
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


_RESTORE_LOCK = threading.Lock()


def _materialize_draft(doc_id: str) -> Session:
    """Load the draft stored for this id once, even when several requests race."""
    existing = SESSIONS.get(doc_id)
    if existing is not None:
        return existing
    with _RESTORE_LOCK:
        existing = SESSIONS.get(doc_id)
        if existing is not None:
            return existing
        return _materialize_draft_unlocked(doc_id)


def _materialize_draft_unlocked(doc_id: str) -> Session:
    """Load the draft stored for this id. Never substitutes a different document."""
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
    s = Session(id=doc_id, filename=str(meta.get("filename") or "draft.osm"), doc=doc,
                signal_timing=signal_plans.timing_from_tags(doc))
    _replay_journal(s)
    _load_analysis(s)
    SESSIONS[doc_id] = s
    return s


def _delete_draft_files(doc_id: str) -> None:
    paths = _draft_paths(doc_id)
    if paths is None:
        return
    for path in (*paths, _journal_path(doc_id), _analysis_path(doc_id)):
        if path is not None and path.is_file():
            path.unlink()


def _changed(s: Session, tx: Any, entry: dict[str, Any] | None, client: str | None = None) -> dict[str, Any]:
    """Journal the edit, answer with only the map features it touched and send the
    same patch to everyone else editing this document."""
    changed = tx is not None and not tx.is_empty
    if entry is not None and tx is not None:
        _journal(s, entry)
    if changed:
        s.revision += 1
    out = _state(s, include_geo=False)
    out["patch"] = geo_patch(s.doc, tx.before, tx.after) if changed else None
    if changed:
        HUB.publish(s.id, {"type": "patch", "by": client, **out}, exclude=client)
    return out


def _safe_filename(name: str) -> str:
    name = Path(name).name or "export.osm"
    name = re.sub(r"[^\w.\-]+", "_", name)
    return name if name.lower().endswith(".osm") else name + ".osm"


# ------------------------------------------------------------------ routes
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__, "netconvert": find_netconvert() is not None}


def _sample_files() -> dict[str, Path]:
    """Files on the start screen: bundled samples, then anything put into data/."""
    out: dict[str, Path] = {}
    for d in (SAMPLES_DIR, DATA_DIR):
        if d.exists():
            for p in sorted(d.glob("*.osm")):
                out[p.name] = p
    return out


@app.get("/api/samples")
def samples() -> list[dict[str, Any]]:
    return [{"name": name, "size": p.stat().st_size} for name, p in _sample_files().items()]


@app.post("/api/samples/{name}/open")
def open_sample(name: str) -> Response:
    path = _sample_files().get(Path(name).name)
    if path is None or not path.is_file():
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
            out["adapters"] = compare_adapters(el.tags)
        elif kind == "node":
            out["lat"], out["lon"] = el.lat, el.lon  # type: ignore[union-attr]
            out["ways"] = [w.id for w in s.doc.ways_of_node(eid)]
        return out


@app.post("/api/documents/{doc_id}/ops")
def run_op(doc_id: str, req: OpRequest, x_client_id: str | None = Header(default=None),
           x_user_name: str | None = Header(default=None)) -> Response:
    s = _session(doc_id)
    with s.lock:
        try:
            result = apply_op(s.doc, s.history, req.op, req.args, req.label, author=_author(x_user_name),
                              reason=req.reason)
        except OpError as e:
            raise HTTPException(422, str(e)) from e
        tx = s.history.last_tx
        entry = {"t": "ops", "ops": [[req.op, req.args]], "label": req.label,
                 "author": tx.author if tx else None, "at": tx.at if tx else None, "reason": req.reason}
        return _json({"result": result, **_changed(s, s.history.last_tx, entry, x_client_id)})


@app.post("/api/documents/{doc_id}/fix-preview")
def run_fix_preview(doc_id: str, req: OpRequest) -> Response:
    """Dry run of a suggested fix for the review card; the document is left as it was."""
    s = _session(doc_id)
    with s.lock:
        try:
            return _json(fix_preview.preview(s.doc, req.op, req.args))
        except OpError as e:
            raise HTTPException(422, str(e)) from e


@app.post("/api/documents/{doc_id}/batch")
def run_batch(doc_id: str, req: BatchRequest, x_client_id: str | None = Header(default=None),
              x_user_name: str | None = Header(default=None)) -> Response:
    s = _session(doc_id)
    with s.lock:
        try:
            results = apply_ops(s.doc, s.history, [(o.op, o.args) for o in req.ops], req.label,
                                author=_author(x_user_name))
        except OpError as e:
            raise HTTPException(422, str(e)) from e
        tx = s.history.last_tx
        entry = {"t": "ops", "ops": [[o.op, o.args] for o in req.ops], "label": req.label,
                 "author": tx.author if tx else None, "at": tx.at if tx else None}
        return _json({"results": results, **_changed(s, s.history.last_tx, entry, x_client_id)})


@app.post("/api/documents/{doc_id}/undo")
def undo(doc_id: str, x_client_id: str | None = Header(default=None)) -> Response:
    s = _session(doc_id)
    with s.lock:
        tx = s.history.undo()
        return _json(_changed(s, tx, {"t": "undo"} if tx else None, x_client_id))


@app.post("/api/documents/{doc_id}/redo")
def redo(doc_id: str, x_client_id: str | None = Header(default=None)) -> Response:
    s = _session(doc_id)
    with s.lock:
        tx = s.history.redo()
        return _json(_changed(s, tx, {"t": "redo"} if tx else None, x_client_id))


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


_STATUS_RU = {"added": "добавлен", "deleted": "удалён", "modified": "изменён"}
_KIND_RU = {"node": "узел", "way": "ребро", "relation": "отношение"}


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


@app.get("/api/documents/{doc_id}/edit-log")
def edit_log(doc_id: str) -> Response:
    """Every applied edit as a Markdown table: the base for docs/fix-log.md.

    One row per undo step, oldest first, with time, author and the touched
    elements and the reason supplied when applying a reviewed fix.
    """
    s = _session(doc_id)
    with s.lock:
        rows = [tx.summary() for tx in s.history.undo_stack]
        filename = s.filename
    out = [f"# Журнал правок: {filename}", "",
           f"Правок: {len(rows)}. Время — UTC. Каждая правка выбрана и применена пользователем в редакторе.", "",
           "| # | Время | Автор | Правка | Объекты | Почему |", "| --- | --- | --- | --- | --- | --- |"]
    for n, r in enumerate(rows, 1):
        when = datetime.fromtimestamp(r["at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if r["at"] else ""
        refs = "; ".join(f"{_KIND_RU.get(x['kind'], x['kind'])} {x['id']}{' ' + x['name'] if x['name'] else ''} "
                         f"{_STATUS_RU.get(x['status'], x['status'])}" for x in r["refs"][:6])
        if r["count"] > 6:
            refs += f" и ещё {r['count'] - 6}"
        out.append(f"| {n} | {when} | {_md_cell(r['author'] or 'без имени')} | {_md_cell(r['label'])} | "
                   f"{_md_cell(refs)} | {_md_cell(r['reason'] or '')} |")
    return Response(content="\n".join(out) + "\n", media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="fix-log.md"'})


@app.get("/api/documents/{doc_id}/export")
def export(doc_id: str, filename: str = Query("team.osm"), renumber: bool = True) -> Response:
    s = _session(doc_id)
    with s.lock:
        data = _export_xml(s, renumber)
    name = _safe_filename(filename)
    return Response(content=data, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/documents/{doc_id}/check/sumo")
def check_sumo(doc_id: str) -> dict[str, Any]:
    s = _session(doc_id)
    with s.lock:
        xml = _export_xml(s)
    return run_netconvert(xml)


@app.post("/api/documents/{doc_id}/simulation/sumo")
def simulate_sumo(doc_id: str, vehicles: int = Query(60, ge=1, le=200)) -> dict:
    s = _session(doc_id)
    with s.lock:
        xml = _export_xml(s)
    try:
        return run_sumo(xml, vehicles)
    except SimulationError as e:
        raise HTTPException(e.status, str(e)) from e


@app.post("/api/documents/{doc_id}/simulation/matsim")
def import_matsim(doc_id: str, network: UploadFile, events: UploadFile,
                  crs: str = Query("EPSG:4326", pattern=r"^[Ee][Pp][Ss][Gg]:[0-9]{1,8}$"),
                  start: int | None = Query(None, ge=0, le=604800)) -> dict:
    _session(doc_id)
    try:
        return read_matsim(unpack_recording(network.file.read(FILE_LIMIT + 1)),
                           unpack_recording(events.file.read(FILE_LIMIT + 1)), crs, start)
    except SimulationError as e:
        raise HTTPException(e.status, str(e)) from e


@app.post("/api/documents/{doc_id}/simulation/webots")
def import_webots(doc_id: str, recording: UploadFile) -> dict:
    _session(doc_id)
    try:
        return read_webots(unpack_recording(recording.file.read(FILE_LIMIT + 1)))
    except SimulationError as e:
        raise HTTPException(e.status, str(e)) from e


@app.post("/api/documents/{doc_id}/check/organizer")
def check_organizer(doc_id: str) -> dict[str, Any]:
    s = _session(doc_id)
    try:
        token, team, base = credentials()
        with s.lock:
            xml = _export_xml(s)
        return upload_osm(xml, token, team, base)
    except OrganizerError as e:
        status = e.status if 400 <= e.status < 500 else 502
        raise HTTPException(status, e.message) from e


# ------------------------------------------------------------ load model
def _load_state(s: Session) -> dict[str, Any]:
    rows, det = _rows_detours(s)
    names = {r["id"]: r["name"] for r in rows}
    return {
        "multiplier": s.multiplier,
        "rows": rows,
        "summary": load_summary(rows),
        "closures": [{"id": wid, "name": names.get(wid, ""), **d} for wid, d in det.items()],
        "snapshots": [{k: v for k, v in snap.items() if k != "rows"} for snap in s.snapshots],
    }


def _snapshot(s: Session, sid: str) -> dict[str, Any]:
    if sid == "current":
        return make_snapshot("Текущее состояние", _rows(s), s.multiplier) | {"id": "current"}
    for snap in s.snapshots:
        if snap["id"] == sid:
            return snap
    raise HTTPException(404, "снимок не найден")


@app.get("/api/documents/{doc_id}/load")
def get_load(doc_id: str) -> Response:
    s = _session(doc_id)
    with s.lock:
        return _json(_load_state(s))


@app.post("/api/documents/{doc_id}/load")
def set_load(doc_id: str, req: LoadRequest, x_client_id: str | None = Header(default=None)) -> Response:
    s = _session(doc_id)
    with s.lock:
        if req.reset_all:
            s.loads.clear()
            s.multiplier = 1.0
        if req.multiplier is not None:
            s.multiplier = round(req.multiplier, 3)
        if req.way_id is not None:
            if req.way_id not in s.doc.ways:
                raise HTTPException(404, "ребро не найдено")
            if req.volume is None:
                s.loads.pop(req.way_id, None)
            else:
                s.loads[req.way_id] = float(req.volume)
        _save_analysis(s)
        HUB.publish(s.id, {"type": "load"}, exclude=x_client_id)
        return _json(_load_state(s))


@app.post("/api/documents/{doc_id}/load/snapshots")
def add_snapshot(doc_id: str, req: SnapshotRequest, x_client_id: str | None = Header(default=None)) -> Response:
    s = _session(doc_id)
    with s.lock:
        name = req.name.strip() or f"Состояние {len(s.snapshots) + 1}"
        s.snapshots.append(make_snapshot(name, _rows(s), s.multiplier))
        s.snapshots = s.snapshots[-20:]
        _save_analysis(s)
        HUB.publish(s.id, {"type": "load"}, exclude=x_client_id)
        return _json(_load_state(s))


@app.delete("/api/documents/{doc_id}/load/snapshots/{sid}")
def delete_snapshot(doc_id: str, sid: str, x_client_id: str | None = Header(default=None)) -> Response:
    s = _session(doc_id)
    with s.lock:
        s.snapshots = [snap for snap in s.snapshots if snap["id"] != sid]
        _save_analysis(s)
        HUB.publish(s.id, {"type": "load"}, exclude=x_client_id)
        return _json(_load_state(s))


@app.get("/api/documents/{doc_id}/load/compare")
def compare_load(doc_id: str, a: str, b: str = "current") -> Response:
    s = _session(doc_id)
    with s.lock:
        return _json(compare_snapshots(_snapshot(s, a), _snapshot(s, b)))


def _author(header: str | None) -> str | None:
    """Collaborator name from the X-User-Name header (URL-encoded so Cyrillic survives HTTP)."""
    return unquote(header).strip()[:32] or None if header else None


def _rows_detours(s: Session) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """Load model with transit vehicles, signal green shares and closures with detours."""
    timing = {nid: cfg for nid, cfg in s.signal_timing.items() if nid in s.doc.nodes}
    rows = road_rows(s.doc, s.loads, s.multiplier,
                     transit_pcu=transit.bus_pcu_by_way(s.doc, s.transit_freq),
                     capacity_factor=signal_plans.capacity_factors(s.doc, timing))
    closed = {w for w in s.closures if w in s.doc.ways}
    return rows, scenario.apply_closures(s.doc, rows, closed)


def _rows(s: Session) -> list[dict[str, Any]]:
    return _rows_detours(s)[0]


# ---------------------------------------------------------- transit
class FrequencyRequest(BaseModel):
    vehicles_per_hour: float | None = Field(default=None, ge=0, le=120)  # null: back to the OSM value


@app.get("/api/documents/{doc_id}/transit/routes")
def transit_routes(doc_id: str) -> Response:
    s = _session(doc_id)
    with s.lock:
        return _json({"routes": transit.routes(s.doc, s.transit_freq)})


@app.get("/api/documents/{doc_id}/transit/routes/{route_id}")
def transit_route(doc_id: str, route_id: int) -> Response:
    s = _session(doc_id)
    with s.lock:
        try:
            return _json(transit.route_geometry(s.doc, route_id, s.transit_freq))
        except KeyError as e:
            raise HTTPException(404, "маршрут не найден") from e


@app.post("/api/documents/{doc_id}/transit/routes/{route_id}")
def set_route_frequency(doc_id: str, route_id: int, req: FrequencyRequest,
                        x_client_id: str | None = Header(default=None)) -> Response:
    s = _session(doc_id)
    with s.lock:
        if route_id not in s.doc.relations:
            raise HTTPException(404, "маршрут не найден")
        if req.vehicles_per_hour is None:
            s.transit_freq.pop(route_id, None)
        else:
            s.transit_freq[route_id] = float(req.vehicles_per_hour)
        _save_analysis(s)
        HUB.publish(s.id, {"type": "load"}, exclude=x_client_id)
        return _json(transit.route_geometry(s.doc, route_id, s.transit_freq))


@app.get("/api/documents/{doc_id}/transit/stops/{node_id}")
def transit_stop(doc_id: str, node_id: int) -> Response:
    s = _session(doc_id)
    with s.lock:
        try:
            return _json(transit.stop_info(s.doc, node_id, s.transit_freq))
        except KeyError as e:
            raise HTTPException(404, "остановка не найдена") from e


# ---------------------------------------------------------- signal timing
class SignalRequest(BaseModel):
    cycle: int | None = Field(default=None, ge=30, le=180)
    main_share: float | None = Field(default=None, ge=0.2, le=0.8)
    optimize: bool = False
    reset: bool = False


def _signal_plan(s: Session, node_id: int) -> dict[str, Any]:
    n = s.doc.nodes.get(node_id)
    if n is None or (n.tags.get("highway") != "traffic_signals" and n.tags.get("crossing") != "traffic_signals"):
        raise HTTPException(404, "на узле нет светофора")
    rows = {r["id"]: r for r in _rows(s)}
    return signal_plans.plan(s.doc, node_id, s.signal_timing.get(node_id), rows)


@app.get("/api/documents/{doc_id}/signals/{node_id}")
def get_signal(doc_id: str, node_id: int) -> Response:
    s = _session(doc_id)
    with s.lock:
        return _json(_signal_plan(s, node_id))


@app.post("/api/documents/{doc_id}/signals/{node_id}")
def set_signal(doc_id: str, node_id: int, req: SignalRequest, x_client_id: str | None = Header(default=None)) -> Response:
    s = _session(doc_id)
    with s.lock:
        plan = _signal_plan(s, node_id)
        if req.reset:
            s.signal_timing.pop(node_id, None)
        else:
            cfg = dict(s.signal_timing.get(node_id, {"cycle": plan["cycle"], "main_share": plan["main_share"]}))
            if req.optimize:
                cfg.update(plan["webster"])
                cfg.pop("saturated", None)
            if req.cycle is not None:
                cfg["cycle"] = req.cycle
            if req.main_share is not None:
                cfg["main_share"] = round(req.main_share, 2)
            s.signal_timing[node_id] = cfg
        _save_analysis(s)
        HUB.publish(s.id, {"type": "load"}, exclude=x_client_id)
        return _json(_signal_plan(s, node_id))


# ------------------------------------------------------ before / after
def _original_validation(s: Session) -> dict[str, Any]:
    if s.original_validation is None:
        s.original_validation = validate(original_document(s.doc))["summary"]
    return s.original_validation


@app.get("/api/documents/{doc_id}/changes")
def get_changes(doc_id: str) -> Response:
    s = _session(doc_id)
    with s.lock:
        return _json({
            "changes": list_changes(s.doc),
            "before": _original_validation(s),
            "after": validate(s.doc)["summary"],
        })


# -------------------------------------------------------------- reports
def _report_data(s: Session, a: str | None, b: str | None) -> dict[str, Any]:
    comparison = None
    if a:
        comparison = compare_snapshots(_snapshot(s, a), _snapshot(s, b or "current"))
    elif len(s.snapshots) >= 2:
        comparison = compare_snapshots(s.snapshots[-2], s.snapshots[-1])
    elif len(s.snapshots) == 1:
        comparison = compare_snapshots(s.snapshots[0], _snapshot(s, "current"))
    rows = _rows(s)
    return {
        "title": "Отчёт по улично-дорожной сети",
        "filename": s.filename,
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "stats": network_stats(s.doc),
        "validation": validate(s.doc),
        "changes": list_changes(s.doc),
        "load": {"multiplier": s.multiplier, "rows": rows, "summary": load_summary(rows)},
        "od": scenario.od_matrix(s.doc),
        "comparison": comparison,
    }


@app.get("/api/documents/{doc_id}/report")
def report_data(doc_id: str, a: str | None = None, b: str | None = None) -> Response:
    s = _session(doc_id)
    with s.lock:
        data = _report_data(s, a, b)
    data["validation"] = {"summary": data["validation"]["summary"],
                          "matrix": report.issue_matrix(data["validation"]["issues"])}
    data["changes"] = {st: sum(1 for c in data["changes"] if c["status"] == st) for st in ("added", "modified", "deleted")}
    data["load"].pop("rows")
    return _json(data)


def _report_file(doc_id: str, a: str | None, b: str | None, kind: str) -> Response:
    s = _session(doc_id)
    with s.lock:
        data = _report_data(s, a, b)
    stem = _safe_filename(s.filename).removesuffix(".osm")
    if kind == "xlsx":
        body = report.build_xlsx(data)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif kind == "pdf":
        body = report.build_pdf(data)
        media = "application/pdf"
    else:
        body = report.build_docx(data)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="report_{stem}.{kind}"'})


@app.get("/api/documents/{doc_id}/report.xlsx")
def report_xlsx(doc_id: str, a: str | None = None, b: str | None = None) -> Response:
    return _report_file(doc_id, a, b, "xlsx")


@app.get("/api/documents/{doc_id}/report.pdf")
def report_pdf(doc_id: str, a: str | None = None, b: str | None = None) -> Response:
    return _report_file(doc_id, a, b, "pdf")


# ------------------------------------------------------ object report
def _object_report(s: Session, kind: str, eid: int) -> dict[str, Any]:
    el = s.doc.get(kind, eid)  # type: ignore[arg-type]
    if el is None:
        raise HTTPException(404, "объект не найден")
    name = el.tags.get("name", "")
    facts: list[list[Any]] = [["Тип", kind], ["ID", eid], ["Название", name or "—"]]
    obj: dict[str, Any] = {"title": f"{name or kind} ({kind}/{eid})", "facts": facts, "tables": []}
    if kind == "way":
        row = next((r for r in _rows(s) if r["id"] == eid), None)
        if row:
            facts += [["Класс", row["highway"]], ["Полос", row["lanes"]], ["Длина, м", row["length_m"]],
                      ["Скорость, км/ч", row["speed"]], ["Пропускная, авт/ч", row["capacity"]],
                      ["Нагрузка в пик, авт/ч", row["volume"]], ["v/c", row["vc"]], ["Уровень обслуживания", row["los"]],
                      ["Время проезда свободно / с нагрузкой, с", f"{row['t0_s']} / {row['t_s']}"],
                      ["Транспорт общего пользования, лег. экв./ч", row.get("transit_pcu", 0)]]
            obj["hourly"] = [round(row["volume"] * k) for k in report.HOURLY_PROFILE]
            obj["capacity"] = row["capacity"]
        routes = [r for r in transit.routes(s.doc, s.transit_freq)
                  if any(m.type == "way" and m.ref == eid for m in s.doc.relations[r["id"]].members)]
        if routes:
            obj["tables"].append(("Маршруты", ["Маршрут", "Вид", "Откуда", "Куда", "Машин/ч"],
                                  [[r["ref"], r["mode_label"], r["from"], r["to"], r["frequency"]] for r in routes]))
    elif kind == "node":
        tags = el.tags
        if tags.get("highway") == "traffic_signals" or tags.get("crossing") == "traffic_signals":
            plan = signal_plans.plan(s.doc, eid, s.signal_timing.get(eid), {r["id"]: r for r in _rows(s)})
            facts += [["Цикл, с", plan["cycle"]], ["Доля зелёного главному", plan["main_share"]]]
            obj["tables"].append(("Фазы", ["Фаза", "Зелёный, с", "Жёлтый, с"],
                                  [[p["name"], p["green_s"], p["yellow_s"]] for p in plan["phases"]]))
            obj["tables"].append(("Подходы", ["Улица", "Фаза", "Азимут", "v/c"],
                                  [[a["name"] or a["highway"], a["group"], a["bearing"], a["vc"]] for a in plan["approaches"]]))
        try:
            st = transit.stop_info(s.doc, eid, s.transit_freq)
            if st["routes"]:
                facts += [["Машин в час", st["vehicles_per_hour"]], ["Жителей в 400 м", st["residents_400m"]],
                          ["Загруженность", st["load_label"]]]
                obj["tables"].append(("Маршруты", ["Маршрут", "Вид", "Откуда", "Куда", "Машин/ч"],
                                      [[r["ref"], r["mode_label"], r["from"], r["to"], r["frequency"]] for r in st["routes"]]))
        except KeyError:
            pass
    obj["tables"].append(("Теги OSM", ["Ключ", "Значение"], [[k, v] for k, v in el.tags.items()]))
    return obj


@app.get("/api/documents/{doc_id}/objects/{kind}/{eid}/report.{fmt}")
def object_report(doc_id: str, kind: Literal["node", "way", "relation"], eid: int, fmt: Literal["xlsx", "pdf"]) -> Response:
    s = _session(doc_id)
    with s.lock:
        obj = _object_report(s, kind, eid)
    body = report.build_object_xlsx(obj) if fmt == "xlsx" else report.build_object_pdf(obj)
    media = "application/pdf" if fmt == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="object_{kind}_{eid}.{fmt}"'})


# ---------------------------------------------------- closures (accidents)
class ClosureRequest(BaseModel):
    way_id: int
    closed: bool = True


@app.post("/api/documents/{doc_id}/scenario/closures")
def set_closure(doc_id: str, req: ClosureRequest, x_client_id: str | None = Header(default=None)) -> Response:
    s = _session(doc_id)
    with s.lock:
        if req.way_id not in s.doc.ways:
            raise HTTPException(404, "ребро не найдено")
        (s.closures.add if req.closed else s.closures.discard)(req.way_id)
        _save_analysis(s)
        HUB.publish(s.id, {"type": "load"}, exclude=x_client_id)
        return _json(_load_state(s))


# ------------------------------------------------------------ OD matrix
@app.get("/api/documents/{doc_id}/od")
def get_od(doc_id: str, grid: int = Query(3, ge=2, le=6), trip_rate: float = Query(scenario.PEAK_TRIP_RATE, gt=0, le=1),
           beta: float = Query(scenario.BETA, gt=0, le=1)) -> Response:
    s = _session(doc_id)
    with s.lock:
        return _json(scenario.od_matrix(s.doc, grid, trip_rate, beta))


# ---------------------------------------------------------------- tasks
class TaskRequest(BaseModel):
    title: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1000)
    criteria: list[dict[str, Any]] = Field(default_factory=list, max_length=30)


def _all_tasks(s: Session) -> list[dict[str, Any]]:
    return scenario.builtin_tasks(s.doc) + s.tasks


@app.get("/api/documents/{doc_id}/tasks")
def list_tasks(doc_id: str) -> Response:
    s = _session(doc_id)
    with s.lock:
        return _json({"tasks": _all_tasks(s)})


@app.post("/api/documents/{doc_id}/tasks")
def create_task(doc_id: str, req: TaskRequest) -> Response:
    s = _session(doc_id)
    with s.lock:
        if not req.criteria:
            raise HTTPException(400, "добавьте хотя бы одно требование")
        s.tasks.append(scenario.new_task(req.title, req.description, req.criteria))
        _save_analysis(s)
        return _json({"tasks": _all_tasks(s)})


@app.delete("/api/documents/{doc_id}/tasks/{task_id}")
def delete_task(doc_id: str, task_id: str) -> Response:
    s = _session(doc_id)
    with s.lock:
        s.tasks = [t for t in s.tasks if t["id"] != task_id]
        _save_analysis(s)
        return _json({"tasks": _all_tasks(s)})


@app.post("/api/documents/{doc_id}/tasks/{task_id}/check")
def check_task(doc_id: str, task_id: str) -> Response:
    s = _session(doc_id)
    with s.lock:
        task = next((t for t in _all_tasks(s) if t["id"] == task_id), None)
        if task is None:
            raise HTTPException(404, "задание не найдено")
        return _json(scenario.check_task(s.doc, task, validate(s.doc), _rows(s)))


@app.get("/api/documents/{doc_id}/report.docx")
def report_docx(doc_id: str, a: str | None = None, b: str | None = None) -> Response:
    return _report_file(doc_id, a, b, "docx")


# --------------------------------------------------------- collaboration
@app.get("/api/documents/{doc_id}/peers")
def get_peers(doc_id: str) -> dict[str, Any]:
    _session(doc_id)
    return {"peers": HUB.peers(doc_id)}


@app.websocket("/api/documents/{doc_id}/ws")
async def collaborate(ws: WebSocket, doc_id: str, client: str = "", name: str = "") -> None:
    """Presence and edit stream for one document. Edits themselves go through HTTP."""
    s = SESSIONS.get(doc_id)
    if s is None and _draft_exists(doc_id):
        s = await asyncio.to_thread(_materialize_draft, doc_id)
    if s is None:
        await ws.close(code=4404)
        return
    await ws.accept()
    peer = Peer(id=client[:40] or uuid.uuid4().hex[:8], name=(name.strip() or "Участник")[:32],
                color=HUB.next_color(), queue=asyncio.Queue(), loop=asyncio.get_running_loop())
    others = HUB.join(doc_id, peer)
    await ws.send_json({"type": "hello", "you": peer.public(), "peers": others, "rev": s.revision})

    async def writer() -> None:
        while True:
            msg = await peer.queue.get()
            await ws.send_text(json.dumps(msg, ensure_ascii=False, separators=(",", ":")))

    task = asyncio.create_task(writer())
    try:
        while True:
            data = await ws.receive_json()
            if isinstance(data, dict) and data.get("type") == "presence":
                HUB.update_presence(doc_id, peer.id, clean_presence(data))
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        task.cancel()
        HUB.leave(doc_id, peer.id)


# --------------------------------------------------------- static frontend
if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        target = (STATIC_DIR / path).resolve()
        if path and target.is_file() and STATIC_DIR.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(STATIC_DIR / "index.html")
