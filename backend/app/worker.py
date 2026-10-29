"""Single-process consumer. Multiple consumers may safely share the PostgreSQL queue."""
import base64
import time
import uuid
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import select, update
from .db import SessionLocal, engine
from .models import DocumentJob, Document, Chunk, Admin, now
from .jobs import claim, LEASE
from .config import settings
from .monitoring import touch_heartbeat, redact_log

class LostLease(Exception):pass

def progress(jid,token,percent,stage):
    with SessionLocal() as db:
        changed=db.execute(update(DocumentJob).where(DocumentJob.id==jid,DocumentJob.lease_token==token,DocumentJob.status=='running',DocumentJob.lease_until>now()).values(progress=percent,stage=stage,lease_until=now()+LEASE,updated_at=now())).rowcount
        db.commit()
        if not changed:raise LostLease()

def owned_job(db,jid,token):
    j=db.scalar(select(DocumentJob).where(DocumentJob.id==jid,DocumentJob.lease_token==token,DocumentJob.status=='running',DocumentJob.lease_until>now()).with_for_update().execution_options(populate_existing=True))
    if not j:raise LostLease()
    return j

def run_one(job_id=None):
    with SessionLocal() as db:
        job=claim(db,'worker-'+uuid.uuid4().hex[:12],job_id);db.commit()
        if not job:return False
        jid,token,kind,payload,did,tenant=job.id,job.lease_token,job.kind,job.payload.copy(),job.document_id,job.tenant_id
    try:
        if kind=='parse':
            from .knowledge import parse_file_result
            progress(jid,token,15,'解析文件内容')
            content,parse_info=parse_file_result(payload['filename'],base64.b64decode(payload['content']))
            progress(jid,token,80,'创建待审核草稿')
            with SessionLocal() as db:
                j=owned_job(db,jid,token)
                actor=db.get(Admin,j.actor_id)
                from .permissions import has_permission
                if not actor or actor.tenant_id!=tenant or not has_permission(actor,'knowledge.write'):raise HTTPException(403,'上传员工已停用或无知识编辑权限，请有权限的员工重试')
                from .document_service import create_document
                doc=create_document(db,{'title':Path(payload['filename']).stem[:240] or '上传资料','content':content},actor)
                j.document_id=doc.id
                j.payload={'filename':payload['filename'],'batch_id':payload.get('batch_id',''),**parse_info}
                j.status='succeeded';j.progress=100
                j.stage=('已生成待审核草稿；OCR 低置信度页需核对' if parse_info['ocr_warnings'] else '已生成待审核草稿')
                j.lease_token=None;j.lease_until=None;db.commit()
        elif kind=='index':
            with SessionLocal() as db:
                doc=db.get(Document,did)
                if not doc or doc.tenant_id!=tenant or doc.version!=payload['version'] or doc.status!='published':
                    j=owned_job(db,jid,token);j.status='superseded';j.stage='资料版本已变化，无需继续';j.lease_token=None;j.lease_until=None;db.commit();return True
                chunks=[(c.id,c.text) for c in db.scalars(select(Chunk).where(Chunk.document_id==did,Chunk.tenant_id==tenant).order_by(Chunk.position))]
            vectors={}
            if settings().embedding_enabled:
                from . import knowledge as k
                progress(jid,token,5,'准备向量模型')
                if k._model is None:k.warm_embeddings()
                if k._model is None:raise HTTPException(503,'向量模型尚不可用；文字检索仍可使用，请稍后重试')
                for start in range(0,len(chunks),16):
                    progress(jid,token,10+int(80*start/max(1,len(chunks))),'构建向量索引')
                    batch=chunks[start:start+16]
                    with k._model_lock:output=list(k._model.embed([text for _,text in batch]))
                    vectors.update({cid:v.tolist() for (cid,_),v in zip(batch,output)})
            progress(jid,token,95,'校验版本并原子写入')
            with SessionLocal() as db:
                doc=db.scalar(select(Document).where(Document.id==did,Document.tenant_id==tenant).with_for_update().execution_options(populate_existing=True))
                j=owned_job(db,jid,token)
                current=set(db.scalars(select(Chunk.id).where(Chunk.document_id==did)))
                if not doc or doc.version!=payload['version'] or doc.status!='published' or current!={cid for cid,_ in chunks}:
                    j.status='superseded';j.stage='资料版本已变化，保留当前索引'
                else:
                    for cid,vector in vectors.items():db.execute(update(Chunk).where(Chunk.id==cid,Chunk.document_id==did).values(embedding=vector))
                    j.status='succeeded';j.stage='向量索引已就绪' if settings().embedding_enabled else '文字索引已就绪（向量功能未启用）';j.progress=100
                j.lease_token=None;j.lease_until=None;db.commit()
        else:raise HTTPException(422,'不支持的任务类型')
    except LostLease:
        pass # Another lease holder is the only one allowed to publish this result.
    except Exception as exc:
        error=str(exc.detail) if isinstance(exc,HTTPException) else '任务处理失败，请检查文件内容或服务状态后重试'
        with SessionLocal() as db:
            db.execute(update(DocumentJob).where(DocumentJob.id==jid,DocumentJob.lease_token==token,DocumentJob.status=='running').values(status='failed',stage='处理失败',error=error[:1000],lease_token=None,lease_until=None,updated_at=now()))
            db.commit()
    return True

def main():
    from .migrations import migrate_schema
    migrate_schema(engine)
    while True:
        try:
            with SessionLocal() as db:
                touch_heartbeat(db, 'worker', 'ok', {'pid': 'worker'})
                db.commit()
            if not run_one():time.sleep(2)
        except Exception as exc:
            # No provider payloads, document content or credentials are logged.
            print('文档任务队列暂不可用，稍后重连: ' + redact_log(str(exc), 120), flush=True)
            try:
                with SessionLocal() as db:
                    touch_heartbeat(db, 'worker', 'fail', {'error': redact_log(str(exc), 120)})
                    db.commit()
            except Exception:
                pass
            time.sleep(5)

if __name__=='__main__':main()
