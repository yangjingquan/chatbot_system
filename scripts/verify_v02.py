"""Run only against a restored rehearsal DB; never writes to the application's DB.

Restore a pg_dump into zhixing_v02_rehearsal, then pipe this file to backend Python.
Uses synthetic data and disables paid model calls and background embeddings.
"""
import os
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

original = os.environ['DATABASE_URL']
os.environ['DATABASE_URL'] = original.rsplit('/', 1)[0] + '/zhixing_v02_rehearsal'
os.environ['DEEPSEEK_API_KEY'] = ''
os.environ['EMBEDDING_ENABLED'] = 'false'
os.environ['REDIS_URL'] = ''
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.db import engine
from app.main import app
from app.migrations import migrate_schema

tables = ('admins', 'documents', 'conversations', 'messages', 'leads', 'followups')
with engine.connect() as connection:
    before = {table: connection.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar() for table in tables}

def verify(response):
    assert response.status_code == 200, (response.status_code, response.text[:300])
    return response.json()

with TestClient(app, headers={'Origin': os.environ.get('PUBLIC_ORIGIN', 'http://localhost:18080')}) as client:
    with engine.connect() as connection:
        after = {table: connection.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar() for table in tables}
    assert before == after, (before, after)
    verify(client.post('/api/auth/login', json={'email': os.environ['ADMIN_EMAIL'], 'password': os.environ['ADMIN_PASSWORD']}))
    me = verify(client.get('/api/auth/me'))
    assert me['role'] == 'admin'
    for document in verify(client.get('/api/admin/documents')):
        assert verify(client.get('/api/admin/documents/' + document['id'] + '/revisions'))
    doc = verify(client.post('/api/admin/documents', json={'title': 'PG 迁移演练', 'content': '迁移演练旧内容 PG-7852', 'status': 'published'}))
    path = '/api/admin/documents/' + doc['id']
    sequence = doc['edit_sequence']
    def edit(index):
        return client.patch(path, json={'expected_sequence': sequence, 'content': f'并发编辑 {index} PG-7852'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(edit, range(2)))
    assert statuses == [200, 409], statuses
    draft = verify(client.get(path))
    assert draft['published_content'] == '迁移演练旧内容 PG-7852'
    published = verify(client.post(path + '/publish', json={'expected_sequence': draft['edit_sequence']}))
    history = verify(client.get(path + '/revisions'))
    original_version = next(v for v in history if v['number'] == 1)
    restored = verify(client.post(path + '/restore', json={'expected_sequence': published['edit_sequence'], 'revision_id': original_version['id'], 'note': 'PG 恢复演练'}))
    assert restored['version'] == 3
    assert restored['published_content'] == '迁移演练旧内容 PG-7852'
    password = 'rehearsal-only-password-' + uuid.uuid4().hex
    person = verify(client.post('/api/admin/staff', json={'name': 'PG 演练顾问', 'email': uuid.uuid4().hex + '@example.invalid', 'password': password, 'role': 'advisor'}))
    advisor = TestClient(app, headers={'Origin': os.environ.get('PUBLIC_ORIGIN', 'http://localhost:18080')})
    verify(advisor.post('/api/auth/login', json={'email': person['email'], 'password': password}))
    session = verify(client.post('/api/widget/sessions', json={}))
    lead = verify(client.post('/api/widget/sessions/' + session['id'] + '/contact', headers={'Authorization': 'Bearer ' + session['token']}, json={'name': 'PG 合成访客', 'contact': 'test@example.invalid', 'consent': True}))
    assert advisor.get('/api/admin/leads/' + lead['id']).status_code == 404
    verify(client.patch('/api/admin/leads/' + lead['id'], json={'owner_id': person['id']}))
    verify(advisor.get('/api/admin/leads/' + lead['id']))
    verify(advisor.get('/api/admin/conversations/' + session['id']))
    assert advisor.get('/api/admin/leads-export').status_code == 403
    verify(client.patch('/api/admin/leads/' + lead['id'], json={'owner_id': None}))
    assert advisor.get('/api/admin/conversations/' + session['id']).status_code == 404
    verify(client.patch('/api/admin/staff/' + person['id'], json={'is_active': False}))
    assert advisor.get('/api/auth/me').status_code == 401
    migrate_schema(engine)
    assert verify(client.get(path))['version'] == 3
    print(json.dumps({'status': 'passed', 'preserved_rows': before, 'concurrent_edit_statuses': statuses,
                      'checks': ['restored-v01-data', 'additive-migration', 'history-backfill', 'draft-isolation', 'publish-and-rollback', 'row-lock-conflict', 'advisor-scope', 'reassignment-revokes', 'disabled-session', 'migration-rerun']}, ensure_ascii=False))
