import csv
import io
from collections import Counter
from datetime import timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session
from .config import settings
from .db import get_db
from .models import Admin, AdminSession, Audit, Company, Conversation, Document, Chunk, Lead, FollowUp, Message, TENANT, now
from .security import admin_user, check_origin, digest, passwords, limit
from .knowledge import ingest_document, retrieve, parse_file, retrieval_status

router = APIRouter()

def record(db, user, action, target=''):
    db.add(Audit(actor=user.name, tenant_id=user.tenant_id, action=action, target=target[:240]))

def owned(db, model, key, tenant_id=TENANT):
    item = db.get(model, key)
    if not item or item.tenant_id != tenant_id:
        raise HTTPException(404, '记录不存在')
    return item

def message_data(m):
    return {'id':m.id, 'role':m.role, 'content':m.content, 'sources':m.sources, 'mode':m.mode,
            'state':m.state, 'created_at':m.created_at.isoformat()}

def document_data(db, d, full=True):
    return {'id':d.id, 'title':d.title, 'category':d.category, 'status':d.status, 'visibility':d.visibility,
            'content':d.content if full else d.content[:100], 'source_url':d.source_url, 'is_demo':d.is_demo,
            'version':d.version, 'chunks':db.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id == d.id)),
            'updated_at':d.updated_at.isoformat()}

def lead_data(l):
    return {k: (getattr(l,k).isoformat() if k.endswith('_at') else getattr(l,k)) for k in
            ('id','conversation_id','name','contact','contact_verified','contact_allowed','level','score','purpose','tags','evidence','stage','owner','manual_level','is_demo','created_at','updated_at')}

def conversation_data(db, c, full=False):
    messages = db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.created_at, Message.id)).all()
    result = {'id':c.id, 'visitor_name':c.visitor_name, 'source_page':c.source_page, 'status':c.status,
              'analysis':c.analysis, 'is_demo':c.is_demo, 'created_at':c.created_at.isoformat(), 'updated_at':c.updated_at.isoformat(),
              'last_message':messages[-1].content[:120] if messages else '', 'message_count':len(messages)}
    if full:
        result['messages'] = [message_data(m) for m in messages]
        lead = db.scalar(select(Lead).where(Lead.conversation_id == c.id))
        result['lead'] = lead_data(lead) if lead else None
    return result

class Login(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=300)

@router.post('/auth/login')
def login(data: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    import secrets
    check_origin(request)
    limit('login:' + (request.client.host if request.client else 'local'), 10, 300)
    user = db.scalar(select(Admin).where(Admin.email == data.email.strip().lower(), Admin.tenant_id == TENANT))
    valid = False
    if user:
        try:
            valid = passwords.verify(data.password, user.password_hash)
        except Exception:
            pass
    if not valid:
        raise HTTPException(401, '邮箱或密码不正确')
    token = secrets.token_urlsafe(48)
    db.add(AdminSession(token_hash=digest(token), admin_id=user.id))
    record(db,user,'登录后台')
    db.commit()
    response.set_cookie('admin_session',token,httponly=True,secure=settings().cookie_secure,samesite='lax',max_age=43200,path='/')
    return {'name':user.name,'email':user.email}

@router.get('/auth/me')
def me(user: Admin = Depends(admin_user)):
    return {'name':user.name,'email':user.email}

@router.post('/auth/logout')
def logout(request: Request, response: Response, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    db.execute(delete(AdminSession).where(AdminSession.token_hash == digest(request.cookies.get('admin_session',''))))
    db.commit()
    response.delete_cookie('admin_session',path='/')
    return {'ok':True}

@router.get('/admin/settings')
def get_settings(user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    config = db.get(Company,user.tenant_id).config
    return {**{k:v for k,v in config.items() if not k.startswith('_') and k != 'knowledge_seeded'},
            'model_configured': bool(settings().deepseek_api_key), 'model':settings().deepseek_model,
            'provider':'DeepSeek', 'retrieval':retrieval_status(), 'demo_notice':'机构、课程及种子会话均为演示资料'}

class ConfigPatch(BaseModel):
    company_name: str | None = Field(None,min_length=1,max_length=80)
    bot_name: str | None = Field(None,min_length=1,max_length=80)
    description: str | None = Field(None,max_length=500)
    welcome: str | None = Field(None,min_length=1,max_length=1000)
    service_hours: str | None = Field(None,max_length=100)
    suggested_questions: list[str] | None = Field(None,max_length=6)

@router.patch('/admin/settings')
def update_settings(data: ConfigPatch, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    values = data.model_dump(exclude_none=True)
    if any(len(q) > 150 for q in values.get('suggested_questions',[])):
        raise HTTPException(422,'推荐问题不能超过 150 字')
    company = db.get(Company,user.tenant_id)
    company.config = {**company.config, **values}
    record(db,user,'更新企业或机器人设置')
    db.commit()
    return get_settings(user,db)

@router.get('/admin/documents')
def documents(q: str = '', user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    query = select(Document).where(Document.tenant_id == user.tenant_id)
    if q:
        query = query.where(Document.title.contains(q[:200]))
    return [document_data(db,d,False) for d in db.scalars(query.order_by(Document.updated_at.desc()).limit(500))]

class DocumentCreate(BaseModel):
    title: str = Field(min_length=1,max_length=240)
    content: str = Field(min_length=1,max_length=200000)
    category: str = Field('未分类',max_length=100)
    status: Literal['draft','published','archived'] = 'draft'
    visibility: Literal['public','internal'] = 'public'

@router.post('/admin/documents/upload')
def upload(file: UploadFile = File(...), user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    from pathlib import Path
    raw = file.file.read(5 * 1024 * 1024 + 1)
    content = parse_file(file.filename or '', raw)
    doc = Document(title=Path(file.filename or '上传资料').stem[:240], content=content, tenant_id=user.tenant_id, status='draft')
    db.add(doc)
    ingest_document(db,doc)
    record(db,user,'上传知识（待审核）',doc.title)
    db.commit()
    return document_data(db,doc)

@router.post('/admin/documents')
def create_document(data: DocumentCreate, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    doc = Document(**data.model_dump(),tenant_id=user.tenant_id)
    db.add(doc)
    ingest_document(db,doc)
    record(db,user,'创建知识',doc.title)
    db.commit()
    return document_data(db,doc)

@router.get('/admin/documents/{doc_id}')
def get_document(doc_id: str, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    return document_data(db,owned(db,Document,doc_id,user.tenant_id))

class DocumentPatch(BaseModel):
    title: str | None = Field(None,min_length=1,max_length=240)
    content: str | None = Field(None,min_length=1,max_length=200000)
    category: str | None = Field(None,max_length=100)
    status: Literal['draft','published','archived'] | None = None
    visibility: Literal['public','internal'] | None = None

@router.patch('/admin/documents/{doc_id}')
def patch_document(doc_id: str, data: DocumentPatch, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    doc = owned(db,Document,doc_id,user.tenant_id)
    changes = data.model_dump(exclude_none=True)
    for key,value in changes.items():
        setattr(doc,key,value)
    doc.version += 1
    doc.updated_at = now()
    if 'content' in changes or 'title' in changes:
        ingest_document(db,doc)
    record(db,user,'更新知识版本',doc.title)
    db.commit()
    return document_data(db,doc)

class RetrievalTest(BaseModel):
    query: str = Field(min_length=1,max_length=1000)
    public_only: bool = True

@router.post('/admin/retrieval-test')
def retrieval_test(data: RetrievalTest, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    return {'sources':retrieve(db,data.query,data.public_only,user.tenant_id), **retrieval_status()}

@router.get('/admin/conversations')
def conversations(q: str = '', include_demo: bool = False, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    query = select(Conversation).where(Conversation.tenant_id == user.tenant_id)
    if not include_demo: query = query.where(Conversation.is_demo.is_(False))
    if q: query = query.where(Conversation.visitor_name.contains(q[:100]))
    return [conversation_data(db,c) for c in db.scalars(query.order_by(Conversation.updated_at.desc()).limit(300))]

@router.get('/admin/conversations/{sid}')
def conversation(sid: str, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    return conversation_data(db,owned(db,Conversation,sid,user.tenant_id),True)

@router.post('/admin/conversations/{sid}/mark-demo')
def mark_demo(sid: str, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    conv=owned(db,Conversation,sid,user.tenant_id)
    conv.is_demo=True
    lead=db.scalar(select(Lead).where(Lead.conversation_id==sid))
    if lead: lead.is_demo=True
    record(db,user,'标记测试记录',sid)
    db.commit()
    return {'is_demo':True}

@router.post('/admin/conversations/{sid}/takeover')
def takeover(sid: str, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    conv = owned(db,Conversation,sid,user.tenant_id)
    conv.status = 'human'
    record(db,user,'接管咨询',sid)
    db.commit()
    return {'status':conv.status}

class TextInput(BaseModel):
    content: str = Field(min_length=1,max_length=4000)

@router.post('/admin/conversations/{sid}/reply')
def reply(sid: str, data: TextInput, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    conv = owned(db,Conversation,sid,user.tenant_id)
    if conv.status != 'human': raise HTTPException(409,'请先接管此咨询')
    msg = Message(conversation_id=sid,role='agent',content=data.content,mode='human')
    db.add(msg)
    conv.updated_at = now()
    db.commit()
    return message_data(msg)

class StatusInput(BaseModel):
    status: Literal['bot','closed']

@router.post('/admin/conversations/{sid}/status')
def change_status(sid: str, data: StatusInput, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    conv = owned(db,Conversation,sid,user.tenant_id)
    conv.status = data.status
    record(db,user,'调整接待状态',sid)
    db.commit()
    return {'status':conv.status}

@router.get('/admin/leads')
def leads(q: str = '', include_demo: bool = False, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    query = select(Lead).where(Lead.tenant_id == user.tenant_id)
    if not include_demo: query = query.where(Lead.is_demo.is_(False))
    if q: query = query.where(Lead.name.contains(q[:100]))
    return [lead_data(l) for l in db.scalars(query.order_by(Lead.created_at.desc()).limit(500))]

@router.get('/admin/leads-export')
def export_leads(include_demo: bool = False, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    entries = leads('',include_demo,user,db)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['姓名','联系方式','核验状态','联系许可','意向等级','跟进阶段','负责人','演示数据'])
    def safe(v):
        s = str(v)
        return "'" + s if s.startswith(('=','+','-','@','\t','\r')) else s
    for row in entries:
        writer.writerow([safe(row['name']),safe(row['contact']),'已核验' if row['contact_verified'] else '待核验','可就本次咨询联系' if row['contact_allowed'] else '已停止联系',row['level'],row['stage'],safe(row['owner']),row['is_demo']])
    record(db,user,'导出线索',str(len(entries)))
    db.commit()
    return StreamingResponse(iter(['\ufeff'+buffer.getvalue()]),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="leads.csv"'})

@router.get('/admin/leads/{lead_id}')
def lead_detail(lead_id: str, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    lead = owned(db,Lead,lead_id,user.tenant_id)
    return {**lead_data(lead),'followups':[{'id':f.id,'author':f.author,'content':f.content,'created_at':f.created_at.isoformat()} for f in db.scalars(select(FollowUp).where(FollowUp.lead_id == lead.id).order_by(FollowUp.created_at.desc()))]}

class LeadPatch(BaseModel):
    owner: str | None = Field(None,max_length=80)
    stage: Literal['新线索','待分配','跟进中','已合格','商机推进','已成交','已失效'] | None = None
    level: Literal['待判定','一般咨询','低意向','中意向','高意向','不适用'] | None = None
    contact_verified: bool | None = None
    automatic_level: bool = False

@router.patch('/admin/leads/{lead_id}')
def patch_lead(lead_id: str, data: LeadPatch, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    lead = owned(db,Lead,lead_id,user.tenant_id)
    changed_level=data.level is not None and data.level!=lead.level
    for k,v in data.model_dump(exclude_none=True,exclude={'automatic_level'}).items(): setattr(lead,k,v)
    if changed_level: lead.manual_level = True
    if data.automatic_level:
        from .chat import update_analysis
        lead.manual_level=False
        update_analysis(db,db.get(Conversation,lead.conversation_id))
    record(db,user,'更新线索',lead.id)
    db.commit()
    return lead_detail(lead_id,user,db)

@router.post('/admin/leads/{lead_id}/followups')
def followup(lead_id: str, data: TextInput, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    owned(db,Lead,lead_id,user.tenant_id)
    db.add(FollowUp(lead_id=lead_id,author=user.name,content=data.content))
    record(db,user,'添加跟进',lead_id)
    db.commit()
    return lead_detail(lead_id,user,db)

@router.get('/admin/overview')
def overview(include_demo: bool = False, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    from zoneinfo import ZoneInfo
    from datetime import timezone
    def business_date(value):
        aware=value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        return aware.astimezone(ZoneInfo('Asia/Shanghai')).date()
    cs = db.scalars(select(Conversation).where(Conversation.tenant_id == user.tenant_id)).all()
    ls = db.scalars(select(Lead).where(Lead.tenant_id == user.tenant_id)).all()
    if not include_demo:
        cs,ls = [c for c in cs if not c.is_demo],[l for l in ls if not l.is_demo]
    docs = db.scalars(select(Document).where(Document.tenant_id == user.tenant_id)).all()
    ids = [c.id for c in cs]
    messages = db.scalars(select(Message).where(Message.conversation_id.in_(ids))).all() if ids else []
    engaged={m.conversation_id for m in messages if m.role=='user'}|{l.conversation_id for l in ls}
    cs=[c for c in cs if c.id in engaged]
    trend=[]
    for i in range(6,-1,-1):
        day = business_date(now() - timedelta(days=i))
        trend.append({'date':day.isoformat(),'conversations':sum(business_date(c.created_at)==day for c in cs),'leads':sum(business_date(l.created_at)==day for l in ls)})
    gaps = [{'question':m.content[:160],'created_at':m.created_at.isoformat()} for m in messages if m.role=='user' and any(a.reply_to==m.id and a.mode=='unknown' for a in messages)]
    return {'include_demo':include_demo,
            'counts':{'conversations':len(cs),'leads':len(ls),'high_intent':sum(l.level=='高意向' for l in ls),
                      'documents':len(docs),'published':sum(d.status=='published' and d.visibility=='public' for d in docs),
                      'pending':sum(l.stage in ('新线索','待分配') for l in ls),'handoffs':sum(c.status=='waiting' for c in cs),
                      'customers':sum(l.stage=='已成交' for l in ls),
                      'qualified':sum(l.stage in ('已合格','商机推进','已成交') for l in ls)},
            'trend':trend,'intent':[{'name':k,'value':v} for k,v in Counter(l.level for l in ls).items()],
            'exams':[{'name':k,'value':v} for k,v in Counter(t for l in ls for t in l.tags if t in ('公安联考','国考','省考','选调生','事业单位','社会招警','辅警','社区工作者')).items()],
            'recent_conversations':[conversation_data(db,c) for c in sorted(cs,key=lambda c:c.updated_at,reverse=True)[:5]],
            'recent_leads':[lead_data(l) for l in sorted(ls,key=lambda l:l.created_at,reverse=True)[:5]],
            'knowledge_gaps':gaps[:20], 'usage':{'total_tokens':sum(m.usage.get('total_tokens',0) for m in messages),'deepseek_answers':sum(m.mode=='deepseek' for m in messages)},
            'retrieval':retrieval_status(), 'model_configured':bool(settings().deepseek_api_key)}

@router.get('/admin/audit')
def audit(user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    return [{'id':a.id,'actor':a.actor,'action':a.action,'target':a.target,'created_at':a.created_at.isoformat()} for a in db.scalars(select(Audit).where(Audit.tenant_id==user.tenant_id).order_by(Audit.created_at.desc()).limit(100))]
