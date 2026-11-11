"""Run a small SUMO scenario or normalize simulator recordings for map playback."""

from __future__ import annotations

import csv
import gzip
import io
import math
import random
import subprocess
import tempfile
from pathlib import Path
from threading import BoundedSemaphore
from xml.etree import ElementTree as ET

from .sumo_check import _ascii_tmp_root, _netconvert_env, find_sumo_binary

FILE_LIMIT = 20 * 1024 * 1024
DECODED_LIMIT = 100 * 1024 * 1024
MAX_SAMPLES = 60_000
MAX_VEHICLES = 500
MAX_FRAMES = 3600
_SUMO_SLOT = BoundedSemaphore()

Vehicle = tuple  # (id, lon, lat, angle) or (id, lon, lat, angle, kind)


class SimulationError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _recording(frames: dict[float, list[Vehicle]]) -> dict:
    if not any(frames.values()):
        raise SimulationError("В записи нет позиций машин.")
    origin = min(frames)
    return {"frames": [{"time": round(time - origin, 6), "vehicles": cars}
                       for time, cars in sorted(frames.items())]}


def _append(frames: dict[float, list[Vehicle]], time: float, car: Vehicle) -> None:
    if len(frames) >= MAX_FRAMES and time not in frames:
        raise SimulationError("Слишком много кадров. Выберите более короткую запись.")
    row = frames.setdefault(time, [])
    if len(row) >= MAX_VEHICLES:
        raise SimulationError("В одном кадре больше 500 машин. Уменьшите выборку.")
    row.append(car)


def _number(value: str | float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite number")
    return number


def _point(lon: str | float, lat: str | float) -> tuple[float, float]:
    x, y = _number(lon), _number(lat)
    if not (-180 <= x <= 180 and -90 <= y <= 90):
        raise ValueError("not WGS84")
    return x, y


def unpack_recording(data: bytes) -> bytes:
    if len(data) > FILE_LIMIT:
        raise SimulationError("Файл записи больше 20 МиБ.", 413)
    if data.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as src:
                data = src.read(DECODED_LIMIT + 1)
        except (OSError, EOFError) as e:
            raise SimulationError("Повреждённый архив .gz.") from e
        if len(data) > DECODED_LIMIT:
            raise SimulationError("Распакованный файл больше 100 МиБ.", 413)
    return data


def read_webots(data: bytes) -> dict:
    """Replay WGS84 samples from a Webots controller; heading is clockwise from north."""
    frames: dict[float, list[Vehicle]] = {}
    seen: set[tuple[float, str]] = set()
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        if not {"time", "id", "lon", "lat", "angle"}.issubset(reader.fieldnames or []):
            raise ValueError("missing columns")
        for row in reader:
            time, car_id = round(_number(row["time"]), 6), row["id"]
            if time < 0 or not car_id or (time, car_id) in seen:
                raise ValueError("invalid time or id")
            if len(seen) >= MAX_SAMPLES:
                raise SimulationError("Запись превышает 60 000 позиций машин.")
            seen.add((time, car_id))
            _append(frames, time, (car_id, *_point(row["lon"], row["lat"]), _number(row["angle"]) % 360))
    except SimulationError:
        raise
    except (ValueError, TypeError, csv.Error) as e:
        raise SimulationError("Ожидается CSV Webots: time,id,lon,lat,angle (координаты WGS84).") from e
    return _recording(frames)


def _coordinate_transform(crs: str):
    if crs.strip().upper() == "EPSG:4326":
        return _point
    try:
        from pyproj import Transformer
        from pyproj.exceptions import ProjError
    except ImportError as e:
        raise SimulationError("Для преобразования системы координат установите pyproj.", 503) from e
    try:
        transform = Transformer.from_crs(crs.strip(), "EPSG:4326", always_xy=True)
    except ProjError as e:
        raise SimulationError("Укажите систему координат сети, например EPSG:32648.") from e
    return lambda x, y: _point(*transform.transform(float(x), float(y)))


def read_matsim(network: bytes, events: bytes, crs: str, start: int | None) -> dict:
    """Interpolate car positions between MATSim's link enter/leave timestamps."""
    transform = _coordinate_transform(crs)
    frames: dict[float, list[Vehicle]] = {}
    active: dict[str, tuple[str, float, float]] = {}
    road_cars: set[str] = set()
    origin = start
    count = 0
    try:
        net = ET.fromstring(network)
        if net.tag != "network":
            raise ValueError("network root")
        nodes = {node.attrib["id"]: transform(node.attrib["x"], node.attrib["y"])
                 for node in net.findall("./nodes/node")}
        links = {link.attrib["id"]: (nodes[link.attrib["from"]], nodes[link.attrib["to"]])
                 for link in net.findall("./links/link")
                 if "car" in link.get("modes", "car").split(",")}
        stream = iter(ET.iterparse(io.BytesIO(events), events=("start", "end")))
        _, root = next(stream)
        if root.tag != "events":
            raise ValueError("events root")
        for action, event in stream:
            if action != "end" or event.tag != "event":
                continue
            a = event.attrib
            kind, car, link = a.get("type"), a.get("vehicle"), a.get("link")
            if car and kind in {"vehicle enters traffic", "entered link", "left link", "vehicle leaves traffic"}:
                time = _number(a["time"])
                if origin is not None and time > origin + 120:
                    # An exit just beyond the window can still close an active segment.
                    if time > origin + 420:
                        break
                if kind == "vehicle enters traffic":
                    if a.get("networkMode") == "car":
                        road_cars.add(car)
                        if origin is None:
                            origin = math.floor(time)
                        active[car] = (link or "", time, _number(a.get("relativePosition", "0")))
                elif kind == "entered link" and car in road_cars:
                    active[car] = (link or "", time, 0)
                else:
                    entry = active.pop(car, None)
                    if kind == "vehicle leaves traffic":
                        road_cars.discard(car)
                    if entry and entry[0] == link and link in links and origin is not None and time > entry[1]:
                        (x, y), (end_x, end_y) = links[link]
                        dx, dy = end_x - x, end_y - y
                        angle = math.degrees(math.atan2(dx * math.cos(math.radians(y)), dy)) % 360
                        end_pos = _number(a.get("relativePosition", "1")) if kind == "vehicle leaves traffic" else 1
                        for tick in range(max(origin, math.ceil(entry[1])), min(origin + 120, math.ceil(time))):
                            ratio = entry[2] + (end_pos - entry[2]) * (tick - entry[1]) / (time - entry[1])
                            ratio = min(1, max(0, ratio))
                            count += 1
                            if count > MAX_SAMPLES:
                                raise SimulationError("Слишком плотный поток MATSim для просмотра. Выберите другой интервал.")
                            _append(frames, tick, (car, x + dx * ratio, y + dy * ratio, angle))
            root.clear()
    except SimulationError:
        raise
    except (ValueError, KeyError, ET.ParseError, StopIteration) as e:
        raise SimulationError("Не удалось прочитать сеть или события MATSim. Проверьте XML и систему координат.") from e
    if frames:
        for tick in range(int(min(frames)), int(max(frames))):
            frames.setdefault(tick, [])
    return _recording(frames)


# Vehicle kinds the editor can simulate: SUMO vehicle class and vType attributes.
# Lengths and speeds are typical values for the vehicle class, not calibrated.
FLEET = {
    "car": {"label": "Легковые", "vClass": "passenger", "length": "4.5", "maxSpeed": "33"},
    "bus": {"label": "Автобусы", "vClass": "bus", "length": "12", "maxSpeed": "22", "guiShape": "bus"},
    "tram": {"label": "Трамваи", "vClass": "tram", "length": "26", "maxSpeed": "19", "guiShape": "rail/railcar"},
    "truck": {"label": "Грузовые", "vClass": "truck", "length": "9", "maxSpeed": "25", "guiShape": "truck"},
    "motorcycle": {"label": "Мотоциклы", "vClass": "motorcycle", "length": "2.2", "maxSpeed": "33"},
    "bicycle": {"label": "Велосипеды", "vClass": "bicycle", "length": "1.8", "maxSpeed": "6", "guiShape": "bicycle"},
}
MAX_FLEET = 300


def parse_fleet(value: str | None, vehicles: int) -> dict[str, int]:
    """«car:40,bus:10» → {"car": 40, "bus": 10}; without it, ``vehicles`` cars as before."""
    if not value:
        return {"car": vehicles}
    fleet: dict[str, int] = {}
    for part in value.split(","):
        kind, _, count = part.partition(":")
        kind = kind.strip()
        if kind not in FLEET:
            raise SimulationError(f"Неизвестный вид транспорта: {kind}", 400)
        try:
            n = int(count)
        except ValueError as e:
            raise SimulationError(f"Количество для «{FLEET[kind]['label']}» должно быть числом", 400) from e
        if n < 0 or n > 200:
            raise SimulationError("Количество одного вида — от 0 до 200", 400)
        if n:
            fleet[kind] = fleet.get(kind, 0) + n
    if not fleet:
        raise SimulationError("Выберите хотя бы один вид транспорта", 400)
    if sum(fleet.values()) > MAX_FLEET:
        raise SimulationError(f"Всего не больше {MAX_FLEET} машин за прогон", 400)
    return fleet


def _allows(lane: ET.Element, vclass: str) -> bool:
    allow, disallow = lane.get("allow"), lane.get("disallow", "")
    if vclass in disallow.split() or "all" in disallow.split():
        return False
    return allow is None or allow == "all" or vclass in allow.split()


def _routes(net: Path, fleet: dict[str, int]) -> tuple[ET.Element, dict[str, int]]:
    """Seeded demand for every requested kind over the lanes SUMO lets that class use.

    Each vehicle starts on a random edge open to its class and follows legal lane
    connections for up to 24 edges, so every kind spreads over all the roads (or
    tracks) where it may drive. Returns the routes and how many of each were placed.
    """
    root = ET.parse(net).getroot()
    rng = random.Random(42)
    routes = ET.Element("routes")
    for kind in fleet:
        ET.SubElement(routes, "vType", id=kind, **{k: v for k, v in FLEET[kind].items() if k != "label"})
    placed: dict[str, int] = {}
    vehicles: list[ET.Element] = []
    for kind, count in fleet.items():
        vclass = FLEET[kind]["vClass"]
        lanes: dict[str, set[str]] = {}
        for edge in root.findall("edge"):
            if edge.get("function") is not None:
                continue
            lanes[edge.attrib["id"]] = {lane.attrib["index"] for lane in edge.findall("lane") if _allows(lane, vclass)}
        adjacency: dict[str, set[str]] = {}
        for c in root.findall("connection"):
            a, b = c.attrib["from"], c.attrib["to"]
            if c.get("fromLane") in lanes.get(a, ()) and c.get("toLane") in lanes.get(b, ()):
                adjacency.setdefault(a, set()).add(b)
        candidates = sorted(e for e, ls in lanes.items() if ls)
        placed[kind] = 0
        if not candidates:
            continue
        for i in range(count):
            route = [rng.choice(candidates)]
            for _ in range(24):
                options = sorted(adjacency.get(route[-1], set()).difference(route))
                if not options:
                    break
                route.append(rng.choice(options))
            v = ET.Element("vehicle", id=f"{kind}-{i + 1}", type=kind, depart=f"{i % 15}",
                           departLane="best", departPos="random_free")
            ET.SubElement(v, "route", edges=" ".join(route))
            vehicles.append(v)
            placed[kind] += 1
    if not vehicles:
        raise SimulationError("В сети нет дорог, по которым могут ехать выбранные виды транспорта.")
    vehicles.sort(key=lambda v: (int(v.attrib["depart"]), v.attrib["id"]))
    routes.extend(vehicles)
    return routes, placed


def _run(binary: str, args: list[str], workdir: Path, stage: str, timeout: int) -> None:
    try:
        result = subprocess.run([binary, *args], cwd=workdir, env=_netconvert_env(), timeout=timeout,
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as e:
        raise SimulationError(f"{stage}: превышено время ожидания.", 504) from e
    except OSError as e:
        raise SimulationError(f"{stage}: не удалось запустить SUMO.", 503) from e
    if result.returncode:
        lines = [line for line in result.stderr.splitlines() if line.startswith("Error")]
        hint = f" {lines[0][:240]}" if lines else ""
        raise SimulationError(f"{stage}: SUMO завершился с ошибкой.{hint}")


def run_sumo(osm: bytes, vehicles: int, fleet: dict[str, int] | None = None) -> dict:
    """``fleet``: vehicles per kind (see FLEET); without it, ``vehicles`` passenger cars."""
    fleet = fleet or {"car": vehicles}
    netconvert, sumo = find_sumo_binary("netconvert"), find_sumo_binary("sumo")
    if not netconvert or not sumo:
        raise SimulationError("SUMO недоступен. Нужны sumo и netconvert на сервере.", 503)
    if not _SUMO_SLOT.acquire(blocking=False):
        raise SimulationError("Уже выполняется прогон SUMO. Повторите через несколько секунд.", 429)
    try:
        with tempfile.TemporaryDirectory(dir=_ascii_tmp_root()) as tmp:
            workdir = Path(tmp)
            (workdir / "input.osm").write_bytes(osm)
            _run(netconvert, ["--osm-files", "input.osm", "-o", "map.net.xml", "--geometry.remove",
                              "--junctions.join", "--tls.guess-signals", "--keep-edges.by-vclass",
                              ",".join(FLEET[k]["vClass"] for k in fleet)],
                 workdir, "Построение сети", 45)
            routes, placed = _routes(workdir / "map.net.xml", fleet)
            ET.ElementTree(routes).write(workdir / "cars.rou.xml")
            _run(sumo, ["-n", "map.net.xml", "-r", "cars.rou.xml", "--end", "90", "--step-length", "0.5",
                        "--fcd-output", "cars.xml", "--fcd-output.geo", "true", "--precision.geo", "7",
                        "--no-step-log", "true", "--ignore-route-errors", "true"], workdir, "Прогон", 25)
            frames: dict[float, list[Vehicle]] = {}
            for step in ET.parse(workdir / "cars.xml").getroot().findall("timestep"):
                time = _number(step.attrib["time"])
                # Keep empty frames so an interval with no cars doesn't retain stale positions.
                frames[time] = []
                for car in step.findall("vehicle"):
                    a = car.attrib
                    _append(frames, time, (a["id"], *_point(a["x"], a["y"]), _number(a["angle"]) % 360,
                                           a.get("type", "car")))
            if not any(frames.values()):
                raise SimulationError("SUMO не смог выпустить машины на этой сети.")
            missing = [FLEET[k]["label"] for k, n in fleet.items() if not placed.get(k)]
            return {"frames": [{"time": time, "vehicles": cars} for time, cars in frames.items()],
                    "fleet": [{"kind": k, "label": FLEET[k]["label"], "requested": n, "placed": placed.get(k, 0)}
                              for k, n in fleet.items()],
                    "warnings": [f"Нет путей для: {', '.join(missing)}"] if missing else []}
    finally:
        _SUMO_SLOT.release()
