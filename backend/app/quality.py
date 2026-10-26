"""Feedback, human acceptance samples and auditable, explicitly estimated model cost."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from typing import Literal
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import get_db
from .models import Admin, Company, Conversation, Message, AnswerFeedback, KnowledgeGap, ModelCall, Evaluation, now
from .permissions import require
from .security import visitor, limit
from .querying import Paging, paginate
from .document_service import aware

router=APIRouter()

ERRORS={'auth':'模型鉴权失败，请管理员检查本地 API Key。','balance':'模型账户余额不足，请联系管理员。','rate_limit':'模型请求过于频繁，请稍后重试。','timeout':'模型响应超时，请稍后重试或转人工。','unavailable':'模型服务暂时繁忙，请稍后重试。','parameters':'模型请求参数不兼容，请联系管理员。','network':'模型网络连接失败，请稍后重试。','protocol':'模型回答未完整结束，请重试或转人工。','interrupted':'模型回答已中断，请重试或转人工。'}
def classify_error(exc):
    if isinstance(exc,httpx.HTTPStatusError):kind={400:'parameters',401:'auth',402:'balance',422:'parameters',429:'rate_limit'}.get(exc.response.status_code,'unavailable')
    elif isinstance(exc,httpx.TimeoutException):kind='timeout'
    elif isinstance(exc,httpx.RequestError):kind='network'
    else:kind='protocol'
    return kind,ERRORS[kind]

def add_gap(db,message,reason):
    gap=db.scalar(select(KnowledgeGap).where(KnowledgeGap.message_id==message.id))
    if gap:return gap
    conv=db.get(Conversation,message.conversation_id);question=db.get(Message,message.reply_to) if message.reply_to else None
    gap=KnowledgeGap(tenant_id=conv.tenant_id,message_id=message.id,question=question.content if question else '待人工核实问题',reason=reason)
    db.add(gap);db.flush();return gap

class FeedbackInput(BaseModel):
    rating:Literal['up','down']
    comment:str=Field('',max_length=1000)

@router.put('/widget/sessions/{sid}/feedback/{mid}')
def feedback(sid:str,mid:str,data:FeedbackInput,request:Request,db:Session=Depends(get_db)):
    visitor(request,sid,db)
    db.scalar(select(Conversation).where(Conversation.id==sid).with_for_update())
    limit('feedback:'+sid,30,300)
    msg=db.get(Message,mid)
    if not msg or msg.conversation_id!=sid or msg.role!='assistant' or msg.state!='completed':raise HTTPException(404,'可评价的回答不存在')
    row=db.get(AnswerFeedback,mid)
    if not row:row=AnswerFeedback(message_id=mid);db.add(row)
    row.rating=data.rating;row.comment=data.comment;row.updated_at=now()
    if data.rating=='down':add_gap(db,msg,'negative_feedback')
    db.commit();return {'rating':row.rating}

def gap_data(db,g):
    f=db.get(AnswerFeedback,g.message_id)
    m=db.get(Message,g.message_id)
    return {'id':g.id,'message_id':g.message_id,'question':g.question,'reason':g.reason,'status':g.status,'note':g.note,'document_id':g.document_id,'comment':f.comment if f else '', 'rating':f.rating if f else None,'answer':m.content,'created_at':g.created_at.isoformat()}

@router.get('/admin/quality/gaps')
def gaps(paging:Paging=Depends(),status:str='',user:Admin=Depends(require('settings.write')),db:Session=Depends(get_db)):
    query=select(KnowledgeGap).where(KnowledgeGap.tenant_id==user.tenant_id)
    if status:query=query.where(KnowledgeGap.status==status)
    return paginate(db,query.order_by(KnowledgeGap.created_at.desc(),KnowledgeGap.id),paging,lambda g:gap_data(db,g))

def lock_gap(db,gid,user):
    row=db.scalar(select(KnowledgeGap).where(KnowledgeGap.id==gid,KnowledgeGap.tenant_id==user.tenant_id).with_for_update().execution_options(populate_existing=True))
    if not row:raise HTTPException(404,'缺口不存在')
    return row

class GapPatch(BaseModel):
    status:Literal['open','processing','resolved','dismissed']
    note:str=Field('',max_length=2000)

@router.patch('/admin/quality/gaps/{gid}')
def patch_gap(gid:str,data:GapPatch,user:Admin=Depends(require('settings.write')),db:Session=Depends(get_db)):
    gap=lock_gap(db,gid,user)
    if data.status in ('resolved','dismissed') and not data.note.strip():raise HTTPException(422,'请填写人工处理结果；创建草稿不等于已解决')
    gap.status=data.status;gap.note=data.note
    from .admin import record
    record(db,user,'处理知识缺口',gid+':'+data.status);db.commit();return gap_data(db,gap)

class GapDraft(BaseModel):
    content:str=Field(min_length=10,max_length=200000)

@router.post('/admin/quality/gaps/{gid}/draft')
def draft(gid:str,data:GapDraft,user:Admin=Depends(require('knowledge.write')),db:Session=Depends(get_db)):
    # Questions contain business conversations; knowledge operators do not gain access through this route.
    from .permissions import check_permission
    check_permission(user,'settings.write')
    gap=lock_gap(db,gid,user)
    if gap.document_id:raise HTTPException(409,'已创建知识草稿，请到知识库继续编辑和审核')
    from .document_service import create_document
    doc=create_document(db,{'title':'咨询补充：'+gap.question[:100],'content':data.content,'category':'咨询补充'},user)
    gap.document_id=doc.id;gap.status='processing';db.commit();return {'id':doc.id,'status':doc.status}

class BudgetInput(BaseModel):
    model:str=Field(min_length=1,max_length=100)
    input_miss:float=Field(ge=0,le=10000,allow_inf_nan=False)
    input_hit:float=Field(ge=0,le=10000,allow_inf_nan=False)
    output:float=Field(ge=0,le=10000,allow_inf_nan=False)
    daily_budget:float=Field(gt=0,le=10000000,allow_inf_nan=False)
    monthly_budget:float=Field(gt=0,le=10000000,allow_inf_nan=False)

@router.put('/admin/quality/budget')
def budget(data:BudgetInput,user:Admin=Depends(require('settings.write')),db:Session=Depends(get_db)):
    company=db.scalar(select(Company).where(Company.id==user.tenant_id).with_for_update())
    company.config={**company.config,'_budget':{**data.model_dump(),'updated_at':now().isoformat()}}
    from .admin import record
    record(db,user,'更新模型估算单价与预算');db.commit();return company.config['_budget']

def record_usage(db,message,model,latency_ms,error_type,pricing=None):
    conv=db.get(Conversation,message.conversation_id)
    pricing=pricing if pricing is not None else db.get(Company,conv.tenant_id).config.get('_budget',{})
    usage={k:v for k,v in (message.usage or {}).items() if k in ('prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens') and isinstance(v,int) and v>=0}
    cost=None
    if pricing.get('model')==model and 'prompt_tokens' in usage and 'completion_tokens' in usage:
        hit=min(usage.get('prompt_cache_hit_tokens',0),usage['prompt_tokens']);miss=usage['prompt_tokens']-hit
        cost=(Decimal(str(pricing['input_hit']))*hit+Decimal(str(pricing['input_miss']))*miss+Decimal(str(pricing['output']))*usage['completion_tokens'])/Decimal(1000000)
    row=db.get(ModelCall,message.id)
    if not row:row=ModelCall(message_id=message.id,tenant_id=conv.tenant_id,model=model);db.add(row)
    row.usage=usage;row.pricing=pricing;row.estimated_cny=cost;row.latency_ms=latency_ms;row.error_type=error_type;row.state=message.state

@router.get('/admin/quality/usage')
def usage_summary(user:Admin=Depends(require('settings.write')),db:Session=Depends(get_db)):
    zone=ZoneInfo('Asia/Shanghai');today=now().astimezone(zone).date()
    rows=db.scalars(select(ModelCall).where(ModelCall.tenant_id==user.tenant_id).order_by(ModelCall.created_at.desc())).all()
    day=[r for r in rows if aware(r.created_at).astimezone(zone).date()==today]
    month=[r for r in rows if aware(r.created_at).astimezone(zone).strftime('%Y-%m')==today.strftime('%Y-%m')]
    def total(rs):return float(sum((r.estimated_cny or Decimal(0) for r in rs),Decimal(0)))
    cfg=db.get(Company,user.tenant_id).config.get('_budget',{})
    return {'pricing':cfg,'calls':len(rows),'tokens':sum(r.usage.get('total_tokens',0) for r in rows),'unpriced_calls':sum(r.estimated_cny is None for r in rows),'unknown_usage_calls':sum('total_tokens' not in r.usage for r in rows),'failed_calls':sum(r.state!='completed' for r in rows),'daily_estimated_cny':total(day),'monthly_estimated_cny':total(month),'daily_alert':bool(cfg and total(day)>=cfg['daily_budget']),'monthly_alert':bool(cfg and total(month)>=cfg['monthly_budget']),'billing_note':'人民币估算，仅含已取得用量并配置对应模型单价的调用；以供应商账单为准。预算为站内告警，不自动停止服务。','recent':[{'message_id':r.message_id,'model':r.model,'usage':r.usage,'state':r.state,'error_type':r.error_type,'latency_ms':r.latency_ms,'estimated_cny':float(r.estimated_cny) if r.estimated_cny is not None else None,'created_at':r.created_at.isoformat()} for r in rows[:30]]}

EVAL_CASES=[
 ('课程',f'{exam}有哪些课程？','current_catalog') for exam in ('公安联考','国考','省考','选调生','事业单位','社会招警','辅警','社区工作者')
]+[('课程','国考现在的价格和班次是什么？','current_catalog'),('政策','2027年浙江省考报名条件是什么？','verify_official_year_region'),('政策','明年国考一定能报吗？','clarify_policy_scope'),('未知','火星量子跃迁口诀','no_evidence_handoff'),('未知','有没有保过保录用承诺？','no_guarantees'),('否定','我不考虑国考课程了，请不要联系我','respect_withdrawal'),('否定','我只咨询考试信息，暂时不报名','no_high_intent'),('售后','我已报名，想咨询退费流程','after_sales'),('售后','已经报名的同学怎么申请调班？','after_sales')]

def eval_data(row):
    reviewed=sum(bool(c.get('verdict')) for c in row.cases)
    return {'id':row.id,'created_at':row.created_at.isoformat(),'cases':row.cases,'human_reviewed':reviewed,'answer_accuracy':sum(c.get('verdict')=='pass' for c in row.cases)/len(row.cases) if reviewed==len(row.cases) else None,'note':'自动结果仅展示检索与规则输出；请填写实际回答并人工核对引用、事实和标签。全部标注后才统计此样本集通过率。'}

@router.post('/admin/quality/evaluations')
def evaluate(user:Admin=Depends(require('settings.write')),db:Session=Depends(get_db)):
    from .knowledge import retrieve
    from .scoring import analyze_messages
    from .models import uid
    cases=[]
    for category,q,expected in EVAL_CASES:
        sources=retrieve(db,q,tenant_id=user.tenant_id)
        analysis=analyze_messages([{'id':'evaluation','content':q}],False)
        cases.append({'id':uid(),'category':category,'question':q,'expected':expected,'sources':sources,'analysis':analysis,'answer':'','verdict':None,'note':''})
    row=Evaluation(tenant_id=user.tenant_id,cases=cases);db.add(row);db.commit();return eval_data(row)

@router.get('/admin/quality/evaluations')
def evaluations(user:Admin=Depends(require('settings.write')),db:Session=Depends(get_db)):
    return [{'id':r.id,'created_at':r.created_at.isoformat(),'human_reviewed':sum(bool(c.get('verdict')) for c in r.cases),'total':len(r.cases)} for r in db.scalars(select(Evaluation).where(Evaluation.tenant_id==user.tenant_id).order_by(Evaluation.created_at.desc()).limit(30))]

@router.get('/admin/quality/evaluations/{eid}')
def evaluation(eid:str,user:Admin=Depends(require('settings.write')),db:Session=Depends(get_db)):
    from .permissions import scoped_object
    return eval_data(scoped_object(db,Evaluation,eid,user))

class ReviewInput(BaseModel):
    answer:str=Field(min_length=1,max_length=10000)
    verdict:Literal['pass','fail']
    note:str=Field(min_length=1,max_length=2000)

@router.put('/admin/quality/evaluations/{eid}/cases/{cid}')
def review(eid:str,cid:str,data:ReviewInput,user:Admin=Depends(require('settings.write')),db:Session=Depends(get_db)):
    row=db.scalar(select(Evaluation).where(Evaluation.id==eid,Evaluation.tenant_id==user.tenant_id).with_for_update().execution_options(populate_existing=True))
    if not row or not any(c['id']==cid for c in row.cases):raise HTTPException(404,'评测样本不存在')
    row.cases=[{**c,**data.model_dump(),'reviewer':user.name,'reviewed_at':now().isoformat()} if c['id']==cid else c for c in row.cases]
    from .admin import record
    record(db,user,'人工验收回答样本',eid+':'+cid);db.commit();return eval_data(row)
