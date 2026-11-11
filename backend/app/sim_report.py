"""Simulation report: traffic metrics measured on the replayed trajectories, and exports.

Every vehicle sample of a recording (SUMO, MATSim or Webots) is snapped to the nearest
drivable OSM way, so the same metrics come out of every engine. Over the window T:

* intensity        N = n · 3600 / T        — vehicles that drove on the road, veh/h;
* throughput       Q = n_out · 3600 / T    — vehicles that left the road onto the next one, veh/h;
* capacity         P = lanes · P_lane · g  — the editor's capacity model (g: signal green share);
* load coefficient Z = N / P               — level of service by ODM 218.2.020-2012, table 1;
* speed, density, trip time, delay (time lost against the speed limit), standstill time,
  queues at intersections, completed trips and an estimate of CO2.
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections import Counter
from typing import Any

from .analysis import LOS_SOURCE_URL, los, projection
from .osm_model import OsmDocument

SNAP_M = 20.0          # a sample further than this from every road is not counted
CELL_M = 50.0
MAX_GAP_S = 2.0        # a longer pause between samples means the vehicle left the recording
STOP_MS = 0.5          # slower than this, m/s, the vehicle is standing
JUNCTION_M = 60.0      # a standing vehicle this close to a junction is queued at it
CAR_SPACE_M = 7.5      # length of a car plus the gap in a standing queue
CO2_IDLE_GS = 0.6      # g/s at idle: about 0.9 l/h of petrol

ENGINE_RU = {"sumo": "SUMO", "matsim": "MATSim", "webots": "Webots"}


def co2_g_per_km(kmh: float) -> float:
    """Average Euro 5 petrol car, a fit close to the COPERT 5 hot-emission curve."""
    return 110 + 2100 / max(kmh, 5.0)


ROAD_COLS = [
    ("id", "ID дороги"), ("name", "Название"), ("highway", "Класс"), ("lanes", "Полос"),
    ("length_m", "Длина, м"), ("vehicles", "Машин за окно"), ("intensity", "Интенсивность N, авт/ч"),
    ("throughput", "Пропущено Q, авт/ч"), ("capacity", "Пропускная способность P, авт/ч"),
    ("load", "Коэффициент загрузки Z"), ("los", "Уровень обслуживания"), ("speed_kmh", "Средняя скорость, км/ч"),
    ("free_speed_kmh", "Разрешённая скорость, км/ч"), ("density", "Плотность, авт/км"),
    ("travel_s", "Среднее время проезда, с"), ("delay_s", "Задержка на машину, с"),
    ("stopped_s", "Простой на машину, с"), ("max_queue", "Макс. очередь, авт"), ("co2_kg", "CO₂, кг"),
]
JUNCTION_COLS = [
    ("id", "ID узла"), ("name", "Перекрёсток"), ("signals", "Светофор"), ("max_queue", "Макс. очередь, авт"),
    ("max_queue_m", "Макс. длина очереди, м"), ("mean_queue", "Средняя очередь, авт"),
    ("stopped_vehicles", "Машин стояло"), ("stopped_s", "Суммарный простой, с"),
]
TRIP_COLS = [
    ("id", "Машина"), ("completed", "Доехала"), ("depart_s", "Въезд, с"), ("arrive_s", "Выезд, с"),
    ("trip_s", "Время поездки, с"), ("distance_m", "Путь, м"), ("speed_kmh", "Средняя скорость, км/ч"),
    ("delay_s", "Время ожидания (задержка), с"), ("stopped_s", "Время простоя, с"), ("co2_g", "CO₂, г"),
]
SUMMARY_RU = [
    ("engine", "Источник"), ("duration_s", "Окно наблюдения T, с"), ("vehicles", "Машин в записи"),
    ("avg_on_network", "Машин в сети в среднем"), ("max_on_network", "Машин в сети максимум"),
    ("completed", "Завершённых поездок"), ("completed_share", "Доля завершённых поездок"),
    ("avg_trip_s", "Среднее время поездки (завершённые), с"), ("avg_time_in_network_s", "Среднее время в сети, с"),
    ("avg_speed_kmh", "Средняя скорость, км/ч"), ("avg_delay_s", "Среднее время ожидания на машину, с"),
    ("avg_stopped_s", "Среднее время простоя на машину, с"), ("stopped_share", "Доля времени без движения"),
    ("roads_used", "Дорог с движением"), ("intensity_total", "Суммарная интенсивность ΣN, авт/ч"),
    ("throughput_total", "Суммарно пропущено ΣQ, авт/ч"), ("network_throughput", "Пропускная способность сети (выезды), авт/ч"),
    ("avg_load", "Средний коэффициент загрузки Z (по длине)"), ("max_load", "Максимальный Z"),
    ("congested", "Дорог с Z ≥ 0,9"), ("overloaded", "Дорог с Z ≥ 1"),
    ("max_queue", "Самая длинная очередь, авт"), ("queued_junctions", "Перекрёстков с очередью ≥ 3 авт"),
    ("veh_km", "Пробег, авт·км"), ("veh_hours", "Время в пути, авт·ч"),
    ("co2_kg", "Выбросы CO₂, кг"), ("co2_g_per_km", "Выбросы CO₂ на км, г/км"),
    ("matched_share", "Доля позиций на дорогах"),
]
PERCENT = {"completed_share", "stopped_share", "matched_share"}
METHOD = [
    ("Интенсивность движения N", "N = n · 3600 / T",
     "n — число машин, проехавших по дороге за окно наблюдения T, с"),
    ("Пропущено машин (фактическая пропускная) Q", "Q = n_вых · 3600 / T",
     "n_вых — машины, съехавшие с дороги на следующую за окно T"),
    ("Пропускная способность P", "P = полос · P_полосы · g",
     "P_полосы — по классу дороги (допущение сценария), g — доля зелёного на светофоре"),
    ("Коэффициент загрузки Z", "Z = N / P", "уровень обслуживания A–F по таблице 1 ОДМ 218.2.020-2012"),
    ("Средняя скорость V", "V = Σd / Σt · 3,6", "d — путь между соседними позициями машины, м; t — время, с"),
    ("Плотность q", "q = n̄ / L", "n̄ — среднее число машин на дороге, L — длина дороги, км"),
    ("Время поездки", "t = t_выезда − t_въезда", "для машин, покинувших сеть до конца окна (завершённые поездки)"),
    ("Время ожидания (задержка)", "D = Σ(t − d / V_разр)", "время, потерянное в заторах и на светофорах против разрешённой скорости"),
    ("Время простоя", f"Σt при v < {STOP_MS} м/с", "время, когда машина стоит"),
    ("Очередь у перекрёстка", f"max n_стоящих · {CAR_SPACE_M} м",
     f"стоящие машины в радиусе {JUNCTION_M:.0f} м от узла, где сходятся дороги"),
    ("Выбросы CO₂", f"E = Σ e(v) · d + {CO2_IDLE_GS} г/с · t_простоя",
     "e(v) = 110 + 2100 / v г/км — средний бензиновый автомобиль Евро-5, приближение кривой COPERT 5"),
]
ASSUMPTIONS = (
    "Показатели измерены по траекториям симуляции: каждая позиция машины привязана к ближайшей "
    f"дороге OSM в пределах {SNAP_M:.0f} м с учётом направления движения. Интенсивность приведена к часу "
    "по окну наблюдения, поэтому при коротком прогоне она чувствительна к числу машин в сценарии. "
    "Пропускная способность — модель редактора (полосы, класс дороги, светофоры), не измерение. "
    "Поездка считается завершённой, если машина покинула сеть до конца окна. "
    "Выбросы CO₂ — оценка для легкового автомобиля, а не измерение."
)


# ------------------------------------------------------------------ matching
class _Snapper:
    def __init__(self, doc: OsmDocument, ways: set[int]):
        self.proj = projection(doc)
        self.segs: list[tuple[int, float, float, float, float]] = []
        self.grid: dict[tuple[int, int], list[int]] = {}
        for wid in ways:
            w = doc.ways[wid]
            pts = [self.proj.to_xy(doc.nodes[r].lat, doc.nodes[r].lon) for r in w.nodes if r in doc.nodes]
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                i = len(self.segs)
                self.segs.append((wid, ax, ay, bx, by))
                for cell in _cells(min(ax, bx), min(ay, by), max(ax, bx), max(ay, by), SNAP_M):
                    self.grid.setdefault(cell, []).append(i)

    def match(self, x: float, y: float, heading: float) -> int | None:
        best, best_cost = None, SNAP_M * 2
        hx, hy = math.sin(math.radians(heading)), math.cos(math.radians(heading))
        for i in self.grid.get((math.floor(x / CELL_M), math.floor(y / CELL_M)), ()):
            wid, ax, ay, bx, by = self.segs[i]
            dx, dy = bx - ax, by - ay
            ll = dx * dx + dy * dy
            t = 0.0 if not ll else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / ll))
            d = math.hypot(x - ax - t * dx, y - ay - t * dy)
            if d > SNAP_M:
                continue
            # prefer the road the car drives along over a crossing street at a junction
            align = abs(hx * dx + hy * dy) / math.sqrt(ll) if ll else 1.0
            cost = d + 10 * (1 - align)
            if cost < best_cost:
                best, best_cost = wid, cost
        return best


def _cells(x0: float, y0: float, x1: float, y1: float, pad: float):
    for cx in range(math.floor((x0 - pad) / CELL_M), math.floor((x1 + pad) / CELL_M) + 1):
        for cy in range(math.floor((y0 - pad) / CELL_M), math.floor((y1 + pad) / CELL_M) + 1):
            yield cx, cy


class _Junctions:
    """Nodes where two or more drivable roads meet, and signalised nodes."""

    def __init__(self, doc: OsmDocument, ways: set[int], snap: _Snapper):
        uses: Counter[int] = Counter()
        names: dict[int, set[str]] = {}
        for wid in ways:
            w = doc.ways[wid]
            for k, ref in enumerate(w.nodes):
                end = k in (0, len(w.nodes) - 1)
                uses[ref] += 2 if end else 1   # a road passing through a node does not make a junction
                names.setdefault(ref, set()).add(w.tags.get("name", ""))
        self.items: dict[int, dict[str, Any]] = {}
        self.grid: dict[tuple[int, int], list[int]] = {}
        for ref, n in uses.items():
            node = doc.nodes.get(ref)
            if node is None:
                continue
            signals = node.tags.get("highway") == "traffic_signals"
            if n < 3 and not signals:
                continue
            x, y = snap.proj.to_xy(node.lat, node.lon)
            label = " / ".join(sorted(s for s in names[ref] if s)) or f"узел {ref}"
            self.items[ref] = {"x": x, "y": y, "name": label, "signals": signals}
            for cell in _cells(x, y, x, y, JUNCTION_M):
                self.grid.setdefault(cell, []).append(ref)

    def nearest(self, x: float, y: float) -> int | None:
        best, best_d = None, JUNCTION_M
        for ref in self.grid.get((math.floor(x / CELL_M), math.floor(y / CELL_M)), ()):
            j = self.items[ref]
            d = math.hypot(x - j["x"], y - j["y"])
            if d <= best_d:
                best, best_d = ref, d
        return best


# ------------------------------------------------------------------ metrics
def compute(doc: OsmDocument, rows: list[dict[str, Any]], recording: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    by_id = {r["id"]: r for r in rows if r["id"] in doc.ways}
    snap = _Snapper(doc, set(by_id))
    junctions = _Junctions(doc, set(by_id), snap)
    frames = recording["frames"]
    times = [f["time"] for f in frames]
    steps = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
    step = steps[len(steps) // 2] if steps else 1.0
    t_start = times[0] if times else 0.0
    t_end = times[-1] if times else 0.0
    # each frame stands for the time until the next one, the last for one step: T = 90 s for SUMO
    dts = [min(MAX_GAP_S, b - a) for a, b in zip(times, times[1:])] + [min(MAX_GAP_S, step)]
    duration = sum(dts) or 1.0

    roads: dict[int, dict[str, Any]] = {}
    jq: dict[int, dict[str, Any]] = {}
    cars: dict[str, dict[str, Any]] = {}
    last: dict[str, tuple[float, int | None, float, float]] = {}   # car -> (time, way, x, y)
    samples = matched = 0
    on_network = 0.0
    max_on = 0
    buckets = max(1, min(30, int(duration // 5) or 1))
    width = duration / buckets
    series = [{"cars": 0.0, "d": 0.0, "t": 0.0, "stopped": 0.0} for _ in range(buckets)]

    def road(wid: int) -> dict[str, Any]:
        return roads.setdefault(wid, {"cars": set(), "out": set(), "occ": 0.0, "d": 0.0, "t": 0.0, "enter": {},
                                      "stay": [], "delay": 0.0, "stopped": 0.0, "max_queue": 0, "co2": 0.0})

    for k, frame in enumerate(frames):
        t = frame["time"]
        dt = dts[k]
        b = min(buckets - 1, int((t - t_start) / width))
        n_cars = len(frame["vehicles"])
        max_on = max(max_on, n_cars)
        on_network += n_cars * dt
        series[b]["cars"] += n_cars * dt
        queue_road: Counter[int] = Counter()
        queue_junction: Counter[int] = Counter()
        for car, lon, lat, angle, *_kind in frame["vehicles"]:
            samples += 1
            x, y = snap.proj.to_xy(lat, lon)
            wid = snap.match(x, y, angle)
            v = cars.setdefault(car, {"first": t, "last": t, "d": 0.0, "t": 0.0, "delay": 0.0, "stopped": 0.0, "co2": 0.0})
            v["last"] = t
            prev = last.get(car)
            if prev and t - prev[0] > MAX_GAP_S:
                prev = None
            if wid is not None:
                matched += 1
                r = road(wid)
                r["cars"].add(car)
                r["occ"] += dt
                if not prev or prev[1] != wid:
                    r["enter"][car] = t
            if prev:
                d, gap = math.hypot(x - prev[2], y - prev[3]), t - prev[0]
                speed = d / gap if gap else 0.0
                standing = speed < STOP_MS
                free = by_id[wid]["speed"] if wid is not None else (by_id[prev[1]]["speed"] if prev[1] is not None else 50)
                delay = max(0.0, gap - d / (free / 3.6)) if free else 0.0
                co2 = CO2_IDLE_GS * gap if standing else co2_g_per_km(speed * 3.6) * d / 1000
                v["d"] += d
                v["t"] += gap
                v["delay"] += delay
                v["co2"] += co2
                series[b]["d"] += d
                series[b]["t"] += gap
                if standing:
                    v["stopped"] += gap
                    series[b]["stopped"] += gap
                    ref = junctions.nearest(x, y)
                    if ref is not None:
                        queue_junction[ref] += 1
                        jq.setdefault(ref, {"cars": set(), "stopped": 0.0, "max": 0, "area": 0.0})
                        jq[ref]["cars"].add(car)
                        jq[ref]["stopped"] += gap
                    if wid is not None:
                        queue_road[wid] += 1
                if wid is not None:
                    r = roads[wid]
                    r["delay"] += delay
                    r["co2"] += co2
                    if standing:
                        r["stopped"] += gap
                    if prev[1] == wid:
                        r["d"] += d
                        r["t"] += gap
                if prev[1] is not None and wid is not None and prev[1] != wid:
                    left = roads[prev[1]]
                    left["out"].add(car)
                    if car in left["enter"]:
                        left["stay"].append(prev[0] - left["enter"].pop(car))
            last[car] = (t, wid, x, y)
        for wid, n in queue_road.items():
            roads[wid]["max_queue"] = max(roads[wid]["max_queue"], n)
        for ref, n in queue_junction.items():
            jq[ref]["max"] = max(jq[ref]["max"], n)
            jq[ref]["area"] += n * dt

    # ---- roads
    road_rows = []
    for wid, a in roads.items():
        base = by_id[wid]
        n, n_out = len(a["cars"]), len(a["out"])
        intensity = n * 3600 / duration
        cap = base["capacity"] or 0
        z = intensity / cap if cap else 0.0
        length_km = base["length_m"] / 1000
        speed = a["d"] / a["t"] * 3.6 if a["t"] else None
        road_rows.append({
            "id": wid, "name": base["name"], "highway": base["highway"], "lanes": base["lanes"],
            "length_m": base["length_m"], "vehicles": n, "intensity": round(intensity),
            "throughput": round(n_out * 3600 / duration), "capacity": round(cap), "load": round(z, 3), "los": los(z),
            "speed_kmh": round(speed, 1) if speed is not None else None, "free_speed_kmh": base["speed"],
            "density": round(a["occ"] / duration / length_km, 1) if length_km else None,
            "travel_s": round(sum(a["stay"]) / len(a["stay"]), 1) if a["stay"] else None,
            "delay_s": round(a["delay"] / n, 1) if n else 0, "stopped_s": round(a["stopped"] / n, 1) if n else 0,
            "max_queue": a["max_queue"], "co2_kg": round(a["co2"] / 1000, 3),
        })
    road_rows.sort(key=lambda r: (-r["load"], -r["intensity"]))

    # ---- intersections
    junction_rows = []
    for ref, q in jq.items():
        j = junctions.items[ref]
        junction_rows.append({
            "id": ref, "name": j["name"], "signals": "да" if j["signals"] else "нет",
            "max_queue": q["max"], "max_queue_m": round(q["max"] * CAR_SPACE_M), "mean_queue": round(q["area"] / duration, 2),
            "stopped_vehicles": len(q["cars"]), "stopped_s": round(q["stopped"], 1),
        })
    junction_rows.sort(key=lambda r: (-r["max_queue"], -r["stopped_s"]))

    # ---- trips: a car that vanished before the end of the window reached its destination
    tail = max(MAX_GAP_S, 2 * step)
    trip_rows = []
    for car, v in cars.items():
        done = v["last"] < t_end - tail + 1e-9 and t_end - t_start > tail
        trip_rows.append({
            "id": car, "completed": "да" if done else "нет", "depart_s": round(v["first"] - t_start, 1),
            "arrive_s": round(v["last"] - t_start, 1) if done else None, "trip_s": round(v["last"] - v["first"], 1),
            "distance_m": round(v["d"]), "speed_kmh": round(v["d"] / v["t"] * 3.6, 1) if v["t"] else None,
            "delay_s": round(v["delay"], 1), "stopped_s": round(v["stopped"], 1), "co2_g": round(v["co2"], 1),
        })
    trip_rows.sort(key=lambda r: r["depart_s"])
    completed = [r for r in trip_rows if r["completed"] == "да"]

    total_d = sum(v["d"] for v in cars.values())
    total_t = sum(v["t"] for v in cars.values())
    total_co2 = sum(v["co2"] for v in cars.values())
    total_len = sum(r["length_m"] for r in road_rows) or 1.0
    n_cars = len(cars) or 1
    by_los = {letter: 0 for letter in "ABCDEF"}
    for r in road_rows:
        by_los[r["los"]] += 1
    summary = {
        "engine": ENGINE_RU.get(meta.get("engine", ""), meta.get("engine", "")),
        "duration_s": round(duration, 1), "vehicles": len(cars),
        "avg_on_network": round(on_network / duration, 1), "max_on_network": max_on,
        "completed": len(completed), "completed_share": round(len(completed) / n_cars, 3),
        "avg_trip_s": round(sum(r["trip_s"] for r in completed) / len(completed), 1) if completed else None,
        "avg_time_in_network_s": round(sum(r["trip_s"] for r in trip_rows) / n_cars, 1),
        "avg_speed_kmh": round(total_d / total_t * 3.6, 1) if total_t else None,
        "avg_delay_s": round(sum(v["delay"] for v in cars.values()) / n_cars, 1),
        "avg_stopped_s": round(sum(v["stopped"] for v in cars.values()) / n_cars, 1),
        "stopped_share": round(sum(v["stopped"] for v in cars.values()) / total_t, 3) if total_t else 0,
        "roads_used": len(road_rows),
        "intensity_total": sum(r["intensity"] for r in road_rows),
        "throughput_total": sum(r["throughput"] for r in road_rows),
        "network_throughput": round(len(completed) * 3600 / duration),
        "avg_load": round(sum(r["load"] * r["length_m"] for r in road_rows) / total_len, 3),
        "max_load": max((r["load"] for r in road_rows), default=0),
        "congested": sum(1 for r in road_rows if r["load"] >= 0.9),
        "overloaded": sum(1 for r in road_rows if r["load"] >= 1.0),
        "max_queue": max((r["max_queue"] for r in junction_rows), default=0),
        "queued_junctions": sum(1 for r in junction_rows if r["max_queue"] >= 3),
        "veh_km": round(total_d / 1000, 2), "veh_hours": round(total_t / 3600, 3),
        "co2_kg": round(total_co2 / 1000, 2),
        "co2_g_per_km": round(total_co2 / (total_d / 1000)) if total_d else None,
        "matched_share": round(matched / samples, 3) if samples else 0,
        "by_los": by_los,
    }
    timeline = [{"t": round(i * width, 1), "on_network": round(s["cars"] / width, 1),
                 "speed_kmh": round(s["d"] / s["t"] * 3.6, 1) if s["t"] else None,
                 "stopped_share": round(s["stopped"] / s["t"], 3) if s["t"] else None}
                for i, s in enumerate(series)]
    return {"title": "Отчёт о симуляции движения", "meta": meta, "summary": summary,
            "roads": road_rows, "junctions": junction_rows, "trips": trip_rows, "timeline": timeline}


# ------------------------------------------------------------------ exports
def _fmt(v: Any) -> Any:
    return "—" if v is None else v


def _summary_rows(rep: dict[str, Any]) -> list[list[Any]]:
    s = rep["summary"]
    return [[label, f"{round(s[k] * 100)} %" if k in PERCENT else _fmt(s[k])] for k, label in SUMMARY_RU]


def build_json(rep: dict[str, Any]) -> bytes:
    return json.dumps(rep, ensure_ascii=False, indent=2).encode("utf-8")


def build_csv(rep: dict[str, Any]) -> bytes:
    """One file, sections separated by a blank line. BOM and ';' so Excel with a Russian
    locale opens it in columns."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")

    def cell(v: Any) -> Any:
        return "" if v is None else str(v).replace(".", ",") if isinstance(v, float) else v

    w.writerow([rep["title"]])
    for label, value in _summary_rows(rep):
        w.writerow([label, cell(value)])
    for title, cols, rows in (("Дороги", ROAD_COLS, rep["roads"]), ("Перекрёстки", JUNCTION_COLS, rep["junctions"]),
                              ("Поездки", TRIP_COLS, rep["trips"])):
        w.writerow([])
        w.writerow([title])
        w.writerow([h for _, h in cols])
        for r in rows:
            w.writerow([cell(r[k]) for k, _ in cols])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def build_xlsx(rep: dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_font, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3A5F")

    def table(ws, top: int, headers: list[str], rows: list[list[Any]], left: int = 1) -> int:
        for j, h in enumerate(headers):
            c = ws.cell(row=top, column=left + j, value=h)
            c.font, c.fill = head_font, head_fill
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for i, r in enumerate(rows, start=1):
            for j, v in enumerate(r):
                ws.cell(row=top + i, column=left + j, value=v)
        return top + len(rows)

    def widths(ws, values: list[int]) -> None:
        for i, w in enumerate(values, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

    def bar(title: str, horizontal: bool = True) -> BarChart:
        ch = BarChart()
        ch.type = "bar" if horizontal else "col"
        ch.title, ch.height, ch.width = title, 10, 18
        return ch

    meta = rep["meta"]
    ws = wb.active
    ws.title = "Сводка"
    ws["A1"] = rep["title"]
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Файл: {meta.get('filename', '')} · {meta.get('generated', '')}"
    end = table(ws, 4, ["Показатель", "Значение"], _summary_rows(rep))
    los_top = end + 2
    table(ws, los_top, ["Уровень обслуживания", "Дорог"], [[k, v] for k, v in rep["summary"]["by_los"].items()])
    widths(ws, [46, 22])
    ch = bar("Дороги по уровню обслуживания (A — свободно, F — затор)", horizontal=False)
    ch.add_data(Reference(ws, min_col=2, min_row=los_top, max_row=los_top + 6), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=los_top + 1, max_row=los_top + 6))
    ws.add_chart(ch, "D4")

    ws = wb.create_sheet("Дороги")
    roads = rep["roads"]
    table(ws, 1, [h for _, h in ROAD_COLS], [[r[k] for k, _ in ROAD_COLS] for r in roads])
    widths(ws, [12, 28, 14, 7, 10, 10, 14, 14, 16, 14, 12, 12, 12, 12, 14, 12, 12, 12, 10])
    ws.freeze_panes = "C2"
    n = min(15, len(roads))
    if n:
        col = {k: i for i, (k, _) in enumerate(ROAD_COLS, start=1)}
        ch = bar("Самые загруженные дороги: N и P, авт/ч")
        ch.add_data(Reference(ws, min_col=col["intensity"], min_row=1, max_row=1 + n), titles_from_data=True)
        ch.add_data(Reference(ws, min_col=col["capacity"], min_row=1, max_row=1 + n), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=2, min_row=2, max_row=1 + n))
        ws.add_chart(ch, "U2")
        ch = bar("Коэффициент загрузки Z")
        ch.add_data(Reference(ws, min_col=col["load"], min_row=1, max_row=1 + n), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=2, min_row=2, max_row=1 + n))
        ws.add_chart(ch, "U24")

    ws = wb.create_sheet("Перекрёстки")
    jr = rep["junctions"]
    table(ws, 1, [h for _, h in JUNCTION_COLS], [[r[k] for k, _ in JUNCTION_COLS] for r in jr])
    widths(ws, [12, 40, 10, 14, 16, 14, 12, 14])
    n = min(15, len(jr))
    if n:
        ch = bar("Длина очередей у перекрёстков, авт")
        ch.add_data(Reference(ws, min_col=4, min_row=1, max_row=1 + n), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=2, min_row=2, max_row=1 + n))
        ws.add_chart(ch, "J2")

    ws = wb.create_sheet("Поездки")
    table(ws, 1, [h for _, h in TRIP_COLS], [[r[k] for k, _ in TRIP_COLS] for r in rep["trips"]])
    widths(ws, [14, 10, 10, 10, 14, 10, 14, 16, 14, 10])
    ws.freeze_panes = "B2"

    ws = wb.create_sheet("Динамика")
    tl = rep["timeline"]
    end = table(ws, 1, ["Время, с", "Машин в сети", "Средняя скорость, км/ч", "Доля стоящих"],
                [[p["t"], p["on_network"], p["speed_kmh"], p["stopped_share"]] for p in tl])
    widths(ws, [12, 16, 22, 14])
    if tl:
        ch = LineChart()
        ch.title, ch.height, ch.width = "Машин в сети и скорость во времени", 9, 18
        ch.add_data(Reference(ws, min_col=2, max_col=3, min_row=1, max_row=end), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=2, max_row=end))
        ws.add_chart(ch, "F2")

    ws = wb.create_sheet("Методика")
    end = table(ws, 1, ["Величина", "Формула", "Обозначения"], [list(m) for m in METHOD])
    ws.cell(row=end + 2, column=1, value=ASSUMPTIONS).alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=end + 2, start_column=1, end_row=end + 2, end_column=3)
    ws.row_dimensions[end + 2].height = 90
    link = ws.cell(row=end + 3, column=1, value="ОДМ 218.2.020-2012, таблица 1")
    link.hyperlink, link.style = LOS_SOURCE_URL, "Hyperlink"
    widths(ws, [40, 34, 80])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _charts(rep: dict[str, Any]) -> dict[str, io.BytesIO]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .report import _png

    out: dict[str, io.BytesIO] = {}
    blue, orange, grey = "#2563EB", "#EA580C", "#94A3B8"

    def hbar(names: list[str], height: float):
        fig, ax = plt.subplots(figsize=(7, height))
        ax.spines[["top", "right"]].set_visible(False)
        return fig, ax, list(range(len(names)))

    top = rep["roads"][:12]
    if top:
        names = [(r["name"] or f"#{r['id']}")[:24] for r in top]
        fig, ax, ys = hbar(names, 0.45 * len(top) + 1)
        ax.barh([y - 0.2 for y in ys], [r["intensity"] for r in top], height=0.4, color=blue, label="Интенсивность N")
        ax.barh([y + 0.2 for y in ys], [r["capacity"] for r in top], height=0.4, color=grey, label="Пропускная способность P")
        ax.set_yticks(ys, names)
        ax.invert_yaxis()
        ax.set_xlabel("авт/ч")
        ax.legend(frameon=False)
        out["flow"] = _png(fig)
        plt.close(fig)

        fig, ax, ys = hbar(names, 0.4 * len(top) + 1)
        ax.barh(ys, [r["load"] for r in top], color=[orange if r["load"] >= 0.9 else blue for r in top])
        ax.axvline(1.0, color=orange, linestyle="--", linewidth=1)
        ax.set_yticks(ys, names)
        ax.invert_yaxis()
        ax.set_xlabel("Коэффициент загрузки Z = N / P")
        out["load"] = _png(fig)
        plt.close(fig)

    jtop = [j for j in rep["junctions"] if j["max_queue"]][:10]
    if jtop:
        names = [j["name"][:30] for j in jtop]
        fig, ax, ys = hbar(names, 0.4 * len(jtop) + 1)
        ax.barh(ys, [j["max_queue"] for j in jtop], color=[orange if j["signals"] == "да" else blue for j in jtop])
        ax.set_yticks(ys, names)
        ax.invert_yaxis()
        ax.set_xlabel("Максимальная очередь, авт (оранжевый — светофор)")
        out["queues"] = _png(fig)
        plt.close(fig)

    tl = rep["timeline"]
    if tl:
        fig, ax = plt.subplots(figsize=(7, 2.8))
        ax.plot([p["t"] for p in tl], [p["on_network"] for p in tl], color=blue, label="Машин в сети")
        ax.set_xlabel("время, с")
        ax.set_ylabel("машин")
        ax2 = ax.twinx()
        ax2.plot([p["t"] for p in tl], [p["speed_kmh"] for p in tl], color=orange, label="Скорость, км/ч")
        ax2.set_ylabel("км/ч")
        fig.legend(frameon=False, loc="upper right")
        ax.spines[["top"]].set_visible(False)
        out["timeline"] = _png(fig)
        plt.close(fig)
    return out


_SHORT = [("name", "Дорога"), ("intensity", "N, авт/ч"), ("throughput", "Q, авт/ч"),
          ("capacity", "P, авт/ч"), ("load", "Z"), ("los", "LOS"), ("speed_kmh", "V, км/ч"), ("max_queue", "Очередь")]
_JSHORT = [("name", "Перекрёсток"), ("signals", "Светофор"), ("max_queue", "Макс., авт"),
           ("max_queue_m", "Макс., м"), ("mean_queue", "Средн., авт"), ("stopped_s", "Простой, с")]


def _rows(items: list[dict[str, Any]], cols: list[tuple[str, str]], limit: int) -> list[list[Any]]:
    return [[(r["name"] or f"#{r['id']}") if k == "name" else _fmt(r[k]) for k, _ in cols] for r in items[:limit]]


def _intro(rep: dict[str, Any]) -> str:
    s, m = rep["summary"], rep["meta"]
    extra = f", {m['vehicles_requested']} машин в сценарии" if m.get("vehicles_requested") else ""
    trip = f"{s['avg_trip_s']} с" if s["avg_trip_s"] is not None else "—"
    return (f"Источник: {s['engine']}{extra}. Окно наблюдения {s['duration_s']} с, машин в записи {s['vehicles']}, "
            f"в сети в среднем {s['avg_on_network']}, завершили поездку {s['completed']} "
            f"(среднее время поездки {trip}). Средняя скорость {_fmt(s['avg_speed_kmh'])} км/ч, "
            f"время ожидания {s['avg_delay_s']} с и простой {s['avg_stopped_s']} с на машину. "
            f"Средний коэффициент загрузки Z = {s['avg_load']}, максимальный {s['max_load']}; "
            f"дорог с Z ≥ 0,9 — {s['congested']}. Самая длинная очередь — {s['max_queue']} авт. "
            f"Выбросы CO₂ — {s['co2_kg']} кг.")


def _sections(rep: dict[str, Any]) -> list[tuple[str, list[str], list[list[Any]], list[str], str | None]]:
    """(heading, table headers, table rows, chart keys, note) shared by Word and PDF."""
    more = len(rep["roads"]) - 25
    return [
        ("2. Интенсивность, пропускная способность и загрузка", [h for _, h in _SHORT], _rows(rep["roads"], _SHORT, 25),
         ["flow", "load"], f"…и ещё {more} дорог. Полный список — в Excel или CSV." if more > 0 else None),
        ("3. Очереди у перекрёстков", [h for _, h in _JSHORT], _rows(rep["junctions"], _JSHORT, 15), ["queues"],
         None if rep["junctions"] else "Машины не стояли у перекрёстков."),
        ("4. Динамика", [], [], ["timeline"], None),
    ]


def build_docx(rep: dict[str, Any]) -> bytes:
    from docx import Document
    from docx.shared import Cm, Pt

    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10.5)
    charts = _charts(rep)

    def tbl(headers: list[str], rows: list[list[Any]]) -> None:
        t = doc.add_table(rows=1, cols=len(headers))
        t.style = "Light Grid Accent 1"
        for i, h in enumerate(headers):
            t.rows[0].cells[i].text = str(h)
        for r in rows:
            cells = t.add_row().cells
            for i, v in enumerate(r):
                cells[i].text = "" if v is None else str(v)

    meta = rep["meta"]
    doc.add_heading(rep["title"], 0)
    doc.add_paragraph(f"Файл: {meta.get('filename', '')} · сформирован {meta.get('generated', '')}")
    doc.add_heading("1. Сводка", 1)
    doc.add_paragraph(_intro(rep))
    tbl(["Показатель", "Значение"], _summary_rows(rep))
    for heading, headers, rows, keys, note in _sections(rep):
        doc.add_heading(heading, 1)
        for key in keys:
            if key in charts:
                doc.add_picture(charts[key], width=Cm(16))
        if rows:
            tbl(headers, rows)
        if note:
            doc.add_paragraph(note)
    doc.add_heading("Методика", 1)
    for name, formula, where in METHOD:
        par = doc.add_paragraph()
        par.add_run(f"{name}: ").bold = True
        par.add_run(f"{formula} — {where}.")
    doc.add_paragraph(ASSUMPTIONS)
    doc.add_paragraph(LOS_SOURCE_URL)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def build_pdf(rep: dict[str, Any]) -> bytes:
    from reportlab.platypus import Paragraph

    from .report import _pdf_build, _pdf_image, _pdf_styles, _pdf_table

    st = _pdf_styles()
    charts = _charts(rep)
    meta = rep["meta"]
    story: list = [Paragraph(rep["title"], st["title"]),
                   Paragraph(f"Файл: {meta.get('filename', '')} · сформирован {meta.get('generated', '')}", st["p"]),
                   Paragraph("1. Сводка", st["h1"]), Paragraph(_intro(rep), st["p"]),
                   _pdf_table([["Показатель", "Значение"]] + _summary_rows(rep), st, widths=[300, 180])]
    for heading, headers, rows, keys, note in _sections(rep):
        story.append(Paragraph(heading, st["h1"]))
        story += [_pdf_image(charts[k], 16) for k in keys if k in charts]
        if rows:
            story.append(_pdf_table([headers] + rows, st))
        if note:
            story.append(Paragraph(note, st["p"]))
    story.append(Paragraph("Методика", st["h1"]))
    for name, formula, where in METHOD:
        story.append(Paragraph(f"<b>{name}:</b> {formula} — {where}.", st["p"]))
    story += [Paragraph(ASSUMPTIONS, st["p"]),
              Paragraph(f'<a href="{LOS_SOURCE_URL}">ОДМ 218.2.020-2012, таблица 1</a>', st["p"])]
    return _pdf_build(story)


BUILDERS = {
    "xlsx": (build_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "pdf": (build_pdf, "application/pdf"),
    "docx": (build_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "csv": (build_csv, "text/csv; charset=utf-8"),
    "json": (build_json, "application/json"),
}
