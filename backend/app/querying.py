"""One authorization and filter contract for lists, CSV exports and dashboards."""
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, cast
from sqlalchemy.dialects.postgresql import JSONB
from .models import Lead, Conversation, Message
from .permissions import scope_leads, scope_conversations

class BusinessFilters(BaseModel):
    q:str=Field('',max_length=200)
    include_demo:bool=False
    date_from:date|None=None
    date_to:date|None=None
    exam_type:str=Field('',max_length=40)
    owner_id:str=Field('',max_length=36)
    stage:str=Field('',max_length=24)
    level:str=Field('',max_length=24)
    contact_state:Literal['','allowed','withdrawn']=''

class Paging(BaseModel):
    page:int=Field(1,ge=1)
    page_size:int=Field(20,ge=1,le=100)

def boundaries(filters):
    if filters.date_from and filters.date_to and filters.date_from>filters.date_to:
        raise HTTPException(422,'开始日期不能晚于结束日期')
    def stamp(day):return datetime.combine(day,time.min,ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc)
    return stamp(filters.date_from) if filters.date_from else None,stamp(filters.date_to+timedelta(days=1)) if filters.date_to else None

def common(query,model,filters):
    start,end=boundaries(filters)
    if start:query=query.where(model.created_at>=start)
    if end:query=query.where(model.created_at<end)
    if not filters.include_demo:query=query.where(model.is_demo.is_(False))
    return query

def json_has(db,column,value):
    if db.bind.dialect.name=='postgresql':return cast(column,JSONB).contains([value])
    values=func.json_each(column).table_valued('value').alias()
    if isinstance(value,dict):
        return select(values.c.value).where(*(func.json_extract(values.c.value,'$.'+key)==item for key,item in value.items())).exists()
    return select(values.c.value).where(values.c.value==value).exists()

def conversation_exam(db,exam):
    return or_(json_has(db,Conversation.analysis['active_exam_types'],exam),json_has(db,Conversation.analysis['needs'],{'exam_type':exam,'state':'active'}))

def lead_conditions(db,filters):
    conditions=[]
    if filters.owner_id:conditions.append(Lead.owner_id.is_(None) if filters.owner_id=='unassigned' else Lead.owner_id==filters.owner_id)
    if filters.stage:conditions.append(Lead.stage==filters.stage)
    if filters.level:conditions.append(Lead.level==filters.level)
    if filters.contact_state:conditions.append(Lead.contact_allowed.is_(filters.contact_state=='allowed'))
    if filters.exam_type:
        linked=select(Conversation.id).where(Conversation.id==Lead.conversation_id,conversation_exam(db,filters.exam_type)).correlate(Lead).exists()
        conditions.append(or_(json_has(db,Lead.tags,filters.exam_type),linked))
    return conditions

def lead_query(db,user,filters):
    query=common(scope_leads(select(Lead),user),Lead,filters).where(*lead_conditions(db,filters))
    if filters.q:
        from .students import contact_hash
        query=query.where(or_(Lead.name.contains(filters.q,autoescape=True),Lead.owner.contains(filters.q,autoescape=True),Lead.contact_hash==contact_hash(filters.q)))
    return query

def conversation_query(db,user,filters,status=''):
    query=common(scope_conversations(select(Conversation),user),Conversation,filters)
    engaged=or_(select(Message.id).where(Message.conversation_id==Conversation.id,Message.role=='user').exists(),select(Lead.id).where(Lead.conversation_id==Conversation.id).exists())
    query=query.where(engaged)
    conditions=lead_conditions(db,filters.model_copy(update={'exam_type':''}))
    if conditions:query=query.where(select(Lead.id).where(Lead.conversation_id==Conversation.id,*conditions).exists())
    if filters.exam_type:
        tagged_lead=select(Lead.id).where(Lead.conversation_id==Conversation.id,json_has(db,Lead.tags,filters.exam_type)).correlate(Conversation).exists()
        query=query.where(or_(conversation_exam(db,filters.exam_type),tagged_lead))
    if status:query=query.where(Conversation.status==status)
    if filters.q:
        needle='%'+filters.q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        query=query.where(or_(Conversation.visitor_name.like(needle,escape='\\'),Conversation.source_page.like(needle,escape='\\'),select(Message.id).where(Message.conversation_id==Conversation.id,Message.content.like(needle,escape='\\')).exists()))
    return query

def count(db,query):return db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0

def paginate(db,query,paging,serialize):
    total=count(db,query)
    rows=db.scalars(query.offset((paging.page-1)*paging.page_size).limit(paging.page_size)).all()
    return {'items':[serialize(row) for row in rows],'total':total,'page':paging.page,'page_size':paging.page_size,'pages':(total+paging.page_size-1)//paging.page_size}
