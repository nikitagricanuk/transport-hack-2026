from fastapi.testclient import TestClient
from app import main


def test_private_upload_stays_off_lobby(raw, monkeypatch):
    monkeypatch.setattr(main, 'SESSIONS', {})
    monkeypatch.setattr(main, '_sample_files', lambda: {})  # isolate from bundled samples
    c = TestClient(main.app)
    did = c.post('/api/documents', files={'file': ('roads.osm', raw, 'application/xml')}).json()['doc_id']
    c.post(f'/api/documents/{did}/ops', json={'op': 'set_tags', 'args': {'kind': 'way', 'id': 100, 'tags': {'maxspeed': '40'}}}).raise_for_status()
    c.post(f'/api/documents/{did}/rename', json={'name': 'Team map'}).raise_for_status()
    main.SESSIONS.clear()
    assert c.get('/api/documents').json() == []  # private drafts are not listed
    restored = c.get(f'/api/documents/{did}').json()
    assert restored['filename'] == 'Team_map.osm'
    assert restored['history']['can_undo']
    assert c.post(f'/api/documents/{did}/rename', json={'name': '   '}).status_code == 400
    assert c.post('/api/documents/missing/rename', json={'name': 'map'}).status_code == 404


def test_new_blank_document_is_private(monkeypatch):
    monkeypatch.setattr(main, 'SESSIONS', {})
    monkeypatch.setattr(main, '_sample_files', lambda: {})  # isolate from bundled samples
    c = TestClient(main.app)
    response = c.post('/api/documents/new', json={'name': 'New map'})
    response.raise_for_status()
    state = response.json()
    assert state['filename'] == 'New_map.osm'
    assert state['geo']['ways']['features'] == []
    assert c.get('/api/documents').json() == []
    assert c.get(f'/api/documents/{state["doc_id"]}').json()['doc_id'] == state['doc_id']
    assert c.post('/api/documents/new', json={'name': ' '}).status_code == 400


def test_starter_is_shared_and_listed(raw, tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'SESSIONS', {})
    sample = tmp_path / 'starter.osm'
    sample.write_bytes(raw)
    monkeypatch.setattr(main, '_sample_files', lambda: {'starter.osm': sample})
    c = TestClient(main.app)
    lobby = c.get('/api/documents').json()
    assert len(lobby) == 1
    assert lobby[0]['filename'] == 'starter.osm'
    assert lobby[0]['shared'] is True
    assert lobby[0]['doc_id'].startswith('sample-')
    first = c.post('/api/workspace/samples/starter.osm/open').json()
    second = c.post('/api/workspace/samples/starter.osm/open').json()
    assert first['doc_id'] == second['doc_id'] == lobby[0]['doc_id']
    main.SESSIONS.clear()
    assert c.post('/api/workspace/samples/starter.osm/open').json()['doc_id'] == first['doc_id']
    assert len(c.get('/api/documents').json()) == 1
    private = c.post('/api/documents', files={'file': ('secret.osm', raw, 'application/xml')}).json()
    rows = c.get('/api/documents').json()
    assert len(rows) == 1 and rows[0]['doc_id'] == first['doc_id']
    assert private['doc_id'] not in {r['doc_id'] for r in rows}
    assert c.post('/api/workspace/samples/missing.osm/open').status_code == 404
