"""Turn raw check output into something a user can read and act on.

* netconvert prints English one-liners («Warning: Found sharp turn with radius
  8.98 at the end of edge '-227484856'.») and then «N total messages of type:
  …» summaries. `interpret_netconvert` groups them by kind, gives each group a
  Russian title, a short «what to do», a severity, the real count and the OSM
  objects involved (edge and junction ids are mapped back to way/node ids).
* The organisers' server answers with JSON or plain text. `interpret_organizer`
  lists its fields with Russian labels instead of showing the raw body.
"""

from __future__ import annotations

import json
import re
from typing import Any

# (key, regex on the message, title, what to do, severity)
_SUMO_KINDS: list[tuple[str, str, str, str, str]] = [
    ("lane_unreachable", r"Lane '([^']+)' is not connected from any incoming edge at junction '([^']+)'",
     "Полоса без въезда на перекрёстке",
     "На полосу нельзя попасть ни с одной дороги: проверьте число полос и манёвры на этом перекрёстке.", "warning"),
    ("left_turn", r"Minor green from edge '([^']+)' to edge '([^']+)'",
     "Поворот налево на светофоре без отдельной полосы",
     "SUMO считает, что тут не хватает полосы для левого поворота; если она есть в реальности — добавьте её.",
     "warning"),
    ("rail_crossing", r"rail crossing '([^']+)' does not have any roads",
     "Ж/д переезд без дороги",
     "Узел помечен как переезд, но дорога через него не проходит: перенесите метку на пересечение с дорогой.",
     "warning"),
    ("junction_shape", r"Shape for junction '([^']+)' has distance",
     "Форма перекрёстка далеко от узла",
     "Узлы перекрёстка сильно разнесены; проверьте, не разорван ли перекрёсток.", "info"),
    ("not_joined", r"Not joining junctions ([\w,]+)",
     "Близкие узлы не объединены в один перекрёсток",
     "SUMO оставил их отдельными; обычно это нормально, но стоит взглянуть на сложные развязки.", "info"),
    ("sharp_turn", r"sharp turn with radius [\d.%]+ at the \w+ of edge '([^']+)'",
     "Крутой поворот геометрии дороги",
     "Резкий излом линии: машины сильно тормозят. Проверьте, не лишняя ли точка в геометрии.", "info"),
    ("slow_turn", r"Speed of \w+ connection '([^']+)'",
     "Скорость на повороте снижена",
     "Малый радиус поворота, SUMO ограничит скорость. Обычно действий не требует.", "info"),
    ("pt_split", r"PT line .* in relation (\d+) seems to be split",
     "Маршрут ОТ разорван",
     "Маршрут выходит за выгрузку или в нём пропуск; SUMO оставит только первую часть.", "info"),
    ("pt_no_stops", r"PT line in relation (\d+) with no stops",
     "Маршрут ОТ без остановок пропущен",
     "Остановки маршрута вне выгрузки; на движение машин это не влияет.", "info"),
    ("pt_stop", r"(?:Removing pt stop '(\d+)'|Removed invalid stop '(\d+)'|Removed \d+ pt stops)",
     "Остановка не привязана к дороге",
     "Остановка стоит далеко от проезжей части или на удалённой дороге: передвиньте её к дороге.", "info"),
    ("restriction", r"Ignoring restriction relation '(\d+)'",
     "Запрет поворота ссылается на дорогу вне выгрузки",
     "Отношение-запрет неполное в этом фрагменте и будет пропущено. Действий обычно не требует.", "info"),
    ("missing_ref", r"No way found for reference '(\d+)' in relation '(\d+)'",
     "Отношение ссылается на путь вне выгрузки",
     "Часть маршрута или запрета вне фрагмента; это особенность выгрузки, а не ошибка графа.", "info"),
    ("turnaround", r"Ambiguity in turnarounds computation at junction '([^']+)'",
     "Неоднозначный разворот на перекрёстке",
     "SUMO не уверен, где разрешить разворот; проверьте перекрёсток, если на видео машины разворачиваются странно.",
     "info"),
    ("angle", r"Found angle of [\d.]+ degrees at edge '([^']+)'",
     "Острый угол в геометрии дороги",
     "Линия дороги делает излом больше 90°: возможно, лишняя или сдвинутая точка.", "info"),
    ("pt_route", r"Cannot revise pt stop localization for pt line '(\d+)'",
     "Маршрут ОТ без дорог в выгрузке",
     "Маршрут проходит вне фрагмента; на движение машин это не влияет.", "info"),
    ("unused_type", r"Discarding (?:unknown compound|unusable type) .*edge '(\d+)'",
     "Тип пути не используется SUMO",
     "Пути этого типа (платформы, водотоки и т. п.) в сеть не попадают — это нормально.", "info"),
]
# netconvert notes about its own environment, not about the graph
_IGNORE = re.compile(r"Environment variable SUMO_HOME")
_COMPILED = [(k, re.compile(rx), t, h, s) for k, rx, t, h, s in _SUMO_KINDS]
_TOTAL = re.compile(r"(\d+) total messages of type: (.*)")


def _osm_ref(sumo_id: str, role: str) -> list[dict[str, Any]]:
    """SUMO edge '-1494549493#1' → way 1494549493; junction 'cluster_1_2' → nodes 1, 2."""
    if role in ("relation", "way"):
        return [{"kind": role, "id": int(sumo_id)}] if sumo_id.isdigit() else []
    if role == "edge":
        m = re.match(r"-?(\d+)", sumo_id.split("_")[0] if "->" not in sumo_id else sumo_id.split("->")[0])
        return [{"kind": "way", "id": int(m.group(1))}] if m else []
    ids = re.findall(r"\d+", sumo_id)
    return [{"kind": "node", "id": int(x)} for x in ids[:4]]


_ROLES = {
    "lane_unreachable": ("edge", "node"), "left_turn": ("edge", "edge"), "rail_crossing": ("node",),
    "junction_shape": ("node",), "not_joined": ("node",), "sharp_turn": ("edge",), "slow_turn": ("edge",),
    "pt_split": ("relation",), "pt_no_stops": ("relation",), "pt_stop": ("node", "node"),
    "restriction": ("relation",), "missing_ref": ("way", "relation"), "turnaround": ("node",),
    "angle": ("edge",), "pt_route": ("relation",), "unused_type": ("way",),
}


def _classify(text: str) -> tuple[str, re.Match | None]:
    for key, rx, *_ in _COMPILED:
        m = rx.search(text)
        if m:
            return key, m
    return "other", None


def interpret_netconvert(errors: list[str], warnings: list[str]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    totals: dict[str, int] = {}

    def group(key: str) -> dict[str, Any]:
        if key not in groups:
            meta = next((x for x in _COMPILED if x[0] == key), None)
            groups[key] = {"key": key, "title": meta[2] if meta else "Прочие предупреждения",
                           "hint": meta[3] if meta else "Сообщения netconvert без отдельного разбора — см. журнал.",
                           "severity": meta[4] if meta else "info", "count": 0, "refs": [], "examples": []}
        return groups[key]

    for line in warnings:
        text = re.sub(r"^Warning:\s*", "", line)
        if _IGNORE.search(text):
            continue
        t = _TOTAL.search(text)
        if t:
            # the summary is a template with % placeholders: fill them so the same patterns match
            key, _ = _classify(t.group(2).replace("%", "0"))
            totals[key] = totals.get(key, 0) + int(t.group(1))
            continue
        key, m = _classify(text)
        g = group(key)
        g["count"] += 1
        if len(g["examples"]) < 3:
            g["examples"].append(text[:200])
        if m:
            for value, role in zip(m.groups(), _ROLES.get(key, ())):
                if value:
                    for ref in _osm_ref(value, role):
                        if ref not in g["refs"] and len(g["refs"]) < 8:
                            g["refs"].append(ref)
    for key, n in totals.items():
        g = group(key)
        g["count"] = max(g["count"], n)
    err = [{"key": "error", "title": "Ошибка netconvert", "severity": "critical",
            "hint": "Сеть не построена: SUMO не примет этот файл. Исправьте ошибку и повторите проверку.",
            "count": 1, "refs": [], "examples": [re.sub(r"^Error:\s*", "", e)[:300]]} for e in errors]
    order = {"critical": 0, "warning": 1, "info": 2}
    ordered = sorted(groups.values(), key=lambda g: (order[g["severity"]], -g["count"]))
    return {"groups": err + ordered,
            "counts": {s: sum(g["count"] for g in err + ordered if g["severity"] == s) for s in order}}


_FIELD_RU = {
    "run_id": "Номер прогона", "id": "Номер прогона", "run": "Прогон", "status": "Статус", "state": "Статус",
    "url": "Страница прогона", "run_url": "Страница прогона", "link": "Страница прогона", "page": "Страница прогона",
    "message": "Сообщение", "detail": "Подробности", "error": "Ошибка", "filename": "Файл", "file": "Файл",
    "team": "Команда", "created_at": "Создан", "created": "Создан", "queued_at": "В очереди с",
    "position": "Место в очереди", "queue_position": "Место в очереди",
}
_STATUS_RU = {
    "queued": "в очереди", "pending": "в очереди", "running": "выполняется", "processing": "выполняется",
    "done": "готово", "finished": "готово", "success": "готово", "completed": "готово",
    "failed": "ошибка", "error": "ошибка",
}


def _fields(data: Any, prefix: str = "") -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if isinstance(data, dict):
        for k, v in data.items():
            label = _FIELD_RU.get(str(k).lower(), str(k).replace("_", " "))
            if isinstance(v, (dict, list)):
                out.extend(_fields(v, f"{prefix}{label} · "))
            else:
                value = "" if v is None else str(v)
                if str(k).lower() in ("status", "state"):
                    value = _STATUS_RU.get(value.lower(), value)
                if isinstance(v, bool):
                    value = "да" if v else "нет"
                out.append({"label": prefix + label, "value": value})
    elif isinstance(data, list):
        for i, v in enumerate(data[:20], 1):
            out.extend(_fields(v, f"{prefix}{i}. ") if isinstance(v, (dict, list))
                       else [{"label": f"{prefix}{i}", "value": str(v)}])
    return out


def interpret_organizer(text: str) -> list[dict[str, str]]:
    """Server reply as labelled fields; plain text stays a single «Сообщение»."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return [{"label": "Сообщение", "value": text}] if text else []
    return _fields(data) if isinstance(data, (dict, list)) else [{"label": "Сообщение", "value": str(data)}]


def organizer_error_text(status: int, body: str) -> str:
    """Readable reason for a rejected upload, from the HTTP status and the reply."""
    reason = {401: "сервер не принял токен команды (проверьте ORGANIZER_TOKEN)",
              403: "у токена нет прав на отправку (проверьте ORGANIZER_TOKEN и TEAM_NAME)",
              404: "адрес сервера проверки неверен (проверьте ORGANIZER_URL)",
              413: "файл слишком большой для сервера проверки",
              429: "слишком много отправок подряд — подождите и повторите"}.get(status)
    fields = interpret_organizer(body)
    details = "; ".join(f["value"] if f["label"] in ("Сообщение", "Подробности", "Ошибка") else
                        f"{f['label']}: {f['value']}" for f in fields if f["value"])
    head = reason or (f"сервер ответил ошибкой {status}" if status >= 500 else "сервер не принял файл")
    return f"Сервер проверки: {head}" + (f". {details}" if details else "")
