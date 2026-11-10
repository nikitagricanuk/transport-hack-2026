"""Apply the editor's own one-click fixes to an .osm until the fix-graph task passes.

Usage: python autofix.py <backend_dir> <in.osm> <out.osm>
"""
import sys
import time
sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, sys.argv[1])
from app.edits import History, OpError, apply_op  # noqa: E402
from app.osm_model import OsmDocument  # noqa: E402
from app.scenario import builtin_tasks, check_task  # noqa: E402
from app.validation import validate  # noqa: E402


def wanted(i):
    if i["severity"] == "critical":
        return True
    if i["category"] == "junction":
        return True
    # paired loose ends of divided roads at the border: close the loop for MatSim
    return any(f["label"].startswith("Граничный разворот") for f in (i.get("fixes") or []))


doc = OsmDocument.parse(open(sys.argv[2], "rb").read())
hist = History()
v0 = validate(doc)
print("before", v0["summary"]["counts"], "components", v0["summary"]["components"], v0["summary"]["levels"])
log = []
skip = set()
for _round in range(80):
    v = validate(doc)
    todo = [i for i in v["issues"] if wanted(i) and i["id"] not in skip and i.get("fixes")]
    if not todo:
        break
    i = todo[0]
    fixes = sorted(i["fixes"], key=lambda f: not f["label"].startswith("Граничный разворот"))
    for f in fixes:
        try:
            apply_op(doc, hist, f["op"], f["args"], author="autofix", at=time.time())
            log.append(f"{i['severity']:8} {i['title']} → {f['label']} ({f['op']} {f['args']})")
            break
        except OpError as e:
            log.append(f"   failed {f['label']}: {e}")
    else:
        skip.add(i["id"])
v = validate(doc)
print("after ", v["summary"]["counts"], "components", v["summary"]["components"], v["summary"]["levels"])
for t in builtin_tasks(doc):
    r = check_task(doc, t, v, [])
    if t["id"] != "no-jams":
        print(" task", t["title"], f"{r['passed']}/{r['total']}", [(x["label"], x["detail"]) for x in r["results"]])
for i in v["issues"]:
    if i["severity"] != "info":
        print(" left", i["severity"], i["category"], i["title"], [r["id"] for r in i["refs"]][:3])
print("\n".join(log))
open(sys.argv[3], "wb").write(doc.to_xml(generator="Редактор УДС autofix"))
print("nodes/ways/rels", len(doc.nodes), len(doc.ways), len(doc.relations))
