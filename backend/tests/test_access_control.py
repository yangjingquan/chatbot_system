import uuid
from app.db import SessionLocal
from app.models import Company, Audit, TENANT

def _set_embed_origins(origins):
    with SessionLocal() as db:
        company = db.get(Company, TENANT)
        company.config = {**company.config, 'allowed_embed_origins': origins}
        db.commit()

def test_admin_routes_require_login(client):
    for path in ('/api/admin/leads', '/api/admin/conversations', '/api/admin/overview', '/api/admin/staff', '/api/admin/settings'):
        assert client.get(path).status_code == 401, path

def test_admin_write_rejects_foreign_origin(admin):
    response = admin.post('/api/admin/documents', headers={'Origin': 'https://evil.example'}, json={
        'title': '越权写入', 'content': '不应成功', 'category': '测试', 'visibility': 'internal', 'status': 'draft'})
    assert response.status_code == 403

def test_failed_login_is_audited(client, admin):
    bad = client.post('/api/auth/login', headers={'Origin': 'http://testserver'},
                      json={'email': 'nobody@example.invalid', 'password': 'definitely-wrong-password'})
    assert bad.status_code == 401
    items = admin.get('/api/admin/audit', params={'q': '登录失败'}).json()['items']
    assert any(row['action'] == '登录失败' and 'nobody@example.invalid' in row['target'] for row in items)

def test_foreign_login_origin_is_audited(client, admin):
    bad = client.post('/api/auth/login', headers={'Origin': 'https://evil.example'},
                      json={'email': 'admin@zhixing.local', 'password': 'x'})
    assert bad.status_code == 403
    items = admin.get('/api/admin/audit', params={'q': '拒绝异常来源'}).json()['items']
    assert any(row['action'] == '拒绝异常来源登录' for row in items)

def test_embed_origin_allowlist_blocks_unknown_site(client, admin):
    previous = admin.get('/api/admin/settings').json().get('allowed_embed_origins') or []
    try:
        _set_embed_origins(['https://www.school.example'])
        denied = client.post('/api/widget/sessions', json={'source_page': '官网嵌入', 'embed_origin': 'https://evil.example'})
        assert denied.status_code == 403
        allowed = client.post('/api/widget/sessions', json={'source_page': '官网嵌入', 'embed_origin': 'https://www.school.example'})
        assert allowed.status_code == 200
        first_party = client.post('/api/widget/sessions', json={'source_page': '官网演示'})
        assert first_party.status_code == 200
        items = admin.get('/api/admin/audit', params={'q': '拒绝嵌入'}).json()['items']
        assert any(row['action'] == '拒绝嵌入来源' and 'evil.example' in row['target'] for row in items)
    finally:
        _set_embed_origins(previous)

def test_settings_can_update_embed_origins(admin):
    previous = admin.get('/api/admin/settings').json().get('allowed_embed_origins') or []
    try:
        response = admin.patch('/api/admin/settings', json={
            'allowed_embed_origins': ['https://www.school.example', 'www.school.example', 'https://admit.school.example']})
        assert response.status_code == 200, response.text
        origins = response.json()['allowed_embed_origins']
        assert origins == ['https://www.school.example', 'https://admit.school.example']
        bad = admin.patch('/api/admin/settings', json={'allowed_embed_origins': ['not a host']})
        assert bad.status_code == 422
    finally:
        admin.patch('/api/admin/settings', json={'allowed_embed_origins': previous})

def test_public_session_rate_limit(client):
    _set_embed_origins(['http://localhost:18080', 'http://127.0.0.1:18080', 'http://testserver'])
    from app.config import settings
    original = settings().public_session_limit
    settings().public_session_limit = 3
    try:
        for _ in range(3):
            assert client.post('/api/widget/sessions', json={'source_page': '限流', 'embed_origin': 'http://testserver'}).status_code == 200
        assert client.post('/api/widget/sessions', json={'source_page': '限流', 'embed_origin': 'http://testserver'}).status_code == 429
    finally:
        settings().public_session_limit = original

def test_visitor_token_still_scopes_conversations(client):
    one = client.post('/api/widget/sessions', json={'embed_origin': 'http://testserver'}).json()
    two = client.post('/api/widget/sessions', json={'embed_origin': 'http://testserver'}).json()
    assert client.get(f'/api/widget/sessions/{one["id"]}', headers={'Authorization': 'Bearer ' + two['token']}).status_code == 404
    assert client.get(f'/api/widget/sessions/{one["id"]}', headers={'Authorization': 'Bearer ' + one['token']}).status_code == 200
