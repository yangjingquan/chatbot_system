import json
import uuid
import pytest
from app import llm
from app.scoring import analyze_intent

def new_session(client):
    data=client.post('/api/widget/sessions',json={'source_page':'回归验证'}).json()
    return data['id'],{'Authorization':'Bearer '+data['token']}

def send(client,sid,headers,text,client_id=None):
    return client.post(f'/api/widget/sessions/{sid}/messages',headers=headers,json={'content':text,'client_id':client_id or str(uuid.uuid4())})

def test_withdrawn_knowledge_not_in_history_replay_or_model_context(admin,monkeypatch):
    from app.config import settings
    import app.chat as chat
    doc=admin.post('/api/admin/documents',json={'title':'星河专属课程','content':'星河专属课程的秘密测试标记是 TEST-HISTORY-924。','status':'published'}).json()
    sid,headers=new_session(admin)
    client_id=str(uuid.uuid4())
    first=send(admin,sid,headers,'星河专属课程',client_id)
    assert 'TEST-HISTORY-924' in first.text
    draft=admin.patch('/api/admin/documents/'+doc['id'],json={'visibility':'internal','expected_sequence':doc['edit_sequence']}).json()
    admin.post('/api/admin/documents/'+doc['id']+'/publish',json={'expected_sequence':draft['edit_sequence']})
    history=admin.get(f'/api/widget/sessions/{sid}',headers=headers).json()
    answer=next(m for m in history['messages'] if m['role']=='assistant')
    assert 'TEST-HISTORY-924' not in answer['content']
    assert answer['sources']==[]
    replay=send(admin,sid,headers,'星河专属课程',client_id)
    assert 'TEST-HISTORY-924' not in replay.text
    captured=[]
    def fake(question,sources,previous):
        captured.extend(previous)
        yield 'delta','国考课程请查看来源。'
    monkeypatch.setattr(settings(),'deepseek_api_key','fake-for-unit-test')
    monkeypatch.setattr(chat,'deepseek_stream',fake)
    send(admin,sid,headers,'国考课程多少钱')
    assert all('TEST-HISTORY-924' not in m['content'] for m in captured if m['role']=='assistant')

def test_latest_purchase_rejection_cancels_old_signals():
    result=analyze_intent('想报公安联考班，预算2000元，下个月考试，想预约试听和报价\n我不打算报名了，不用联系我',True)
    assert result['level']!='高意向'
    assert result['contactable'] is False

def test_contact_withdrawal_persisted_and_manual_level_not_accidentally_locked(admin):
    sid,headers=new_session(admin)
    send(admin,sid,headers,'想报公安联考班，预算2000元，下个月考试，想预约试听和报价')
    lead=admin.post(f'/api/widget/sessions/{sid}/contact',headers=headers,json={'name':'回归访客','contact':'test@example.invalid','consent':True}).json()
    detail=admin.get('/api/admin/leads/'+lead['id']).json()
    changed=admin.patch('/api/admin/leads/'+lead['id'],json={'stage':'跟进中','level':detail['level']}).json()
    assert changed['manual_level'] is False
    send(admin,sid,headers,'我不打算报名了，不用联系我')
    detail=admin.get('/api/admin/leads/'+lead['id']).json()
    assert detail['contact_allowed'] is False
    assert detail['level']!='高意向'

def test_can_restore_automatic_level(admin):
    sid,headers=new_session(admin)
    lead=admin.post(f'/api/widget/sessions/{sid}/contact',headers=headers,json={'name':'自动分级','contact':'test@example.invalid','consent':True}).json()
    admin.patch('/api/admin/leads/'+lead['id'],json={'level':'高意向'})
    result=admin.patch('/api/admin/leads/'+lead['id'],json={'automatic_level':True}).json()
    assert result['manual_level'] is False
    assert result['level']!='高意向'

def test_contact_can_only_resume_after_explicit_new_consent(admin):
    sid,headers=new_session(admin)
    payload={'name':'重新授权','contact':'test@example.invalid','consent':True}
    lead=admin.post(f'/api/widget/sessions/{sid}/contact',headers=headers,json=payload).json()
    send(admin,sid,headers,'不用联系我，我不打算报名了')
    send(admin,sid,headers,'谢谢')
    assert admin.get('/api/admin/leads/'+lead['id']).json()['contact_allowed'] is False
    admin.post(f'/api/widget/sessions/{sid}/contact',headers=headers,json=payload)
    send(admin,sid,headers,'我现在想报国考课程')
    detail=admin.get('/api/admin/leads/'+lead['id']).json()
    assert detail['contact_allowed'] is True
    assert '国考' in detail['tags']

def test_existing_summaries_upgrade_without_overwriting_manual_level(admin):
    from app.db import SessionLocal
    from app.models import Conversation
    from app.chat import backfill_analysis
    sid,headers=new_session(admin)
    lead=admin.post(f'/api/widget/sessions/{sid}/contact',headers=headers,json={'name':'旧数据升级','contact':'test@example.invalid','consent':True}).json()
    send(admin,sid,headers,'不打算报名了')
    admin.patch('/api/admin/leads/'+lead['id'],json={'level':'高意向'})
    with SessionLocal() as db:
        conv=db.get(Conversation,sid)
        conv.analysis={'rule_version':'training-v2','level':'高意向'}
        db.commit()
        backfill_analysis(db)
        db.commit()
    result=admin.get('/api/admin/conversations/'+sid).json()
    assert result['analysis']['purchase_state']=='declined'
    assert result['lead']['level']=='高意向'
    assert result['lead']['manual_level'] is True

def test_lead_inherits_preexisting_demo_marker(admin):
    sid,headers=new_session(admin)
    admin.post(f'/api/admin/conversations/{sid}/mark-demo')
    lead=admin.post(f'/api/widget/sessions/{sid}/contact',headers=headers,json={'name':'先标记后留资','contact':'test@example.invalid','consent':True}).json()
    assert admin.get('/api/admin/leads/'+lead['id']).json()['is_demo'] is True

class FakeStream:
    def __init__(self,lines):self.lines=lines
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def raise_for_status(self):pass
    def iter_lines(self):yield from self.lines

def fake_client(monkeypatch,lines):
    class Client:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def stream(self,*args,**kwargs):return FakeStream(lines)
    monkeypatch.setattr(llm.httpx,'Client',Client)

def chunk(content='',finish=None):
    return 'data: '+json.dumps({'choices':[{'delta':{'content':content},'finish_reason':finish}]})

def test_model_eof_without_done_is_failure(monkeypatch):
    fake_client(monkeypatch,[chunk('报名费为')])
    with pytest.raises(RuntimeError):list(llm.deepseek_stream('价格',[],[]))

def test_model_length_truncation_is_failure(monkeypatch):
    fake_client(monkeypatch,[chunk('部分回答'),chunk(finish='length'),'data: [DONE]'])
    with pytest.raises(RuntimeError):list(llm.deepseek_stream('价格',[],[]))

def test_model_explicit_completion_is_success(monkeypatch):
    fake_client(monkeypatch,[chunk('完整回答'),chunk(finish='stop'),'data: [DONE]'])
    assert list(llm.deepseek_stream('价格',[],[]))==[('delta','完整回答')]
