"""Time the editor's main steps on an .osm through the real HTTP API (in-process).

Usage: python backend/scripts/bench.py data/irkutsk_city.osm [--no-sumo]

Prints open, validate, fix preview, SUMO check and export times plus the
validator's issue counts. Nothing is fixed: one preview is a dry run.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def timed(label: str, fn):
    t0 = time.perf_counter()
    out = fn()
    print(f"{label:<22} {time.perf_counter() - t0:7.2f} s")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("osm")
    ap.add_argument("--no-sumo", action="store_true")
    args = ap.parse_args()
    raw = Path(args.osm).read_bytes()
    print(f"{args.osm}: {len(raw) / 1e6:.1f} MB")
    c = TestClient(app)
    st = timed("open (parse + map)", lambda: c.post("/api/documents",
                                                     files={"file": (Path(args.osm).name, raw, "application/xml")}).json())
    doc_id = st["doc_id"]
    print(f"{'':<22} nodes {st['stats'].get('nodes')}, ways {st['stats'].get('ways')}")
    v = timed("validate", lambda: c.get(f"/api/documents/{doc_id}/validate").json())
    s = v["summary"]
    print(f"{'':<22} critical {s['counts']['critical']}, warning {s['counts']['warning']}, info {s['counts']['info']}, "
          f"components {s['components']}")
    fix = next((f for i in v["issues"] for f in (i.get("fixes") or [])), None)
    if fix:
        timed("fix preview (1)", lambda: c.post(f"/api/documents/{doc_id}/fix-preview",
                                                json={"op": fix["op"], "args": fix["args"]}))
    if not args.no_sumo:
        r = timed("SUMO check", lambda: c.post(f"/api/documents/{doc_id}/check/sumo").json())
        print(f"{'':<22} ok {r.get('ok')}, errors {len(r.get('errors', []))}, stats {r.get('stats')}")
    timed("export", lambda: c.get(f"/api/documents/{doc_id}/export").content)


if __name__ == "__main__":
    main()
