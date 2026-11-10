"""Analysis response builders. No session, history, or journal writes."""
from __future__ import annotations

import math
import time
from dataclasses import asdict

import networkx as nx
from shapely.geometry import MultiPoint, Polygon, box
from shapely.ops import unary_union

from . import routing as r
from .analysis import original_document
from .validation import Issue


def issue(title, description, *, refs=None, location=None, fixes=None, category="connectivity", prefix="route"):
    fixes = fixes or []
    return asdict(Issue(id=f"{prefix}-{title}-{'-'.join(str(x['id']) for x in refs or [])}", severity="info",
                        category=category, title=title, description=description, refs=refs or [],
                        location=location, fixes=fixes, fix=fixes[0] if fixes else None, levels=[]))


def point(doc, value):
    if value.get("node_id") is not None:
        n = doc.nodes.get(value["node_id"])
        if n is None:
            return None
        return n.lat, n.lon
    if value.get("way_id") is not None:
        w = doc.ways.get(value["way_id"])
        if w is None:
            return None
        nodes = [doc.nodes[n] for n in w.nodes if n in doc.nodes]
        if not nodes:
            return None
        if len(nodes) >= 4 and w.is_closed:
            p = Polygon([(n.lon,n.lat) for n in nodes]).centroid
            return p.y,p.x
        return sum(n.lat for n in nodes)/len(nodes),sum(n.lon for n in nodes)/len(nodes)
    if value.get("lat") is None or value.get("lon") is None:
        return None
    return value["lat"],value["lon"]


def assumptions(rg, buffer_m=None):
    out = [{"key": "walk_kmh", "value": rg.params["walk_kmh"],
            "label": f"Пешком {rg.params['walk_kmh']} км/ч — допущение. Ступени без штрафа; поправка на рельеф не применяется: нет высот."}]
    if rg.mode == "car":
        out.append({"key": "car_speed", "label": "maxspeed / ПДД 10.2; классовые скорости — допущение. Без задержек светофоров и штрафов поворота. Только запреты с via-узлом."})
    if buffer_m is not None:
        out.append({"key": "buffer_m", "value": buffer_m, "label": f"Коридор {buffer_m} м — допущение; concave hull 0,3 — способ отрисовки."})
    return out


def base(rg, buffer_m=None):
    return {"read_only": True, "compare": None, "issues": [], "assumptions": assumptions(rg,buffer_m),
            "sources": [{"label": "ПДД РФ п.10.2", "url": "https://www.consultant.ru/document/cons_doc_LAW_2709/"}],
            "restrictions_applied": rg.applied, "restrictions_skipped": rg.skipped_restrictions}


def snaps(doc, rg, values, max_m=None):
    found, issues = [], []
    for value in values:
        coord = point(doc,value)
        s = r.snap(rg,*coord,max_m or (60 if rg.mode == "car" else 80)) if coord else None
        if s is None:
            issues.append(issue("Точка далеко от сети", "Не удалось привязать объект: вне радиуса поиска или объект отсутствует.",
                                location=list(reversed(coord)) if coord else None))
        found.append(s)
    return found,issues


def diagnose(doc, rg, a, b):
    g = rg.g
    components = list(nx.weakly_connected_components(g))
    ca = next(c for c in components if a.a in c)
    cb = next(c for c in components if b.a in c)
    if ca != cb:
        from .edits import way_level
        from .roads import can_connect
        deadline=time.monotonic()+2
        index=r.GridIndex(30)
        for segment in rg.segments:
            u,v,wid,pa,pb=segment
            if u in cb and v in cb:
                index.insert_box(*pa,*pb,segment)
        best=None
        for n in ca:
            if time.monotonic()>deadline:
                break
            xy=g.nodes[n]['xy']
            incident={e['way'] for _,_,e in [*g.in_edges(n,data=True),*g.out_edges(n,data=True)]}
            source=[doc.ways[wid] for wid in incident]
            for u,v,wid,pa,pb in index.query(*xy,30):
                q,fraction,distance=r.project_on_segment(xy,pa,pb)
                target=doc.ways[wid]
                tags={'highway':'footway' if rg.mode=='walk' else 'service'}
                if distance>30 or any(way_level(w.tags)!=way_level(target.tags) for w in source):
                    continue
                if any(can_connect(tags,w.tags) for w in source+[target]):
                    continue
                if best is None or distance<best[0]:
                    best=(distance,n,wid,q,fraction,u,v)
        fixes,refs,location=[],[],None
        if best:
            distance,n,wid,q,fraction,u,v=best
            lat,lon=rg.proj.to_latlon(*q)
            target={'id':u} if fraction<1e-8 else {'id':v} if fraction>1-1e-8 else {'lat':lat,'lon':lon,'way_id':wid}
            refs=[{'kind':'node','id':n},{'kind':'way','id':wid}]
            location=[doc.nodes[n].lon,doc.nodes[n].lat]
            if g.to_undirected(as_view=True).degree(n)==1:
                fixes.append({'label':f'Присоединить конец ({distance:.0f} м) — проверить по снимку','op':'connect_endpoint','args':{'node_id':n,'way_id':wid}})
            fixes.append({'label':f'Соединить ({distance:.0f} м) — проверить по снимку','op':'add_way',
                          'args':{'points':[{'id':n},target],'tags':{'highway':'footway' if rg.mode=='walk' else 'service'}}})
        text=f'Компоненты: {len(ca)} и {len(cb)} узлов. '
        text+=f'Ближайший совместимый разрыв: {best[0]:.1f} м.' if best else 'Разрыв не локализован в радиусе 30 м.'
        return [issue('Нет пути: граф разорван',text,refs=refs,location=location,fixes=fixes)]
    if rg.restrictions:
        relaxed = r.RoutingGraph(g,rg.mode,rg.proj,rg.seg_index,{},[],rg.params,rg.segments)
        unrestricted = r.shortest_path(relaxed,a,b,"time")
        if unrestricted:
            blocking = set()
            for first,second in zip(unrestricted["edges"],unrestricted["edges"][1:]):
                rule = rg.restrictions.get((first[2]["way"],second[0]))
                to_way = second[2]["way"]
                if rule and (to_way in rule["no"] or (rule["only"] is not None and to_way not in rule["only"])):
                    blocking.update(rule["ids"])
            ids = sorted(blocking)
            return [issue("Нет пути: запрет поворота", "Без запретов путь существует. Проверьте ограничения; удаление допустимо только после проверки.",
                          refs=[{"kind":"relation","id":i} for i in ids],
                          fixes=[{"label":f"Удалить отношение {i}","op":"delete","args":{"kind":"relation","id":i}} for i in ids[:20]])]
    # Identify an actual directed obstruction on an undirected path.
    virtual,(start,end) = r.virtual_graph(rg,[a,b])
    path = nx.shortest_path(virtual.to_undirected(),start,end)
    for u,v in zip(path,path[1:]):
        if not virtual.has_edge(u,v):
            wid = next(iter(virtual[v][u].values()))["way"]
            return [issue("Нет пути: одностороннее ребро", "Проверьте направление дороги по снимку перед изменением.",
                          refs=[{"kind":"way","id":wid}], category="direction",
                          fixes=[{"label":"Развернуть ребро","op":"reverse_way","args":{"id":wid}},
                                 {"label":"Двустороннее движение","op":"set_tags","args":{"kind":"way","id":wid,"tags":{"oneway" if rg.mode == "car" else "oneway:foot":None}}}])]
    return [issue("Нет пути", "Направления или ограничения не позволяют проехать между точками. Разрыв не локализован.")]


def route(doc, req, rg=None, *, diagnosis=True, related_issues=()):
    rg = rg or r.build_graph(doc,req.mode,walk_kmh=req.walk_kmh,use_restrictions=req.use_restrictions)
    out = {**base(rg),"found":False,"path":None,"stats":None,"mode":rg.mode,"metric":req.metric}
    (a,b),out["issues"] = snaps(doc,rg,[req.start.model_dump(),req.to.model_dump()])
    if a is None or b is None:
        return out
    result = r.shortest_path(rg,a,b,req.metric)
    if result is None:
        if diagnosis:
            out["issues"] = diagnose(doc,rg,a,b)
            components=list(nx.weakly_connected_components(rg.g))
            source=next(c for c in components if a.a in c)
            target=next(c for c in components if b.a in c)
            if source!=target:
                smaller=min((source,target),key=len)
                ways={wid for u,v,wid,_,_ in rg.segments if u in smaller or v in smaller}
                out['issues'].extend(i for i in related_issues if i['category'] in {'connectivity','junction','pedestrian'} and any(ref['kind']=='way' and ref['id'] in ways for ref in i['refs']))
        return out
    streets = {}
    for _,_,e in result["edges"]:
        wid = e["way"]
        if wid not in streets:
            w = doc.ways[wid]
            streets[wid] = {"id":wid,"name":w.tags.get("name",str(wid)),"highway":w.tags.get("highway"),
                            "length_m":0,"time_s":0,"maxspeed_kmh":r._speed(w.tags)}
        streets[wid]["length_m"] += e["l"]
        streets[wid]["time_s"] += e["t"]
    out.update(found=True,path={"type":"Feature","properties":{"kind":"route"},
                               "geometry":{"type":"LineString","coordinates":result["coordinates"]}},
               stats={"length_m":result["length_m"],"time_s":result["time_s"],
                      "avg_kmh":result["length_m"]/result["time_s"]*3.6 if result["time_s"] else 0,
                      "edges":len(result["edges"]),"ways":list(streets.values()),
                      "snap":{"from_m":a.distance,"to_m":b.distance},
                      "restrictions_applied":rg.applied,"restrictions_skipped":rg.skipped_restrictions})
    return out


def feature(rg, geometry, **props):
    return {"type":"Feature","geometry":r.geojson(rg,geometry),"properties":props}


def fc(features):
    return {"type":"FeatureCollection","features":features}


def isochrone(doc,req,rg):
    out = {**base(rg,req.buffer_m),"bands":[],"mode":rg.mode,"boundary_limited":False}
    (start,),out["issues"] = snaps(doc,rg,[req.start.model_dump()])
    if start is None:
        return out
    g,(node,) = r.virtual_graph(rg,[start])
    costs,_,_ = r.search(rg,g,{node:0},"time",req.step_min*req.count*60)
    prev_km,prev_area = 0,0
    bounds = doc.bounds()
    previous_polygon = None
    for i in range(1,req.count+1):
        minutes = i*req.step_min
        result = r.reached(rg,g,costs,minutes*60,"time")
        poly = r.polygon(result,req.how,req.buffer_m)
        if previous_polygon is not None:
            poly = poly.union(previous_polygon)
        previous_polygon = poly
        km,area = result.length_m/1000,round(poly.area/1e6,9)
        out["bands"].append({"minutes":minutes,"area_km2":area,"ring_area_km2":area-prev_area,
                             "road_km":km,"ring_road_km":max(0,km-prev_km),"nodes":sum(c<=minutes*60 for c in result.cost.values()),
                             "geometry":r.geojson(rg,poly)})
        prev_km,prev_area = km,area
        if bounds:
            # Bounds are south, west, north, east.
            south,west,north,east = bounds
            for n,c in result.cost.items():
                if c > minutes*60:
                    continue
                lat,lon = rg.proj.to_latlon(*g.nodes[n]["xy"])
                if min(abs(lat-south)*110540,abs(lat-north)*110540,abs(lon-west)*rg.proj.kx,abs(lon-east)*rg.proj.kx)<40:
                    out["boundary_limited"] = True
    out["network_road_km"] = sum(math.dist(pa,pb) for _,_,_,pa,pb in rg.segments)/1000
    return out


def catchment(doc,req,rg):
    out = {**base(rg,req.buffer_m),"limit_m":req.limit_m if req.limit_min is None else req.limit_min*req.walk_kmh/60*1000,
           "relief_correction":False,"stops":[]}
    polygons, results, components = [], [], set()
    component_of = {n:i for i,c in enumerate(nx.weakly_connected_components(rg.g)) for n in c}
    for label, ids in (("A",req.set_a),("B",req.set_b)):
        selected = []
        for nid in ids:
            n = doc.nodes.get(nid)
            s = r.snap(rg,n.lat,n.lon,60) if n else None
            out["stops"].append({"id":nid,"name":n.tags.get("name",str(nid)) if n else str(nid),
                                 "coord":[n.lon,n.lat] if n else [],"set":label,"snap_m":s.distance if s else None,"used":s is not None})
            if s:
                selected.append(s)
                components.add(component_of[s.a])
            else:
                out["issues"].append(issue("Остановка не связана с пешеходной сетью", "Нет привязки в пределах 60 м.",
                                           refs=[{"kind":"node","id":nid}],prefix="catch",category="pedestrian"))
        g,nodes = r.virtual_graph(rg,selected)
        result = r.reach(rg,{node:snap.distance for node,snap in zip(nodes,selected)},out["limit_m"],"length",graph=g)
        polygons.append(r.polygon(result,"buffer",req.buffer_m))
        results.append(result)
    a,b = polygons
    buildings, shapes, counts, none_ids = [], [], {"both":0,"one_side":0,"none":0}, []
    for w in doc.ways.values():
        if not w.tags.get("building") or not w.is_closed:
            continue
        coords = [rg.proj.to_xy(doc.nodes[n].lat,doc.nodes[n].lon) for n in w.nodes if n in doc.nodes]
        if len(coords)<4:
            continue
        shape = Polygon(coords).buffer(0)
        if shape.is_empty:
            continue
        p = shape.centroid
        aa,bb = a.covers(p),b.covers(p)
        cls = "both" if aa and bb else "one_side" if aa or bb else "none"
        counts[cls] += 1
        if cls == "none":
            none_ids.append(w.id)
        buildings.append(feature(rg,shape,building_class=cls,id=w.id))
        shapes.append(shape)
    study = unary_union(shapes).convex_hull if shapes else MultiPoint([])
    bounds = doc.bounds()
    if bounds:
        south,west,north,east = bounds
        study = study.intersection(box(*rg.proj.to_xy(south,west),*rg.proj.to_xy(north,east)))
    uncovered = study.difference(a.union(b))
    out.update(polygons=fc([feature(rg,p,set=label,area_km2=p.area/1e6)
                            for label,p in (("A",a),("B",b),("AB",a.intersection(b)),("uncovered",uncovered))]),
               buildings={"total":sum(counts.values()),**counts,"none_ids":none_ids},building_features=fc(buildings),
               network={"walk_km_total":sum(math.dist(pa,pb) for _,_,_,pa,pb in rg.segments)/1000,
                        "walk_km_reached_a":results[0].length_m/1000,"walk_km_reached_b":results[1].length_m/1000,
                        "components_with_stops":len(components)})
    out["sources"] = [{"label":"СП 42.13330.2016 п.11.24 — 500 м; поправка на рельеф не применяется"},
                      {"label":"СП 131.13330.2025 табл. А.1: подрайон IВ — предварительная классификация, требует проверки по рис. А.1"}]
    return out


def compare(doc,req,out,compute):
    if req.compare == "original":
        before = compute(original_document(doc))
        out["compare"] = {"before":before}
        if out.get("stats") and before.get("stats"):
            out["compare"].update(delta_s=out["stats"]["time_s"]-before["stats"]["time_s"],
                                  delta_m=out["stats"]["length_m"]-before["stats"]["length_m"])
    return out
