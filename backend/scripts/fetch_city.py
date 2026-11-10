"""Build a whole-city road extract from Overpass, for the reproducibility demo.

The result is a plain .osm (roads for motor traffic, their nodes with signals
and crossings, and a <bounds> element), opened in the editor exactly as
downloaded: the point is to show detection at city scale, not to fix it.

Usage:
    python backend/scripts/fetch_city.py                      # Irkutsk -> data/irkutsk_city.osm
    python backend/scripts/fetch_city.py --bbox 55.70,37.50,55.80,37.70 --out data/moscow_center.osm

Public Overpass mirrors are often at capacity; the script tries each mirror
with exponential backoff. Use --tiles 2 to split a large bounding box into four requests.
"""

from __future__ import annotations

import argparse
import gzip
import http.client
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import NamedTemporaryFile

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


BBox = tuple[float, float, float, float]


def query(bbox: BBox) -> str:
    b = ",".join(f"{v:.8f}" for v in bbox)
    return (f'[out:xml][timeout:300][maxsize:1073741824];'
            f'(way["highway"~"^({ROADS})$"]({b}););(._;>;);out meta;')


def fetch(q: str, attempts: int = 4) -> ET.Element:
    data = urllib.parse.urlencode({"data": q}).encode()
    last = ""
    for attempt in range(attempts):
        for url in MIRRORS:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    body = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                root = ET.fromstring(body)
                if root.tag != "osm" or root.find("remark") is not None:
                    raise ValueError("Overpass did not return a complete OSM result")
                return root
            except (OSError, EOFError, http.client.HTTPException, ET.ParseError, ValueError) as exc:
                last = f"{url}: {exc}"
                print(f"  {last}", file=sys.stderr)
        if attempt + 1 < attempts:
            delay = 10 * 2**attempt
            print(f"attempt {attempt + 1} failed, retrying in {delay} s", file=sys.stderr)
            time.sleep(delay)
    raise SystemExit(f"Overpass unavailable: {last}")


def tiles(bbox: BBox, count: int):
    south, west, north, east = bbox
    lat = [south + (north - south) * i / count for i in range(count)] + [north]
    lon = [west + (east - west) * i / count for i in range(count)] + [east]
    for i in range(count):
        for j in range(count):
            yield lat[i], lon[j], lat[i + 1], lon[j + 1]


def city_extract(bbox: BBox, count: int) -> bytes:
    root = ET.Element("osm", {"version": "0.6", "generator": "uds-editor fetch_city.py"})
    south, west, north, east = bbox
    ET.SubElement(root, "bounds", minlat=str(south), minlon=str(west), maxlat=str(north), maxlon=str(east))
    objects: dict[str, dict[str, ET.Element]] = {"node": {}, "way": {}}
    for i, tile in enumerate(tiles(bbox, count), start=1):
        print(f"Overpass tile {i}/{count**2}", flush=True)
        result = fetch(query(tile))
        if i == 1 and (note := result.find("note")) is not None:
            root.append(note)
        for kind, seen in objects.items():
            for item in result.iterfind(kind):
                object_id = item.attrib["id"]
                previous = seen.get(object_id)
                if previous is None or int(item.get("version", "0")) > int(previous.get("version", "0")):
                    seen[object_id] = item
    if not objects["way"]:
        raise SystemExit("Overpass returned no roads")
    if any(nd.attrib["ref"] not in objects["node"] for way in objects["way"].values() for nd in way.iterfind("nd")):
        raise SystemExit("Incomplete OSM extract: at least one road node is missing")
    for seen in objects.values():
        root.extend(seen.values())
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", help="south,west,north,east (default: Irkutsk)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "data" / "irkutsk_city.osm"))
    ap.add_argument("--tiles", type=int, choices=range(1, 5), default=1, help="split the bbox into N by N queries")
    args = ap.parse_args()
    try:
        south, west, north, east = map(float, args.bbox.split(",")) if args.bbox else IRKUTSK
    except ValueError:
        ap.error("--bbox needs four numbers: south,west,north,east")
    bbox = south, west, north, east
    if not all(math.isfinite(v) for v in bbox) or not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        ap.error("--bbox must be a valid south,west,north,east rectangle")
    t0 = time.time()
    xml = city_extract(bbox, args.tiles)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=out.parent, prefix=f"{out.name}.", suffix=".part", delete=False) as stream:
        partial = Path(stream.name)
    try:
        partial.write_bytes(xml)
        partial.replace(out)
    finally:
        partial.unlink(missing_ok=True)
    print(f"{out}: {len(xml) / 1e6:.1f} MB, {xml.count(b'<node ')} nodes, {xml.count(b'<way ')} ways, "
          f"{time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
