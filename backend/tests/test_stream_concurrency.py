"""Concurrent source invalidation while an answer is still streaming."""
import uuid
from sqlalchemy import select
from app.config import settings
from app.db import SessionLocal
from app.models import Conversation, Document, CourseBatch, Message
from test_catalog import course, batch

def session(admin):
    c=admin.post('/api/widget/sessions',json={}).json()
    return c,{'Authorization':'Bearer '+c['token']}

def enable_fake_stream(monkeypatch, factory):
    import app.chat as chat
    monkeypatch.setattr(settings(),'deepseek_api_key','fake-for-concurrency-test')
    monkeypatch.setattr(chat,'deepseek_stream',factory)

def test_mid_stream_document_archive_stops_and_history_withdraws(admin,monkeypatch):
    marker='STALE-ARCHIVE-'+uuid.uuid4().hex[:8]
    phrase='星河蓝鲸并发归档 '+marker
    created=admin.post('/api/admin/documents',json={
        'title':phrase,'content':f'{phrase} 的秘密测试标记，旧政策说明仍有效。','status':'published'
    })
    assert created.status_code==200,created.text
    doc=created.json()
    hit=admin.post('/api/admin/retrieval-test',json={'query':phrase}).json()['sources']
    assert any(s['document_id']==doc['id'] for s in hit), (doc, hit)
    c,headers=session(admin)
    ticks={'n':0}
    def fake(question,sources,previous):
        assert any(s.get('document_id')==doc['id'] for s in sources)
        for i in range(24):
            ticks['n']+=1
            if ticks['n']==3:
                with SessionLocal() as other:
                    row=other.get(Document,doc['id'])
                    row.status='archived'
                    other.commit()
            yield 'delta',f'继续引用{marker}片段{i}。'
    enable_fake_stream(monkeypatch,fake)
    result=admin.post(f'/api/widget/sessions/{c["id"]}/messages',headers=headers,
                      json={'content':phrase,'client_id':uuid.uuid4().hex})
    assert result.status_code==200
    body=result.text
    assert 'event: error' in body
    assert '重新提问' in body or '转人工' in body
    error_at=body.index('event: error')
    assert marker not in body[error_at:]
    history=admin.get(f'/api/widget/sessions/{c["id"]}',headers=headers).json()
    answer=next(m for m in history['messages'] if m['role']=='assistant')
    assert marker not in answer['content']
    assert answer['sources']==[]
    assert answer['mode']=='withdrawn' or answer['state'] in ('withdrawn','cancelled','failed')
    with SessionLocal() as db:
        stored=db.scalar(select(Message).where(Message.conversation_id==c['id'],Message.role=='assistant'))
        assert stored.state=='cancelled'

def test_mid_stream_price_change_stops_stale_quote(admin,monkeypatch):
    item=course(admin,name='并发改价课程'+uuid.uuid4().hex[:6])
    row=batch(admin,item,price_cents=110000)
    c,headers=session(admin)
    ticks={'n':0}
    def fake(question,sources,previous):
        assert any(s.get('kind')=='catalog' and s.get('course_id')==item['id'] for s in sources)
        for i in range(24):
            ticks['n']+=1
            if ticks['n']==3:
                with SessionLocal() as other:
                    b=other.get(CourseBatch,row['id'])
                    b.price_cents=990000
                    b.version+=1
                    other.commit()
            yield 'delta',f'当前报价仍是1100元，片段{i}。'
        yield 'delta','最终仍声称1100元。'
    enable_fake_stream(monkeypatch,fake)
    result=admin.post(f'/api/widget/sessions/{c["id"]}/messages',headers=headers,
                      json={'content':item['name']+'多少钱','client_id':uuid.uuid4().hex})
    assert result.status_code==200
    body=result.text
    assert 'event: error' in body
    assert '重新提问' in body or '转人工' in body
    error_at=body.index('event: error')
    assert '1100' not in body[error_at:]
    history=admin.get(f'/api/widget/sessions/{c["id"]}',headers=headers).json()
    answer=next(m for m in history['messages'] if m['role']=='assistant')
    assert '1100' not in answer['content']
    assert answer['sources']==[]

def test_mid_stream_human_takeover_stops_bot_answer(admin,monkeypatch):
    c,headers=session(admin)
    uid=admin.get('/api/auth/me').json()['id']
    admin.post('/api/admin/documents',json={
        'title':'人工切换资料','content':'人工切换资料 HUMAN-SWITCH-5521 国考课程说明。','status':'published'
    })
    ticks={'n':0}
    def fake(question,sources,previous):
        for i in range(24):
            ticks['n']+=1
            if ticks['n']==3:
                with SessionLocal() as other:
                    conv=other.get(Conversation,c['id'])
                    conv.status='human'
                    conv.assigned_to_id=uid
                    other.commit()
            yield 'delta',f'机器人仍在回答片段{i}。'
    enable_fake_stream(monkeypatch,fake)
    result=admin.post(f'/api/widget/sessions/{c["id"]}/messages',headers=headers,
                      json={'content':'人工切换资料','client_id':uuid.uuid4().hex})
    assert result.status_code==200
    body=result.text
    assert 'event: error' in body
    assert '人工' in body
    assert '机器人仍在回答片段23' not in body
    with SessionLocal() as db:
        answer=db.scalar(select(Message).where(Message.conversation_id==c['id'],Message.role=='assistant'))
        assert answer.state=='cancelled'
