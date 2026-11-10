"""Build a whole-city road extract from Overpass, for the reproducibility demo.

The result is a plain .osm (roads for motor traffic, their nodes with signals
and crossings, and a <bounds> element), opened in the editor exactly as
downloaded: the point is to show detection at city scale, not to fix it.

Usage:
    python backend/scripts/fetch_city.py                      # Irkutsk -> data/irkutsk_city.osm
    python backend/scripts/fetch_city.py --bbox 55.70,37.50,55.80,37.70 --out data/moscow_center.osm

Public Overpass mirrors are often at capacity; the script tries each mirror
with exponential backoff.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Irkutsk city limits, roughly (south, west, north, east)
IRKUTSK = (52.20, 104.15, 52.36, 104.40)
MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
ROADS = ("motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|"
         "motorway_link|trunk_link|primary_link|secondary_link|tertiary_link")
USER_AGENT = "uds-editor-hackathon/1.0 (city extract script)"


def query(bbox: tuple[float, float, float, float]) -> str:
    b = ",".join(f"{v:.5f}" for v in bbox)
    return (f'[out:xml][timeout:300][maxsize:1073741824];'
            f'(way["highway"~"^({ROADS})$"]({b}););(._;>;);out meta;')


def fetch(q: str, attempts: int = 4) -> bytes:
    data = urllib.parse.urlencode({"data": q}).encode()
    delay = 10.0
    last = ""
    for attempt in range(attempts):
        for url in MIRRORS:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=400) as resp:
                    body = resp.read()
                if body.lstrip().startswith(b"<?xml") and b"<osm" in body[:500]:
                    return body
                last = f"{url}: not OSM ({body[:120]!r})"
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = f"{url}: {e}"
            print(f"  {last}", file=sys.stderr)
        print(f"attempt {attempt + 1} failed, retrying in {delay:.0f} s", file=sys.stderr)
        time.sleep(delay)
        delay *= 2
    raise SystemExit(f"Overpass unavailable: {last}")


def add_bounds(xml: bytes, bbox: tuple[float, float, float, float]) -> bytes:
    """Overpass output has no <bounds>; the validator needs it to tell the extract edge from dead ends."""
    s, w, n, e = bbox
    bounds = f'<bounds minlat="{s}" minlon="{w}" maxlat="{n}" maxlon="{e}"/>'.encode()
    return re.sub(rb"(<osm[^>]*>)", lambda m: m.group(1) + b"\n  " + bounds, xml, count=1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", help="south,west,north,east (default: Irkutsk)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "data" / "irkutsk_city.osm"))
    args = ap.parse_args()
    bbox = tuple(float(x) for x in args.bbox.split(",")) if args.bbox else IRKUTSK
    if len(bbox) != 4:
        raise SystemExit("--bbox needs four numbers: south,west,north,east")
    t0 = time.time()
    xml = add_bounds(fetch(query(bbox)), bbox)  # type: ignore[arg-type]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(xml)
    print(f"{out}: {len(xml) / 1e6:.1f} MB, {xml.count(b'<node ')} nodes, {xml.count(b'<way ')} ways, "
          f"{time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
