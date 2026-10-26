"""Human reception and trial applications. Conversation scope remains the authority."""
from datetime import datetime, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, and_
from sqlalchemy.orm import Session
from .db import get_db
from .models import Admin, Conversation, ConversationRead, Message, Presence, Lead, Trial, Course, CourseBatch, now
from .permissions import require, scoped_object, scope_conversations, has_permission
from .document_service import aware
from .security import visitor, limit
from .querying import Paging, paginate

router=APIRouter()

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

def presence_data(db,staff):
    p=db.get(Presence,staff.id)
    status=p.status if p and aware(p.heartbeat_at)>now()-timedelta(seconds=90) else 'offline'
    return {'id':staff.id,'name':staff.name,'role':staff.role,'status':status}

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
    query=select(Admin).where(Admin.tenant_id==user.tenant_id,Admin.is_active.is_(True),Admin.role.in_(['admin','support','advisor'])).order_by(Admin.name,Admin.id)
    return [presence_data(db,a) for a in db.scalars(query)]

@router.get('/admin/reception-summary')
def summary(user:Admin=Depends(require('conversations.read')),db:Session=Depends(get_db)):
    convs=db.scalars(scope_conversations(select(Conversation).where(Conversation.status!='closed'),user)).all()
    return {'waiting':sum(c.status=='waiting' for c in convs),'unread':sum(unread(db,c,user) for c in convs)}

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

@router.post('/admin/conversations/{sid}/transfer')
def transfer(sid:str,data:TransferInput,user:Admin=Depends(require('conversations.handle')),db:Session=Depends(get_db)):
    conv=lock_conversation(db,sid,user)
    if conv.status!='human' or (conv.assigned_to_id!=user.id and user.role!='admin'):raise HTTPException(409,'仅当前接待人或管理员可转交人工会话')
    target=db.get(Admin,data.assigned_to_id)
    if not target or target.tenant_id!=user.tenant_id or not has_permission(target,'conversations.handle'):raise HTTPException(422,'请选择启用的客服或顾问')
    lead=db.scalar(select(Lead).where(Lead.conversation_id==sid,Lead.tenant_id==user.tenant_id))
    if target.role=='advisor' and lead and lead.owner_id!=target.id:
        raise HTTPException(422,'顾问只能接待本人负责的线索；请先在线索或学员档案分配负责人')
    from .admin import record
    conv.assigned_to_id=target.id;conv.updated_at=now()
    record(db,user,'转交人工会话',sid+':'+target.id);db.commit()
    return {'status':conv.status,'assigned_to_id':target.id}

def trial_data(db,t,public=False):
    batch=db.get(CourseBatch,t.batch_id);course=db.get(Course,batch.course_id) if batch else None
    advisor=db.get(Admin,t.advisor_id) if t.advisor_id else None
    result={k:(getattr(t,k).isoformat() if isinstance(getattr(t,k),datetime) else getattr(t,k)) for k in ('id','conversation_id','batch_id','requested_at','scheduled_at','advisor_id','status','version','created_at')}
    result.update(course_name=course.name if course else '历史课程',batch_name=batch.name if batch else '历史班次',advisor_name=advisor.name if advisor else '待分配')
    if not public:result['note']=t.note
    return result

class TrialInput(BaseModel):
    batch_id:str=Field(max_length=36)
    requested_at:datetime
    client_id:str=Field(min_length=8,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')

@router.get('/widget/trial-batches')
def public_batches(db:Session=Depends(get_db)):
    return [{'id':b.id,'course_name':c.name,'name':b.name,'mode':b.mode,'location':b.location,'schedule':b.schedule,'is_demo':c.is_demo} for b,c in db.execute(select(CourseBatch,Course).join(Course,CourseBatch.course_id==Course.id).where(Course.tenant_id=='zhixing-demo',Course.status=='active',CourseBatch.sale_status=='open'))]

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
    b=db.scalar(select(CourseBatch).where(CourseBatch.id==data.batch_id,CourseBatch.tenant_id==conv.tenant_id).with_for_update())
    c=db.get(Course,b.course_id) if b else None
    if not c or c.status!='active' or b.sale_status!='open':raise HTTPException(422,'该班次当前不接受试听申请')
    t=Trial(tenant_id=conv.tenant_id,conversation_id=sid,batch_id=b.id,requested_at=data.requested_at,client_id=data.client_id)
    db.add(t);db.commit();return trial_data(db,t,True)

@router.get('/admin/trials')
def trials(paging:Paging=Depends(),status:str='',include_demo:bool=True,user:Admin=Depends(require('conversations.read')),db:Session=Depends(get_db)):
    scoped=scope_conversations(select(Conversation.id),user)
    if not include_demo:scoped=scoped.where(Conversation.is_demo.is_(False))
    query=select(Trial).where(Trial.tenant_id==user.tenant_id,Trial.conversation_id.in_(scoped))
    if status:query=query.where(Trial.status==status)
    return paginate(db,query.order_by(Trial.created_at.desc(),Trial.id),paging,lambda t:trial_data(db,t))

class TrialPatch(BaseModel):
    version:int=Field(ge=1)
    status:Literal['pending','confirmed','attended','cancelled']
    advisor_id:str|None=Field(None,max_length=36)
    scheduled_at:datetime|None=None
    note:str=Field('',max_length=1000)

@router.patch('/admin/trials/{tid}')
def patch_trial(tid:str,data:TrialPatch,user:Admin=Depends(require('conversations.handle')),db:Session=Depends(get_db)):
    from .students import crm_guard
    crm_guard(db,user,'conversations.handle')
    t=db.scalar(select(Trial).where(Trial.id==tid,Trial.tenant_id==user.tenant_id).with_for_update().execution_options(populate_existing=True))
    if not t:raise HTTPException(404,'申请不存在')
    scoped_object(db,Conversation,t.conversation_id,user)
    if t.version!=data.version:raise HTTPException(409,'申请已变化，请刷新')
    allowed={'pending':{'confirmed','cancelled'},'confirmed':{'confirmed','attended','cancelled'},'attended':set(),'cancelled':set()}
    if data.status not in allowed[t.status]:raise HTTPException(409,'不允许此状态变更')
    if data.status=='confirmed':
        a=db.get(Admin,data.advisor_id or t.advisor_id)
        scheduled=data.scheduled_at or t.scheduled_at
        lead=db.scalar(select(Lead).where(Lead.conversation_id==t.conversation_id))
        if not lead or not lead.contact_allowed:raise HTTPException(409,'该访客已停止联系，请先取得新的联系许可')
        if not a or a.tenant_id!=user.tenant_id or not has_permission(a,'conversations.handle'):raise HTTPException(422,'请选择有效顾问或客服')
        if a.role=='advisor' and not db.scalar(scope_conversations(select(Conversation.id).where(Conversation.id==t.conversation_id),a)):raise HTTPException(422,'请先将线索分配给该顾问')
        if not scheduled or aware(scheduled)<=now():raise HTTPException(422,'请填写未来的确认时间')
        b=db.get(CourseBatch,t.batch_id);c=db.get(Course,b.course_id)
        if c.status!='active' or b.sale_status!='open':raise HTTPException(409,'班次已停止接收申请')
        t.advisor_id=a.id;t.scheduled_at=scheduled
    t.status=data.status;t.note=data.note;t.version+=1
    from .admin import record
    record(db,user,'更新试听申请',t.id+':'+t.status);db.commit();return trial_data(db,t)
