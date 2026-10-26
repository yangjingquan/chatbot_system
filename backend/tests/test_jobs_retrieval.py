from datetime import timedelta
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Document, Chunk, now

def test_retrieves_after_5000_chunks_with_database_filters(admin):
    from app.knowledge import _retrieve_documents
    with SessionLocal() as db:
        d=Document(title='容量干扰资料',content='一般说明',status='published',is_demo=True)
        db.add(d);db.flush()
        db.add_all([Chunk(document_id=d.id,text='容量干扰',terms='容量 干扰',position=i) for i in range(5001)])
        target=Document(title='稀有检索词',content='稀有检索词问答',status='published')
        hidden=Document(title='稀有检索词',content='不得命中',status='published',visibility='internal')
        expired=Document(title='稀有检索词',content='过期',status='published',valid_until=now()-timedelta(days=1))
        db.add_all([target,hidden,expired]);db.flush()
        for doc in (target,hidden,expired):db.add(Chunk(document_id=doc.id,text=doc.content,terms='稀有检索词 稀有 检索词',position=0))
        db.commit()
        sources=_retrieve_documents(db,'稀有检索词',limit=4)
        assert [s['document_id'] for s in sources]==[target.id]

def test_durable_parse_failure_retry_and_draft(admin):
    from app.worker import run_one
    from app.models import DocumentJob
    response=admin.post('/api/admin/document-jobs/upload',files={'file':('reliable.txt',b'bad\x00text','text/plain')})
    assert response.status_code==200,response.text
    job=response.json();assert job['status']=='queued'
    assert run_one(job['id'])
    failed=admin.get('/api/admin/document-jobs/'+job['id']).json()
    assert failed['status']=='failed' and failed['error']
    assert admin.post('/api/admin/document-jobs/'+job['id']+'/retry').status_code==200
    with SessionLocal() as db:
        import base64
        row=db.get(DocumentJob,job['id']);row.payload={**row.payload,'content':base64.b64encode('持久化解析测试内容。'.encode()).decode()};db.commit()
    assert run_one(job['id'])
    finished=admin.get('/api/admin/document-jobs/'+job['id']).json()
    assert finished['status']=='succeeded' and finished['progress']==100
    assert admin.get('/api/admin/documents/'+finished['document_id']).json()['status']=='draft'
    assert admin.post('/api/admin/document-jobs/'+job['id']+'/retry').status_code==409

def test_expired_lease_and_stale_index_cannot_overwrite(admin):
    from app.jobs import enqueue_index, claim
    from app.models import DocumentJob
    from app.worker import run_one
    with SessionLocal() as db:
        doc=Document(title='租约恢复',content='第一版',status='published',version=1)
        db.add(doc);db.flush();job=enqueue_index(db,doc);db.commit();jid=job.id;did=doc.id
        leased=claim(db,'crashed-worker',jid);db.commit();assert leased.id==jid
        leased.lease_until=now()-timedelta(seconds=1);db.commit()
        recovered=claim(db,'new-worker',jid);db.commit();assert recovered.id==jid and recovered.attempts==2
        recovered.status='queued';recovered.lease_until=None
        db.get(Document,did).version=2;db.commit()
    assert run_one(jid)
    with SessionLocal() as db:
        assert db.get(DocumentJob,jid).status=='superseded'
        assert db.get(Document,did).version==2
