from fastapi.testclient import TestClient
from app import main


def test_index_and_rename_survive_restart_with_history(raw, monkeypatch):
    monkeypatch.setattr(main, 'SESSIONS', {})
    c = TestClient(main.app)
    did = c.post('/api/documents', files={'file': ('roads.osm', raw, 'application/xml')}).json()['doc_id']
    c.post(f'/api/documents/{did}/ops', json={'op': 'set_tags', 'args': {'kind': 'way', 'id': 100, 'tags': {'maxspeed': '40'}}}).raise_for_status()
    c.post(f'/api/documents/{did}/rename', json={'name': 'Team map'}).raise_for_status()
    main.SESSIONS.clear()
    rows = c.get('/api/documents').json()
    assert len(rows) == 1 and rows[0]['filename'] == 'Team_map.osm'
    assert rows[0]['doc_id'] == did and rows[0]['updated_at'] > 0
    assert not main.SESSIONS  # listing doesn't load maps
    restored = c.get(f'/api/documents/{did}').json()
    assert restored['filename'] == 'Team_map.osm'
    assert restored['history']['can_undo']
    assert c.post(f'/api/documents/{did}/rename', json={'name': '   '}).status_code == 400
    assert c.post('/api/documents/missing/rename', json={'name': 'map'}).status_code == 404


def test_new_blank_document(monkeypatch):
    monkeypatch.setattr(main, 'SESSIONS', {})
    c = TestClient(main.app)
    response = c.post('/api/documents/new', json={'name': 'New map'})
    response.raise_for_status()
    state = response.json()
    assert state['filename'] == 'New_map.osm'
    assert state['geo']['ways']['features'] == []
    assert c.get('/api/documents').json()[0]['doc_id'] == state['doc_id']
    assert c.post('/api/documents/new', json={'name': ' '}).status_code == 400


def test_starter_is_shared_and_restored(raw, tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'SESSIONS', {})
    sample = tmp_path / 'starter.osm'
    sample.write_bytes(raw)
    monkeypatch.setattr(main, '_sample_files', lambda: {'starter.osm': sample})
    c = TestClient(main.app)
    first = c.post('/api/workspace/samples/starter.osm/open').json()
    second = c.post('/api/workspace/samples/starter.osm/open').json()
    assert first['doc_id'] == second['doc_id']
    main.SESSIONS.clear()
    assert c.post('/api/workspace/samples/starter.osm/open').json()['doc_id'] == first['doc_id']
    assert len(c.get('/api/documents').json()) == 1
    assert c.post('/api/workspace/samples/missing.osm/open').status_code == 404
