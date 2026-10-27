import csv
import io
from collections import Counter
from datetime import datetime, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session
from .config import settings
from .db import get_db
from .models import Admin, AdminSession, Audit, Company, Conversation, Document, Chunk, Lead, FollowUp, Message, TENANT, now
from .security import admin_user, check_origin, digest, passwords, limit, client_ip, record_security_event, validate_embed_origin_list
from .knowledge import ingest_document, retrieve, parse_file, retrieval_status
from .permissions import require, check_permission, user_data, scoped_object, scope_leads, scope_conversations, has_permission
from .querying import BusinessFilters, Paging, lead_query, conversation_query, paginate, boundaries

router = APIRouter()

def record(db, user, action, target=''):
    db.add(Audit(actor=user.name, tenant_id=user.tenant_id, action=action, target=target[:240]))

def owned(db, model, key, user):
    return scoped_object(db,model,key,user)

def message_data(m):
    return {'id':m.id, 'role':m.role, 'content':m.content, 'sources':m.sources, 'mode':m.mode,
            'state':m.state, 'created_at':m.created_at.isoformat()}

def lead_data(l):
    return {k: (getattr(l,k).isoformat() if k.endswith('_at') and getattr(l,k) else getattr(l,k)) for k in
            ('id','conversation_id','student_id','next_followup_at','name','contact','contact_verified','contact_allowed','level','score','purpose','tags','evidence','stage','owner','owner_id','manual_level','is_demo','created_at','updated_at')}

def conversation_data(db, c, full=False, user=None):
    from .reception import unread
    from .privacy import redact_text
    messages = db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.created_at, Message.id)).all()
    preview=redact_text(messages[-1].content)[:120] if messages else ''
    result = {'id':c.id, 'visitor_name':c.visitor_name, 'source_page':c.source_page, 'status':c.status,
              'analysis':c.analysis, 'assigned_to_id':c.assigned_to_id, 'is_demo':c.is_demo, 'created_at':c.created_at.isoformat(), 'updated_at':c.updated_at.isoformat(),
              'last_message':preview, 'message_count':len(messages)}
    result['unread_count']=unread(db,c,user)
    result['waiting_since']=c.waiting_since.isoformat() if c.waiting_since else None
    assignee=db.get(Admin,c.assigned_to_id) if c.assigned_to_id else None
    result['assigned_name']=assignee.name if assignee else '未分配'
    if full:
        result['messages'] = [message_data(m) for m in messages]
        lead = db.scalar(scope_leads(select(Lead).where(Lead.conversation_id == c.id),user)) if user else None
        result['lead'] = lead_data(lead) if lead else None
    return result

class Login(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=300)

@router.post('/auth/login')
def login(data: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    import secrets
    ip = client_ip(request)
    email = data.email.strip().lower()
    try:
        check_origin(request)
    except HTTPException as exc:
        if exc.status_code == 403:
            record_security_event(db, '拒绝异常来源登录', target=email[:80], details=f'ip={ip}; origin={request.headers.get("origin","")}', actor='匿名')
            db.commit()
        raise
    limit('login:' + ip, 10, 300)
    user = db.scalar(select(Admin).where(Admin.email == email, Admin.tenant_id == TENANT))
    valid = False
    if user and user.is_active:
        try:
            valid = passwords.verify(data.password, user.password_hash)
        except Exception:
            pass
    if not valid:
        record_security_event(db, '登录失败', target=email[:80], details=f'ip={ip}', actor='匿名')
        db.commit()
        raise HTTPException(401, '邮箱或密码不正确')
    token = secrets.token_urlsafe(48)
    db.add(AdminSession(token_hash=digest(token), admin_id=user.id))
    record(db,user,'登录后台')
    db.commit()
    response.set_cookie('admin_session',token,httponly=True,secure=settings().cookie_secure,samesite='lax',max_age=43200,path='/')
    return user_data(user)

@router.get('/auth/me')
def me(user: Admin = Depends(admin_user)):
    return user_data(user)

@router.post('/auth/logout')
def logout(request: Request, response: Response, user: Admin = Depends(admin_user), db: Session = Depends(get_db)):
    db.execute(delete(AdminSession).where(AdminSession.token_hash == digest(request.cookies.get('admin_session',''))))
    db.commit()
    response.delete_cookie('admin_session',path='/')
    return {'ok':True}

@router.get('/admin/settings')
def get_settings(user: Admin = Depends(require('settings.read')), db: Session = Depends(get_db)):
    from .privacy import DEFAULT_PRIVACY_NOTICE
    config = db.get(Company,user.tenant_id).config
    public={k:v for k,v in config.items() if not k.startswith('_') and k != 'knowledge_seeded'}
    if not public.get('privacy_notice'):
        public['privacy_notice']=DEFAULT_PRIVACY_NOTICE
    return {**public,
            'model_configured': bool(settings().deepseek_api_key), 'model':settings().deepseek_model,
            'provider':'DeepSeek', 'retrieval':retrieval_status(), 'demo_notice':'机构、课程及种子会话均为演示资料'}

class ConfigPatch(BaseModel):
    company_name: str | None = Field(None,min_length=1,max_length=80)
    bot_name: str | None = Field(None,min_length=1,max_length=80)
    description: str | None = Field(None,max_length=500)
    welcome: str | None = Field(None,min_length=1,max_length=1000)
    privacy_notice: str | None = Field(None,min_length=1,max_length=1000)
    service_hours: str | None = Field(None,max_length=100)
    suggested_questions: list[str] | None = Field(None,max_length=6)
    allowed_embed_origins: list[str] | None = Field(None,max_length=30)

@router.patch('/admin/settings')
def update_settings(data: ConfigPatch, user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    values = data.model_dump(exclude_none=True)
    if any(len(q) > 150 for q in values.get('suggested_questions',[])):
        raise HTTPException(422,'推荐问题不能超过 150 字')
    if 'allowed_embed_origins' in values:
        values['allowed_embed_origins'] = validate_embed_origin_list(values['allowed_embed_origins'])
    company = db.get(Company,user.tenant_id)
    company.config = {**company.config, **values}
    record(db,user,'更新企业或机器人设置')
    db.commit()
    return get_settings(user,db)

@router.get('/admin/conversations')
def conversations(filters:BusinessFilters=Depends(),paging:Paging=Depends(),status:str='',user:Admin=Depends(require('conversations.read')),db:Session=Depends(get_db)):
    query=conversation_query(db,user,filters,status).order_by(Conversation.waiting_since.asc().nulls_last(),Conversation.created_at.desc(),Conversation.id.desc())
    return paginate(db,query,paging,lambda c:conversation_data(db,c,user=user))

@router.get('/admin/conversations/{sid}')
def conversation(sid: str, user: Admin = Depends(require('conversations.read')), db: Session = Depends(get_db)):
    return conversation_data(db,owned(db,Conversation,sid,user),True,user)

@router.post('/admin/conversations/{sid}/mark-demo')
def mark_demo(sid: str, user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    conv=owned(db,Conversation,sid,user)
    conv.is_demo=True
    lead=db.scalar(select(Lead).where(Lead.conversation_id==sid))
    if lead: lead.is_demo=True
    record(db,user,'标记测试记录',sid)
    db.commit()
    return {'is_demo':True}

@router.post('/admin/conversations/{sid}/takeover')
def takeover(sid: str, user: Admin = Depends(require('conversations.handle')), db: Session = Depends(get_db)):
    from .reception import lock_conversation
    conv = lock_conversation(db,sid,user)
    if conv.assigned_to_id and conv.assigned_to_id!=user.id and user.role!='admin':
        raise HTTPException(409,'此咨询正由其他员工接待，请由管理员调整接待人')
    conv.status = 'human'
    conv.assigned_to_id = user.id
    conv.waiting_since = None
    record(db,user,'接管咨询',sid)
    db.commit()
    return {'status':conv.status}

class TextInput(BaseModel):
    content: str = Field(min_length=1,max_length=4000)

@router.post('/admin/conversations/{sid}/reply')
def reply(sid: str, data: TextInput, user: Admin = Depends(require('conversations.handle')), db: Session = Depends(get_db)):
    from .reception import lock_conversation
    conv = lock_conversation(db,sid,user)
    if conv.status != 'human': raise HTTPException(409,'请先接管此咨询')
    if conv.assigned_to_id and conv.assigned_to_id!=user.id and user.role!='admin':
        raise HTTPException(409,'此咨询由其他员工接待，请先申请接管')
    msg = Message(conversation_id=sid,role='agent',content=data.content,mode='human')
    db.add(msg)
    conv.updated_at = now()
    db.commit()
    return message_data(msg)

class StatusInput(BaseModel):
    status: Literal['bot','closed']

@router.post('/admin/conversations/{sid}/status')
def change_status(sid: str, data: StatusInput, user: Admin = Depends(require('conversations.handle')), db: Session = Depends(get_db)):
    from .reception import lock_conversation
    conv = lock_conversation(db,sid,user)
    if conv.assigned_to_id and conv.assigned_to_id!=user.id and user.role!='admin':
        raise HTTPException(409,'此咨询正由其他员工接待，请由管理员调整接待状态')
    conv.status = data.status
    conv.assigned_to_id = None
    conv.waiting_since = None
    record(db,user,'调整接待状态',sid)
    db.commit()
    return {'status':conv.status}

@router.get('/admin/leads')
def leads(filters:BusinessFilters=Depends(),paging:Paging=Depends(),user:Admin=Depends(require('leads.read')),db:Session=Depends(get_db)):
    return paginate(db,lead_query(db,user,filters).order_by(Lead.created_at.desc(),Lead.id.desc()),paging,lead_data)

@router.get('/admin/leads-export')
def export_leads(filters:BusinessFilters=Depends(),user:Admin=Depends(require('leads.export')),db:Session=Depends(get_db)):
    entries=[lead_data(l) for l in db.scalars(lead_query(db,user,filters).order_by(Lead.created_at.desc(),Lead.id.desc()))]
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
def lead_detail(lead_id: str, user: Admin = Depends(require('leads.read')), db: Session = Depends(get_db)):
    lead = owned(db,Lead,lead_id,user)
    return {**lead_data(lead),'followups':[{'id':f.id,'author':f.author,'content':f.content,'created_at':f.created_at.isoformat()} for f in db.scalars(select(FollowUp).where(FollowUp.lead_id == lead.id).order_by(FollowUp.created_at.desc()))]}

class LeadPatch(BaseModel):
    owner: str | None = Field(None,max_length=80)
    owner_id: str | None = Field(None,max_length=36)
    stage: Literal['新线索','待分配','跟进中','已合格','商机推进','已成交','已失效'] | None = None
    level: Literal['待判定','一般咨询','低意向','中意向','高意向','不适用'] | None = None
    contact_verified: bool | None = None
    automatic_level: bool = False
    next_followup_at:datetime|None=None

@router.patch('/admin/leads/{lead_id}')
def patch_lead(lead_id: str, data: LeadPatch, user: Admin = Depends(require('leads.write')), db: Session = Depends(get_db)):
    from .students import crm_guard,lock_student,lock_conversations
    crm_guard(db,user,'leads.write')
    lead = owned(db,Lead,lead_id,user)
    db.refresh(lead)
    if user.role=='advisor' and lead.owner_id!=user.id:raise HTTPException(404,'线索已转交')
    if lead.student_id:lock_student(db,lead.student_id,user)
    lock_conversations(db,[lead.conversation_id])
    lead=db.scalar(scope_leads(select(Lead).where(Lead.id==lead_id),user).with_for_update().execution_options(populate_existing=True))
    if not lead:raise HTTPException(404,'线索已转交')
    if 'owner_id' in data.model_fields_set or data.owner is not None:
        check_permission(user,'leads.assign')
        if data.owner is not None:
            raise HTTPException(422,'请从员工列表选择负责人，不接受文字姓名分配')
        employee=db.get(Admin,data.owner_id) if data.owner_id else None
        if data.owner_id and (not employee or employee.tenant_id!=user.tenant_id or not employee.is_active or employee.role not in ('admin','advisor','support')):
            raise HTTPException(422,'请选择启用且可以跟进线索的员工')
        conv=db.get(Conversation,lead.conversation_id)
        from .students import assign_lead, assign_student, lock_student
        if lead.student_id:assign_student(db,lock_student(db,lead.student_id,user),employee)
        else:assign_lead(db,lead,employee)
    changed_level=data.level is not None and data.level!=lead.level
    for k,v in data.model_dump(exclude_none=True,exclude={'automatic_level','owner','owner_id'}).items(): setattr(lead,k,v)
    if changed_level: lead.manual_level = True
    if data.automatic_level:
        from .chat import update_analysis
        lead.manual_level=False
        update_analysis(db,db.get(Conversation,lead.conversation_id))
    if 'next_followup_at' in data.model_fields_set:lead.next_followup_at=data.next_followup_at
    if lead.stage=='已成交' and not lead.student_id:
        from .students import from_lead
        from_lead(db,lead)
    record(db,user,'更新线索',lead.id)
    db.commit()
    return lead_detail(lead_id,user,db)

@router.post('/admin/leads/{lead_id}/followups')
def followup(lead_id: str, data: TextInput, user: Admin = Depends(require('leads.write')), db: Session = Depends(get_db)):
    owned(db,Lead,lead_id,user)
    db.add(FollowUp(lead_id=lead_id,author=user.name,content=data.content))
    record(db,user,'添加跟进',lead_id)
    db.commit()
    return lead_detail(lead_id,user,db)

@router.get('/admin/overview')
def overview(filters:BusinessFilters=Depends(),user:Admin=Depends(require('analytics.read')),db:Session=Depends(get_db)):
    from zoneinfo import ZoneInfo
    from datetime import timezone
    def business_date(value):
        aware=value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        return aware.astimezone(ZoneInfo('Asia/Shanghai')).date()
    cs = db.scalars(conversation_query(db,user,filters)).all()
    ls = db.scalars(lead_query(db,user,filters)).all()
    docs = db.scalars(select(Document).where(Document.tenant_id == user.tenant_id)).all()
    ids = [c.id for c in cs]
    messages = db.scalars(select(Message).where(Message.conversation_id.in_(ids))).all() if ids else []
    engaged={m.conversation_id for m in messages if m.role=='user'}|{l.conversation_id for l in ls}
    cs=[c for c in cs if c.id in engaged]
    contacted=set(db.scalars(select(Lead.conversation_id).where(Lead.conversation_id.in_([c.id for c in cs]),Lead.tenant_id==user.tenant_id))) if cs else set()
    trend=[]
    for i in range(6,-1,-1):
        day = business_date(now() - timedelta(days=i))
        trend.append({'date':day.isoformat(),'conversations':sum(business_date(c.created_at)==day for c in cs),'leads':sum(business_date(l.created_at)==day for l in ls)})
    from .privacy import redact_text
    gaps = [{'question':redact_text(m.content)[:160],'created_at':m.created_at.isoformat()} for m in messages if m.role=='user' and any(a.reply_to==m.id and a.mode=='unknown' for a in messages)]
    from .document_service import document_active
    from .students import todo_query,student_query
    from .querying import count
    tasks=todo_query(db,user,filters)
    from .scoring import EXAMS
    related={c.id:c for c in db.scalars(select(Conversation).where(Conversation.tenant_id==user.tenant_id,Conversation.id.in_([l.conversation_id for l in ls])))} if ls else {}
    exam_counts=Counter()
    for lead in ls:
        analysis=related[lead.conversation_id].analysis if lead.conversation_id in related else {}
        names=set(lead.tags)|set(analysis.get('active_exam_types',[]))|{n.get('exam_type') for n in analysis.get('needs',[]) if n.get('state')=='active'}
        exam_counts.update(sorted(names.intersection(EXAMS)))
    return {'include_demo':filters.include_demo,'filters':filters.model_dump(mode='json'),
            'counts':{'conversations':len(cs),'leads':len(ls),'contacted_conversations':len(contacted),'high_intent':sum(l.level=='高意向' for l in ls),
                      'documents':len(docs),'published':sum(document_active(d) and d.visibility=='public' for d in docs),
                      'pending':sum(l.stage in ('新线索','待分配') for l in ls),'handoffs':sum(c.status=='waiting' for c in cs),
                      'customers':sum(l.stage=='已成交' for l in ls),
                      'qualified':sum(l.stage in ('已合格','商机推进','已成交') for l in ls)},
            'student_count':count(db,student_query(db,user,filters)),
            'todo_count':count(db,tasks),'todos':[lead_data(l) for l in db.scalars(tasks.limit(5))],
            'trend':trend,'intent':[{'name':k,'value':v} for k,v in Counter(l.level for l in ls).items()],
            'exams':[{'name':k,'value':v} for k,v in exam_counts.items()],
            'recent_conversations':[conversation_data(db,c) for c in sorted(cs,key=lambda c:c.updated_at,reverse=True)[:5]],
            'recent_leads':[lead_data(l) for l in sorted(ls,key=lambda l:l.created_at,reverse=True)[:5]],
            'knowledge_gaps':gaps[:20], 'usage':{'total_tokens':sum(m.usage.get('total_tokens',0) for m in messages),'deepseek_answers':sum(m.mode=='deepseek' for m in messages)},
            'retrieval':retrieval_status(), 'model_configured':bool(settings().deepseek_api_key)}

@router.get('/admin/audit')
def audit(filters:BusinessFilters=Depends(),paging:Paging=Depends(),user:Admin=Depends(require('audit.read')),db:Session=Depends(get_db)):
    query=select(Audit).where(Audit.tenant_id==user.tenant_id)
    start,end=boundaries(filters)
    if start:query=query.where(Audit.created_at>=start)
    if end:query=query.where(Audit.created_at<end)
    if filters.q:query=query.where(Audit.action.contains(filters.q,autoescape=True))
    return paginate(db,query.order_by(Audit.created_at.desc(),Audit.id.desc()),paging,lambda a:{'id':a.id,'actor':a.actor,'action':a.action,'target':a.target,'details':a.details,'created_at':a.created_at.isoformat()})

@router.get('/admin/filter-options')
def filter_options(user:Admin=Depends(require('leads.read')),db:Session=Depends(get_db)):
    query=select(Admin).where(Admin.tenant_id==user.tenant_id,Admin.role.in_(['admin','advisor','support']))
    if user.role=='advisor':query=query.where(Admin.id==user.id)
    return [{'id':a.id,'name':a.name,'is_active':a.is_active} for a in db.scalars(query.order_by(Admin.name,Admin.id))]
