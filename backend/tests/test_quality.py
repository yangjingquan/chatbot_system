import uuid
import httpx
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Message, Conversation

def answer(admin):
    c=admin.post('/api/widget/sessions',json={}).json()
    with SessionLocal() as db:
        q=Message(conversation_id=c['id'],role='user',content='不在资料中的考试咨询'+uuid.uuid4().hex)
        db.add(q);db.flush()
        m=Message(conversation_id=c['id'],role='assistant',content='暂无依据，请转人工',reply_to=q.id,mode='unknown')
        db.add(m);db.commit();mid=m.id
    return c,mid

def test_feedback_ownership_and_gap_draft_review(admin):
    c,mid=answer(admin);other=admin.post('/api/widget/sessions',json={}).json()
    path=f'/api/widget/sessions/{c["id"]}/feedback/{mid}'
    assert admin.put(path,headers={'Authorization':'Bearer '+other['token']},json={'rating':'down'}).status_code==404
    headers={'Authorization':'Bearer '+c['token']}
    assert admin.put(path,headers=headers,json={'rating':'down','comment':'请核实试听流程'}).status_code==200
    assert admin.put(path,headers=headers,json={'rating':'down'}).status_code==200
    gaps=admin.get('/api/admin/quality/gaps').json()['items']
    gap=next(g for g in gaps if g['message_id']==mid)
    assert len([g for g in gaps if g['message_id']==mid])==1
    draft=admin.post('/api/admin/quality/gaps/'+gap['id']+'/draft',json={'content':'已核实的试听流程说明。请先预约，由顾问确认。'})
    assert draft.status_code==200,draft.text
    assert draft.json()['status']=='draft'
    assert admin.patch('/api/admin/quality/gaps/'+gap['id'],json={'status':'resolved','note':''}).status_code==422
    assert admin.patch('/api/admin/quality/gaps/'+gap['id'],json={'status':'resolved','note':'已人工核实并更新服务流程'}).status_code==200

def test_error_classification_and_usage_estimation(admin):
    from app.quality import classify_error, record_usage
    req=httpx.Request('POST','https://provider.invalid')
    for code,kind in [(401,'auth'),(402,'balance'),(429,'rate_limit'),(503,'unavailable')]:
        assert classify_error(httpx.HTTPStatusError('secret body',request=req,response=httpx.Response(code,request=req)))[0]==kind
    assert classify_error(httpx.ReadTimeout('secret'))[0]=='timeout'
    c,mid=answer(admin)
    with SessionLocal() as db:
        m=db.get(Message,mid);m.mode='deepseek';m.usage={'prompt_tokens':1000000,'completion_tokens':100000,'total_tokens':1100000}
        record_usage(db,m,'configured-model',123,'');db.commit()
    summary=admin.get('/api/admin/quality/usage').json()
    assert summary['unpriced_calls']>=1
    config={'model':'configured-model','input_miss':2,'input_hit':1,'output':4,'daily_budget':1,'monthly_budget':10}
    assert admin.put('/api/admin/quality/budget',json=config).status_code==200
    c,mid=answer(admin)
    with SessionLocal() as db:
        m=db.get(Message,mid);m.mode='deepseek';m.usage={'prompt_tokens':1000000,'completion_tokens':100000,'total_tokens':1100000}
        record_usage(db,m,'configured-model',123,'');db.commit()
    summary=admin.get('/api/admin/quality/usage').json()
    assert summary['daily_estimated_cny']==2.4
    assert summary['daily_alert'] is True
    assert summary['unpriced_calls']>=1

def test_evaluation_requires_human_answer_label(admin):
    run=admin.post('/api/admin/quality/evaluations',json={})
    assert run.status_code==200,run.text
    result=run.json()
    assert set(x['category'] for x in result['cases'])=={'课程','政策','未知','否定','售后'}
    assert result['human_reviewed']==0
    assert result['answer_accuracy'] is None
    sample=result['cases'][0]
    assert admin.put(f'/api/admin/quality/evaluations/{result["id"]}/cases/{sample["id"]}',json={'answer':'人工抽样填写的回答','verdict':'pass','note':'引用与当前课程一致'}).status_code==200
    latest=admin.get('/api/admin/quality/evaluations/'+result['id']).json()
    assert latest['human_reviewed']==1
    assert latest['answer_accuracy'] is None

def test_stream_failure_is_classified_and_usage_is_unknown_not_zero(admin,monkeypatch):
    import app.chat as chat
    from app.config import settings
    monkeypatch.setattr(settings(),'deepseek_api_key','test-provider-key')
    def broken(*args):
        raise httpx.ReadTimeout('must-not-leak-provider-details')
        yield
    monkeypatch.setattr(chat,'deepseek_stream',broken)
    c=admin.post('/api/widget/sessions',json={}).json()
    result=admin.post(f'/api/widget/sessions/{c["id"]}/messages',headers={'Authorization':'Bearer '+c['token']},json={'content':'国考有哪些课程？','client_id':uuid.uuid4().hex})
    assert '"type": "timeout"' in result.text
    assert 'must-not-leak' not in result.text
    usage=admin.get('/api/admin/quality/usage').json()
    call=usage['recent'][0]
    assert call['error_type']=='timeout' and call['state']=='failed'
    assert call['estimated_cny'] is None and call['usage']=={}
