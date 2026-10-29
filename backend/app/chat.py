import json
import re
import secrets
import time
from urllib.parse import urlsplit
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .config import settings
from .db import get_db
from .models import Company, Conversation, Document, Lead, Message, TENANT, now
from .security import digest, limit, visitor, client_ip, assert_embed_allowed
from .knowledge import retrieve, retrieval_status, historical_commercial_text
from .scoring import analyze_messages, WITHDRAW_CONTACT, RULE_VERSION
from .llm import deepseek_stream
from .admin import message_data
from .document_service import document_active, infer_context, matches_context

router = APIRouter(prefix='/widget')

def event(kind, data):
    return f'event: {kind}\ndata: {json.dumps(data,ensure_ascii=False)}\n\n'

def _catalog_source_stale(db, source, context=None):
    from .catalog import source_valid
    from .models import Course, CourseBatch
    course_id=source.get('course_id')
    db.scalar(select(Course).where(Course.id==course_id).execution_options(populate_existing=True))
    db.scalars(select(CourseBatch).where(CourseBatch.course_id==course_id).execution_options(populate_existing=True)).all()
    return not source_valid(db,source,context)

def _document_source_stale(db, source, context=None):
    doc=db.scalar(select(Document).where(Document.id==source.get('document_id')).execution_options(populate_existing=True))
    return (not doc or doc.tenant_id!=TENANT or not document_active(doc) or
            doc.visibility!='public' or doc.version!=source.get('version') or
            historical_commercial_text({**source,'knowledge_type':doc.knowledge_type}) or
            (context is not None and not matches_context(doc,context)))

def source_stale_reason(db, sources, context=None):
    """Return 'catalog', 'document', or None after re-reading current publish/price state."""
    for source in sources or []:
        if source.get('kind')=='catalog':
            if _catalog_source_stale(db,source,context):
                return 'catalog'
        elif _document_source_stale(db,source,context):
            return 'document'
    return None

def sources_still_valid(db, sources, context=None):
    return source_stale_reason(db,sources,context) is None

def public_message_data(db, message, context=None):
    result=message_data(message)
    from .models import AnswerFeedback
    feedback=db.get(AnswerFeedback,message.id) if message.role=='assistant' else None
    result['feedback']=feedback.rating if feedback else None
    if message.role=='assistant' and message.sources:
        reason=source_stale_reason(db,message.sources,context)
        if reason=='catalog':
            result.update(content='原回答的课程、班次或价格已变化，请重新提问获取当前有效信息。',sources=[],mode='withdrawn',state='withdrawn')
        elif reason=='document':
            result.update(content='原回答引用的资料已更新、过期或撤回，请重新提问获取当前有效信息。',
                          sources=[],mode='withdrawn',state='withdrawn')
    return result

def update_analysis(db, conv):
    from datetime import timezone
    user_messages = db.scalars(select(Message).where(Message.conversation_id==conv.id,Message.role=='user').order_by(Message.created_at,Message.id)).all()
    lead = db.scalar(select(Lead).where(Lead.conversation_id==conv.id))
    if lead and user_messages:
        def aware(value):return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        if lead.contact_allowed and any(WITHDRAW_CONTACT.search(m.content) and aware(m.created_at)>=aware(lead.consent_at) for m in user_messages):
            from .privacy_lifecycle import withdraw_lead_contact
            withdraw_lead_contact(db,lead,actor_name='系统',note='访客在咨询中撤回联系许可')
    analysis = analyze_messages([{'id':m.id,'content':m.content} for m in user_messages],bool(lead and lead.contact_allowed),respect_withdrawal=False)
    analysis['knowledge_context']=infer_context('',[m.content for m in user_messages])
    conv.analysis = analysis
    if lead:
        lead.tags,lead.evidence,lead.score,lead.purpose = analysis['tags'],analysis['evidence'],analysis['score'],analysis['purpose']
        if not lead.manual_level: lead.level=analysis['level']
    return analysis

def backfill_analysis(db):
    # Upgrade persisted v0.1 summaries once; manually confirmed lead levels remain intact.
    for conv in db.scalars(select(Conversation)):
        if conv.analysis.get('rule_version')!=RULE_VERSION:update_analysis(db,conv)

@router.get('/config')
def config(db: Session = Depends(get_db)):
    from .privacy import DEFAULT_PRIVACY_NOTICE
    values=db.get(Company,TENANT).config
    return {**{k:values[k] for k in ('company_name','bot_name','welcome','suggested_questions','service_hours','contact_notice')},
            'privacy_notice':values.get('privacy_notice') or DEFAULT_PRIVACY_NOTICE,
            'mode':'deepseek' if settings().deepseek_api_key else 'extractive',
            'model':settings().deepseek_model, 'retrieval':retrieval_status(),
            'demo_notice':'知行公考为演示机构，课程及价格为虚构设定，考试政策以官方公告为准。'}

class SessionCreate(BaseModel):
    source_page: str = Field('官网',max_length=300)
    embed_origin: str | None = Field(None,max_length=300)

@router.post('/sessions')
def new_session(data: SessionCreate, request: Request, db: Session = Depends(get_db)):
    ip=client_ip(request)
    cfg=settings()
    limit('session:'+ip, cfg.public_session_limit, cfg.public_session_window)
    embed_origin=assert_embed_allowed(db, request, data.embed_origin)
    token=secrets.token_urlsafe(48)
    page=data.source_page
    if page.startswith(('http://','https://')):
        parts=urlsplit(page)
        page=f'{parts.scheme}://{parts.netloc}{parts.path}'[:300]
    elif page in ('官网','官网演示','官网嵌入') or not page:
        page=embed_origin
    conv=Conversation(token_hash=digest(token),source_page=page[:300])
    db.add(conv)
    db.commit()
    return {'id':conv.id,'token':token,'embed_origin':embed_origin}

@router.get('/sessions/{session_id}')
def history(session_id: str, request: Request, db: Session = Depends(get_db)):
    conv=visitor(request,session_id,db)
    messages=db.scalars(select(Message).where(Message.conversation_id==conv.id).order_by(Message.created_at,Message.id)).all()
    return {'id':conv.id,'status':conv.status,'messages':[public_message_data(db,m) for m in messages],
            'has_contact':bool(db.scalar(select(Lead.id).where(Lead.conversation_id==conv.id)))}

class ChatInput(BaseModel):
    content: str = Field(min_length=1,max_length=2000)
    client_id: str = Field(min_length=8,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')

@router.post('/sessions/{session_id}/messages')
def chat(session_id: str, data: ChatInput, request: Request, db: Session = Depends(get_db)):
    conv=visitor(request,session_id,db)
    # Serialize creation of pending messages across simultaneous HTTP requests.
    conv=db.scalar(select(Conversation).where(Conversation.id==conv.id).with_for_update().execution_options(populate_existing=True))
    if not data.content.strip(): raise HTTPException(422,'请输入问题')
    limit('chat:'+conv.id, settings().public_chat_limit, settings().public_chat_window)
    limit('chat-ip:'+client_ip(request), settings().public_chat_limit * 3, settings().public_chat_window)
    existing=db.scalar(select(Message).where(Message.conversation_id==conv.id,Message.client_id==data.client_id))
    if existing:
        answer=db.scalar(select(Message).where(Message.reply_to==existing.id))
        if answer and answer.state=='completed':
            visible=public_message_data(db,answer)
            def replay():
                yield event('meta',{'mode':visible['mode'],'sources':visible['sources']})
                yield event('delta',{'text':visible['content']})
                yield event('done',{'id':answer.id,'status':conv.status})
            return StreamingResponse(replay(),media_type='text/event-stream')
        if conv.status in ('human','waiting'):
            return StreamingResponse(iter([event('done',{'status':conv.status,'waiting':True})]),media_type='text/event-stream')
        raise HTTPException(409,'此问题仍在处理或上次响应中断，请等待或重新发送')
    if conv.status=='closed': raise HTTPException(409,'咨询已结束，请新建咨询')
    pending=db.scalar(select(Message).where(Message.conversation_id==conv.id,Message.state=='pending'))
    if pending:
        from datetime import timezone
        created=pending.created_at.replace(tzinfo=timezone.utc) if pending.created_at.tzinfo is None else pending.created_at
        if (now()-created).total_seconds()<120:
            raise HTTPException(409,'正在回答上一条问题，请稍候')
        pending.state='failed'
    user_message=Message(conversation_id=conv.id,role='user',content=data.content.strip(),client_id=data.client_id)
    db.add(user_message)
    conv.updated_at=now()
    try: db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409,'问题已经提交，请刷新会话')
    update_analysis(db,conv)
    if conv.status in ('human','waiting'):
        status=conv.status
        tenant_id=conv.tenant_id
        sid=conv.id
        mid=user_message.id
        db.commit()
        from .reception_events import publish
        publish(tenant_id,'message.user',conversation_id=sid,message_id=mid,status=status)
        return StreamingResponse(iter([event('done',{'status':status,'waiting':True})]),media_type='text/event-stream')
    if re.search(r'转人工|人工客服|真人客服|找人工',data.content):
        conv.status='waiting'
        conv.waiting_since=conv.waiting_since or now()
        tenant_id=conv.tenant_id
        sid=conv.id
        db.commit()
        from .reception_events import publish
        publish(tenant_id,'queue.waiting',conversation_id=sid)
        return StreamingResponse(iter([event('delta',{'text':'已为您登记人工接待请求。可以继续补充需求，工作人员接管后会在这里回复。'}),event('done',{'status':'waiting','waiting':True})]),media_type='text/event-stream')
    previous=db.scalars(select(Message).where(Message.conversation_id==conv.id,Message.id!=user_message.id).order_by(Message.created_at.desc()).limit(4)).all()
    # Carry the latest explicit exam topic into short follow-up questions.
    topic=''
    if not any(t in data.content for t in ('公安联考','国考','省考','选调','事业','招警','辅警','社区')):
        for old in previous:
            if old.role=='user':
                match=re.search(r'公安联考|国考|省考|选调生|事业单位|事业编|社会招警|辅警|社区工作者',old.content)
                if match:
                    topic=match.group()+' '
                    break
    active_exams=conv.analysis.get('active_exam_types',[])
    if len(active_exams)==1 and not any(t in data.content for t in ('公安联考','国考','省考','选调','事业','招警','辅警','社区')):
        topic=active_exams[0]+' '
    sources=retrieve(db,topic+data.content,context=conv.analysis.get('knowledge_context') or infer_context(topic+data.content))
    mode=('deepseek' if settings().deepseek_api_key else 'extractive') if sources else 'unknown'
    budget_degraded=False
    if mode=='deepseek':
        from .quality import should_degrade_for_budget
        degrade_reason=should_degrade_for_budget(db,conv.tenant_id,settings().deepseek_model)
        if degrade_reason:
            # Explicit admin opt-in only; default remains alert-without-stopping.
            mode='extractive'
            budget_degraded=True
    answer=Message(conversation_id=conv.id,role='assistant',content='',reply_to=user_message.id,
                   mode=mode,state='pending',sources=sources)
    db.add(answer)
    db.flush()
    pricing=db.get(Company,conv.tenant_id).config.get('_budget',{}).copy()
    if mode=='deepseek':
        from .quality import record_usage
        record_usage(db,answer,settings().deepseek_model,0,'',pricing)
    db.commit()

    def stream():
        text=''
        complete=False
        usage={}
        started=time.monotonic()
        error_type='interrupted'
        knowledge_context=conv.analysis.get('knowledge_context') or {}
        stale_messages={
            'catalog':'回答所依据的课程、班次或价格已变化，请重新提问或转人工核实。',
            'document':'回答所依据的资料已更新、下架或过期，请重新提问或转人工核实。',
        }
        def stop_for_stale():
            reason=source_stale_reason(db,sources,knowledge_context)
            if not reason:
                return None
            answer.state='cancelled'
            return stale_messages[reason]
        def stop_for_human():
            db.refresh(conv)
            if conv.status=='bot':
                return None
            answer.state='cancelled'
            return '已切换人工服务，机器人回答已停止。'
        try:
            yield event('meta',{'mode':mode,'sources':sources,'budget_degraded':budget_degraded})
            if mode=='unknown':
                body='当前已发布资料中没有找到足够依据。请补充目标考试、地区或课程名称；涉及具体报名时间和报考资格，请核对对应年度官方公告，也可以转人工核实。'
                pieces=[body[i:i+24] for i in range(0,len(body),24)]
                for piece in pieces:
                    message=stop_for_human() or stop_for_stale()
                    if message:
                        yield event('error',{'message':message})
                        return
                    text+=piece
                    yield event('delta',{'text':piece})
            elif mode=='extractive':
                message=stop_for_human() or stop_for_stale()
                if message:
                    yield event('error',{'message':message})
                    return
                if budget_degraded:
                    text='今日或本月模型估算费用已达预算，已按管理员设置改为资料摘录，暂不调用付费模型。如需调整请联系管理员。\n\n'
                else:
                    text='当前未配置 DeepSeek，以下为已发布资料摘录：\n\n'
                yield event('delta',{'text':text})
                for i,source in enumerate(sources[:2]):
                    message=stop_for_human() or stop_for_stale()
                    if message:
                        yield event('error',{'message':message})
                        return
                    piece=f'[{i+1}] {source["title"]}\n{source["text"][:650]}\n\n'
                    text+=piece
                    yield event('delta',{'text':piece})
                end='以上经营资料为演示设定。如需实际报价、开班日期或政策资格确认，请转人工核实。'
                message=stop_for_human() or stop_for_stale()
                if message:
                    yield event('error',{'message':message})
                    return
                text+=end
                yield event('delta',{'text':end})
            else:
                history_msgs=[public_message_data(db,m,knowledge_context) for m in reversed(previous)]
                for index,(kind,piece) in enumerate(deepseek_stream(data.content,sources,history_msgs)):
                    if kind=='usage':
                        usage=piece
                        continue
                    # Re-check handoff and cited source versions before every model delta.
                    message=stop_for_human() or stop_for_stale()
                    if message:
                        yield event('error',{'message':message})
                        return
                    text+=piece
                    yield event('delta',{'text':piece})
                if not text.strip(): raise ValueError('empty model output')
            message=stop_for_human() or stop_for_stale()
            if message:
                yield event('error',{'message':message})
                return
            complete=True
            error_type=''
            yield event('done',{'id':answer.id,'status':conv.status})
        except GeneratorExit:
            raise
        except Exception as exc:
            from .quality import classify_error
            error_type,error_message=classify_error(exc)
            yield event('error',{'message':error_message,'type':error_type})
        finally:
            answer.content=text
            answer.usage=usage
            if answer.state!='cancelled': answer.state='completed' if complete else 'failed'
            if mode=='deepseek':
                from .quality import record_usage
                record_usage(db,answer,settings().deepseek_model,int((time.monotonic()-started)*1000),error_type,pricing)
            elif mode=='unknown' and complete:
                from .quality import add_gap
                add_gap(db,answer,'unknown_answer')
            conv.updated_at=now()
            db.commit()
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})

class ContactInput(BaseModel):
    name: str = Field(min_length=1,max_length=80)
    contact: str = Field(min_length=5,max_length=160)
    consent: Literal[True]

@router.post('/sessions/{session_id}/contact')
def contact(session_id: str, data: ContactInput, request: Request, db: Session = Depends(get_db)):
    conv=visitor(request,session_id,db)
    # Match CRM lock order so staff/ownership changes cannot race first-contact attribution.
    db.scalar(select(Company).where(Company.id==conv.tenant_id).with_for_update())
    conv=db.scalar(select(Conversation).where(Conversation.id==conv.id).with_for_update().execution_options(populate_existing=True))
    limit('contact:'+conv.id,6,300)
    limit('contact-ip:'+client_ip(request),20,300)
    value=data.contact.strip()
    if not re.fullmatch(r'(1[3-9]\d{9}|[^\s@]+@[^\s@]+\.[^\s@]+)',value):
        raise HTTPException(422,'请输入有效的中国大陆手机号或邮箱')
    lead=db.scalar(select(Lead).where(Lead.conversation_id==conv.id))
    if not lead:
        lead=Lead(conversation_id=conv.id,name=data.name.strip() or '网站访客',contact=value,
                  consent_text=db.get(Company,TENANT).config['contact_notice'],is_demo=conv.is_demo,contact_allowed=True)
        db.add(lead)
        # An explicitly transferred anonymous advisor conversation stays with that advisor.
        # Existing leads always retain their independently managed CRM owner.
        if conv.assigned_to_id:
            from .models import Admin
            assigned=db.get(Admin,conv.assigned_to_id)
            if assigned and assigned.tenant_id==conv.tenant_id and assigned.is_active and assigned.role=='advisor':
                lead.owner_id=assigned.id;lead.owner=assigned.name
    else:
        lead.name,lead.contact=data.name.strip() or '网站访客',value
        lead.contact_verified=False
        lead.contact_allowed=True
        lead.consent_at=now()
    from .students import contact_hash
    lead.contact_hash=contact_hash(value)
    conv.visitor_name=lead.name
    db.flush()
    update_analysis(db,conv)
    db.commit()
    return {'id':lead.id,'message':'已记录本次咨询的联系请求，联系方式待人工核验。'}

@router.post('/sessions/{session_id}/handoff')
def handoff(session_id: str, request: Request, db: Session = Depends(get_db)):
    conv=visitor(request,session_id,db)
    conv=db.scalar(select(Conversation).where(Conversation.id==conv.id).with_for_update().execution_options(populate_existing=True))
    if conv.status=='closed':raise HTTPException(409,'咨询已结束，请新建咨询')
    if conv.status!='human':
        conv.status='waiting'
        conv.waiting_since=conv.waiting_since or now()
    tenant_id=conv.tenant_id
    sid=conv.id
    status=conv.status
    db.commit()
    if status=='waiting':
        from .reception_events import publish
        publish(tenant_id,'queue.waiting',conversation_id=sid)
    return {'status':status}
