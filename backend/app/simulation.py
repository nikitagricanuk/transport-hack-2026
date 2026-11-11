"""Run a small SUMO scenario or normalize simulator recordings for map playback."""

from __future__ import annotations

import csv
import gzip
import io
import math
import random
import re
import subprocess
import tempfile
from pathlib import Path
from threading import BoundedSemaphore
from xml.etree import ElementTree as ET

from .norms import default_limit, speed_kmh
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


# ------------------------------------------------------------ ПДД РФ
# netconvert options that make the network follow the Russian rules as far as the graph tells:
# lanes, turn arrows and per-lane access from OSM; yellow 3 s and all-red 2 s between phases
# (ГОСТ Р 52289-2019); at an unregulated junction of equal roads the vehicle on the right goes
# first (ПДД 13.11, SUMO right_before_left) for urban speeds; no U-turns at junctions except at
# dead ends (ПДД 8.11 limits them, OSM rarely says where they are allowed).
RU_NETCONVERT = [
    "--osm.turn-lanes", "true", "--osm.lane-access", "true",
    "--tls.yellow.time", "3", "--tls.allred.time", "2",
    "--junctions.right-before-left.speed-threshold", "22.3",
    "--no-turnarounds.except-deadend", "true",
]
RULES = [
    "Правостороннее движение; разрешённая скорость — тег maxspeed, иначе 60 км/ч в населённом пункте, "
    "20 км/ч в жилой зоне и на дворовых проездах, 110 км/ч на автомагистрали (ПДД 10.2, 10.3).",
    "Число полос, стрелки turn:lanes, запреты поворотов, односторонние улицы и доступ по видам транспорта — из графа.",
    "Нерегулируемые перекрёстки: главная дорога по классу; равнозначные — «помеха справа» (ПДД 13.11).",
    "Светофоры работают по программам редактора: фазы, стрелки (дополнительные секции), жёлтый 3 с и "
    "все красные 2 с (ГОСТ Р 52289-2019). Поворот налево на основной зелёный — уступая встречным (ПДД 13.4).",
    "Без разворотов на перекрёстках, кроме тупиков.",
]


def apply_ru_speeds(osm: bytes) -> bytes:
    """Write the speed limit that applies under the Russian rules onto every road without one.

    SUMO would otherwise take its own defaults per class (50 km/h on a residential street);
    RU:urban and the other shorthands become numbers it understands.
    """
    root = ET.fromstring(osm)
    for way in root.iter("way"):
        tags = {t.get("k"): t for t in way.findall("tag")}
        hw = tags.get("highway")
        if hw is None:
            continue
        cls = hw.get("v", "")
        current = tags.get("maxspeed")
        kmh = speed_kmh(current.get("v")) if current is not None else None
        if kmh is None:
            kmh = 110 if cls.removesuffix("_link") == "motorway" else default_limit(cls)
        if current is None:
            ET.SubElement(way, "tag", k="maxspeed", v=str(kmh))
        else:
            current.set("v", str(kmh))
    return ET.tostring(root, encoding="utf-8")


def _utm(lat: float, lon: float, zone: int) -> tuple[float, float]:
    """WGS84 → UTM easting/northing (northern hemisphere), as netconvert projects OSM."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    phi, lam = math.radians(lat), math.radians(lon)
    lam0 = math.radians(zone * 6 - 183)
    n = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    t, c = math.tan(phi) ** 2, ep2 * math.cos(phi) ** 2
    aa = math.cos(phi) * (lam - lam0)
    m = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
             - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))
    x = k0 * n * (aa + (1 - t + c) * aa ** 3 / 6 + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * aa ** 5 / 120) + 500000.0
    y = k0 * (m + n * math.tan(phi) * (aa * aa / 2 + (5 - t + 9 * c + 4 * c * c) * aa ** 4 / 24
                                        + (61 - 58 * t + t * t + 600 * c - 330 * ep2) * aa ** 6 / 720))
    return x, y


def _net_xy(root: ET.Element):
    """lat/lon → network coordinates, or None when the network is not a UTM projection."""
    loc = root.find("location")
    if loc is None:
        return None
    zone = re.search(r"\+zone=(\d+)", loc.get("projParameter", ""))
    if not zone or "+south" in loc.get("projParameter", ""):
        return None
    ox, oy = map(float, loc.get("netOffset", "0,0").split(","))
    z = int(zone.group(1))

    def to_xy(lat: float, lon: float) -> tuple[float, float]:
        x, y = _utm(lat, lon, z)
        return x + ox, y + oy
    return to_xy


SIGNAL_MATCH_M = 60.0   # a SUMO traffic light this close to an editor signal runs its plan


def _angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def signal_programs(net: Path, signals: list[dict]) -> tuple[ET.Element | None, list[dict]]:
    """SUMO programs that run the editor's signal plans.

    ``signals``: per signal node of the exported file — ``node_id``, ``axis`` (bearing of the
    main direction, degrees) and ``phases`` [{green_s, moves}]. Every link of the matching SUMO
    traffic light gets its group from the bearing of the lane it comes from (within 45° of the
    axis — main, else cross) and its turn from the connection: a phase releasing the group
    gives through and right turns «G» and a left turn «g» (it yields to oncoming traffic); an
    arrow of the group's turn gives that turn «G» on its own. Each phase is followed by 3 s of
    yellow and 2 s of all-red for the links that stop. The traffic light runs the program from
    t = 0, so the report can tell the phase at any moment.
    """
    if not signals:
        return None, []
    root = ET.parse(net).getroot()
    by_node = {int(sg["node_id"]): sg for sg in signals}
    lanes: dict[str, list[tuple[float, float]]] = {}
    for edge in root.findall("edge"):
        for lane in edge.findall("lane"):
            pts = [tuple(map(float, p.split(","))) for p in lane.get("shape", "").split()]
            lanes[lane.attrib["id"]] = pts
    links: dict[str, dict[int, tuple[str, str]]] = {}   # tl -> link index -> (from lane, dir)
    for c in root.findall("connection"):
        tl = c.get("tl")
        if tl is None or c.get("linkIndex") is None:
            continue
        links.setdefault(tl, {})[int(c.attrib["linkIndex"])] = (f"{c.attrib['from']}_{c.attrib['fromLane']}", c.get("dir", "s"))
    # where each traffic light stands: the junction its controlled lanes lead into
    edge_to = {e.attrib["id"]: e.get("to") for e in root.findall("edge")}
    junction_xy = {j.attrib["id"]: (float(j.get("x", 0)), float(j.get("y", 0))) for j in root.findall("junction")}
    tl_xy: dict[str, tuple[float, float]] = {}
    for c in root.findall("connection"):
        if c.get("tl") and c.get("tl") not in tl_xy and edge_to.get(c.attrib["from"]) in junction_xy:
            tl_xy[c.attrib["tl"]] = junction_xy[edge_to[c.attrib["from"]]]
    to_xy = _net_xy(root)
    sig_xy = {int(sg["node_id"]): to_xy(sg["lat"], sg["lon"]) for sg in signals
              if to_xy and "lat" in sg} if to_xy else {}
    out = ET.Element("additional")
    applied: list[dict] = []
    for tl in root.findall("tlLogic"):
        tid = tl.attrib["id"]
        if tid not in links:
            continue
        # the editor signal on this junction (its id is in the SUMO id), else the nearest one
        nodes = [int(x) for x in re.findall(r"\d+", tid) if int(x) in by_node]
        if not nodes and tid in tl_xy and sig_xy:
            tx, ty = tl_xy[tid]
            near = sorted((math.hypot(x - tx, y - ty), nid) for nid, (x, y) in sig_xy.items())
            nodes = [nid for d, nid in near if d <= SIGNAL_MATCH_M]
        if not nodes:
            continue
        sg = by_node[nodes[0]]
        axis = float(sg["axis"])
        n_links = max(links[tid]) + 1
        group_turn: list[tuple[str, str]] = []
        for i in range(n_links):
            lane_id, d = links[tid].get(i, ("", "s"))
            pts = lanes.get(lane_id, [])
            group = "cross"
            if len(pts) >= 2:
                (x0, y0), (x1, y1) = pts[-2], pts[-1]
                outward = (math.degrees(math.atan2(x0 - x1, y0 - y1)) + 360) % 360   # from the junction back up the lane
                if _angle_diff(outward, axis) <= 45 or _angle_diff(outward, axis + 180) <= 45:
                    group = "main"
            turn = "left" if d in ("l", "L", "t") else "right" if d in ("r", "R") else "through"
            group_turn.append((group, turn))

        def state(moves: set[str]) -> str:
            chars = []
            for group, turn in group_turn:
                if turn != "through" and f"{group}_{turn}" in moves:
                    chars.append("G")
                elif group in moves:
                    chars.append("g" if turn == "left" else "G")
                else:
                    chars.append("r")
            return "".join(chars)

        phases = sg["phases"]
        states = [state(set(p["moves"])) for p in phases]
        logic = ET.SubElement(out, "tlLogic", id=tid, type="static", programID="uds", offset="0")
        for i, p in enumerate(phases):
            cur, nxt = states[i], states[(i + 1) % len(states)]
            ET.SubElement(logic, "phase", duration=str(int(p["green_s"])), state=cur)
            yellow = "".join("y" if c in "Gg" and n == "r" else c for c, n in zip(cur, nxt))
            red = "".join("r" if c in "Gg" and n == "r" else c for c, n in zip(cur, nxt))
            ET.SubElement(logic, "phase", duration="3", state=yellow)
            ET.SubElement(logic, "phase", duration="2", state=red)
        applied += [{"node_id": nid, "tl": tid, "links": n_links, "program_of": sg["node_id"]} for nid in nodes]
    return (out if len(out) else None), applied


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


def run_sumo(osm: bytes, vehicles: int, fleet: dict[str, int] | None = None,
             signals: list[dict] | None = None) -> dict:
    """``fleet``: vehicles per kind (see FLEET); without it, ``vehicles`` passenger cars.
    ``signals``: the editor's signal plans (see ``signal_programs``) that SUMO should run."""
    fleet = fleet or {"car": vehicles}
    netconvert, sumo = find_sumo_binary("netconvert"), find_sumo_binary("sumo")
    if not netconvert or not sumo:
        raise SimulationError("SUMO недоступен. Нужны sumo и netconvert на сервере.", 503)
    if not _SUMO_SLOT.acquire(blocking=False):
        raise SimulationError("Уже выполняется прогон SUMO. Повторите через несколько секунд.", 429)
    try:
        with tempfile.TemporaryDirectory(dir=_ascii_tmp_root()) as tmp:
            workdir = Path(tmp)
            (workdir / "input.osm").write_bytes(apply_ru_speeds(osm))
            _run(netconvert, ["--osm-files", "input.osm", "-o", "map.net.xml", "--geometry.remove",
                              "--junctions.join", "--tls.guess-signals", "--keep-edges.by-vclass",
                              ",".join(FLEET[k]["vClass"] for k in fleet), *RU_NETCONVERT,
                              *(["--osm.bike-access", "true"] if "bicycle" in fleet else [])],
                 workdir, "Построение сети", 45)
            routes, placed = _routes(workdir / "map.net.xml", fleet)
            ET.ElementTree(routes).write(workdir / "cars.rou.xml")
            programs, applied = signal_programs(workdir / "map.net.xml", signals or [])
            extra: list[str] = []
            if programs is not None:
                ET.ElementTree(programs).write(workdir / "uds.tll.xml")
                extra = ["-a", "uds.tll.xml"]
            _run(sumo, ["-n", "map.net.xml", "-r", "cars.rou.xml", *extra, "--end", "90", "--step-length", "0.5",
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
                    "warnings": [f"Нет путей для: {', '.join(missing)}"] if missing else [],
                    "rules": RULES,
                    "signals": [{**sg, "applied": any(a["node_id"] == sg["node_id"] for a in applied)}
                                for sg in (signals or [])]}
    finally:
        _SUMO_SLOT.release()
