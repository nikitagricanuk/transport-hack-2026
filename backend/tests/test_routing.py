import math
import pytest
from pathlib import Path

from fastapi.testclient import TestClient
from shapely.geometry import shape

from app import routing as r
from app.analysis_requests import RouteRequest
from app.edits import History, apply_op
from app.fix_preview import preview
from app.main import app
from app.network_analysis import route
from app.osm_model import Member, Node, OsmDocument, Relation, Way
from app.pedestrian import check
from app.validation import validate


def network(oneway=False):
    d = OsmDocument()
    # One kilometre along the equator, with a vertex in the middle.
    d.nodes = {i:Node(i,0,x/111320) for i,x in ((1,0),(2,500),(3,1000))}
    d.ways = {10:Way(10,[1,2,3],{"highway":"residential","maxspeed":"60",**({"oneway":"yes"} if oneway else {})})}
    return d


def test_snaps_same_edge_obey_direction_and_no_mutation():
    d = network(True)
    before = d.to_xml()
    rg = r.build_graph(d,"car")
    a,b = [r.snap(rg,0,x/111320,60) for x in (100,400)]
    result = r.shortest_path(rg,a,b,"time")
    assert math.isclose(result["length_m"],300,abs_tol=.1)
    assert math.isclose(result["time_s"],18,abs_tol=.01)
    assert r.shortest_path(rg,b,a,"length") is None
    assert d.to_xml() == before


def test_restrictions_and_parallel_ways():
    d = network()
    d.nodes[4] = Node(4,.0045,500/111320)
    d.ways[11] = Way(11,[2,4,3],{"highway":"residential"})
    d.ways[10].nodes = [1,2]
    d.ways[12] = Way(12,[2,3],{"highway":"residential"})
    d.relations[20] = Relation(20,[Member("way",10,"from"),Member("way",12,"to"),Member("node",2,"via")],
                               {"type":"restriction","restriction":"no_right_turn"})
    d.relations[21] = Relation(21,[Member("way",10,"from"),Member("way",12,"to"),Member("way",11,"via")],
                               {"type":"restriction","restriction":"no_right_turn"})
    rg = r.build_graph(d,"car")
    result = r.shortest_path(rg,r.snap(rg,0,0,60),r.snap(rg,0,1000/111320,60),"length")
    assert 11 in [e[2]["way"] for e in result["edges"]]
    assert result["length_m"] > 1100
    assert rg.skipped_restrictions == [21]
    # A parallel allowed road must survive the way-keyed graph.
    d.ways[13] = Way(13,[2,3],{"highway":"residential"})
    rg = r.build_graph(d,"car")
    assert rg.g.number_of_edges(2,3) == 2


def test_walk_rules_and_direction():
    for tags in ({"highway":"motorway"},{"highway":"footway","foot":"no"},
                 {"highway":"residential","sidewalk":"separate"},{"highway":"residential","sidewalk":"no"}):
        assert not r.walkable(tags)
    assert r.walkable({"highway":"steps"})
    assert r.walkable({"highway":"cycleway","foot":"designated"})
    rg = r.build_graph(network(True),"walk")
    assert rg.g.has_edge(3,2)


def test_partial_reach_unions_directions():
    rg = r.build_graph(network(),"walk")
    start = r.snap(rg,0,250/111320,60)
    g,(node,) = r.virtual_graph(rg,[start])
    reach = r.reach(rg,{node:0},500,"length",graph=g)
    assert math.isclose(reach.length_m,750,abs_tol=.1)
    assert r.polygon(reach).area > 750*80
    full = r.reach(rg,{1:0,3:0},1000,"length")
    assert math.isclose(full.length_m,1000,abs_tol=.1)


def test_probe_connector_restores_document_even_on_error():
    d = network()
    d.nodes[4] = Node(4,0,1020/111320)
    d.nodes[5] = Node(5,0,1200/111320)
    d.ways[11] = Way(11,[4,5],{"highway":"footway"})
    req = RouteRequest.model_validate({"from":{"node_id":1},"to":{"node_id":5},"mode":"walk"})
    assert not route(d,req)["found"]
    before = d.to_xml()
    fix = route(d,req)["issues"][0]["fixes"][0]
    result = preview(d,fix["op"],fix["args"],probe=lambda doc:route(doc,req))
    assert result["probe"]["found"]
    assert before == d.to_xml()
    assert d._next_new_id == -1
    def failing_probe(doc):
        raise RuntimeError("probe failed")
    with pytest.raises(RuntimeError):
        preview(d,fix["op"],fix["args"],probe=failing_probe)
    assert d.to_xml() == before and d._next_new_id == -1


def test_pedestrian_info_and_reviewed_fix():
    d = network()
    d.ways[10].tags["highway"] = "primary_link"
    p = check(d)
    assert p and all(i["severity"] == "info" and i["levels"] == [] for i in p)
    old = validate(d)["summary"]["counts"]
    fix = p[0]["fixes"][0]
    apply_op(d,History(),fix["op"],fix["args"])
    assert not check(d)
    new = validate(d)["summary"]["counts"]
    assert (old["warning"],old["critical"]) == (new["warning"],new["critical"])


def test_api_readonly_bands_and_catchment():
    c = TestClient(app)
    uploaded = c.post("/api/documents",files={"file":("test.osm",network().to_xml(),"application/xml")}).json()
    root = f"/api/documents/{uploaded['doc_id']}"
    before = c.get(root).json()
    route_req = {"from":{"lat":0,"lon":0},"to":{"node_id":3}}
    assert c.post(root+"/analysis/route",json=route_req).json()["found"]
    resp = c.post(root+"/analysis/isochrone",json={"from":{"node_id":1},"step_min":.5,"count":3})
    assert resp.status_code == 200,resp.text
    bands = resp.json()["bands"]
    assert math.isclose(bands[0]["road_km"],.5,abs_tol=.01)
    assert bands[0]["area_km2"] <= bands[1]["area_km2"] <= bands[2]["area_km2"]
    catch = c.post(root+"/analysis/catchment",json={"set_a":[1],"set_b":[3]}).json()
    polygons = {f["properties"]["set"]:shape(f["geometry"]) for f in catch["polygons"]["features"]}
    assert polygons["AB"].area <= min(polygons["A"].area,polygons["B"].area)
    after = c.get(root).json()
    assert before["rev"] == after["rev"] and before["history"] == after["history"]
    assert c.post(root+"/analysis/isochrone",json={"from":{"node_id":1},"step_min":10,"count":10}).status_code == 400


def test_raw_restrictions_offer_repair_and_preview_is_readonly():
    d = OsmDocument.parse((Path(__file__).parents[2]/"data/example.osm").read_bytes())
    repairs = [f for i in validate(d)["issues"] for f in i["fixes"] if f["op"] == "replace_restriction_way"]
    assert repairs
    before = d.to_xml()
    assert preview(d,repairs[0]["op"],repairs[0]["args"])["counts"]["modified"] == 1
    assert d.to_xml() == before


def test_edit_log_retains_review_reason(raw):
    c = TestClient(app)
    root = '/api/documents/'+c.post('/api/documents',files={'file':('test.osm',raw,'application/xml')}).json()['doc_id']
    label = 'Указать скорость: Нет maxspeed — ПДД 10.2: нет maxspeed'
    response = c.post(root+'/ops',json={'op':'set_tags','args':{'kind':'way','id':100,'tags':{'maxspeed':'60'}},'label':label,'reason':'ПДД 10.2: нет maxspeed'})
    assert response.status_code == 200,response.text
    assert '| ПДД 10.2: нет maxspeed |' in c.get(root+'/edit-log').text
    c.post(root+'/undo')
    c.post(root+'/redo')
    assert '| ПДД 10.2: нет maxspeed |' in c.get(root+'/edit-log').text
