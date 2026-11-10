"""Optional local check: run SUMO netconvert on the exported file.

Lets the team see lane/junction/signal problems in seconds instead of waiting
for the organisers' server. Works only when `netconvert` is on PATH
(`pip install eclipse-sumo` provides it).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .check_report import interpret_netconvert


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


def _ascii_tmp_root() -> str | None:
    """A temp root whose full path is ASCII, or None to use the default.

    netconvert (Xerces) on Windows cannot open files under a non-ASCII path, and
    the default %TEMP% sits in the user profile, e.g. a Cyrillic user name.
    """
    default = os.path.realpath(tempfile.gettempdir())
    if default.isascii():
        return None
    for cand in (os.path.join(os.environ.get("PUBLIC", ""), "uds-tmp"),
                 os.path.join(os.environ.get("ProgramData", ""), "uds-tmp")):
        if not cand.isascii() or not os.path.isabs(cand):
            continue
        try:
            os.makedirs(cand, exist_ok=True)
            return cand
        except OSError:
            continue
    return None


def _netconvert_env() -> dict[str, str]:
    """Environment for netconvert: drop a non-ASCII SUMO_HOME.

    With SUMO_HOME under a Cyrillic path netconvert quits with "unknown error";
    without it the binary finds its data next to itself.
    """
    env = dict(os.environ)
    if not env.get("SUMO_HOME", "").isascii():
        env.pop("SUMO_HOME")
    return env


def run_netconvert(osm_xml: bytes, timeout: int = 180) -> dict:
    exe = find_netconvert()
    if exe is None:
        return {"available": False, "ok": None, "errors": [], "warnings": [],
                "message": "netconvert не найден. Установите: pip install eclipse-sumo"}
    with tempfile.TemporaryDirectory(dir=_ascii_tmp_root()) as tmp:
        src = Path(tmp) / "input.osm"
        out = Path(tmp) / "net.net.xml"
        src.write_bytes(osm_xml)
        # Relative names with cwd=tmp: netconvert cannot open paths with non-ASCII
        # characters on Windows (e.g. a Cyrillic user profile in %TEMP%).
        cmd = [exe, "--osm-files", src.name, "--output-file", out.name,
               "--geometry.remove", "--junctions.join", "--tls.guess-signals",
               "--no-turnarounds.except-deadend", "--verbose", "false"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=tmp,
                                  env=_netconvert_env())
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
        return {"available": True, "ok": proc.returncode == 0 and not errors,
                "report": interpret_netconvert(errors, warnings), "returncode": proc.returncode,
                "errors": errors[:100], "warnings": warnings[:200], "warning_count": len(warnings),
                "stats": stats, "message": "ok" if proc.returncode == 0 else "netconvert завершился с ошибкой"}
