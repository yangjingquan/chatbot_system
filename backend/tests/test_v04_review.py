"""Regressions from the single independent v0.4 review."""
import uuid
from datetime import timedelta
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Conversation, Message, Document, Chunk, DocumentJob, now
from test_permissions import employee

def session(admin):
    c=admin.post('/api/widget/sessions',json={}).json()
    return c,{'Authorization':'Bearer '+c['token']}

def contact(admin,c,headers):
    return admin.post(f'/api/widget/sessions/{c["id"]}/contact',headers=headers,json={'name':'评审合成访客','contact':'review-v04@example.invalid','consent':True})

def test_anonymous_advisor_keeps_scope_after_first_contact_without_overriding_existing_owner(admin):
    person,advisor=employee(admin,'advisor');other,other_client=employee(admin,'advisor')
    c,headers=session(admin);path='/api/admin/conversations/'+c['id']
    assert admin.post(path+'/takeover').status_code==200
    assert admin.post(path+'/transfer',json={'assigned_to_id':person['id']}).status_code==200
    assert advisor.get(path).status_code==200
    lead=contact(admin,c,headers).json()
    result=advisor.get(path)
    assert result.status_code==200,result.text
    assert result.json()['lead']['owner_id']==person['id']
    assert advisor.post(path+'/reply',json={'content':'留资后继续接待'}).status_code==200
    assert other_client.get(path).status_code==404
    assert admin.patch('/api/admin/leads/'+lead['id'],json={'owner_id':other['id']}).status_code==200
    assert contact(admin,c,headers).status_code==200
    assert admin.get('/api/admin/leads/'+lead['id']).json()['owner_id']==other['id']
    assert advisor.get(path).status_code==404

def test_chat_refreshes_status_after_authenticated_read_before_lock(admin,monkeypatch):
    import app.chat as chat
    original=chat.visitor;c,headers=session(admin);uid=admin.get('/api/auth/me').json()['id']
    def takeover_between_reads(request,sid,db):
        conv=original(request,sid,db)
        with SessionLocal() as other:
            current=other.get(Conversation,sid);current.status='human';current.assigned_to_id=uid;other.commit()
        return conv
    monkeypatch.setattr(chat,'visitor',takeover_between_reads)
    r=admin.post(f'/api/widget/sessions/{c["id"]}/messages',headers=headers,json={'content':'请转人工','client_id':uuid.uuid4().hex})
    assert r.status_code==200
    with SessionLocal() as db:
        conv=db.get(Conversation,c['id']);assert conv.status=='human'
        assert conv.assigned_to_id==uid
        assert not db.scalar(select(Message.id).where(Message.conversation_id==c['id'],Message.role=='assistant'))

def test_ended_batches_are_hidden_and_rechecked_at_confirmation(admin):
    course=admin.post('/api/admin/courses',json={'name':'评审试听课程','exam_type':'国考','hours':24}).json()
    b=admin.post('/api/admin/courses/'+course['id']+'/batches',json={'name':'评审批次','sale_status':'open','ends_at':(now()+timedelta(days=10)).isoformat()}).json()
    c,headers=session(admin);assert contact(admin,c,headers).status_code==200
    payload={'batch_id':b['id'],'requested_at':(now()+timedelta(days=1)).isoformat(),'client_id':uuid.uuid4().hex}
    trial=admin.post(f'/api/widget/sessions/{c["id"]}/trials',headers=headers,json=payload).json()
    assert 'id' in trial,trial
    assert admin.patch('/api/admin/batches/'+b['id'],json={'expected_version':b['version'],'ends_at':(now()-timedelta(days=1)).isoformat()}).status_code==200
    assert b['id'] not in [x['id'] for x in admin.get('/api/widget/trial-batches').json()]
    assert admin.post(f'/api/widget/sessions/{c["id"]}/trials',headers=headers,json={**payload,'client_id':uuid.uuid4().hex}).status_code==422
    uid=admin.get('/api/auth/me').json()['id']
    assert admin.patch('/api/admin/trials/'+trial['id'],json={'version':trial['version'],'status':'confirmed','advisor_id':uid,'scheduled_at':payload['requested_at']}).status_code==409

def test_backfill_schedules_missing_seed_vectors_once_and_respects_failed_limit(admin,monkeypatch):
    from app.jobs import backfill_index_jobs
    from app.worker import run_one
    from app.config import settings
    import app.knowledge as k
    import app.main as main
    from fastapi.testclient import TestClient
    import numpy as np
    class Model:
        def embed(self,texts):return [np.array([1.0]+[0.0]*511) for _ in texts]
    monkeypatch.setattr(settings(),'embedding_enabled',True);monkeypatch.setattr(k,'_model',Model())
    monkeypatch.setattr(main,'warm_embeddings',lambda:None)
    with SessionLocal() as db:
        published=Document(title='种子缺失向量',content='种子资料',status='published',version=1)
        draft=Document(title='不能为草稿补索引',content='草稿',status='draft',version=0)
        archived=Document(title='不能为下架补索引',content='下架',status='archived',version=1)
        db.add_all([published,draft,archived]);db.flush()
        for d in (published,draft,archived):db.add(Chunk(document_id=d.id,text=d.content,terms='种子 资料',position=0))
        db.commit()
        with TestClient(main.app):pass # Exercise the real startup backfill, without downloads.
        jobs=db.scalars(select(DocumentJob).where(DocumentJob.document_id.in_([published.id,draft.id,archived.id]))).all()
        assert len(jobs)==1 and jobs[0].document_id==published.id
        jid=jobs[0].id
        backfill_index_jobs(db);db.commit()
        assert len(db.scalars(select(DocumentJob).where(DocumentJob.document_id==published.id)).all())==1
    assert run_one(jid)
    with SessionLocal() as db:
        assert db.scalar(select(Chunk).where(Chunk.document_id==published.id)).embedding is not None
        backfill_index_jobs(db);db.commit()
        assert len(db.scalars(select(DocumentJob).where(DocumentJob.document_id==published.id)).all())==1
        chunk=db.scalar(select(Chunk).where(Chunk.document_id==published.id));chunk.embedding=None
        j=db.get(DocumentJob,jid);j.status='failed';j.attempts=3;db.commit()
        backfill_index_jobs(db);db.commit()
        assert len(db.scalars(select(DocumentJob).where(DocumentJob.document_id==published.id)).all())==1
