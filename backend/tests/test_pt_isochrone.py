import math

from fastapi.testclient import TestClient
from shapely.geometry import shape

from app.analysis_requests import PtRequest
from app.main import app
from app.osm_model import Member, Node, OsmDocument, Relation, Way
from app.pt_analysis import analyse, dijkstra
from app.routing import build_graph


def transit_network():
    d = OsmDocument()
    # 3 km route, with platforms on the walk geometry.
    d.nodes = {i:Node(i,0,(i-1)*1500/111320,{"public_transport":"platform"}) for i in (1,2,3)}
    d.ways[10] = Way(10,[1,2,3],{"highway":"footway"})
    d.ways[11] = Way(11,[1,2,3],{"highway":"residential"})
    d.relations[100] = Relation(100,[Member("way",11),*[Member("node",i,"platform") for i in (1,2,3)]],
                                {"type":"route","route":"bus","ref":"1"})
    return d


def test_wait_ride_and_manual_mock_provenance():
    d = transit_network()
    req = PtRequest.model_validate({"from":{"node_id":1},"bands_min":[5,10,15],"headway_overrides":{"100":10},"headway_sources":{"100":"mock"},"how":"buffer"})
    before = d.to_xml()
    out = analyse(d,req,build_graph(d,"walk"))
    end = next(p for p in out["explain"]["sample_points"] if math.isclose(p["coord"][0],3000/111320,abs_tol=1e-8))
    assert math.isclose(end["wait_s"],300,abs_tol=.1)
    assert math.isclose(end["ride_s"],540,abs_tol=.1)
    assert math.isclose(end["total_s"],840,abs_tol=.1)
    assert math.isclose(end["total_s"],end["walk_s"]+end["wait_s"]+end["ride_s"])
    assert out["routes"][0]["headway_source"] == "тестовые данные"
    assert d.to_xml() == before
    assert out["bands"][0]["with_transfer"]["area_km2"] <= out["bands"][1]["with_transfer"]["area_km2"]
    assert shape(out["bands"][2]["with_transfer"]["geometry"]).is_valid


def test_transfer_limit_and_parallel_lower_headway():
    import networkx as nx
    g = nx.MultiDiGraph()
    for u,v,t,kind in [("s","r1",300,"board"),("s","fast",100,"board"),("fast","mid",60,"ride"),
                        ("r1","mid",60,"ride"),("mid","w",0,"alight"),("w","r2",100,"board"),("r2","end",60,"ride")]:
        g.add_edge(u,v,t=t,kind=kind,route=1 if u != "r2" else 2)
    assert "end" not in dijkstra(g,"s",1000,1)
    costs = dijkstra(g,"s",1000,2)
    assert costs["mid"]["cost"] == 160
    assert costs["end"]["cost"] == 320
    assert costs["end"]["wait_s"] == 200


def test_clipped_route_never_chords_missing_way():
    d = transit_network()
    d.relations[100].members.insert(1,Member("way",999))
    # No other route piece after the absent member: the available piece is still
    # valid, but missing geometry must be reported separately.
    req = PtRequest.model_validate({"from":{"node_id":1}})
    out = analyse(d,req,build_graph(d,"walk"))
    assert out["read_only"]
    assert any("разорван" in i["title"] for i in out["issues"])


def test_pt_api_read_only_and_invalid_input():
    c = TestClient(app)
    root = '/api/documents/'+c.post('/api/documents',files={'file':('test.osm',transit_network().to_xml(),'application/xml')}).json()['doc_id']
    before = c.get(root).json()
    assert c.get(root+'/analysis/pt-params').json()['routes'][0]['headway_min'] == 10
    response = c.post(root+'/analysis/pt-isochrone',json={'from':{'node_id':1},'headway_overrides':{'100':3},'headway_sources':{'100':'manual'}})
    assert response.status_code == 200,response.text
    assert response.json()['routes'][0]['headway_source'] == 'введено вручную'
    after = c.get(root).json()
    assert before['rev'] == after['rev'] and before['history'] == after['history']
    assert c.post(root+'/analysis/pt-isochrone',json={'from':{'node_id':1},'headway_overrides':{'100':-1}}).status_code == 400
