"""Durable database queue; claims are exclusive and fenced by renewable lease tokens."""
import base64
import uuid
from datetime import timedelta
from pathlib import Path
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select, or_, and_, func
from sqlalchemy.orm import Session
from .db import get_db
from .models import Admin, Document, DocumentJob, Chunk, now
from .permissions import require, scoped_object
from .querying import Paging, paginate

router=APIRouter(prefix='/admin/document-jobs')
LEASE=timedelta(minutes=10)

def job_data(j):
    return {k:(getattr(j,k).isoformat() if k.endswith('_at') and getattr(j,k) else getattr(j,k)) for k in ('id','kind','status','document_id','progress','stage','attempts','error','created_at','updated_at')}

def enqueue_index(db,doc):
    db.flush()
    doc=db.scalar(select(Document).where(Document.id==doc.id,Document.tenant_id==doc.tenant_id).with_for_update().execution_options(populate_existing=True))
    if doc.status!='published':raise HTTPException(409,'仅已发布资料可建立向量索引')
    existing=db.scalar(select(DocumentJob).where(DocumentJob.document_id==doc.id,DocumentJob.kind=='index',DocumentJob.payload['version'].as_integer()==doc.version,DocumentJob.status.in_(['queued','running'])))
    if existing:return existing
    j=DocumentJob(tenant_id=doc.tenant_id,document_id=doc.id,kind='index',payload={'version':doc.version})
    db.add(j);db.flush();return j

def backfill_index_jobs(db):
    null_vector=Chunk.embedding.is_(None)
    if db.bind.dialect.name=='sqlite':null_vector=or_(null_vector,func.json_type(Chunk.embedding)=='null')
    missing=select(Chunk.id).where(Chunk.document_id==Document.id,null_vector).exists()
    docs=db.scalars(select(Document).where(Document.status=='published',missing).order_by(Document.id).with_for_update(skip_locked=True)).all()
    for doc in docs:
        # Completed/failed attempts retain their history and cap across restarts.
        previous=db.scalar(select(DocumentJob.id).where(DocumentJob.document_id==doc.id,DocumentJob.kind=='index',DocumentJob.payload['version'].as_integer()==doc.version))
        if not previous:enqueue_index(db,doc)

def claim(db,worker,job_id=None):
    eligible=or_(DocumentJob.status=='queued',and_(DocumentJob.status=='running',DocumentJob.lease_until<now()))
    exhausted=db.scalars(select(DocumentJob).where(eligible,DocumentJob.attempts>=3).with_for_update(skip_locked=True)).all()
    for j in exhausted:j.status='failed';j.error='超过 3 次尝试，请检查文件或重新提交任务';j.stage='重试已耗尽';j.lease_token=None;j.lease_until=None
    query=select(DocumentJob).where(eligible,DocumentJob.attempts<3)
    if job_id:query=query.where(DocumentJob.id==job_id)
    j=db.scalar(query.order_by(DocumentJob.created_at,DocumentJob.id).with_for_update(skip_locked=True).limit(1).execution_options(populate_existing=True))
    if not j:return None
    j.status='running';j.attempts+=1;j.lease_token=worker+':'+uuid.uuid4().hex;j.lease_until=now()+LEASE;j.error='';j.progress=1;j.stage='正在处理';db.flush()
    return j

@router.post('/upload')
def upload(file:UploadFile=File(...),user:Admin=Depends(require('knowledge.write')),db:Session=Depends(get_db)):
    raw=file.file.read(5*1024*1024+1)
    filename=Path(file.filename or '').name[:240]
    if len(raw)>5*1024*1024:raise HTTPException(413,'文件不能超过 5 MB')
    if Path(filename).suffix.lower() not in ('.md','.txt','.pdf','.docx'):raise HTTPException(415,'支持 Markdown、TXT、文本 PDF 和 DOCX')
    if not raw:raise HTTPException(422,'文件为空')
    j=DocumentJob(tenant_id=user.tenant_id,actor_id=user.id,kind='parse',payload={'filename':filename,'content':base64.b64encode(raw).decode()})
    db.add(j);db.commit();return job_data(j)

@router.get('')
def jobs(paging:Paging=Depends(),status:str='',user:Admin=Depends(require('knowledge.read')),db:Session=Depends(get_db)):
    query=select(DocumentJob).where(DocumentJob.tenant_id==user.tenant_id)
    if status:query=query.where(DocumentJob.status==status)
    return paginate(db,query.order_by(DocumentJob.created_at.desc(),DocumentJob.id),paging,job_data)

@router.get('/{jid}')
def job(jid:str,user:Admin=Depends(require('knowledge.read')),db:Session=Depends(get_db)):
    return job_data(scoped_object(db,DocumentJob,jid,user))

@router.post('/{jid}/retry')
def retry(jid:str,user:Admin=Depends(require('knowledge.write')),db:Session=Depends(get_db)):
    j=db.scalar(select(DocumentJob).where(DocumentJob.id==jid,DocumentJob.tenant_id==user.tenant_id).with_for_update().execution_options(populate_existing=True))
    if not j:raise HTTPException(404,'任务不存在')
    if j.status!='failed' or j.attempts>=3:raise HTTPException(409,'仅失败且未达到 3 次尝试上限的任务可重试')
    if j.kind=='parse':j.actor_id=user.id
    j.status='queued';j.stage='等待重试';j.progress=0;j.error='';j.lease_token=None;j.lease_until=None
    from .admin import record
    record(db,user,'重试文档任务',jid);db.commit();return job_data(j)

@router.post('/reindex/{did}')
def reindex(did:str,user:Admin=Depends(require('knowledge.write')),db:Session=Depends(get_db)):
    doc=scoped_object(db,Document,did,user)
    if doc.status!='published':raise HTTPException(409,'仅已发布资料可重建向量索引')
    job=enqueue_index(db,doc);db.commit();return job_data(job)
