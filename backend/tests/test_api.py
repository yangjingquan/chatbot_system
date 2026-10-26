import uuid

def session(client):
    res = client.post('/api/widget/sessions', json={'source_page': '测试站点'})
    assert res.status_code == 200
    data = res.json()
    return data['id'], {'Authorization': 'Bearer ' + data['token']}

def test_auth_and_csrf(client):
    assert client.get('/api/admin/documents').status_code == 401
    bad = client.post('/api/auth/login', headers={'Origin': 'http://evil.example'}, json={'email':'a','password':'b'})
    assert bad.status_code == 403

def test_login_brute_force_is_limited(client):
    for _ in range(10):
        response=client.post('/api/auth/login',headers={'Origin':'http://testserver'},json={'email':'invalid@example.invalid','password':'wrong'})
        assert response.status_code==401
    assert client.post('/api/auth/login',headers={'Origin':'http://testserver'},json={'email':'invalid@example.invalid','password':'wrong'}).status_code==429

def test_admin_config_never_returns_key(admin):
    data = admin.get('/api/admin/settings').json()
    assert 'deepseek_api_key' not in str(data)
    assert 'model_configured' in data

def test_public_retrieval_and_unpublish(admin):
    res = admin.post('/api/admin/documents', json={'title':'隔离测试', 'content':'专属测试短语星河蓝鲸 ZX-TEST-187', 'category':'测试', 'visibility':'internal', 'status':'published'})
    assert res.status_code == 200
    doc = res.json()
    matches = admin.post('/api/admin/retrieval-test', json={'query':'星河蓝鲸 ZX-TEST-187', 'public_only':True}).json()
    assert not any(s['document_id'] == doc['id'] for s in matches['sources'])
    admin.patch('/api/admin/documents/' + doc['id'], json={'visibility':'public'})
    matches = admin.post('/api/admin/retrieval-test', json={'query':'星河蓝鲸 ZX-TEST-187'}).json()
    assert any(s['document_id'] == doc['id'] for s in matches['sources'])
    admin.patch('/api/admin/documents/' + doc['id'], json={'status':'archived'})
    matches = admin.post('/api/admin/retrieval-test', json={'query':'星河蓝鲸 ZX-TEST-187'}).json()
    assert not any(s['document_id'] == doc['id'] for s in matches['sources'])

def test_visitor_cannot_read_other_conversation(client):
    sid, headers = session(client)
    other, other_headers = session(client)
    assert client.get(f'/api/widget/sessions/{sid}', headers=other_headers).status_code == 404
    assert client.get(f'/api/widget/sessions/{sid}', headers=headers).status_code == 200

def test_contact_requires_consent_and_is_idempotent(client):
    sid, headers = session(client)
    payload = {'name':'测试访客', 'contact':'13800000000', 'consent':False}
    assert client.post(f'/api/widget/sessions/{sid}/contact', headers=headers, json=payload).status_code == 422
    payload['consent'] = True
    one = client.post(f'/api/widget/sessions/{sid}/contact', headers=headers, json=payload)
    two = client.post(f'/api/widget/sessions/{sid}/contact', headers=headers, json=payload)
    assert one.status_code == two.status_code == 200
    assert one.json()['id'] == two.json()['id']

def test_messages_extractive_and_retry(client):
    sid, headers = session(client)
    payload = {'content':'公安联考基础班多少钱', 'client_id':str(uuid.uuid4())}
    first = client.post(f'/api/widget/sessions/{sid}/messages', headers=headers, json=payload)
    assert first.status_code == 200
    assert 'extractive' in first.text
    assert '1680' in first.text
    retry = client.post(f'/api/widget/sessions/{sid}/messages', headers=headers, json=payload)
    assert 'done' in retry.text
    history = client.get(f'/api/widget/sessions/{sid}', headers=headers).json()
    assert len([m for m in history['messages'] if m['role'] == 'user']) == 1
    assert all('ZX-INTERNAL-4827' not in m['content'] for m in history['messages'])

def test_handoff_stops_bot_and_agent_can_reply(admin):
    sid, headers = session(admin)
    assert admin.post(f'/api/widget/sessions/{sid}/handoff', headers=headers).status_code == 200
    payload = {'content':'国考课程多少钱', 'client_id':str(uuid.uuid4())}
    response = admin.post(f'/api/widget/sessions/{sid}/messages', headers=headers, json=payload)
    assert 'waiting' in response.text
    assert admin.post(f'/api/admin/conversations/{sid}/takeover').status_code == 200
    assert admin.post(f'/api/admin/conversations/{sid}/reply', json={'content':'您好，我是人工顾问'}).status_code == 200
    history = admin.get(f'/api/widget/sessions/{sid}', headers=headers).json()
    assert any(m['role'] == 'agent' for m in history['messages'])

def test_demo_metrics_excluded_by_default(admin):
    assert admin.get('/api/admin/overview').json()['include_demo'] is False

def test_invalid_upload_is_visible(admin):
    bad = admin.post('/api/admin/documents/upload', files={'file':('bad.exe', b'bad', 'application/octet-stream')})
    assert bad.status_code == 415

def test_contact_encrypted_at_rest(client):
    from app.db import engine
    from sqlalchemy import text
    sid,headers=session(client)
    response=client.post(f'/api/widget/sessions/{sid}/contact',headers=headers,json={'name':'测试加密','contact':'13900000000','consent':True})
    assert response.status_code==200
    with engine.connect() as conn:
        raw=conn.execute(text('SELECT contact FROM leads WHERE conversation_id=:id'),{'id':sid}).scalar()
    assert raw.startswith('enc:v1:')
    assert '13900000000' not in raw

def test_unknown_question_has_no_fabricated_sources(client):
    sid,headers=session(client)
    response=client.post(f'/api/widget/sessions/{sid}/messages',headers=headers,json={'content':'木星卫星表面化学成分XYZQ','client_id':str(uuid.uuid4())})
    assert 'unknown' in response.text
    assert '没有找到足够依据' in response.text

def test_mark_demo_excludes_test_traffic(admin):
    sid,headers=session(admin)
    admin.post(f'/api/widget/sessions/{sid}/messages',headers=headers,json={'content':'未知问题XYZQ','client_id':str(uuid.uuid4())})
    before=admin.get('/api/admin/overview').json()['counts']['conversations']
    assert admin.post(f'/api/admin/conversations/{sid}/mark-demo').status_code==200
    after=admin.get('/api/admin/overview').json()['counts']['conversations']
    assert after==before-1

def test_opening_widget_without_engagement_does_not_inflate_consultations(admin):
    before=admin.get('/api/admin/overview').json()['counts']['conversations']
    sid,headers=session(admin)
    assert admin.get('/api/admin/overview').json()['counts']['conversations']==before
    admin.post(f'/api/widget/sessions/{sid}/contact',headers=headers,json={'name':'直接留资','contact':'test@example.invalid','consent':True})
    assert admin.get('/api/admin/overview').json()['counts']['conversations']==before+1
