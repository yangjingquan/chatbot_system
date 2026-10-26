from datetime import timedelta
import uuid
from app.models import now
from test_permissions import employee

def create(admin,**extra):
    body={'title':'版本验收知识','content':'星河初版课程价格为100元，仅供测试。','status':'published',**extra}
    response=admin.post('/api/admin/documents',json=body)
    assert response.status_code==200,response.text
    return response.json()

def mutate(client,doc,action,**payload):
    return client.post(f'/api/admin/documents/{doc["id"]}/{action}',json={'expected_sequence':doc['edit_sequence'],**payload})

def test_edit_is_draft_until_review_and_rollback_makes_new_version(admin):
    doc=create(admin)
    edited=admin.patch('/api/admin/documents/'+doc['id'],json={'expected_sequence':doc['edit_sequence'],'content':'星河新版课程价格为200元，仅供测试。'}).json()
    assert edited['draft_status']=='draft'
    assert edited['published_content'].startswith('星河初版')
    result=admin.post('/api/admin/retrieval-test',json={'query':'星河课程价格'}).json()
    assert not any('星河新版' in s['text'] for s in result['sources'])
    submitted=mutate(admin,edited,'submit').json()
    assert submitted['draft_status']=='submitted'
    published=mutate(admin,submitted,'publish',note='已核验价格').json()
    assert published['version']==2
    assert published['published_content'].startswith('星河新版')
    history=admin.get(f'/api/admin/documents/{doc["id"]}/revisions').json()
    assert len(history)==2
    original=next(r for r in history if r['number']==1)
    restored=mutate(admin,published,'restore',revision_id=original['id'],note='恢复原始测试资料').json()
    assert restored['version']==3
    assert restored['published_content'].startswith('星河初版')

def test_knowledge_operator_may_submit_but_not_publish(admin):
    user,client=employee(admin,'knowledge')
    doc=create(admin,status='draft')
    edited=client.patch('/api/admin/documents/'+doc['id'],json={'expected_sequence':doc['edit_sequence'],'content':'知识运营已检查的草稿内容'}).json()
    assert mutate(client,edited,'submit').status_code==200
    fresh=admin.get('/api/admin/documents/'+doc['id']).json()
    assert mutate(client,fresh,'publish').status_code==403
    assert client.post('/api/admin/documents',json={'title':'越权发布','content':'测试内容','status':'published'}).status_code==403
    assert mutate(admin,fresh,'reject',note='请补充来源').status_code==200
    rejected=client.get('/api/admin/documents/'+doc['id']).json()
    assert rejected['draft_status']=='draft'
    assert '请补充来源' in rejected['review_note']

def test_stale_edit_and_submitted_edit_cannot_override_review(admin):
    doc=create(admin)
    first=admin.patch('/api/admin/documents/'+doc['id'],json={'expected_sequence':doc['edit_sequence'],'content':'第一次编辑内容'}).json()
    assert admin.patch('/api/admin/documents/'+doc['id'],json={'expected_sequence':doc['edit_sequence'],'content':'过期编辑覆盖'}).status_code==409
    submitted=mutate(admin,first,'submit').json()
    assert admin.patch('/api/admin/documents/'+doc['id'],json={'expected_sequence':submitted['edit_sequence'],'content':'待审核中偷偷修改'}).status_code==409

def test_policies_require_year_exam_region_and_verified_source(admin):
    invalid=admin.post('/api/admin/documents',json={'title':'政策测试','content':'招录政策测试','status':'published','knowledge_type':'policy'})
    assert invalid.status_code==422
    doc=create(admin,title='星河专属政策验收',content='星河专属政策验收 POLICY-7428：2027江苏国考课程适用条件。',knowledge_type='policy',exam_type='国考',exam_year=2027,region='江苏',source_url='https://www.scs.gov.cn/',policy_verified=True)
    assert doc['exam_year']==2027
    for query,should_match in [('2027江苏省国考课程',True),('2027浙江省国考课程',False),('江苏省国考课程',False),('2027国考课程',False)]:
        result=admin.post('/api/admin/retrieval-test',json={'query':query+' 星河专属政策验收 POLICY-7428'}).json()
        assert any(s['document_id']==doc['id'] for s in result['sources']) is should_match,query

def test_future_expired_and_invalid_dates_are_filtered(admin):
    old=create(admin,content='过期星河政策测试',valid_until=(now()-timedelta(days=1)).isoformat())
    future=create(admin,content='未来星河政策测试',valid_from=(now()+timedelta(days=1)).isoformat())
    result=admin.post('/api/admin/retrieval-test',json={'query':'星河政策测试'}).json()
    assert all(s['document_id'] not in (old['id'],future['id']) for s in result['sources'])
    bad=admin.post('/api/admin/documents',json={'title':'日期错误','content':'测试','valid_from':'2027-02-02T00:00:00+08:00','valid_until':'2027-01-01T23:59:59+08:00'})
    assert bad.status_code==422

def test_expiry_removes_old_public_history_without_changing_version(admin,monkeypatch):
    import app.document_service as service
    doc=create(admin,title='星河有效资料',content='有效期秘密标记 EXPIRY-1842',valid_until=(now()+timedelta(days=1)).isoformat())
    session=admin.post('/api/widget/sessions',json={}).json()
    headers={'Authorization':'Bearer '+session['token']}
    result=admin.post(f'/api/widget/sessions/{session["id"]}/messages',headers=headers,json={'content':'星河有效资料','client_id':str(uuid.uuid4())})
    assert 'EXPIRY-1842' in result.text
    moment=now()+timedelta(days=2)
    monkeypatch.setattr(service,'now',lambda:moment)
    result=admin.get(f'/api/widget/sessions/{session["id"]}',headers=headers).json()
    assert all('EXPIRY-1842' not in m['content'] for m in result['messages'] if m['role']=='assistant')

def test_migration_rerun_keeps_accounts_documents_and_history(admin):
    from app.db import engine
    from app.migrations import migrate_schema
    doc=create(admin)
    before=admin.get('/api/admin/documents/'+doc['id']).json()
    migrate_schema(engine)
    after=admin.get('/api/admin/documents/'+doc['id']).json()
    assert after['version']==before['version']
    assert len(admin.get('/api/admin/documents/'+doc['id']+'/revisions').json())==1

def test_switch_region_does_not_feed_previous_policy_answer_to_model(admin,monkeypatch):
    from app.config import settings
    import app.chat as chat
    doc=create(admin,title='星河地区政策',content='星河地区政策秘密标记 REGION-9524，仅适用江苏国考。',knowledge_type='policy',exam_type='国考',exam_year=2027,region='江苏',source_url='https://www.scs.gov.cn/',policy_verified=True)
    session=admin.post('/api/widget/sessions',json={}).json()
    headers={'Authorization':'Bearer '+session['token']}
    first=admin.post(f'/api/widget/sessions/{session["id"]}/messages',headers=headers,json={'content':'2027江苏国考星河地区政策 REGION-9524','client_id':str(uuid.uuid4())})
    assert 'REGION-9524' in first.text
    captured=[]
    def fake(question,sources,previous):
        captured.extend(previous)
        assert all(s['document_id']!=doc['id'] for s in sources)
        yield 'delta','请核对浙江对应的官方资料。'
    monkeypatch.setattr(settings(),'deepseek_api_key','fake-for-unit-test')
    monkeypatch.setattr(chat,'deepseek_stream',fake)
    admin.post(f'/api/widget/sessions/{session["id"]}/messages',headers=headers,json={'content':'2027浙江国考课程多少钱','client_id':str(uuid.uuid4())})
    assert captured
    assert all('REGION-9524' not in m['content'] for m in captured if m['role']=='assistant')
