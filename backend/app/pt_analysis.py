"""Frequency-based transit reach with explicit request-local headways.

No guessed routes across missing extract geometry. Each boarding pays a new
wait; staying aboard the same route pays none. Mock data is never real data.
"""
from __future__ import annotations

import heapq
import itertools
import math

from shapely.geometry import LineString, Point

from . import routing as r
from .network_analysis import base, fc, feature, issue, snaps
from .transit import _interval_minutes, _is_route, _is_stop_role


def route_pieces(doc, rel, proj):
    pieces, current = [], []
    for member in rel.members:
        if member.type != "way" or _is_stop_role(member.role):
            continue
        w = doc.ways.get(member.ref)
        if w is None:
            if len(current)>1:
                pieces.append(current)
            current = []
            continue
        # A missing vertex breaks the chain, never creates a fictitious chord.
        chunks, chunk = [], []
        for n in w.nodes:
            if n not in doc.nodes:
                if len(chunk)>1:
                    chunks.append(chunk)
                chunk = []
            else:
                chunk.append(n)
        if len(chunk)>1:
            chunks.append(chunk)
        for ids in chunks:
            if current and current[-1] == ids[-1]:
                ids = ids[::-1]
            elif current and current[0] == ids[0]:
                current.reverse()
            elif current and current[0] == ids[-1]:
                current.reverse()
                ids = ids[::-1]
            if current and current[-1] != ids[0]:
                if len(current)>1:
                    pieces.append(current)
                current = []
            current.extend(ids[1:] if current else ids)
    if len(current)>1:
        pieces.append(current)
    return [LineString([proj.to_xy(doc.nodes[n].lat,doc.nodes[n].lon) for n in ids]) for ids in pieces]


def headway(rel,req=None):
    if req and rel.id in req.headway_overrides:
        source = "тестовые данные" if req.headway_sources.get(rel.id) == "mock" else "введено вручную"
        return req.headway_overrides[rel.id],source
    interval = _interval_minutes(rel.tags.get("interval"))
    return (interval,"OSM interval") if interval else (10.,"по умолчанию — допущение")


def params(doc):
    proj,rows = r.projection(doc),[]
    for rel in doc.relations.values():
        if not _is_route(rel):
            continue
        value,source = headway(rel)
        stops = {m.ref for m in rel.members if m.type == "node" and _is_stop_role(m.role) and m.ref in doc.nodes}
        rows.append({"relation_id":rel.id,"mode":rel.tags["route"],"ref":rel.tags.get("ref",str(rel.id)),
                     "name":rel.tags.get("name",""),"headway_min":value,"headway_source":source,
                     "stops_in_extract":len(stops),"stop_ids":[m.ref for m in rel.members if m.type == "node" and _is_stop_role(m.role) and m.ref in doc.nodes],"pieces":len(route_pieces(doc,rel,proj))})
    return {"routes":rows,"defaults":{"pt_kmh":20,"walk_kmh":4.5,"wait_factor":.5,"max_transfers":2},
            "coverage":{"relations_total":len(rows),"osm_interval":sum(row["headway_source"] == "OSM interval" for row in rows),
                        "default":sum(row["headway_source"] != "OSM interval" for row in rows)},
            "read_only":True,"sources":[{"label":"OSM текущего документа; интервалы без данных вводятся вручную. Тестовые интервалы — синтетические."}]}


def dijkstra(g,start,budget,max_boardings):
    counter = itertools.count()
    initial = (start,0)
    costs,parts,prev,routes = {initial:0.},{initial:(0.,0.,0.)},{},{initial:frozenset()}
    queue = [(0.,next(counter),initial)]
    while queue:
        cost,_,state = heapq.heappop(queue)
        if cost != costs[state]:
            continue
        node,boards = state
        for _,v,e in g.out_edges(node,data=True):
            count = boards + (e["kind"] == "board")
            value = cost+e["t"]
            nxt = (v,count)
            if count > max_boardings or value > budget or value >= costs.get(nxt,math.inf):
                continue
            costs[nxt] = value
            p = list(parts[state])
            p[1 if e["kind"] == "board" else 2 if e["kind"] == "ride" else 0] += e["t"]
            parts[nxt] = tuple(p)
            routes[nxt] = routes[state] | ({e["route"]} if e["kind"] == "ride" else set())
            prev[nxt] = state
            heapq.heappush(queue,(value,next(counter),nxt))
    best = {}
    for state,cost in costs.items():
        n,_ = state
        if n not in best or cost < costs[best[n]]:
            best[n] = state
    return {n:{"cost":costs[s],"walk_s":parts[s][0],"wait_s":parts[s][1],"ride_s":parts[s][2],"routes":routes[s]} for n,s in best.items()}


def analyse(doc,req,rg):
    out = {**base(rg,req.buffer_m),"bands":[],"boundary_points":fc([]),"unreachable_stops":[],
           "boundary_limited":False,"explain":{"sample_points":[]}}
    (start,),out["issues"] = snaps(doc,rg,[req.start.model_dump()])
    if start is None:
        return out
    relations = [rel for rel in doc.relations.values() if _is_route(rel)]
    stop_ids = sorted({m.ref for rel in relations for m in rel.members if m.type == "node" and _is_stop_role(m.role) and m.ref in doc.nodes})
    all_snaps,used = [start],{}
    for nid in stop_ids:
        n = doc.nodes[nid]
        s = r.snap(rg,n.lat,n.lon,60)
        if s is None:
            out["issues"].append(issue("Остановка не связана с пешеходной сетью","Не удалось привязать остановку в пределах 60 м.",refs=[{"kind":"node","id":nid}],prefix="pt",category="pedestrian"))
        else:
            used[nid] = (len(all_snaps),s)
            all_snaps.append(s)
    walk,nodes = r.virtual_graph(rg,all_snaps)
    g = walk.copy()
    for nid,(i,s) in used.items():
        stop = ("stop",nid)
        for a,b in ((nodes[i],stop),(stop,nodes[i])):
            g.add_edge(a,b,kind="access",t=s.distance/(req.walk_kmh/3.6))
    route_rows = []
    for rel in relations:
        pieces = route_pieces(doc,rel,rg.proj)
        stops = []
        seen = set()
        for m in rel.members:
            if m.type != "node" or not _is_stop_role(m.role) or m.ref not in used or m.ref in seen:
                continue
            seen.add(m.ref)
            n = doc.nodes[m.ref]
            p = Point(*rg.proj.to_xy(n.lat,n.lon))
            if not pieces:
                continue
            idx = min(range(len(pieces)),key=lambda i:pieces[i].distance(p))
            if pieces[idx].distance(p)>80:
                continue
            stops.append((m.ref,idx,pieces[idx].project(p)))
        # OSM member order is stop order. Orient each isolated piece accordingly.
        reverse = {i for i in range(len(pieces)) if len(vals := [s[2] for s in stops if s[1] == i])>1 and vals[-1]<vals[0]}
        stops = [(nid,i,pieces[i].length-pos if i in reverse else pos) for nid,i,pos in stops]
        interval,source = headway(rel,req)
        route_rows.append({"relation_id":rel.id,"ref":rel.tags.get("ref",str(rel.id)),"headway_min":interval,"headway_source":source})
        broken = len(pieces)>1 or not pieces or any(
            m.type == "way" and not _is_stop_role(m.role) and
            (m.ref not in doc.ways or any(n not in doc.nodes for n in doc.ways[m.ref].nodes))
            for m in rel.members)
        for i,(nid,piece,pos) in enumerate(stops):
            state,stop = ("route",rel.id,i),("stop",nid)
            g.add_edge(stop,state,kind="board",t=req.wait_factor*interval*60)
            g.add_edge(state,stop,kind="alight",t=0)
            if i+1 < len(stops):
                _,next_piece,next_pos = stops[i+1]
                if piece == next_piece and next_pos >= pos:
                    speed = req.pt_kmh.get(rel.tags["route"],20)/3.6
                    g.add_edge(state,("route",rel.id,i+1),kind="ride",t=float(next_pos-pos)/speed,route=rel.id)
                else:
                    broken = True
        if broken:
            out["issues"].append(issue(f"Маршрут {rel.tags.get('ref',rel.id)} разорван в выгрузке",
                                       "Поездки между несвязанными фрагментами маршрута не моделируются.",refs=[{"kind":"relation","id":rel.id}],prefix="pt"))
    out["routes"] = route_rows
    max_budget = max(req.bands_min)*60
    no_transfer = dijkstra(g,nodes[0],max_budget,1)
    with_transfer = dijkstra(g,nodes[0],max_budget,1+req.max_transfers)
    boundaries = []
    bounds = doc.bounds()
    previous_polygons = {}
    for minutes in req.bands_min:
        band = {"minutes":minutes}
        for key,costs in (("no_transfer",no_transfer),("with_transfer",with_transfer)):
            walk_costs = {(n,None):v["cost"] for n,v in costs.items() if n in walk}
            result = r.reached(rg,walk,walk_costs,minutes*60,"time")
            poly = r.polygon(result,req.how,req.buffer_m)
            if key in previous_polygons:
                poly = poly.union(previous_polygons[key])
            if key == "with_transfer":
                poly = poly.union(previous_polygons["no_transfer"])
            previous_polygons[key] = poly
            reached_stops = [nid for nid in stop_ids if costs.get(("stop",nid),{}).get("cost",math.inf)<=minutes*60]
            route_ids = sorted({rid for v in costs.values() if v["cost"]<=minutes*60 for rid in v["routes"]})
            band[key] = {"area_km2":poly.area/1e6,"stops_reached":len(reached_stops),"stop_ids":reached_stops,
                         "walk_nodes_reached":int(sum(v["cost"]<=minutes*60 for n,v in costs.items() if n in walk)),
                         "routes_used":[doc.relations[i].tags.get("ref",str(i)) for i in route_ids],"geometry":r.geojson(rg,poly)}
            if key == "with_transfer":
                boundaries.extend(feature(rg,Point(*p),band=minutes) for p in result.candidates)
                if bounds:
                    south,west,north,east = bounds
                    for n,c in result.cost.items():
                        if c>minutes*60:
                            continue
                        lat,lon = rg.proj.to_latlon(*walk.nodes[n]["xy"])
                        if min(abs(lat-south)*110540,abs(lat-north)*110540,abs(lon-west)*rg.proj.kx,abs(lon-east)*rg.proj.kx)<40:
                            out["boundary_limited"] = True
        out["bands"].append(band)
    reached_stops = out["bands"][-1]["with_transfer"]["stop_ids"]
    out["unreachable_stops"] = [nid for nid in stop_ids if nid not in reached_stops]
    out["stops"] = [{"id":nid,"coord":[doc.nodes[nid].lon,doc.nodes[nid].lat],"reached":nid in reached_stops} for nid in stop_ids]
    # Every reachable vertex has its exact walk/wait/ride decomposition. The map
    # chooses a nearby sample; no invented costs inside a drawing-only polygon.
    for n,v in with_transfer.items():
        if n in walk:
            out["explain"]["sample_points"].append({"coord":list(reversed(rg.proj.to_latlon(*walk.nodes[n]["xy"]))),
                                                    **{k:v[k] for k in ("walk_s","wait_s","ride_s")},"total_s":v["cost"]})
    out["boundary_points"] = fc(boundaries)
    out["assumptions"] += [
        {"key":"pt_kmh","label":f"Скорости ОТ, км/ч — допущение: {req.pt_kmh}. Без расписаний и пробок."},
        {"key":"waiting","label":f"Ожидание = {req.wait_factor} интервала — допущение; при каждой посадке, включая пересадку."},
        {"key":"headways","label":"Приоритет интервала: введённый вручную / тестовый → OSM interval → 10 мин по умолчанию. Тестовые значения синтетические; реальными данными не являются."},
        {"key":"geometry","label":"Геометрия маршрутов обрезана выгрузкой. Кандидаты границы объединяются по физическому ребру; сохраняются крайние точки. Полигон — отрисовка, доступность определяется по сети."}]
    out["sources"] = [{"label":"Маршруты и остановки: OSM текущего документа. Интервалы: ручной ввод / OSM / подписанные допущения."}]
    return out
