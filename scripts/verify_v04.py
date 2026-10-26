"""Real PostgreSQL regression. Operates ONLY on the disposable restored v0.4 rehearsal DB."""
import os, uuid, json, threading, time
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
os.environ['DATABASE_URL']=os.environ['DATABASE_URL'].rsplit('/',1)[0]+'/zhixing_v04_rehearsal'
os.environ['DEEPSEEK_API_KEY']='';os.environ['EMBEDDING_ENABLED']='false';os.environ['REDIS_URL']=''
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from app.main import app
from app.db import SessionLocal, engine
from app.models import Document, Chunk, CourseBatch, DocumentJob, now
from app.jobs import claim
from app.worker import run_one, progress, LostLease
from app.knowledge import _retrieve_documents
from app.migrations import migrate_schema

def ok(r):
    assert r.status_code==200,(r.status_code,r.text[:200])
    return r.json()

with engine.connect() as db:
    assert db.execute(text('SELECT current_database()')).scalar()=='zhixing_v04_rehearsal'
    tables=('documents','conversations','leads','students','courses','course_batches')
    before={t:db.execute(text('SELECT count(*) FROM '+t)).scalar() for t in tables}

with TestClient(app,headers={'Origin':os.environ['PUBLIC_ORIGIN']}) as admin:
    with engine.connect() as db:assert before=={t:db.execute(text('SELECT count(*) FROM '+t)).scalar() for t in tables}
    ok(admin.post('/api/auth/login',json={'email':os.environ['ADMIN_EMAIL'],'password':os.environ['ADMIN_PASSWORD']}))
    people=[]
    for i in range(2):
        password=uuid.uuid4().hex+'-test-only';email=uuid.uuid4().hex+'@example.invalid'
        staff=ok(admin.post('/api/admin/staff',json={'name':'v0.4并发客服'+str(i),'email':email,'password':password,'role':'support'}))
        c=TestClient(app,headers={'Origin':os.environ['PUBLIC_ORIGIN']});ok(c.post('/api/auth/login',json={'email':email,'password':password}));people.append((staff,c))
    session=ok(admin.post('/api/widget/sessions',json={}))
    headers={'Authorization':'Bearer '+session['token']};sid=session['id']
    ok(admin.post(f'/api/widget/sessions/{sid}/handoff',headers=headers))
    barrier=threading.Barrier(2)
    def takeover(i):
        barrier.wait();return people[i][1].post(f'/api/admin/conversations/{sid}/takeover').status_code
    with ThreadPoolExecutor(max_workers=2) as pool:claims=sorted(pool.map(takeover,range(2)))
    assert claims==[200,409],claims
    detail=ok(admin.get('/api/admin/conversations/'+sid))
    assert detail['assigned_to_id'] in [p['id'] for p,_ in people]
    import app.chat as chat
    stale_session=ok(admin.post('/api/widget/sessions',json={}))
    visitor_original=chat.visitor
    def takeover_after_initial_read(request,session_id,db):
        conv=visitor_original(request,session_id,db)
        if session_id==stale_session['id']:ok(people[0][1].post('/api/admin/conversations/'+session_id+'/takeover'))
        return conv
    chat.visitor=takeover_after_initial_read
    try:
        response=admin.post('/api/widget/sessions/'+stale_session['id']+'/messages',headers={'Authorization':'Bearer '+stale_session['token']},json={'content':'请转人工','client_id':uuid.uuid4().hex})
        assert response.status_code==200
        assert ok(admin.get('/api/admin/conversations/'+stale_session['id']))['status']=='human'
    finally:chat.visitor=visitor_original
    ok(admin.post(f'/api/widget/sessions/{sid}/contact',headers=headers,json={'name':'v0.4并发测试','contact':'pg-v04@example.invalid','consent':True}))
    with SessionLocal() as db:batch=db.scalar(select(CourseBatch).where(CourseBatch.sale_status=='open'));bid=batch.id
    trial=ok(admin.post(f'/api/widget/sessions/{sid}/trials',headers=headers,json={'batch_id':bid,'requested_at':(now()+timedelta(days=7)).isoformat(),'client_id':uuid.uuid4().hex}))
    body={'version':trial['version'],'status':'confirmed','advisor_id':detail['assigned_to_id'],'scheduled_at':(now()+timedelta(days=7)).isoformat()}
    barrier=threading.Barrier(2)
    def confirm(i):barrier.wait();return people[i][1].patch('/api/admin/trials/'+trial['id'],json=body).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:confirmations=sorted(pool.map(confirm,range(2)))
    assert confirmations==[200,409],confirmations
    # Confirmation must reread a concurrently paused batch after its lock is released.
    current=next(t for t in ok(admin.get('/api/admin/trials'))['items'] if t['id']==trial['id'])
    with SessionLocal() as blocker,ThreadPoolExecutor(max_workers=1) as pool:
        b=blocker.scalar(select(CourseBatch).where(CourseBatch.id==bid).with_for_update())
        b.sale_status='paused';blocker.flush()
        future=pool.submit(lambda:people[0][1].patch('/api/admin/trials/'+trial['id'],json={**body,'version':current['version']}))
        deadline=time.monotonic()+8;waiting=False
        try:
            while time.monotonic()<deadline:
                with engine.connect() as conn:
                    waiting=bool(conn.execute(text("SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query ILIKE '%course_batches%' AND pid<>pg_backend_pid()")).first())
                if waiting:break
                if future.done():break
                time.sleep(.02)
        finally:blocker.commit()
        assert waiting,'Confirmation did not wait for current batch state'
        assert future.result(timeout=8).status_code==409
    job=ok(admin.post('/api/admin/document-jobs/upload',files={'file':('concurrent.txt','并发解析只能生成一份草稿'.encode(),'text/plain')}))
    barrier=threading.Barrier(2)
    def take_job(i):
        with SessionLocal() as db:
            barrier.wait();j=claim(db,'pg-worker-'+str(i),job['id']);db.commit();return j.lease_token if j else None
    with ThreadPoolExecutor(max_workers=2) as pool:leases=list(pool.map(take_job,range(2)))
    assert sum(bool(t) for t in leases)==1,leases
    old_token=next(t for t in leases if t)
    with SessionLocal() as db:
        j=db.get(DocumentJob,job['id']);assert j.attempts==1;j.lease_until=now()-timedelta(seconds=1);db.commit()
    try:progress(job['id'],old_token,50,'过期工作进程不应写入')
    except LostLease:pass
    else:raise AssertionError('Expired worker renewed its lease')
    assert run_one(job['id'])
    assert ok(admin.get('/api/admin/document-jobs/'+job['id']))['status']=='succeeded'
    try:progress(job['id'],old_token,99,'旧工作进程不能覆盖结果')
    except LostLease:pass
    else:raise AssertionError('Stale worker wrote to completed task')
    with SessionLocal() as db:
        doc=Document(title='PG容量干扰',content='干扰数据',status='published',is_demo=True);db.add(doc);db.flush()
        db.add_all([Chunk(document_id=doc.id,text='无关说明',terms='干扰 文本',position=i) for i in range(5100)])
        target=Document(title='超容量精准检索',content='容量边界独特短语',status='published',is_demo=True)
        hidden=Document(title='超容量精准检索',content='内部不可命中',status='published',visibility='internal',is_demo=True)
        wrong=Document(title='超容量精准检索',content='其他租户不可命中',status='published',tenant_id='other-tenant',is_demo=True)
        db.add_all([target,hidden,wrong]);db.flush()
        for d in (target,hidden,wrong):db.add(Chunk(document_id=d.id,tenant_id=d.tenant_id,text=d.content,terms='超容量 精准 检索',position=0))
        db.commit()
        result=_retrieve_documents(db,'超容量精准检索')
        assert [s['document_id'] for s in result]==[target.id],result
        assert db.execute(text("SELECT 1 FROM pg_indexes WHERE indexname='ix_chunks_terms_gin'")).first()
        # Exercise actual pgvector SQL distance, including visibility filters.
        import numpy as np
        import app.knowledge as k
        class QueryModel:
            def query_embed(self,q):yield np.array([1.0]+[0.0]*511)
        original=k._model;k._model=QueryModel()
        try:
            for d in (target,hidden,wrong):
                c=db.scalar(select(Chunk).where(Chunk.document_id==d.id));c.embedding=[1.0]+[0.0]*511
            db.commit();result=_retrieve_documents(db,'词库之外语义提问')
            assert [s['document_id'] for s in result]==[target.id],result
        finally:k._model=original
    migrate_schema(engine)
    print(json.dumps({'status':'passed','preserved_rows':before,'concurrent_takeover':claims,'concurrent_confirmation':confirmations,'exclusive_job_claims':1,'capacity_chunks':5100,'checks':['additive-repeat-migration','human-claim-lock','post-lock-state-refresh','trial-version-lock','confirmation-vs-pause-lock','skip-locked-exclusive-claim','expired-lease-recovery','stale-worker-fencing','postgres-gin-search-beyond-5000','tenant-public-filters','pgvector-sql-distance']},ensure_ascii=False))
