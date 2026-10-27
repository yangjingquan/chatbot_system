"""Student identity is explicitly linked by staff, never inferred from contact equality."""
import hashlib
import hmac
import re
from datetime import datetime, timedelta, time
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, or_
from sqlalchemy.orm import Session
from .config import settings
from .db import get_db
from .models import Admin, Company, Student, StudentInterest, Course, Lead, Conversation, FollowUp, Audit, Trial, now
from .permissions import require, scoped_object, scope_students, scope_leads, check_permission
from .querying import BusinessFilters, Paging, common, json_has, lead_query, paginate
from .documents import Exam
from .document_service import aware

router=APIRouter(prefix='/admin')

def contact_hash(value):
    normalized=value.strip().casefold()
    if re.fullmatch(r'[+\d\s()-]+',normalized):normalized=re.sub(r'[^\d]','',normalized)
    return hmac.new(settings().session_secret.encode(),('contact:'+normalized).encode(),hashlib.sha256).hexdigest()

def student_query(db,user,filters):
    query=common(scope_students(select(Student),user),Student,filters).where(Student.status=='active')
    if filters.q:
        matching=select(Lead.student_id).where(Lead.tenant_id==user.tenant_id,Lead.contact_hash==contact_hash(filters.q))
        query=query.where(or_(Student.name.contains(filters.q,autoescape=True),Student.id.in_(matching)))
    if filters.owner_id:query=query.where(Student.owner_id.is_(None) if filters.owner_id=='unassigned' else Student.owner_id==filters.owner_id)
    if filters.exam_type:query=query.where(json_has(db,Student.exam_targets,filters.exam_type))
    return query

def student_data(db,s,full=False,user=None):
    owner=db.get(Admin,s.owner_id) if s.owner_id else None
    result={'id':s.id,'name':s.name,'owner_id':s.owner_id,'owner':owner.name if owner else '未分配','exam_targets':s.exam_targets,'notes':s.notes,
            'version':s.version,'status':s.status,'merged_into_id':s.merged_into_id,'is_demo':s.is_demo,'created_at':s.created_at.isoformat(),'updated_at':s.updated_at.isoformat()}
    if full:
        from .admin import lead_data, message_data
        leads=db.scalars(scope_leads(select(Lead).where(Lead.student_id==s.id),user)).all()
        result['leads']=[lead_data(l) for l in leads]
        result['contacts']=[{'contact':l.contact,'verified':l.contact_verified,'contact_allowed':l.contact_allowed,'source_lead_id':l.id} for l in leads]
        result['interests']=[{'id':i.id,'exam_type':i.exam_type,'course_id':i.course_id,'course_name':(db.get(Course,i.course_id).name if i.course_id and db.get(Course,i.course_id) else ''),'target_year':i.target_year,'status':i.status,'notes':i.notes} for i in db.scalars(select(StudentInterest).where(StudentInterest.student_id==s.id))]
        result['followups']=[{'id':f.id,'lead_id':f.lead_id,'content':f.content,'author':f.author,'created_at':f.created_at.isoformat()} for f in db.scalars(select(FollowUp).where(FollowUp.lead_id.in_([l.id for l in leads])).order_by(FollowUp.created_at.desc()))] if leads else []
        from .reception import trial_data
        conv_ids=[l.conversation_id for l in leads]
        result['trials']=[trial_data(db,t) for t in db.scalars(select(Trial).where(Trial.conversation_id.in_(conv_ids)).order_by(Trial.created_at.desc()))] if conv_ids else []
    return result

def audit(db,user,action,key,details=''):db.add(Audit(tenant_id=user.tenant_id,actor=user.name,action=action,target=key,details=details))

def crm_guard(db,user,permission='students.write'):
    # One tenant-level relationship lock keeps link/merge/assignment order consistent.
    db.scalar(select(Company).where(Company.id==user.tenant_id).with_for_update())
    db.refresh(user)
    check_permission(user,permission)

def lock_conversations(db,ids):
    if ids:db.scalars(select(Conversation).where(Conversation.id.in_(ids)).order_by(Conversation.id).with_for_update().execution_options(populate_existing=True)).all()

def lock_student(db,key,user,version=None):
    crm_guard(db,user)
    scoped_object(db,Student,key,user)
    s=db.scalar(scope_students(select(Student).where(Student.id==key),user).with_for_update().execution_options(populate_existing=True))
    if not s:raise HTTPException(404,'档案已转交或不在授权范围')
    if s.status!='active':raise HTTPException(409,'该档案已合并，请打开目标档案')
    if version is not None and s.version!=version:raise HTTPException(409,'学员资料已变化，请重新加载')
    return s

def assign_lead(db,l,owner):
    l.owner_id=owner.id if owner else None;l.owner=owner.name if owner else '未分配'
    c=db.get(Conversation,l.conversation_id)
    if c.assigned_to_id and c.assigned_to_id!=l.owner_id:
        c.assigned_to_id=None
        if c.status=='human':c.status='waiting'

def assign_student(db,s,owner):
    ids=list(db.scalars(select(Lead.conversation_id).where(Lead.student_id==s.id)))
    lock_conversations(db,ids)
    for l in db.scalars(select(Lead).where(Lead.student_id==s.id).order_by(Lead.id).with_for_update().execution_options(populate_existing=True)):assign_lead(db,l,owner)
    s.owner_id=owner.id if owner else None;s.version+=1
    frontier=[s.id];seen={s.id}
    while frontier:
        ancestors=db.scalars(select(Student).where(Student.tenant_id==s.tenant_id,Student.merged_into_id.in_(frontier)).order_by(Student.id).with_for_update()).all()
        frontier=[]
        for archived in ancestors:
            if archived.id in seen:continue
            seen.add(archived.id);frontier.append(archived.id)
            archived.owner_id=s.owner_id;archived.merged_into_id=s.id;archived.version+=1

def from_lead(db,l):
    from .scoring import EXAMS
    s=Student(tenant_id=l.tenant_id,name=l.name,owner_id=l.owner_id,exam_targets=[t for t in l.tags if t in EXAMS],is_demo=l.is_demo)
    db.add(s);db.flush();l.student_id=s.id
    return s

def backfill_crm(db):
    for lead in db.scalars(select(Lead)):
        if not lead.contact_hash:lead.contact_hash=contact_hash(lead.contact)
        if lead.stage=='已成交' and not lead.student_id:from_lead(db,lead)

class StudentInput(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    exam_targets:list[Exam]=Field(default_factory=list,max_length=8)
    notes:str=Field('',max_length=6000)

class StudentPatch(BaseModel):
    expected_version:int=Field(ge=1)
    name:str|None=Field(None,min_length=1,max_length=100)
    exam_targets:list[Exam]|None=Field(None,max_length=8)
    notes:str|None=Field(None,max_length=6000)
    owner_id:str|None=Field(None,max_length=36)

@router.get('/students')
def students(filters:BusinessFilters=Depends(),paging:Paging=Depends(),user:Admin=Depends(require('students.read')),db:Session=Depends(get_db)):
    return paginate(db,student_query(db,user,filters).order_by(Student.created_at.desc(),Student.id.desc()),paging,lambda s:student_data(db,s))

@router.post('/students')
def create(data:StudentInput,user:Admin=Depends(require('students.write')),db:Session=Depends(get_db)):
    if not data.name.strip():raise HTTPException(422,'请输入学员姓名')
    s=Student(tenant_id=user.tenant_id,owner_id=user.id,**data.model_dump());db.add(s);db.flush()
    audit(db,user,'新建学员档案',s.id);db.commit();return student_data(db,s,True,user)

@router.get('/students/{student_id}')
def detail(student_id:str,user:Admin=Depends(require('students.read')),db:Session=Depends(get_db)):
    return student_data(db,scoped_object(db,Student,student_id,user),True,user)

@router.patch('/students/{student_id}')
def patch(student_id:str,data:StudentPatch,user:Admin=Depends(require('students.write')),db:Session=Depends(get_db)):
    s=lock_student(db,student_id,user,data.expected_version)
    if 'owner_id' in data.model_fields_set:
        check_permission(user,'leads.assign')
        owner=db.get(Admin,data.owner_id) if data.owner_id else None
        if data.owner_id and (not owner or owner.tenant_id!=user.tenant_id or not owner.is_active or owner.role not in ('admin','support','advisor')):raise HTTPException(422,'请选择有效的线索负责人')
        assign_student(db,s,owner)
    for key,value in data.model_dump(exclude_unset=True,exclude={'expected_version','owner_id'}).items():
        if value is None or (key=='name' and not value.strip()):raise HTTPException(422,'学员必填字段不可为空')
        setattr(s,key,value)
    s.version+=1;audit(db,user,'更新学员档案',s.id);db.commit();return student_data(db,s,True,user)

@router.get('/leads/{lead_id}/student-candidates')
def candidates(lead_id:str,user:Admin=Depends(require('students.read')),db:Session=Depends(get_db)):
    lead=scoped_object(db,Lead,lead_id,user)
    ids=select(Lead.student_id).where(Lead.tenant_id==user.tenant_id,Lead.contact_hash==contact_hash(lead.contact),Lead.student_id.is_not(None))
    query=scope_students(select(Student).where(Student.id.in_(ids),Student.status=='active'),user)
    return [{**student_data(db,s),'warning':'联系方式相同仅为疑似重复，未核验不代表同一人，须人工确认。'} for s in db.scalars(query)]

class LinkInput(BaseModel):
    student_id:str|None=None
    identity_confirmed:bool=False

@router.post('/leads/{lead_id}/student')
def link(lead_id:str,data:LinkInput,user:Admin=Depends(require('students.write')),db:Session=Depends(get_db)):
    crm_guard(db,user)
    lead=scoped_object(db,Lead,lead_id,user)
    if data.student_id and not data.identity_confirmed:raise HTTPException(422,'关联已有档案前须人工核实为同一学员')
    target=lock_student(db,data.student_id,user) if data.student_id else None
    lock_conversations(db,[lead.conversation_id])
    lead=db.scalar(scope_leads(select(Lead).where(Lead.id==lead_id).with_for_update().execution_options(populate_existing=True),user))
    if not lead:raise HTTPException(404,'线索不在授权范围')
    if lead.student_id:
        if data.student_id and data.student_id!=lead.student_id:raise HTTPException(409,'已有关联学员，请由管理员核实后解除或合并')
        return student_data(db,scoped_object(db,Student,lead.student_id,user),True,user)
    if target:
        if target.is_demo!=lead.is_demo:raise HTTPException(422,'演示记录与真实业务记录不能关联')
        if target.owner_id!=lead.owner_id:
            check_permission(user,'leads.assign')
            assign_lead(db,lead,db.get(Admin,target.owner_id) if target.owner_id else None)
        lead.student_id=target.id;target.version+=1
        target.exam_targets=list(dict.fromkeys(target.exam_targets+[t for t in lead.tags if t in ('公安联考','国考','省考','选调生','事业单位','社会招警','辅警','社区工作者')]))
    else:target=from_lead(db,lead)
    audit(db,user,'人工关联学员与咨询',lead.id);db.commit();return student_data(db,target,True,user)

@router.delete('/leads/{lead_id}/student')
def unlink(lead_id:str,user:Admin=Depends(require('students.merge')),db:Session=Depends(get_db)):
    crm_guard(db,user,'students.merge')
    lead=scoped_object(db,Lead,lead_id,user)
    if lead.student_id:
        s=lock_student(db,lead.student_id,user);s.version+=1
        lead.student_id=None
    audit(db,user,'解除学员关联',lead.id);db.commit();return {'ok':True}

class InterestInput(BaseModel):
    exam_type:Exam
    target_year:int|None=Field(None,ge=2000,le=2100)
    course_id:str|None=None
    status:Literal['exploring','interested','enrolled','closed']='exploring'
    notes:str=Field('',max_length=3000)

@router.post('/students/{student_id}/interests')
def interest(student_id:str,data:InterestInput,user:Admin=Depends(require('students.write')),db:Session=Depends(get_db)):
    s=lock_student(db,student_id,user)
    if data.course_id:
        course=scoped_object(db,Course,data.course_id,user)
        if course.exam_type!=data.exam_type:raise HTTPException(422,'课程与考试方向不一致')
    db.add(StudentInterest(student_id=s.id,**data.model_dump()));s.version+=1
    s.exam_targets=list(dict.fromkeys([*s.exam_targets,data.exam_type]))
    audit(db,user,'添加学员课程需求',s.id);db.commit();return student_data(db,s,True,user)

@router.patch('/students/{student_id}/interests/{interest_id}')
def edit_interest(student_id:str,interest_id:str,data:InterestInput,user:Admin=Depends(require('students.write')),db:Session=Depends(get_db)):
    s=lock_student(db,student_id,user)
    row=db.get(StudentInterest,interest_id)
    if not row or row.student_id!=s.id:raise HTTPException(404,'需求不存在')
    if data.course_id and scoped_object(db,Course,data.course_id,user).exam_type!=data.exam_type:raise HTTPException(422,'课程与考试方向不一致')
    for key,value in data.model_dump().items():setattr(row,key,value)
    s.version+=1;audit(db,user,'更新学员课程需求',s.id);db.commit();return student_data(db,s,True,user)

class MergeInput(BaseModel):
    target_id:str
    source_version:int=Field(ge=1)
    target_version:int=Field(ge=1)
    identity_confirmed:Literal[True]
    reason:str=Field(min_length=1,max_length=1000)

@router.post('/students/{student_id}/merge')
def merge(student_id:str,data:MergeInput,user:Admin=Depends(require('students.merge')),db:Session=Depends(get_db)):
    crm_guard(db,user,'students.merge')
    if student_id==data.target_id or not data.reason.strip():raise HTTPException(422,'请选择其他目标档案并填写核验原因')
    rows={s.id:s for s in db.scalars(select(Student).where(Student.id.in_([student_id,data.target_id]),Student.tenant_id==user.tenant_id).order_by(Student.id).with_for_update().execution_options(populate_existing=True))}
    if len(rows)!=2:raise HTTPException(404,'学员档案不存在')
    source,target=rows[student_id],rows[data.target_id]
    if source.status!='active' or target.status!='active' or source.version!=data.source_version or target.version!=data.target_version:raise HTTPException(409,'档案已变化，请重新加载后合并')
    if source.is_demo!=target.is_demo:raise HTTPException(422,'演示档案与真实业务档案不能合并')
    lock_conversations(db,list(db.scalars(select(Lead.conversation_id).where(Lead.student_id.in_([source.id,target.id])))))
    for lead in db.scalars(select(Lead).where(Lead.student_id==source.id).order_by(Lead.id).with_for_update()):lead.student_id=target.id
    for row in db.scalars(select(StudentInterest).where(StudentInterest.student_id==source.id)):row.student_id=target.id
    target.exam_targets=list(dict.fromkeys(target.exam_targets+source.exam_targets))
    target.notes='\n'.join(x for x in [target.notes,('合并自 '+source.name+'：'+source.notes) if source.notes else ''] if x)
    source.status='merged';source.merged_into_id=target.id;source.owner_id=target.owner_id;source.version+=1
    db.flush();assign_student(db,target,db.get(Admin,target.owner_id) if target.owner_id else None)
    audit(db,user,'合并学员档案',source.id+' → '+target.id,data.reason);db.commit();return student_data(db,target,True,user)

def todo_query(db,user,filters,state='all'):
    query=lead_query(db,user,filters).where(Lead.next_followup_at.is_not(None),Lead.contact_allowed.is_(True),Lead.stage.not_in(['已成交','已失效']))
    today=datetime.combine(aware(now()).astimezone(ZoneInfo('Asia/Shanghai')).date(),time.min,ZoneInfo('Asia/Shanghai'))
    if state=='overdue':query=query.where(Lead.next_followup_at<now())
    elif state=='today':query=query.where(Lead.next_followup_at>=today,Lead.next_followup_at<today+timedelta(days=1))
    elif state=='upcoming':query=query.where(Lead.next_followup_at>=now())
    return query.order_by(Lead.next_followup_at,Lead.id)

@router.get('/todos')
def todos(filters:BusinessFilters=Depends(),paging:Paging=Depends(),state:Literal['all','overdue','today','upcoming']='all',user:Admin=Depends(require('leads.read')),db:Session=Depends(get_db)):
    from .admin import lead_data
    return paginate(db,todo_query(db,user,filters,state),paging,lead_data)

class CompleteInput(BaseModel):
    content:str=Field('已完成本次跟进',min_length=1,max_length=4000)

@router.post('/leads/{lead_id}/complete-followup')
def complete(lead_id:str,data:CompleteInput,user:Admin=Depends(require('leads.write')),db:Session=Depends(get_db)):
    from .admin import lead_detail
    lead=scoped_object(db,Lead,lead_id,user);lead.next_followup_at=None
    db.add(FollowUp(lead_id=lead.id,author=user.name,content=data.content));audit(db,user,'完成跟进待办',lead.id);db.commit();return lead_detail(lead.id,user,db)
