import csv
import io
from datetime import datetime
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
    from .reception import unread, waiting_seconds, is_timed_out, reception_config
    from .analytics import last_message_preview
    if full:
        messages = db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.created_at, Message.id)).all()
        from .privacy import redact_text
        preview = redact_text(messages[-1].content)[:120] if messages else ''
        message_count = len(messages)
        last_message_role = messages[-1].role if messages else None
    else:
        preview, message_count, last_message_role = last_message_preview(db, c.id)
        messages = None
    result = {'id':c.id, 'visitor_name':c.visitor_name, 'source_page':c.source_page, 'status':c.status,
              'analysis':c.analysis, 'assigned_to_id':c.assigned_to_id, 'is_demo':c.is_demo, 'created_at':c.created_at.isoformat(), 'updated_at':c.updated_at.isoformat(),
              'last_message':preview, 'last_message_role':last_message_role, 'message_count':message_count}
    result['unread_count']=unread(db,c,user)
    result['waiting_since']=c.waiting_since.isoformat() if c.waiting_since else None
    result['waiting_seconds']=waiting_seconds(c)
    cfg=reception_config(db,c.tenant_id)
    result['timed_out']=is_timed_out(c,cfg['wait_timeout_seconds'])
    result['handoff_note']=getattr(c,'handoff_note','') or ''
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
            'provider':'DeepSeek', 'retrieval':retrieval_status()}

class ConfigPatch(BaseModel):
    company_name: str | None = Field(None,min_length=1,max_length=80)
    bot_name: str | None = Field(None,min_length=1,max_length=80)
    description: str | None = Field(None,max_length=500)
    welcome: str | None = Field(None,min_length=1,max_length=1000)
    privacy_notice: str | None = Field(None,min_length=1,max_length=1000)
    service_hours: str | None = Field(None,max_length=100)
    suggested_questions: list[str] | None = Field(None,max_length=6)
    allowed_embed_origins: list[str] | None = Field(None,max_length=30)
    reception_assign_mode: Literal['manual','least_busy'] | None = None
    reception_max_active: int | None = Field(None,ge=1,le=50)
    reception_wait_timeout_seconds: int | None = Field(None,ge=30,le=86400)
    canned_replies: list[dict] | None = Field(None,max_length=40)

@router.patch('/admin/settings')
def update_settings(data: ConfigPatch, user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    values = data.model_dump(exclude_none=True)
    if any(len(q) > 150 for q in values.get('suggested_questions',[])):
        raise HTTPException(422,'推荐问题不能超过 150 字')
    if 'allowed_embed_origins' in values:
        values['allowed_embed_origins'] = validate_embed_origin_list(values['allowed_embed_origins'])
    if 'canned_replies' in values:
        from .models import uid as new_uid
        cleaned=[]
        for item in values['canned_replies'] or []:
            title=str(item.get('title') or '').strip()[:40]
            content=str(item.get('content') or '').strip()[:1000]
            if not title or not content:raise HTTPException(422,'常用回复需要标题和正文')
            cleaned.append({'id':str(item.get('id') or new_uid())[:36],'title':title,'content':content})
        values['canned_replies']=cleaned
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
    record(db,user,'移入隔离数据',sid)
    db.commit()
    return {'is_demo':True}

@router.post('/admin/conversations/{sid}/takeover')
def takeover(sid: str, user: Admin = Depends(require('conversations.handle')), db: Session = Depends(get_db)):
    from .reception import lock_conversation, active_human_count, reception_config
    from sqlalchemy import update as sa_update, or_
    conv = lock_conversation(db,sid,user)
    if conv.status=='closed':
        raise HTTPException(409,'咨询已结束，无法接管')
    if conv.assigned_to_id==user.id and conv.status=='human':
        return {'status':'human','assigned_to_id':user.id}
    cfg=reception_config(db,user.tenant_id)
    claiming_new=not conv.assigned_to_id or conv.assigned_to_id!=user.id
    if claiming_new and active_human_count(db,user.id)>=cfg['max_active'] and user.role!='admin':
        raise HTTPException(409,f'当前接待数已达上限 {cfg["max_active"]}，请先结束或转交会话')
    if conv.assigned_to_id and conv.assigned_to_id!=user.id and user.role!='admin':
        raise HTTPException(409,'此咨询正由其他员工接待，请由管理员调整接待人')
    # Conditional update keeps concurrent claims mutually exclusive even if row locks are weak.
    if user.role=='admin' and conv.assigned_to_id and conv.assigned_to_id!=user.id:
        conv.status='human';conv.assigned_to_id=user.id;conv.waiting_since=None;conv.updated_at=now()
    else:
        result=db.execute(sa_update(Conversation).where(
            Conversation.id==sid,
            or_(Conversation.assigned_to_id.is_(None),Conversation.assigned_to_id==user.id)
        ).values(status='human',assigned_to_id=user.id,waiting_since=None,updated_at=now()))
        if result.rowcount==0:
            raise HTTPException(409,'此咨询正由其他员工接待，请刷新后重试')
        db.refresh(conv)
    record(db,user,'接管咨询',sid)
    db.commit()
    from .reception_events import publish
    publish(user.tenant_id,'conversation.taken',conversation_id=sid,assigned_to_id=user.id)
    return {'status':'human','assigned_to_id':user.id}

class TextInput(BaseModel):
    content: str = Field(min_length=1,max_length=4000)
    client_id: str | None = Field(None,min_length=8,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')

@router.post('/admin/conversations/{sid}/reply')
def reply(sid: str, data: TextInput, user: Admin = Depends(require('conversations.handle')), db: Session = Depends(get_db)):
    from .reception import lock_conversation
    conv = lock_conversation(db,sid,user)
    if conv.status != 'human': raise HTTPException(409,'请先接管此咨询')
    if conv.assigned_to_id and conv.assigned_to_id!=user.id and user.role!='admin':
        raise HTTPException(409,'此咨询由其他员工接待，请先申请接管')
    if data.client_id:
        existing=db.scalar(select(Message).where(Message.conversation_id==sid,Message.client_id==data.client_id))
        if existing:return message_data(existing)
    msg = Message(conversation_id=sid,role='agent',content=data.content,mode='human',client_id=data.client_id)
    db.add(msg)
    conv.updated_at = now()
    db.commit()
    from .reception_events import publish
    publish(user.tenant_id,'message.agent',conversation_id=sid,message_id=msg.id)
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
    if data.status!='human':
        conv.handoff_note = ''
    record(db,user,'调整接待状态',sid)
    db.commit()
    from .reception_events import publish
    event = 'conversation.closed' if data.status == 'closed' else 'conversation.returned'
    publish(user.tenant_id,event,conversation_id=sid,status=data.status)
    return {'status':conv.status}

@router.get('/admin/leads')
def leads(filters:BusinessFilters=Depends(),paging:Paging=Depends(),user:Admin=Depends(require('leads.read')),db:Session=Depends(get_db)):
    return paginate(db,lead_query(db,user,filters).order_by(Lead.created_at.desc(),Lead.id.desc()),paging,lead_data)

@router.get('/admin/leads-export')
def export_leads(filters:BusinessFilters=Depends(),user:Admin=Depends(require('leads.export')),db:Session=Depends(get_db)):
    entries=[lead_data(l) for l in db.scalars(lead_query(db,user,filters).order_by(Lead.created_at.desc(),Lead.id.desc()))]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['姓名','联系方式','核验状态','联系许可','意向等级','跟进阶段','负责人'])
    def safe(v):
        s = str(v)
        return "'" + s if s.startswith(('=','+','-','@','\t','\r')) else s
    for row in entries:
        writer.writerow([safe(row['name']),safe(row['contact']),'已核验' if row['contact_verified'] else '待核验','可就本次咨询联系' if row['contact_allowed'] else '已停止联系',row['level'],row['stage'],safe(row['owner'])])
    record(db,user,'导出线索',str(len(entries)))
    db.commit()
    return StreamingResponse(iter(['\ufeff'+buffer.getvalue()]),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="leads.csv"'})

@router.get('/admin/leads/{lead_id}')
def lead_detail(lead_id: str, user: Admin = Depends(require('leads.read')), db: Session = Depends(get_db)):
    lead = owned(db,Lead,lead_id,user)
    from .models import Trial
    from .reception import trial_data
    trials=[trial_data(db,t) for t in db.scalars(select(Trial).where(Trial.conversation_id==lead.conversation_id).order_by(Trial.created_at.desc()))]
    analysis=(db.get(Conversation,lead.conversation_id).analysis or {}) if lead.conversation_id else {}
    score_meta={
        'score_role':analysis.get('score_role') or 'followup_assist',
        'classifier':analysis.get('classifier') or 'rules',
        'confidence':analysis.get('confidence'),
        'uncertainty':analysis.get('uncertainty'),
        'rule_version':analysis.get('rule_version'),
    }
    return {**lead_data(lead),'followups':[{'id':f.id,'author':f.author,'content':f.content,'created_at':f.created_at.isoformat()} for f in db.scalars(select(FollowUp).where(FollowUp.lead_id == lead.id).order_by(FollowUp.created_at.desc()))],'trials':trials,'score_meta':score_meta}

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
    from .analytics import (
        METRIC_DEFINITIONS, build_overview_counts, trend_series, lead_level_counts, exam_lead_counts,
        knowledge_gap_rows, message_usage, first_agent_reply_latency, document_counts,
    )
    from .privacy import redact_text
    from .students import todo_query, todo_data as followup_todo_data, student_query
    from .querying import count
    from .knowledge_maintenance import sync_expiry_todos, list_knowledge_todos, todo_data
    counts = build_overview_counts(db, user, filters)
    doc_stats = document_counts(db, user.tenant_id)
    tasks = todo_query(db, user, filters)
    sync_expiry_todos(db, user.tenant_id)
    knowledge_tasks = list_knowledge_todos(db, user, status='open')
    recent_cs = db.scalars(
        conversation_query(db, user, filters).order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(5)
    ).all()
    recent_ls = db.scalars(
        lead_query(db, user, filters).order_by(Lead.created_at.desc(), Lead.id.desc()).limit(5)
    ).all()
    gaps = knowledge_gap_rows(db, user, filters)
    reply = first_agent_reply_latency(db, user, filters)
    return {
        'include_demo': filters.include_demo,
        'filters': filters.model_dump(mode='json'),
        'metric_definitions': METRIC_DEFINITIONS,
        'counts': counts,
        'student_count': count(db, student_query(db, user, filters)),
        'todo_count': count(db, tasks),
        'todos': [followup_todo_data(db, row) for row in db.execute(tasks.limit(5))],
        'knowledge_todo_count': count(db, knowledge_tasks),
        'knowledge_todos': [todo_data(db, t) for t in db.scalars(knowledge_tasks.limit(5))],
        'expired_documents': doc_stats['expired_documents'],
        'trend': trend_series(db, user, filters),
        'intent': lead_level_counts(db, user, filters),
        'exams': exam_lead_counts(db, user, filters),
        'recent_conversations': [conversation_data(db, c, user=user) for c in recent_cs],
        'recent_leads': [lead_data(l) for l in recent_ls],
        'knowledge_gaps': [{'question': redact_text(m.content)[:160], 'created_at': m.created_at.isoformat()} for m in gaps],
        'usage': message_usage(db, user, filters),
        'reply_latency': reply,
        'retrieval': retrieval_status(),
        'model_configured': bool(settings().deepseek_api_key),
    }

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
