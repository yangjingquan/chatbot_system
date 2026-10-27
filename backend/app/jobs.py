"""Durable database queue; claims are exclusive and fenced by renewable lease tokens."""
import base64
import uuid
from datetime import timedelta
from pathlib import Path
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select, or_, and_, func
from sqlalchemy.orm import Session
from .db import get_db
from .models import Admin, Document, DocumentJob, Chunk, now
from .permissions import require, scoped_object
from .querying import Paging, paginate
from .knowledge import MAX_FILE_BYTES, MAX_FILE_MB, ALLOWED_SUFFIXES

router=APIRouter(prefix='/admin/document-jobs')
LEASE=timedelta(minutes=10)
BATCH_LIMIT=30

def classify_error(message: str) -> str:
    text=message or ''
    if 'OCR' in text or '扫描' in text: return 'ocr'
    if '超过' in text or 'MB' in text: return 'size'
    if '支持' in text or '类型' in text: return 'type'
    if '为空' in text or '空' == text: return 'empty'
    if '编码' in text or '加密' in text or '解析' in text or '无效' in text: return 'parse'
    return 'other' if text else ''

def job_data(j):
    payload=j.payload or {}
    filename=payload.get('filename') or ''
    result={k:(getattr(j,k).isoformat() if k.endswith('_at') and getattr(j,k) else getattr(j,k)) for k in ('id','kind','status','document_id','progress','stage','attempts','error','created_at','updated_at')}
    result.update(filename=filename,file_type=Path(filename).suffix.lower() if filename else '',
                  error_code=payload.get('error_code') or classify_error(j.error),
                  batch_id=payload.get('batch_id') or '',
                  validation_error=bool(payload.get('validation_error')))
    return result

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

def create_parse_job(db,user,filename,raw,batch_id=''):
    """Always create a job row so batch partial failures remain visible and filterable."""
    name=Path(filename or '').name[:240]
    suffix=Path(name).suffix.lower()
    payload={'filename':name,'batch_id':batch_id or ''}
    error=code=''
    if not name or suffix not in ALLOWED_SUFFIXES:
        error,code='支持 Markdown、TXT、文本 PDF 和 DOCX','type'
    elif len(raw)>MAX_FILE_BYTES:
        error,code=f'文件不能超过 {MAX_FILE_MB} MB','size'
    elif not raw:
        error,code='文件为空','empty'
    if error:
        payload['error_code']=code;payload['validation_error']=True
        job=DocumentJob(tenant_id=user.tenant_id,actor_id=user.id,kind='parse',status='failed',
                        progress=0,stage='校验失败',error=error,payload=payload,attempts=0)
        db.add(job);db.flush();return job
    payload['content']=base64.b64encode(raw).decode()
    job=DocumentJob(tenant_id=user.tenant_id,actor_id=user.id,kind='parse',payload=payload)
    db.add(job);db.flush();return job

def retry_job(db,j,user):
    if j.status!='failed':raise HTTPException(409,'仅失败任务可重试')
    if (j.payload or {}).get('validation_error'):raise HTTPException(409,'校验失败的任务须修正文件后重新上传')
    if j.attempts>=3:raise HTTPException(409,'已达到 3 次尝试上限，请修正后重新上传或重建索引')
    if j.kind=='parse' and not (j.payload or {}).get('content'):raise HTTPException(409,'缺少原始文件内容，请重新上传')
    if j.kind=='parse':j.actor_id=user.id
    j.status='queued';j.stage='等待重试';j.progress=0;j.error='';j.lease_token=None;j.lease_until=None
    return j

@router.post('/upload')
def upload(file:UploadFile=File(...),user:Admin=Depends(require('knowledge.write')),db:Session=Depends(get_db)):
    raw=file.file.read(MAX_FILE_BYTES+1)
    job=create_parse_job(db,user,file.filename or '',raw)
    if job.status=='failed' and (job.payload or {}).get('validation_error'):
        # Preserve previous single-upload HTTP errors for simple callers.
        db.rollback()
        code=413 if job.error and '超过' in job.error else 415 if '支持' in (job.error or '') else 422
        raise HTTPException(code,job.error)
    db.commit();return job_data(job)

@router.post('/upload-batch')
def upload_batch(files:list[UploadFile]=File(...),user:Admin=Depends(require('knowledge.write')),db:Session=Depends(get_db)):
    if not files:raise HTTPException(422,'请至少选择一个文件')
    if len(files)>BATCH_LIMIT:raise HTTPException(422,f'单次最多上传 {BATCH_LIMIT} 个文件')
    batch_id=uuid.uuid4().hex
    items=[]
    for upload_file in files:
        raw=upload_file.file.read(MAX_FILE_BYTES+1)
        items.append(job_data(create_parse_job(db,user,upload_file.filename or '',raw,batch_id=batch_id)))
    from .admin import record
    record(db,user,'批量上传文档',f'{batch_id}:{len(items)}')
    db.commit()
    return {'batch_id':batch_id,'total':len(items),
            'queued':sum(i['status']=='queued' for i in items),
            'failed':sum(i['status']=='failed' for i in items),'items':items}

@router.get('')
def jobs(paging:Paging=Depends(),status:str='',kind:str='',file_type:str='',error_code:str='',error_q:str='',
         user:Admin=Depends(require('knowledge.read')),db:Session=Depends(get_db)):
    query=select(DocumentJob).where(DocumentJob.tenant_id==user.tenant_id)
    if status:query=query.where(DocumentJob.status==status)
    if kind in ('parse','index'):query=query.where(DocumentJob.kind==kind)
    if file_type:
        suffix=file_type if file_type.startswith('.') else '.'+file_type
        suffix=suffix.lower()
        if db.bind.dialect.name=='postgresql':
            query=query.where(DocumentJob.payload['filename'].as_string().ilike(f'%{suffix}'))
        else:
            query=query.where(DocumentJob.payload['filename'].as_string().like(f'%{suffix}'))
    if error_code:
        # Prefer structured code; fall back to message heuristics for older rows.
        codes={'size':'超过','type':'支持','ocr':'OCR','empty':'为空','parse':'解析','other':''}
        needle=codes.get(error_code,error_code)
        if error_code=='other':
            query=query.where(DocumentJob.error!='',~DocumentJob.error.contains('超过'),~DocumentJob.error.contains('支持'),
                              ~DocumentJob.error.contains('OCR'),~DocumentJob.error.contains('为空'),~DocumentJob.error.contains('解析'))
        elif needle:
            query=query.where(or_(DocumentJob.payload['error_code'].as_string()==error_code,DocumentJob.error.contains(needle)))
        else:
            query=query.where(DocumentJob.payload['error_code'].as_string()==error_code)
    if error_q:query=query.where(DocumentJob.error.contains(error_q[:100],autoescape=True))
    return paginate(db,query.order_by(DocumentJob.created_at.desc(),DocumentJob.id),paging,job_data)

@router.get('/{jid}')
def job(jid:str,user:Admin=Depends(require('knowledge.read')),db:Session=Depends(get_db)):
    return job_data(scoped_object(db,DocumentJob,jid,user))

@router.post('/{jid}/retry')
def retry(jid:str,user:Admin=Depends(require('knowledge.write')),db:Session=Depends(get_db)):
    j=db.scalar(select(DocumentJob).where(DocumentJob.id==jid,DocumentJob.tenant_id==user.tenant_id).with_for_update().execution_options(populate_existing=True))
    if not j:raise HTTPException(404,'任务不存在')
    retry_job(db,j,user)
    from .admin import record
    record(db,user,'重试文档任务',jid);db.commit();return job_data(j)

class RetryBatchInput(BaseModel):
    job_ids:list[str]=Field(min_length=1,max_length=50)

@router.post('/retry-batch')
def retry_batch(data:RetryBatchInput,user:Admin=Depends(require('knowledge.write')),db:Session=Depends(get_db)):
    results=[]
    for jid in data.job_ids:
        j=db.scalar(select(DocumentJob).where(DocumentJob.id==jid,DocumentJob.tenant_id==user.tenant_id).with_for_update().execution_options(populate_existing=True))
        if not j:
            results.append({'id':jid,'ok':False,'error':'任务不存在'});continue
        try:
            retry_job(db,j,user)
            results.append({'id':jid,'ok':True,'job':job_data(j)})
        except HTTPException as exc:
            results.append({'id':jid,'ok':False,'error':str(exc.detail)})
    from .admin import record
    record(db,user,'批量重试文档任务',f'{sum(r["ok"] for r in results)}/{len(results)}')
    db.commit()
    return {'total':len(results),'succeeded':sum(r['ok'] for r in results),'failed':sum(not r['ok'] for r in results),'items':results}

@router.post('/reindex/{did}')
def reindex(did:str,user:Admin=Depends(require('knowledge.write')),db:Session=Depends(get_db)):
    doc=scoped_object(db,Document,did,user)
    if doc.status!='published':raise HTTPException(409,'仅已发布资料可重建向量索引')
    job=enqueue_index(db,doc);db.commit();return job_data(job)
