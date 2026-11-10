"""Informational pedestrian checks; never affect simulation readiness."""
from . import routing as r
from .network_analysis import issue
from .roads import is_drivable


def check(doc):
    rg = r.build_graph(doc,"walk")
    out = []
    sidewalks = [(a,b,pa,pb) for a,b,wid,pa,pb in rg.segments if doc.ways[wid].tags.get("footway") == "sidewalk"]
    index = r.GridIndex(40)
    from shapely.geometry import LineString
    from shapely.ops import unary_union
    for a,b,pa,pb in sidewalks:
        line = LineString([pa,pb]).buffer(20)
        index.insert_box(*pa,*pb,line)
    for w in doc.ways.values():
        tags = w.tags
        if not is_drivable(tags) or tags.get("foot") is not None:
            continue
        title, separate = None, False
        if tags.get("sidewalk") == "no" or tags.get("sidewalk:both") == "no":
            title = "Проезжая часть без тротуара в пешеходном графе"
        elif tags.get("highway","").endswith("_link") or tags.get("highway") in {"motorway","trunk"} or tags.get("tunnel") not in (None,"no"):
            title = "Съезд/тоннель может создавать ложный пешеходный путь"
        elif tags.get("highway") in {"primary","secondary","tertiary"} and "sidewalk" not in tags:
            total,covered = 0.,0.
            for a,b in zip(w.nodes,w.nodes[1:]):
                if a not in doc.nodes or b not in doc.nodes:
                    continue
                pa,pb = [rg.proj.to_xy(doc.nodes[n].lat,doc.nodes[n].lon) for n in (a,b)]
                line = LineString([pa,pb])
                total += line.length
                nearby = index.query((pa[0]+pb[0])/2,(pa[1]+pb[1])/2,line.length/2+20)
                if nearby:
                    covered += line.intersection(unary_union(nearby)).length
            if total and covered/total >= .6:
                title,separate = "Тротуар нарисован отдельно — проезжая часть не для пешеходов",True
        if not title:
            continue
        fixes = [{"label":"Тротуар отдельно" if separate else "Исключить из пешеходного графа", "op":"set_tags",
                  "args":{"kind":"way","id":w.id,"tags":{"sidewalk":"separate"} if separate else {"foot":"no"}}},
                 {"label":"Оставить: пешеходы ходят по проезжей части", "op":"set_tags",
                  "args":{"kind":"way","id":w.id,"tags":{"foot":"yes"}}}]
        n = next((doc.nodes[n] for n in w.nodes if n in doc.nodes),None)
        out.append(issue(title,"Эвристика OSM: проверьте по снимку и выберите явный тег. На автомобильные скорости и полосы не влияет.",
                         refs=[{"kind":"way","id":w.id}],location=[n.lon,n.lat] if n else None,
                         fixes=fixes,category="pedestrian",prefix="catch"))
    for n in doc.nodes.values():
        if not (n.tags.get("highway") == "bus_stop" or n.tags.get("public_transport") == "platform" or n.tags.get("railway") == "tram_stop"):
            continue
        if r.snap(rg,n.lat,n.lon,60) is None:
            out.append(issue("Остановка не связана с пешеходной сетью","Нет пешеходного ребра в радиусе 60 м. Проверьте отсутствующий подход по снимку.",
                             refs=[{"kind":"node","id":n.id}],location=[n.lon,n.lat],category="pedestrian",prefix="catch"))
    return out
