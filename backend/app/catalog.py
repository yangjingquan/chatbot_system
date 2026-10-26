"""Structured course facts are the authoritative source of commercial information."""
import hashlib
import re
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import get_db
from .models import Admin, Course, CourseBatch, Company, Audit, TENANT, now
from .permissions import require, scoped_object
from .querying import Paging, paginate
from .documents import Exam
from .document_service import aware
from .commercial import has_amount, course_question

router=APIRouter(prefix='/admin')

class CourseInput(BaseModel):
    name:str=Field(min_length=1,max_length=160)
    exam_type:Exam
    hours:int=Field(ge=1,le=10000)
    class_type:str=Field('基础班',min_length=1,max_length=80)
    description:str=Field('',max_length=6000)
    status:Literal['active','paused']='active'

class CoursePatch(BaseModel):
    expected_version:int=Field(ge=1)
    name:str|None=Field(None,min_length=1,max_length=160)
    exam_type:Exam|None=None
    hours:int|None=Field(None,ge=1,le=10000)
    class_type:str|None=Field(None,min_length=1,max_length=80)
    description:str|None=Field(None,max_length=6000)
    status:Literal['active','paused']|None=None

class BatchInput(BaseModel):
    name:str=Field(min_length=1,max_length=160)
    mode:Literal['online','offline','hybrid']='online'
    location:str=Field('',max_length=240)
    schedule:str=Field('待人工确认',max_length=3000)
    starts_at:datetime|None=None
    ends_at:datetime|None=None
    price_cents:int|None=Field(None,ge=0,le=100000000)
    price_from:datetime|None=None
    price_until:datetime|None=None
    sale_status:Literal['draft','open','paused','sold_out','ended']='draft'

class BatchPatch(BaseModel):
    expected_version:int=Field(ge=1)
    name:str|None=Field(None,min_length=1,max_length=160)
    mode:Literal['online','offline','hybrid']|None=None
    location:str|None=Field(None,max_length=240)
    schedule:str|None=Field(None,max_length=3000)
    starts_at:datetime|None=None
    ends_at:datetime|None=None
    price_cents:int|None=Field(None,ge=0,le=100000000)
    price_from:datetime|None=None
    price_until:datetime|None=None
    sale_status:Literal['draft','open','paused','sold_out','ended']|None=None

def row_data(row):
    return {column.name:(getattr(row,column.name).isoformat() if isinstance(getattr(row,column.name),datetime) else getattr(row,column.name)) for column in row.__table__.columns}

def course_data(db,c):
    return {**row_data(c),'batches':[row_data(b) for b in db.scalars(select(CourseBatch).where(CourseBatch.course_id==c.id).order_by(CourseBatch.created_at,CourseBatch.id))]}

def check_batch(b):
    for start,end in ((b.starts_at,b.ends_at),(b.price_from,b.price_until)):
        if start and end and aware(start)>aware(end):raise HTTPException(422,'开始时间不能晚于结束时间')
    if b.mode in ('offline','hybrid') and not b.location.strip():raise HTTPException(422,'线下或混合授课须填写上课地点')
    for text in (b.name,b.location,b.schedule):check_commercial_text(text)

def batch_available(b,c):
    return bool(c and b and c.tenant_id==b.tenant_id and c.status=='active' and b.sale_status=='open' and (not b.ends_at or aware(b.ends_at)>=aware(now())))

def check_commercial_text(value):
    if has_amount(value):raise HTTPException(422,'价格和课时请填写专用字段，不要写入名称、说明或开班安排')

def record(db,user,action,target):db.add(Audit(tenant_id=user.tenant_id,actor=user.name,action=action,target=target))

def locked(db,model,key,user,version):
    row=db.scalar(select(model).where(model.id==key,model.tenant_id==user.tenant_id).with_for_update().execution_options(populate_existing=True))
    if not row:raise HTTPException(404,'课程或班次不存在')
    if row.version!=version:raise HTTPException(409,'资料已变化，请重新加载后编辑')
    return row

@router.get('/courses')
def courses(q:str='',exam_type:str='',status:str='',paging:Paging=Depends(),user:Admin=Depends(require('courses.read')),db:Session=Depends(get_db)):
    query=select(Course).where(Course.tenant_id==user.tenant_id)
    if q:query=query.where(Course.name.contains(q[:200],autoescape=True))
    if exam_type:query=query.where(Course.exam_type==exam_type)
    if status:query=query.where(Course.status==status)
    return paginate(db,query.order_by(Course.created_at.desc(),Course.id.desc()),paging,lambda c:course_data(db,c))

@router.get('/courses/{course_id}')
def detail(course_id:str,user:Admin=Depends(require('courses.read')),db:Session=Depends(get_db)):
    return course_data(db,scoped_object(db,Course,course_id,user))

@router.post('/courses')
def create(data:CourseInput,user:Admin=Depends(require('courses.write')),db:Session=Depends(get_db)):
    for value in (data.name,data.class_type,data.description):check_commercial_text(value)
    c=Course(tenant_id=user.tenant_id,**data.model_dump());db.add(c);db.flush()
    record(db,user,'新增课程',c.id);db.commit();return course_data(db,c)

@router.patch('/courses/{course_id}')
def patch(course_id:str,data:CoursePatch,user:Admin=Depends(require('courses.write')),db:Session=Depends(get_db)):
    c=locked(db,Course,course_id,user,data.expected_version)
    for key,value in data.model_dump(exclude_unset=True,exclude={'expected_version'}).items():
        if value is None:raise HTTPException(422,'课程必填字段不可为空')
        setattr(c,key,value)
    for value in (c.name,c.class_type,c.description):check_commercial_text(value)
    c.version+=1;record(db,user,'更新课程',c.id);db.commit();return course_data(db,c)

@router.post('/courses/{course_id}/batches')
def create_batch(course_id:str,data:BatchInput,user:Admin=Depends(require('courses.write')),db:Session=Depends(get_db)):
    scoped_object(db,Course,course_id,user)
    b=CourseBatch(course_id=course_id,tenant_id=user.tenant_id,**data.model_dump());check_batch(b);db.add(b);db.flush()
    record(db,user,'新增班次',b.id);db.commit();return row_data(b)

@router.patch('/batches/{batch_id}')
def patch_batch(batch_id:str,data:BatchPatch,user:Admin=Depends(require('courses.write')),db:Session=Depends(get_db)):
    b=locked(db,CourseBatch,batch_id,user,data.expected_version)
    for key,value in data.model_dump(exclude_unset=True,exclude={'expected_version'}).items():
        if value is None and key in ('name','mode','location','schedule','sale_status'):raise HTTPException(422,'班次必填字段不可为空')
        setattr(b,key,value)
    check_batch(b);b.version+=1;record(db,user,'更新班次或价格',b.id);db.commit();return row_data(b)

def public_source(db,c):
    def safe(value,fallback):return fallback if has_amount(value) else value
    title=safe(c.name,c.exam_type+'课程（名称待校对）')
    pieces=[f'{title}；考试：{c.exam_type}；班型：{safe(c.class_type,"班型待校对")}。']
    versions=[str(c.version)]
    if c.status!='active':pieces.append('该课程已停售，不能报价或接受报名。')
    else:
        pieces.append(f'{c.hours}课时，每课时45分钟。{safe(c.description,"课程说明待管理员校对。")}')
        batches=db.scalars(select(CourseBatch).where(CourseBatch.course_id==c.id,CourseBatch.tenant_id==c.tenant_id).order_by(CourseBatch.created_at,CourseBatch.id)).all()
        visible=[b for b in batches if b.sale_status!='draft']
        if not visible:pieces.append('当前暂无已确认可报名班次，价格和开班时间需人工核实。')
        for b in visible:
            versions.append(b.id+':'+str(b.version))
            open_now=batch_available(b,c)
            label='可咨询报名' if open_now else {'paused':'停售','sold_out':'已满班','ended':'已结束'}.get(b.sale_status,'已结束')
            pieces.append(f'班次：{safe(b.name,"班次名称待校对")}；状态：{label}。')
            if not open_now:continue
            pieces.append(f'授课方式：'+{'online':'线上','offline':'线下','hybrid':'线上及线下'}[b.mode]+f'；地点：{safe(b.location,"地点待校对") or "线上"}；安排：{safe(b.schedule,"安排待校对")}。')
            if b.starts_at:pieces.append(f'开班时间：{aware(b.starts_at).isoformat()}。')
            price_valid=(not b.price_from or aware(b.price_from)<=aware(now())) and (not b.price_until or aware(b.price_until)>=aware(now()))
            if b.price_cents is not None and price_valid:pieces.append(f'当前有效价格：{b.price_cents/100:.2f}元。')
            else:pieces.append('当前没有有效报价，须由人工确认，不能沿用历史价格。')
    if c.is_demo:pieces.append('以上为虚构演示课程、价格及安排，不构成真实报价。')
    text='\n'.join(pieces)
    version=hashlib.sha256(('|'.join(versions)+text).encode()).hexdigest()[:20]
    return {'kind':'catalog','course_id':c.id,'document_id':'catalog:'+c.id,'chunk_id':'catalog:'+c.id,'title':title,'category':'课程业务数据','text':text,'version':version,'exam_type':c.exam_type,'exam_year':None,'region':'全国','is_demo':c.is_demo,'source_url':None}

def catalog_sources(db,query,context,tenant_id=TENANT):
    rows=db.scalars(select(Course).where(Course.tenant_id==tenant_id).order_by(Course.name,Course.id)).all()
    exact=[c for c in rows if c.name in query]
    if exact:return [public_source(db,c) for c in exact]
    if not course_question(query):return []
    exam=context.get('exam_type')
    return [public_source(db,c) for c in rows if c.exam_type==exam] if exam else []

def source_valid(db,source,context=None):
    c=db.get(Course,source.get('course_id'))
    return bool(c and c.tenant_id==TENANT and (not context or not context.get('exam_type') or context['exam_type']==c.exam_type) and public_source(db,c)['version']==source.get('version'))

def seed_catalog(db):
    company=db.get(Company,TENANT)
    if company.config.get('_catalog_seeded'):return
    records=[('公安联考','公安联考基础班',1680,48),('国考','公务员国考基础班',1280,40),('省考','公务员省考基础班',1380,44),('选调生','选调生考试基础班',1480,40),('事业单位','事业单位考试基础班',980,32),('社会招警','社会招警基础班',1580,48),('辅警','辅警招聘基础班',680,24),('社区工作者','社区工作者考试基础班',780,28)]
    for exam,name,price,hours in records:
        c=Course(name=name,exam_type=exam,hours=hours,class_type='基础班',is_demo=True,description='线上教学和电子练习资料；考试报名费、交通食宿不包含。实际交易须人工确认。')
        db.add(c);db.flush();db.add(CourseBatch(course_id=c.id,name='演示基础班',price_cents=price*100,sale_status='open',schedule='线上直播与回放；实际开班日期由顾问确认'))
    company.config={**company.config,'_catalog_seeded':True}
