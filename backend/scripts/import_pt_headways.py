"""Dev-only import of supplied route intervals; never needed by the runtime.

Usage: python backend/scripts/import_pt_headways.py --xlsx passports.xlsx --shp Routes.shp --out headways.json
Install pyshp separately for shapefile input. Integer Excel cells are not times.
"""

import argparse
from datetime import time
import json
import logging
from pathlib import Path
import re


def minutes(value):
    if isinstance(value, time):
        return value.hour * 60 + value.minute + value.second / 60
    if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?s", value):
        return float(value[:-1]) / 60
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xlsx", type=Path)
    ap.add_argument("--shp", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    rows = {}
    if args.xlsx:
        from openpyxl import load_workbook

        wb = load_workbook(args.xlsx, read_only=True, data_only=True)
        sheet = wb["Осень-весна 2022-2023гг"]
        for row in sheet.iter_rows(min_row=6, values_only=True):
            ref = str(row[1]).strip()
            interval = minutes(row[21]) if len(row) > 21 else None
            if interval is None or interval <= 0:
                logging.warning("Skipped invalid Excel interval for route %s", ref)
                continue
            key = ("bus", ref)
            if key in rows:
                logging.warning("Duplicate route %s: using smaller interval", ref)
            if key not in rows or interval < rows[key]["headway_min"]:
                rows[key] = dict(mode="bus", ref=ref, headway_min=interval, source="паспорт 2022–2023")
        wb.close()
    if args.shp:
        import shapefile

        with shapefile.Reader(str(args.shp)) as reader:
            for record in reader.iterRecords():
                r = record.as_dict()
                mode = {"A": "bus", "T": "tram", "TB": "trolleybus"}.get(r.get("TSYSCODE"))
                match = re.search(r"\d+\w*", r.get("LINENAME", ""))
                interval = minutes(r.get("HEADWAY"))
                if mode and match and interval and interval > 0:
                    ref = match[0]
                    rows[mode, ref] = dict(mode=mode, ref=ref, headway_min=interval, source="ИД7 Routes.shp")
    args.out.write_text(json.dumps(list(rows.values()), ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
