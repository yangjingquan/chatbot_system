"""Human reception and trial applications. Conversation scope remains the authority."""
import asyncio
from datetime import datetime, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, and_, update
from sqlalchemy.orm import Session
from .db import get_db
from .models import Admin, Conversation, ConversationRead, Message, Presence, Lead, Trial, Course, CourseBatch, Company, Student, FollowUp, now, uid
from .permissions import require, scoped_object, scope_conversations, has_permission
from .document_service import aware
from .security import visitor, limit
from .querying import Paging, paginate

router=APIRouter()

DEFAULT_WAIT_TIMEOUT=300
DEFAULT_MAX_ACTIVE=5
DEFAULT_ASSIGN_MODE='least_busy'

def reception_config(db, tenant_id):
    company=db.get(Company,tenant_id)
    cfg=(company.config if company else {}) or {}
    try:timeout=int(cfg.get('reception_wait_timeout_seconds') or DEFAULT_WAIT_TIMEOUT)
    except (TypeError,ValueError):timeout=DEFAULT_WAIT_TIMEOUT
    try:max_active=int(cfg.get('reception_max_active') or DEFAULT_MAX_ACTIVE)
    except (TypeError,ValueError):max_active=DEFAULT_MAX_ACTIVE
    mode=cfg.get('reception_assign_mode') or DEFAULT_ASSIGN_MODE
    if mode not in ('manual','least_busy'):mode=DEFAULT_ASSIGN_MODE
    replies=cfg.get('canned_replies') or []
    if not isinstance(replies,list):replies=[]
    clean=[]
    for item in replies[:40]:
        if not isinstance(item,dict):continue
        title=str(item.get('title') or '').strip()[:40]
        content=str(item.get('content') or '').strip()[:1000]
        if not title or not content:continue
        rid=str(item.get('id') or uid())[:36]
        clean.append({'id':rid,'title':title,'content':content})
    return {'wait_timeout_seconds':max(30,min(timeout,86400)),'max_active':max(1,min(max_active,50)),
            'assign_mode':mode,'canned_replies':clean}

def waiting_seconds(conv):
    if conv.status!='waiting' or not conv.waiting_since:return None
    return max(0,int((now()-aware(conv.waiting_since)).total_seconds()))

def is_timed_out(conv,timeout_s):
    secs=waiting_seconds(conv)
    return secs is not None and secs>=timeout_s

def active_human_count(db,admin_id):
    return db.scalar(select(func.count()).select_from(Conversation).where(
        Conversation.assigned_to_id==admin_id,Conversation.status=='human')) or 0

def lock_conversation(db,sid,user):
    from .students import crm_guard
    crm_guard(db,user,'conversations.handle')
    row=db.scalar(scope_conversations(select(Conversation).where(Conversation.id==sid),user).with_for_update().execution_options(populate_existing=True))
    if not row:raise HTTPException(404,'会话不存在或已转交')
    return row

def unread(db,conv,user):
    if not user:return 0
    read=db.get(ConversationRead,(conv.id,user.id))
    query=select(func.count()).select_from(Message).where(Message.conversation_id==conv.id,Message.role=='user')
    if read:query=query.where(or_(Message.created_at>read.read_at,and_(Message.created_at==read.read_at,Message.id>read.message_id)))
    return db.scalar(query)

def presence_data(db,staff,cfg=None):
    p=db.get(Presence,staff.id)
    status=p.status if p and aware(p.heartbeat_at)>now()-timedelta(seconds=90) else 'offline'
    cfg=cfg or reception_config(db,staff.tenant_id)
    active=active_human_count(db,staff.id)
    return {'id':staff.id,'name':staff.name,'role':staff.role,'status':status,
            'active_count':active,'max_active':cfg['max_active'],'at_capacity':active>=cfg['max_active']}

def online_receivers(db,tenant_id,cfg=None):
    cfg=cfg or reception_config(db,tenant_id)
    query=select(Admin).where(Admin.tenant_id==tenant_id,Admin.is_active.is_(True),Admin.role.in_(['admin','support','advisor']))
    staff=[]
    for a in db.scalars(query):
        row=presence_data(db,a,cfg)
        if row['status']=='online' and has_permission(a,'conversations.handle'):
            staff.append((a,row))
    return staff

def pick_least_busy(db,tenant_id,cfg=None):
    candidates=[(row['active_count'],a.id,a) for a,row in online_receivers(db,tenant_id,cfg) if not row['at_capacity'] and a.role!='advisor']
    if not candidates:return None
    candidates.sort(key=lambda x:(x[0],x[1]))
    return candidates[0][2]

class PresenceInput(BaseModel):
    status:Literal['online','away','offline']

@router.put('/admin/presence')
def heartbeat(data:PresenceInput,user:Admin=Depends(require('conversations.handle')),db:Session=Depends(get_db)):
    p=db.get(Presence,user.id)
    if not p:p=Presence(admin_id=user.id);db.add(p)
    p.status=data.status;p.heartbeat_at=now();db.commit()
    return presence_data(db,user)

@router.get('/admin/reception-staff')
def staff(user:Admin=Depends(require('conversations.read')),db:Session=Depends(get_db)):
    cfg=reception_config(db,user.tenant_id)
    query=select(Admin).where(Admin.tenant_id==user.tenant_id,Admin.is_active.is_(True),Admin.role.in_(['admin','support','advisor'])).order_by(Admin.name,Admin.id)
    return [presence_data(db,a,cfg) for a in db.scalars(query)]

@router.get('/admin/reception-summary')
def summary(user:Admin=Depends(require('conversations.read')),db:Session=Depends(get_db)):
    from .analytics import unread_total, reception_queue_stats
    cfg=reception_config(db,user.tenant_id)
    queue=reception_queue_stats(db,user,cfg['wait_timeout_seconds'])
    return {
        'waiting':queue['waiting'],'unread':unread_total(db,user),
        'timed_out':queue['timed_out'],'my_active':active_human_count(db,user.id),
        'wait_timeout_seconds':cfg['wait_timeout_seconds'],'assign_mode':cfg['assign_mode'],
        'max_active':cfg['max_active'],'oldest_wait_seconds':queue['oldest_wait_seconds'],
        'timed_out_ids':queue['timed_out_ids'],
        'pulse':now().isoformat(),
    }

@router.get('/admin/canned-replies')
def canned_replies(user:Admin=Depends(require('conversations.handle')),db:Session=Depends(get_db)):
    return reception_config(db,user.tenant_id)['canned_replies']

class ClaimNextInput(BaseModel):
    prefer_timed_out:bool=True

@router.post('/admin/reception/claim-next')
def claim_next(data:ClaimNextInput|None=None,user:Admin=Depends(require('conversations.handle')),db:Session=Depends(get_db)):
    """Claim the oldest waiting conversation under the current assignment rule."""
    data=data or ClaimNextInput()
    cfg=reception_config(db,user.tenant_id)
    if active_human_count(db,user.id)>=cfg['max_active'] and user.role!='admin':
        raise HTTPException(409,f'当前接待数已达上限 {cfg["max_active"]}，请先结束或转交会话')
    from .students import crm_guard
    crm_guard(db,user,'conversations.handle')
    query=scope_conversations(select(Conversation).where(Conversation.status=='waiting',Conversation.assigned_to_id.is_(None)),user)
    waiting=db.scalars(query.order_by(Conversation.waiting_since.asc().nulls_last(),Conversation.created_at.asc(),Conversation.id).with_for_update().execution_options(populate_existing=True)).all()
    if data.prefer_timed_out:
        timed=[c for c in waiting if is_timed_out(c,cfg['wait_timeout_seconds'])]
        target=timed[0] if timed else (waiting[0] if waiting else None)
    else:
        target=waiting[0] if waiting else None
    if not target:raise HTTPException(404,'当前没有可接管的排队会话')
    if cfg['assign_mode']=='least_busy' and user.role!='admin':
        best=pick_least_busy(db,user.tenant_id,cfg)
        if best and best.id!=user.id and active_human_count(db,best.id)<active_human_count(db,user.id):
            raise HTTPException(409,'按最少负载规则，请由更空闲的在线客服优先接管，或由管理员强制分配')
    result=db.execute(update(Conversation).where(
        Conversation.id==target.id,Conversation.status=='waiting',
        or_(Conversation.assigned_to_id.is_(None),Conversation.assigned_to_id==user.id)
    ).values(status='human',assigned_to_id=user.id,waiting_since=None,updated_at=now()))
    if result.rowcount==0:
        raise HTTPException(409,'该会话已被其他客服接管，请刷新后重试')
    from .admin import record
    record(db,user,'接管咨询',target.id)
    db.commit()
    from .reception_events import publish
    publish(user.tenant_id,'conversation.taken',conversation_id=target.id,assigned_to_id=user.id)
    return {'status':'human','assigned_to_id':user.id,'conversation_id':target.id}

class ReadInput(BaseModel):
    message_id:str=Field(max_length=36)

@router.post('/admin/conversations/{sid}/read')
def mark_read(sid:str,data:ReadInput,user:Admin=Depends(require('conversations.handle')),db:Session=Depends(get_db)):
    conv=lock_conversation(db,sid,user)
    msg=db.get(Message,data.message_id)
    if not msg or msg.conversation_id!=sid:raise HTTPException(404,'消息不存在')
    read=db.get(ConversationRead,(sid,user.id))
    if not read:read=ConversationRead(conversation_id=sid,admin_id=user.id,read_at=msg.created_at,message_id=msg.id);db.add(read)
    elif (aware(msg.created_at),msg.id)>(aware(read.read_at),read.message_id):read.read_at=msg.created_at;read.message_id=msg.id
    db.commit();return {'unread_count':unread(db,conv,user)}

class TransferInput(BaseModel):
    assigned_to_id:str=Field(max_length=36)
    note:str=Field('',max_length=1000)

@router.post('/admin/conversations/{sid}/transfer')
def transfer(sid:str,data:TransferInput,user:Admin=Depends(require('conversations.handle')),db:Session=Depends(get_db)):
    conv=lock_conversation(db,sid,user)
    if conv.status!='human' or (conv.assigned_to_id!=user.id and user.role!='admin'):raise HTTPException(409,'仅当前接待人或管理员可转交人工会话')
    target=db.get(Admin,data.assigned_to_id)
    if not target or target.tenant_id!=user.tenant_id or not has_permission(target,'conversations.handle'):raise HTTPException(422,'请选择启用的客服或顾问')
    lead=db.scalar(select(Lead).where(Lead.conversation_id==sid,Lead.tenant_id==user.tenant_id))
    if target.role=='advisor' and lead and lead.owner_id!=target.id:
        raise HTTPException(422,'顾问只能接待本人负责的线索；请先在线索或学员档案分配负责人')
    cfg=reception_config(db,user.tenant_id)
    if target.role!='admin' and target.id!=user.id and active_human_count(db,target.id)>=cfg['max_active']:
        raise HTTPException(409,f'对方接待数已达上限 {cfg["max_active"]}，请选择其他接待人或先转交已有会话')
    from .admin import record
    note=(data.note or '').strip()
    if note:conv.handoff_note=note[:1000]
    conv.assigned_to_id=target.id;conv.updated_at=now()
    record(db,user,'转交人工会话',sid+':'+target.id+((':'+note[:120]) if note else ''))
    db.commit()
    from .reception_events import publish
    publish(user.tenant_id,'conversation.transferred',conversation_id=sid,assigned_to_id=target.id,from_id=user.id)
    return {'status':conv.status,'assigned_to_id':target.id,'handoff_note':conv.handoff_note or ''}

def trial_data(db,t,public=False):
    batch=db.get(CourseBatch,t.batch_id);course=db.get(Course,batch.course_id) if batch else None
    advisor=db.get(Admin,t.advisor_id) if t.advisor_id else None
    result={k:(getattr(t,k).isoformat() if isinstance(getattr(t,k),datetime) else getattr(t,k)) for k in ('id','conversation_id','batch_id','requested_at','scheduled_at','advisor_id','status','version','created_at')}
    result.update(course_name=course.name if course else '历史课程',batch_name=batch.name if batch else '历史班次',advisor_name=advisor.name if advisor else '待分配')
    if not public:
        result['note']=t.note
        result['reason']=getattr(t,'reason','') or ''
        lead=db.scalar(select(Lead).where(Lead.conversation_id==t.conversation_id))
        if lead:
            student=db.get(Student,lead.student_id) if lead.student_id else None
            result.update(lead_id=lead.id,lead_name=lead.name,lead_stage=lead.stage,lead_owner=lead.owner,lead_owner_id=lead.owner_id,
                          contact_allowed=lead.contact_allowed,next_followup_at=lead.next_followup_at.isoformat() if lead.next_followup_at else None,
                          student_id=lead.student_id,student_name=student.name if student else None)
        else:
            result.update(lead_id=None,lead_name=None,lead_stage=None,lead_owner=None,lead_owner_id=None,contact_allowed=None,next_followup_at=None,student_id=None,student_name=None)
        result['manual_actions']=trial_manual_actions(t,lead if lead else None)
        result['auto_todo_rule']=TRIAL_AUTO_TODO.get(t.status,'')
    return result

# Which status changes auto-write a follow-up note and set next_followup_at.
TRIAL_AUTO_TODO={
    'pending':'申请后自动生成「确认试听」待办（约 24 小时内）',
    'confirmed':'确认后自动把待办时间设为试听确认时间',
    'attended':'参加后自动生成「试听后转化」待办（约 1 天后）；线索阶段需顾问人工确认',
    'no_show':'未参加须填原因，并自动生成「未参加回访」待办（约 4 小时后）',
    'cancelled':'取消须填原因，并自动生成「取消后回访」待办（约 1 天后）；是否标失效需人工确认',
}

def trial_manual_actions(t,lead):
    actions=[]
    if lead is None:actions.append('该会话尚无线索，需访客留资后才能跟进')
    else:
        if not lead.owner_id:actions.append('线索尚未分配负责人，确认试听不会自动改负责人')
        elif t.advisor_id and lead.owner_id!=t.advisor_id:actions.append('试听顾问与线索负责人不一致，是否改派需人工确认')
        if not lead.student_id:actions.append('学员档案需人工核实身份后关联，不会因试听自动创建')
        if t.status=='attended':actions.append('参加后线索阶段（商机推进/已成交等）需顾问人工改阶段')
        if t.status in ('cancelled','no_show'):actions.append('取消/未参加后是否将线索标为已失效需顾问人工确认')
        if not lead.contact_allowed:actions.append('联系许可已撤回，请先取得新许可再跟进')
    return actions

def schedule_trial_todo(db,lead,when,content,author='系统'):
    """Write a follow-up note and set next_followup_at. Does not change stage/owner/student."""
    if lead is None or not lead.contact_allowed:return False
    lead.next_followup_at=when
    db.add(FollowUp(lead_id=lead.id,author=author,content=content[:4000]))
    return True

def locked_batch(db,bid,tenant):
    initial=db.scalar(select(CourseBatch).where(CourseBatch.id==bid,CourseBatch.tenant_id==tenant))
    if not initial:return None,None
    # Course then batch is the shared ordering; reread latest values after waiting on either.
    course=db.scalar(select(Course).where(Course.id==initial.course_id,Course.tenant_id==tenant).with_for_update().execution_options(populate_existing=True))
    batch=db.scalar(select(CourseBatch).where(CourseBatch.id==bid,CourseBatch.tenant_id==tenant).with_for_update().execution_options(populate_existing=True))
    return batch,course

class TrialInput(BaseModel):
    batch_id:str=Field(max_length=36)
    requested_at:datetime
    client_id:str=Field(min_length=8,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')

@router.get('/widget/trial-batches')
def public_batches(db:Session=Depends(get_db)):
    return [{'id':b.id,'course_name':c.name,'name':b.name,'mode':b.mode,'location':b.location,'schedule':b.schedule,'is_demo':c.is_demo} for b,c in db.execute(select(CourseBatch,Course).join(Course,CourseBatch.course_id==Course.id).where(Course.tenant_id=='zhixing-demo',CourseBatch.tenant_id==Course.tenant_id,Course.status=='active',CourseBatch.sale_status=='open',or_(CourseBatch.ends_at.is_(None),CourseBatch.ends_at>=now())))]

@router.get('/widget/sessions/{sid}/trials')
def visitor_trials(sid:str,request:Request,db:Session=Depends(get_db)):
    visitor(request,sid,db)
    return [trial_data(db,t,True) for t in db.scalars(select(Trial).where(Trial.conversation_id==sid).order_by(Trial.created_at.desc()))]

@router.post('/widget/sessions/{sid}/trials')
def create_trial(sid:str,data:TrialInput,request:Request,db:Session=Depends(get_db)):
    conv=visitor(request,sid,db)
    db.scalar(select(Conversation).where(Conversation.id==sid).with_for_update())
    existing=db.scalar(select(Trial).where(Trial.conversation_id==sid,Trial.client_id==data.client_id))
    if existing:return trial_data(db,existing,True)
    limit('trial:'+sid,6,300)
    lead=db.scalar(select(Lead).where(Lead.conversation_id==sid,Lead.contact_allowed.is_(True)))
    if not lead:raise HTTPException(422,'请先自愿留下本次试听的联系方式并同意联系')
    if not now()<aware(data.requested_at)<now()+timedelta(days=180):raise HTTPException(422,'请选择未来 180 天内的意向时间')
    from .catalog import batch_available
    b,c=locked_batch(db,data.batch_id,conv.tenant_id)
    if not batch_available(b,c):raise HTTPException(422,'该班次当前不接受试听申请')
    if b.ends_at and aware(data.requested_at)>aware(b.ends_at):raise HTTPException(422,'意向试听时间不能晚于班次结束时间')
    t=Trial(tenant_id=conv.tenant_id,conversation_id=sid,batch_id=b.id,requested_at=data.requested_at,client_id=data.client_id)
    db.add(t);db.flush()
    schedule_trial_todo(db,lead,now()+timedelta(hours=24),
                        f'【试听·自动待办】访客申请试听「{c.name if c else "课程"} / {b.name}」，意向时间 {aware(data.requested_at).isoformat()}。请确认时间与顾问。',
                        author='系统')
    if lead.stage in ('新线索','待分配'):lead.stage='跟进中'
    db.commit();return trial_data(db,t,True)

@router.get('/admin/trials')
def trials(paging:Paging=Depends(),status:str='',include_demo:bool=True,user:Admin=Depends(require('conversations.read')),db:Session=Depends(get_db)):
    scoped=scope_conversations(select(Conversation.id),user)
    if not include_demo:scoped=scoped.where(Conversation.is_demo.is_(False))
    query=select(Trial).where(Trial.tenant_id==user.tenant_id,Trial.conversation_id.in_(scoped))
    if status:query=query.where(Trial.status==status)
    return paginate(db,query.order_by(Trial.created_at.desc(),Trial.id),paging,lambda t:trial_data(db,t))

@router.get('/admin/trials/{tid}')
def trial_detail(tid:str,user:Admin=Depends(require('conversations.read')),db:Session=Depends(get_db)):
    t=db.scalar(select(Trial).where(Trial.id==tid,Trial.tenant_id==user.tenant_id))
    if not t:raise HTTPException(404,'申请不存在')
    scoped_object(db,Conversation,t.conversation_id,user)
    result=trial_data(db,t)
    lead=db.scalar(select(Lead).where(Lead.conversation_id==t.conversation_id))
    if lead:
        from .admin import lead_data
        from .permissions import scope_leads
        scoped_lead=db.scalar(scope_leads(select(Lead).where(Lead.id==lead.id),user))
        if scoped_lead:
            result['lead']=lead_data(scoped_lead)
            result['followups']=[{'id':f.id,'author':f.author,'content':f.content,'created_at':f.created_at.isoformat()} for f in db.scalars(select(FollowUp).where(FollowUp.lead_id==lead.id).order_by(FollowUp.created_at.desc()).limit(20))]
            if lead.student_id:
                from .students import student_data
                from .permissions import scope_students
                student=db.scalar(scope_students(select(Student).where(Student.id==lead.student_id),user))
                result['student']=student_data(db,student) if student else {'id':lead.student_id,'name':'不在授权范围','restricted':True}
        else:
            result['lead']={'id':lead.id,'restricted':True,'name':'不在授权范围'}
            result['followups']=[];result['student']=None
    else:
        result['lead']=None;result['followups']=[];result['student']=None
    return result

class TrialPatch(BaseModel):
    version:int=Field(ge=1)
    status:Literal['pending','confirmed','attended','no_show','cancelled']
    advisor_id:str|None=Field(None,max_length=36)
    scheduled_at:datetime|None=None
    note:str=Field('',max_length=1000)
    reason:str=Field('',max_length=1000)

@router.patch('/admin/trials/{tid}')
def patch_trial(tid:str,data:TrialPatch,user:Admin=Depends(require('conversations.handle')),db:Session=Depends(get_db)):
    from .students import crm_guard
    crm_guard(db,user,'conversations.handle')
    t=db.scalar(select(Trial).where(Trial.id==tid,Trial.tenant_id==user.tenant_id).with_for_update().execution_options(populate_existing=True))
    if not t:raise HTTPException(404,'申请不存在')
    scoped_object(db,Conversation,t.conversation_id,user)
    if t.version!=data.version:raise HTTPException(409,'申请已变化，请刷新')
    allowed={'pending':{'confirmed','cancelled'},'confirmed':{'confirmed','attended','no_show','cancelled'},'attended':set(),'no_show':set(),'cancelled':set()}
    if data.status not in allowed.get(t.status,set()):raise HTTPException(409,'不允许此状态变更')
    lead=db.scalar(select(Lead).where(Lead.conversation_id==t.conversation_id).with_for_update().execution_options(populate_existing=True))
    reason=(data.reason or '').strip()
    if data.status in ('cancelled','no_show') and not reason:raise HTTPException(422,'取消或未参加须填写原因')
    if data.status=='confirmed':
        a=db.get(Admin,data.advisor_id or t.advisor_id)
        scheduled=data.scheduled_at or t.scheduled_at
        if not lead or not lead.contact_allowed:raise HTTPException(409,'该访客已停止联系，请先取得新的联系许可')
        if not a or a.tenant_id!=user.tenant_id or not has_permission(a,'conversations.handle'):raise HTTPException(422,'请选择有效顾问或客服')
        if a.role=='advisor' and not db.scalar(scope_conversations(select(Conversation.id).where(Conversation.id==t.conversation_id),a)):raise HTTPException(422,'请先将线索分配给该顾问')
        if not scheduled or aware(scheduled)<=now():raise HTTPException(422,'请填写未来的确认时间')
        from .catalog import batch_available
        b,c=locked_batch(db,t.batch_id,user.tenant_id)
        if not batch_available(b,c):raise HTTPException(409,'班次已结束或停止接收申请')
        if b.ends_at and aware(scheduled)>aware(b.ends_at):raise HTTPException(422,'确认时间不能晚于班次结束时间')
        t.advisor_id=a.id;t.scheduled_at=scheduled
        course_name=c.name if c else '课程'
        batch_name=b.name
        schedule_trial_todo(db,lead,aware(scheduled),
                            f'【试听·自动待办】已确认试听「{course_name} / {batch_name}」，确认时间 {aware(scheduled).isoformat()}，顾问 {a.name}。请按时接待。',
                            author=user.name)
    elif data.status=='attended':
        schedule_trial_todo(db,lead,now()+timedelta(days=1),
                            f'【试听·自动待办】访客已参加试听。请顾问人工确认后续阶段（商机推进/成交等），并完成转化跟进。',
                            author=user.name)
    elif data.status=='no_show':
        t.reason=reason
        schedule_trial_todo(db,lead,now()+timedelta(hours=4),
                            f'【试听·自动待办】访客未参加试听。原因：{reason}。请尽快回访。',
                            author=user.name)
    elif data.status=='cancelled':
        t.reason=reason
        schedule_trial_todo(db,lead,now()+timedelta(days=1),
                            f'【试听·自动待办】试听已取消。原因：{reason}。请确认是否继续跟进或人工标为已失效。',
                            author=user.name)
    t.status=data.status;t.note=data.note;t.version+=1
    if data.status not in ('cancelled','no_show') and reason:t.reason=reason
    from .admin import record
    record(db,user,'更新试听申请',t.id+':'+t.status+((':'+reason[:80]) if reason else ''))
    db.commit()
    return trial_detail(t.id,user,db)


def _admin_from_websocket(websocket: WebSocket, db: Session):
    """Authenticate reception WS with the same admin_session cookie as HTTP."""
    from .security import digest
    from .models import AdminSession
    from datetime import datetime, timezone
    token = websocket.cookies.get('admin_session', '')
    login = db.get(AdminSession, digest(token)) if token else None
    if not login:
        return None
    created = login.created_at.replace(tzinfo=timezone.utc) if login.created_at.tzinfo is None else login.created_at
    if (datetime.now(timezone.utc) - created).total_seconds() > 43200:
        db.delete(login); db.commit()
        return None
    user = db.get(Admin, login.admin_id)
    if not user or not user.is_active or not has_permission(user, 'conversations.read'):
        return None
    return user

@router.websocket('/admin/reception/ws')
async def reception_ws(websocket: WebSocket):
    """Subscribe to tenant-scoped reception events. Cookie session required.

    Events: queue.waiting, conversation.taken, conversation.transferred,
    conversation.closed, conversation.returned, message.user, message.agent, ping.
    In-memory fan-out only (single backend process).
    """
    from .db import SessionLocal
    from .reception_events import subscribe, unsubscribe, encode
    await websocket.accept()
    with SessionLocal() as db:
        user = _admin_from_websocket(websocket, db)
        if not user:
            await websocket.close(code=4401)
            return
        tenant_id = user.tenant_id
        admin_id = user.id
    queue = subscribe(tenant_id)
    stop = asyncio.Event()

    async def push_events():
        while not stop.is_set():
            try:
                message = await asyncio.wait_for(queue.get(), timeout=25)
            except asyncio.TimeoutError:
                await websocket.send_text(encode({'type': 'ping'}))
                continue
            await websocket.send_text(encode(message))

    async def read_client():
        while not stop.is_set():
            raw = await websocket.receive_text()
            if isinstance(raw, str) and 'ping' in raw.lower():
                await websocket.send_text(encode({'type': 'pong'}))

    try:
        await websocket.send_text(encode({'type': 'connected', 'tenant_id': tenant_id, 'admin_id': admin_id}))
        push_task = asyncio.create_task(push_events())
        read_task = asyncio.create_task(read_client())
        done, pending = await asyncio.wait({push_task, read_task}, return_when=asyncio.FIRST_COMPLETED)
        stop.set()
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception() if not task.cancelled() else None
            if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                pass
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        unsubscribe(tenant_id, queue)
