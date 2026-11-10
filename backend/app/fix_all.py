"""«Править всё»: which suggested fixes are safe to apply together, which need a person.

Safe: the issue has exactly one suggestion, and that suggestion only sets tags,
smooths Z, re-points a turn restriction or removes a broken relation. Nothing
is drawn, joined or deleted on the road network, so there is no choice to make.

Disputed (left to the user, with the reason): several alternatives, a road or
node removal, new geometry (joins, connectors, fictitious border edges), or no
ready fix at all.

The editor shows the whole list and applies only what the user confirms, as one
undo step that lands in the edit log.
"""

from __future__ import annotations

import json
from typing import Any

SAFE_OPS = {"set_tags", "smooth_z", "set_restriction_via"}
GEOMETRY_OPS = {"add_way", "connect_endpoint", "merge_nodes", "insert_node", "split_way", "move_node", "move_way",
                "attach_to_road", "separate_ways", "reverse_way"}


def _reason(issue: dict[str, Any]) -> str | None:
    """None when the issue's fix is safe, else why the user has to decide."""
    fixes = issue.get("fixes") or []
    if not fixes:
        return "готового исправления нет — нужна ручная правка"
    if len(fixes) > 1:
        return f"{len(fixes)} варианта — выберите подходящий по снимку или на месте"
    fix = fixes[0]
    op, args = fix.get("op"), fix.get("args") or {}
    if op == "delete" and args.get("kind") == "relation":
        return None
    if op in ("delete", "delete_ways"):
        return "удаляет объект — проверьте, что его нет в реальности"
    if op in GEOMETRY_OPS:
        if (args.get("tags") or {}).get("uds:fringe") == "yes":
            return "добавляет фиктивное ребро на границе выгрузки — решите, нужно ли оно"
        return "меняет геометрию сети — проверьте на снимке"
    if op not in SAFE_OPS:
        return "нестандартная операция — примените вручную"
    return None


def plan(validation: dict[str, Any]) -> dict[str, Any]:
    safe: list[dict[str, Any]] = []
    disputed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for issue in validation["issues"]:
        if issue["severity"] == "info" and not issue.get("fixes"):
            continue   # notes without a fix are not errors to correct
        reason = _reason(issue)
        base = {"issue_id": issue["id"], "title": issue["title"], "severity": issue["severity"],
                "description": issue["description"], "refs": issue["refs"], "location": issue.get("location")}
        if reason:
            disputed.append({**base, "reason": reason, "options": len(issue.get("fixes") or [])})
            continue
        fix = issue["fixes"][0]
        key = json.dumps([fix["op"], fix["args"]], sort_keys=True, ensure_ascii=False)
        if key in seen:   # one bridge flagged twice gets one «Плавный Z»
            continue
        seen.add(key)
        safe.append({**base, "label": fix["label"], "op": fix["op"], "args": fix["args"]})
    return {"safe": safe, "disputed": disputed}
