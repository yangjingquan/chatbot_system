"""Transactional knowledge drafts, immutable published revisions and eligibility."""
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from fastapi import HTTPException
from sqlalchemy import select, func
from .models import Admin, Document, DocumentRevision, Audit, now
from .knowledge import ingest_document
from .permissions import check_permission
from .scoring import exam_names

FIELDS=('title','content','category','visibility','source_url','is_demo','knowledge_type','exam_type','region','exam_year','valid_from','valid_until','responsible_id','policy_verified')
REGIONS=('北京','天津','河北','山西','内蒙古','辽宁','吉林','黑龙江','上海','江苏','浙江','安徽','福建','江西','山东','河南','湖北','湖南','广东','广西','海南','重庆','四川','贵州','云南','西藏','陕西','甘肃','青海','宁夏','新疆','香港','澳门','台湾')

def aware(value):
    if value is None:return None
    if isinstance(value,str):value=datetime.fromisoformat(value.replace('Z','+00:00'))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

def snapshot(doc):
    return {k:(getattr(doc,k).isoformat() if isinstance(getattr(doc,k),datetime) else getattr(doc,k)) for k in FIELDS}

def pending(db,doc):
    return db.scalar(select(DocumentRevision).where(DocumentRevision.document_id==doc.id,DocumentRevision.status.in_(['draft','submitted'])).order_by(DocumentRevision.number.desc()))

def ensure_history(db):
    for doc in db.scalars(select(Document)):
        if not db.scalar(select(DocumentRevision.id).where(DocumentRevision.document_id==doc.id)):
            db.add(DocumentRevision(document_id=doc.id,tenant_id=doc.tenant_id,number=max(1,doc.version),
                 status='published' if doc.status in ('published','archived') else 'draft',data=snapshot(doc),author_name='v0.1 历史基线'))
    db.flush()

def document_active(doc):
    moment=aware(now())
    return doc.status=='published' and (not doc.valid_from or aware(doc.valid_from)<=moment) and (not doc.valid_until or aware(doc.valid_until)>=moment)

def effective_status(doc):
    if doc.status!='published':return doc.status
    if doc.valid_from and aware(doc.valid_from)>aware(now()):return 'scheduled'
    if doc.valid_until and aware(doc.valid_until)<aware(now()):return 'expired'
    return 'published'

def infer_context(query,history=None):
    contexts,pending_fields={},{}
    focus=[]
    for text in [*(history or []),query]:
        fields={}
        years=set(re.findall(r'(?<!\d)(20\d{2})(?!\d)',text))
        if years:fields['exam_year']=int(next(iter(years))) if len(years)==1 else None
        elif re.search(r'今年|明年|本年度',text):fields['exam_year']=None
        regions=[r for r in REGIONS if r in text]
        if re.search(r'全国|不限地区|各省',text):regions.append('全国')
        if regions:fields['region']=regions[0] if len(regions)==1 else None
        exams=exam_names(text)
        switched=re.split(r'改考|转考|换成|改成|改为|转为',text)
        if len(switched)>1 and exam_names(switched[-1]):exams=exam_names(switched[-1])
        if exams:
            for exam in exams:
                # Fields from another exam must never be combined into a new policy scope.
                if exam not in contexts:contexts[exam]=pending_fields.copy() if not focus else {}
            focus=exams
            pending_fields={}
        if focus:
            for exam in focus:contexts[exam].update(fields)
        else:pending_fields.update(fields)
    if len(focus)==1:return {'exam_type':focus[0],**contexts[focus[0]]}
    if focus:return {'exam_type':None,'exam_year':None,'region':None}
    return pending_fields

def matches_context(doc,context):
    if not document_active(doc):return False
    if doc.exam_year is not None and doc.exam_year!=context.get('exam_year'):return False
    if doc.exam_type not in ('通用','') and doc.exam_type!=context.get('exam_type'):return False
    if doc.region not in ('全国','通用',''):
        normalized=next((r for r in REGIONS if r in doc.region),doc.region)
        if normalized!=context.get('region'):return False
    return True

def validate_data(db,data,user,publishing=False):
    start,end=aware(data.get('valid_from')),aware(data.get('valid_until'))
    if start and end and start>end:raise HTTPException(422,'生效时间不能晚于失效时间')
    url=data.get('source_url')
    if url and (urlsplit(url).scheme not in ('https','http') or not urlsplit(url).netloc):
        raise HTTPException(422,'来源必须是有效的 HTTP(S) 网址')
    owner=data.get('responsible_id')
    if owner:
        member=db.get(Admin,owner)
        if not member or member.tenant_id!=user.tenant_id or not member.is_active or member.role not in ('admin','knowledge'):
            raise HTTPException(422,'知识负责人必须是启用的管理员或知识运营')
    if publishing and data.get('knowledge_type')=='policy':
        if not data.get('exam_year') or data.get('exam_type') in (None,'','通用') or not data.get('region') or not url or not data.get('policy_verified'):
            raise HTTPException(422,'具体考试政策须填写考试、适用年度、地区、来源网址并确认已人工核对来源')

def audit(db,user,action,doc):
    db.add(Audit(tenant_id=user.tenant_id,actor=user.name,action=action,target=doc.id))

def locked(db,doc_id,user,expected):
    doc=db.scalar(select(Document).where(Document.id==doc_id,Document.tenant_id==user.tenant_id).with_for_update().execution_options(populate_existing=True))
    if not doc:raise HTTPException(404,'知识不存在')
    if expected is None or expected!=doc.edit_sequence:raise HTTPException(409,'资料已变化或缺少修订序号，请重新加载后操作')
    return doc

def next_number(db,doc):
    return (db.scalar(select(func.max(DocumentRevision.number)).where(DocumentRevision.document_id==doc.id)) or 0)+1

def new_draft(db,doc,data,user):
    revision=DocumentRevision(document_id=doc.id,tenant_id=user.tenant_id,number=next_number(db,doc),data=data,
                              status='draft',author_id=user.id,author_name=user.name)
    db.add(revision);db.flush()
    return revision

def apply_published(db,doc,revision,user,note=''):
    validate_data(db,revision.data,user,True)
    for previous in db.scalars(select(DocumentRevision).where(DocumentRevision.document_id==doc.id,DocumentRevision.status=='published')):
        previous.status='superseded'
    for key,value in revision.data.items():
        if key in FIELDS:setattr(doc,key,aware(value) if key in ('valid_from','valid_until') else value)
    doc.version=revision.number;doc.status='published';doc.edit_sequence+=1;doc.updated_at=now()
    revision.status='published';revision.reviewer_name=user.name;revision.review_note=note;revision.updated_at=now()
    ingest_document(db,doc)
    from .jobs import enqueue_index
    enqueue_index(db,doc)

def create_document(db,data,user,status='draft'):
    if status!='draft':check_permission(user,'knowledge.publish')
    validate_data(db,data,user,status=='published')
    if not data.get('responsible_id'):data={**data,'responsible_id':user.id}
    doc=Document(**{k:aware(v) if k in ('valid_from','valid_until') else v for k,v in data.items() if k in FIELDS},
                 tenant_id=user.tenant_id,status='draft',version=0)
    db.add(doc);db.flush()
    revision=new_draft(db,doc,snapshot(doc),user)
    if status=='published':apply_published(db,doc,revision,user,'创建时由管理员审核发布')
    elif status=='archived':doc.status='archived'
    audit(db,user,'创建知识草稿' if status=='draft' else '创建并发布知识',doc)
    return doc

def save_draft(db,doc_id,changes,user,expected):
    doc=locked(db,doc_id,user,expected)
    revision=pending(db,doc)
    if revision and revision.status=='submitted':raise HTTPException(409,'资料正在审核，须退回草稿后修改')
    data={**(revision.data if revision else snapshot(doc)),**{k:v.isoformat() if isinstance(v,datetime) else v for k,v in changes.items() if k in FIELDS}}
    validate_data(db,data,user)
    if revision:
        revision.data=data;revision.author_id=user.id;revision.author_name=user.name;revision.updated_at=now()
    else:revision=new_draft(db,doc,data,user)
    doc.edit_sequence+=1;doc.updated_at=now()
    audit(db,user,'保存知识草稿',doc)
    return doc

def transition(db,doc_id,user,expected,action,note='',revision_id=None):
    doc=locked(db,doc_id,user,expected)
    revision=pending(db,doc)
    if action in ('publish','reject','archive','restore'):check_permission(user,'knowledge.publish')
    if action=='archive':
        doc.status='archived';doc.edit_sequence+=1
    elif action=='restore':
        if revision:raise HTTPException(409,'已有未发布草稿，请先处理草稿后回滚')
        original=db.get(DocumentRevision,revision_id)
        if not original or original.document_id!=doc.id or original.tenant_id!=user.tenant_id or original.status not in ('published','superseded'):
            raise HTTPException(404,'可回滚的历史版本不存在')
        revision=new_draft(db,doc,original.data.copy(),user)
        apply_published(db,doc,revision,user,f'回滚版本 {original.number}：{note}')
    else:
        if not revision:raise HTTPException(409,'没有待处理的草稿')
        if action=='submit':
            if revision.status!='draft':raise HTTPException(409,'草稿已提交')
            validate_data(db,revision.data,user,True)
            revision.status='submitted';doc.edit_sequence+=1
        elif action=='reject':
            if revision.status!='submitted':raise HTTPException(409,'仅待审核资料可以退回')
            if not note.strip():raise HTTPException(422,'请填写退回原因')
            revision.status='draft';revision.review_note=note;revision.reviewer_name=user.name;doc.edit_sequence+=1
        elif action=='publish':apply_published(db,doc,revision,user,note)
        else:raise HTTPException(404,'不支持的知识操作')
    doc.updated_at=now()
    audit(db,user,{'submit':'提交知识审核','publish':'审核发布知识','reject':'退回知识草稿','archive':'下架知识','restore':'回滚知识版本'}[action],doc)
    return doc

def revision_data(revision,full=False):
    result={'id':revision.id,'number':revision.number,'status':revision.status,'title':revision.data['title'],
            'author_name':revision.author_name,'reviewer_name':revision.reviewer_name,'review_note':revision.review_note,
            'created_at':revision.created_at.isoformat(),'updated_at':revision.updated_at.isoformat()}
    if full:result['data']=revision.data
    return result

def document_data(db,doc,full=True):
    draft=pending(db,doc)
    data=(draft.data if draft else snapshot(doc)).copy()
    if not full:data['content']=data['content'][:100]
    owner=db.get(Admin,data.get('responsible_id')) if data.get('responsible_id') else None
    result={**data,'id':doc.id,'status':doc.status,'effective_status':effective_status(doc),'version':doc.version,'published_visibility':doc.visibility,
            'edit_sequence':doc.edit_sequence,'draft_status':draft.status if draft else None,
            'draft_number':draft.number if draft else None,'review_note':draft.review_note if draft else '',
            'responsible_name':owner.name if owner else '历史资料待指定',
            'updated_at':doc.updated_at.isoformat()}
    if full:result['published_content']=doc.content if doc.version else ''
    return result
