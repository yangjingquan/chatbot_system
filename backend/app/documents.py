from datetime import datetime
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from .db import get_db
from .models import Admin, Document, DocumentRevision, Chunk
from .permissions import require, scoped_object
from .knowledge import parse_file, retrieve, retrieval_status
from . import document_service as service

router=APIRouter(prefix='/admin')
Exam=Literal['通用','公安联考','国考','省考','选调生','事业单位','社会招警','辅警','社区工作者']

class KnowledgeInput(BaseModel):
    title:str=Field(min_length=1,max_length=240)
    content:str=Field(min_length=1,max_length=200000)
    category:str=Field('未分类',max_length=100)
    visibility:Literal['public','internal']='public'
    status:Literal['draft','published','archived']='draft'
    knowledge_type:Literal['general','course','policy']='general'
    exam_type:Exam='通用'
    exam_year:int|None=Field(None,ge=2000,le=2100)
    region:str=Field('全国',min_length=1,max_length=80)
    source_url:str|None=Field(None,max_length=2000)
    responsible_id:str|None=Field(None,max_length=36)
    policy_verified:bool=False
    valid_from:datetime|None=None
    valid_until:datetime|None=None

class KnowledgePatch(BaseModel):
    title:str|None=Field(None,min_length=1,max_length=240)
    content:str|None=Field(None,min_length=1,max_length=200000)
    category:str|None=Field(None,max_length=100)
    visibility:Literal['public','internal']|None=None
    knowledge_type:Literal['general','course','policy']|None=None
    exam_type:Exam|None=None
    exam_year:int|None=Field(None,ge=2000,le=2100)
    region:str|None=Field(None,min_length=1,max_length=80)
    source_url:str|None=Field(None,max_length=2000)
    responsible_id:str|None=Field(None,max_length=36)
    policy_verified:bool|None=None
    valid_from:datetime|None=None
    valid_until:datetime|None=None
    expected_sequence:int=Field(ge=0)
    # Kept for explicit legacy withdrawal only; published edits always use a draft.
    status:Literal['draft','published','archived']|None=None

class ActionInput(BaseModel):
    expected_sequence:int=Field(ge=0)
    note:str=Field('',max_length=1000)
    revision_id:str|None=None

def serialize(db,doc,full=True):
    return {**service.document_data(db,doc,full),'chunks':db.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id==doc.id))}

@router.get('/knowledge-responsibles')
def responsibles(user:Admin=Depends(require('knowledge.read')),db:Session=Depends(get_db)):
    return [{'id':a.id,'name':a.name,'role':a.role} for a in db.scalars(select(Admin).where(Admin.tenant_id==user.tenant_id,Admin.is_active.is_(True),Admin.role.in_(['admin','knowledge'])).order_by(Admin.name))]

@router.get('/documents')
def documents(q:str='',user:Admin=Depends(require('knowledge.read')),db:Session=Depends(get_db)):
    query=select(Document).where(Document.tenant_id==user.tenant_id)
    docs=[serialize(db,d,False) for d in db.scalars(query.order_by(Document.updated_at.desc()).limit(500))]
    return [d for d in docs if q[:200] in d['title']] if q else docs

@router.post('/documents/upload')
def upload(file:UploadFile=File(...),user:Admin=Depends(require('knowledge.write')),db:Session=Depends(get_db)):
    content=parse_file(file.filename or '',file.file.read(5*1024*1024+1))
    doc=service.create_document(db,{'title':Path(file.filename or '上传知识').stem[:240],'content':content},user)
    db.commit();return serialize(db,doc)

@router.post('/documents')
def create(data:KnowledgeInput,user:Admin=Depends(require('knowledge.write')),db:Session=Depends(get_db)):
    doc=service.create_document(db,data.model_dump(exclude={'status'}),user,data.status)
    db.commit();return serialize(db,doc)

@router.get('/documents/{doc_id}')
def get(doc_id:str,user:Admin=Depends(require('knowledge.read')),db:Session=Depends(get_db)):
    return serialize(db,scoped_object(db,Document,doc_id,user))

@router.patch('/documents/{doc_id}')
def patch(doc_id:str,data:KnowledgePatch,user:Admin=Depends(require('knowledge.write')),db:Session=Depends(get_db)):
    values=data.model_dump(exclude_unset=True,exclude={'status','expected_sequence'})
    if any(values.get(key) is None for key in values if key in ('title','content','category','visibility','knowledge_type','exam_type','region','policy_verified')):
        raise HTTPException(422,'必要知识字段不可为空')
    if data.status=='archived':
        if values:raise HTTPException(422,'请分别保存草稿和执行下架操作')
        doc=service.transition(db,doc_id,user,data.expected_sequence,'archive')
    else:
        doc=service.save_draft(db,doc_id,values,user,data.expected_sequence)
    db.commit();return serialize(db,doc)

@router.post('/documents/{doc_id}/{action}')
def action(doc_id:str,action:str,data:ActionInput,user:Admin=Depends(require('knowledge.write')),db:Session=Depends(get_db)):
    doc=service.transition(db,doc_id,user,data.expected_sequence,action,data.note,data.revision_id)
    db.commit();return serialize(db,doc)

@router.get('/documents/{doc_id}/revisions')
def history(doc_id:str,user:Admin=Depends(require('knowledge.read')),db:Session=Depends(get_db)):
    scoped_object(db,Document,doc_id,user)
    return [service.revision_data(r) for r in db.scalars(select(DocumentRevision).where(DocumentRevision.document_id==doc_id).order_by(DocumentRevision.number.desc()))]

@router.get('/documents/{doc_id}/revisions/{revision_id}')
def revision(doc_id:str,revision_id:str,user:Admin=Depends(require('knowledge.read')),db:Session=Depends(get_db)):
    scoped_object(db,Document,doc_id,user)
    row=db.get(DocumentRevision,revision_id)
    if not row or row.document_id!=doc_id or row.tenant_id!=user.tenant_id:raise HTTPException(404,'版本不存在')
    return service.revision_data(row,True)

class RetrievalInput(BaseModel):
    query:str=Field(min_length=1,max_length=1000)
    public_only:bool=True

@router.post('/retrieval-test')
def retrieval(data:RetrievalInput,user:Admin=Depends(require('knowledge.read')),db:Session=Depends(get_db)):
    return {'sources':retrieve(db,data.query,data.public_only,user.tenant_id),**retrieval_status(),'context':service.infer_context(data.query)}
