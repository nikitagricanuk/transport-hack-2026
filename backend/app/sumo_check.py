"""Optional local check: run SUMO netconvert on the exported file.

Lets the team see lane/junction/signal problems in seconds instead of waiting
for the organisers' server. Works only when `netconvert` is on PATH
(`pip install eclipse-sumo` provides it).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def find_netconvert() -> str | None:
    exe = shutil.which("netconvert")
    if exe:
        return exe
    try:
        import sumo  # type: ignore[import-not-found]

        cand = Path(sumo.SUMO_HOME) / "bin" / "netconvert"
        for p in (cand, cand.with_suffix(".exe")):
            if p.exists():
                return str(p)
    except Exception:
        pass
    return None


def run_netconvert(osm_xml: bytes, timeout: int = 180) -> dict:
    exe = find_netconvert()
    if exe is None:
        return {"available": False, "ok": None, "errors": [], "warnings": [],
                "message": "netconvert не найден. Установите: pip install eclipse-sumo"}
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input.osm"
        out = Path(tmp) / "net.net.xml"
        src.write_bytes(osm_xml)
        cmd = [exe, "--osm-files", str(src), "--output-file", str(out),
               "--geometry.remove", "--junctions.join", "--tls.guess-signals",
               "--no-turnarounds.except-deadend", "--verbose", "false"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"available": True, "ok": False, "errors": ["netconvert: превышено время ожидания"],
                    "warnings": [], "message": "timeout"}
        log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        errors = [line.strip() for line in log.splitlines() if line.strip().startswith("Error")]
        warnings = [line.strip() for line in log.splitlines() if line.strip().startswith("Warning")]
        stats = {}
        if out.exists():
            text = out.read_text(encoding="utf-8", errors="ignore")
            stats = {
                "edges": len(re.findall(r"<edge id=\"(?!:)", text)),
                "junctions": len(re.findall(r"<junction ", text)),
                "traffic_lights": len(re.findall(r"<tlLogic ", text)),
            }
        return {"available": True, "ok": proc.returncode == 0 and not errors, "returncode": proc.returncode,
                "errors": errors[:100], "warnings": warnings[:200], "warning_count": len(warnings),
                "stats": stats, "message": "ok" if proc.returncode == 0 else "netconvert завершился с ошибкой"}
