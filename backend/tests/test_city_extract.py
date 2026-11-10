import gzip
import http.client
import io
import xml.etree.ElementTree as ET

from scripts import fetch_city


class Response(io.BytesIO):
    headers = {"Content-Encoding": "gzip"}


def test_fetch_retries_truncated_and_incomplete_overpass_responses(monkeypatch):
    def open_request(request, timeout):
        assert request.get_header("Accept-encoding") == "gzip"
        if request.full_url == mirrors[0]:
            raise http.client.IncompleteRead(b"<osm>")
        return Response(gzip.compress(replies[request.full_url]))

    mirrors = [f"https://example.test/{i}" for i in range(3)]
    replies = {
        mirrors[1]: b"<osm><remark>timeout</remark></osm>",
        mirrors[2]: b'<osm><node id="1" lat="1" lon="2"/></osm>',
    }
    monkeypatch.setattr(fetch_city, "MIRRORS", mirrors)
    monkeypatch.setattr(fetch_city.urllib.request, "urlopen", open_request)
    assert fetch_city.fetch("test", attempts=1).find("node").get("id") == "1"


def test_tiles_merge_shared_nodes_and_keep_newest_version(monkeypatch):
    replies = iter([
        '<osm><node id="1" version="1"/><node id="2"/><way id="3"><nd ref="1"/><nd ref="2"/></way></osm>',
        '<osm><node id="1" version="2"/><node id="2"/><way id="3"><nd ref="1"/><nd ref="2"/></way></osm>',
        '<osm/>', '<osm/>',
    ])
    queries = []

    def fetch(query):
        queries.append(query)
        return ET.fromstring(next(replies))

    monkeypatch.setattr(fetch_city, "fetch", fetch)
    extract = ET.fromstring(fetch_city.city_extract((10, 20, 12, 22), 2))
    assert len(set(queries)) == 4
    assert extract.find("bounds").attrib == {"minlat": "10", "minlon": "20", "maxlat": "12", "maxlon": "22"}
    assert [node.get("id") for node in extract.findall("node")] == ["1", "2"]
    assert extract.find("node").get("version") == "2"
    assert len(extract.findall("way")) == 1
