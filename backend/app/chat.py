import json
import re
import secrets
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
from .security import digest, limit, visitor
from .knowledge import retrieve, retrieval_status, historical_commercial_text
from .scoring import analyze_messages, WITHDRAW_CONTACT, RULE_VERSION
from .llm import deepseek_stream
from .admin import message_data
from .document_service import document_active, infer_context, matches_context

router = APIRouter(prefix='/widget')

def event(kind, data):
    return f'event: {kind}\ndata: {json.dumps(data,ensure_ascii=False)}\n\n'

def public_message_data(db, message, context=None):
    result=message_data(message)
    if message.role=='assistant' and message.sources:
        for source in message.sources:
            if source.get('kind')=='catalog':
                from .catalog import source_valid
                if source_valid(db,source,context):continue
                result.update(content='原回答的课程、班次或价格已变化，请重新提问获取当前有效信息。',sources=[],mode='withdrawn',state='withdrawn')
                break
            doc=db.get(Document,source.get('document_id'))
            if (not doc or doc.tenant_id!=TENANT or not document_active(doc) or
                doc.visibility!='public' or doc.version!=source.get('version') or
                historical_commercial_text({**source,'knowledge_type':doc.knowledge_type}) or
                (context is not None and not matches_context(doc,context))):
                result.update(content='原回答引用的资料已更新、过期或撤回，请重新提问获取当前有效信息。',
                              sources=[],mode='withdrawn',state='withdrawn')
                break
    return result

def update_analysis(db, conv):
    from datetime import timezone
    user_messages = db.scalars(select(Message).where(Message.conversation_id==conv.id,Message.role=='user').order_by(Message.created_at,Message.id)).all()
    lead = db.scalar(select(Lead).where(Lead.conversation_id==conv.id))
    if lead and user_messages:
        def aware(value):return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        if any(WITHDRAW_CONTACT.search(m.content) and aware(m.created_at)>=aware(lead.consent_at) for m in user_messages):
            lead.contact_allowed=False
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
    values=db.get(Company,TENANT).config
    return {**{k:values[k] for k in ('company_name','bot_name','welcome','suggested_questions','service_hours','contact_notice')},
            'mode':'deepseek' if settings().deepseek_api_key else 'extractive',
            'model':settings().deepseek_model, 'retrieval':retrieval_status(),
            'demo_notice':'知行公考为演示机构，课程及价格为虚构设定，考试政策以官方公告为准。'}

class SessionCreate(BaseModel):
    source_page: str = Field('官网',max_length=300)

@router.post('/sessions')
def new_session(data: SessionCreate, request: Request, db: Session = Depends(get_db)):
    limit('session:'+(request.client.host if request.client else 'local'),30,60)
    token=secrets.token_urlsafe(48)
    page=data.source_page
    if page.startswith(('http://','https://')):
        parts=urlsplit(page)
        page=f'{parts.scheme}://{parts.netloc}{parts.path}'[:300]
    conv=Conversation(token_hash=digest(token),source_page=page)
    db.add(conv)
    db.commit()
    return {'id':conv.id,'token':token}

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
    conv=db.scalar(select(Conversation).where(Conversation.id==conv.id).with_for_update())
    if not data.content.strip(): raise HTTPException(422,'请输入问题')
    limit('chat:'+conv.id,20)
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
        db.commit()
        return StreamingResponse(iter([event('done',{'status':conv.status,'waiting':True})]),media_type='text/event-stream')
    if re.search(r'转人工|人工客服|真人客服|找人工',data.content):
        conv.status='waiting'
        db.commit()
        return StreamingResponse(iter([event('delta',{'text':'已为您登记人工接待请求。可以继续补充需求，工作人员接管后会在这里回复。'}),event('done',{'status':'waiting','waiting':True})]),media_type='text/event-stream')
    previous=db.scalars(select(Message).where(Message.conversation_id==conv.id,Message.id!=user_message.id).order_by(Message.created_at.desc()).limit(6)).all()
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
    answer=Message(conversation_id=conv.id,role='assistant',content='',reply_to=user_message.id,
                   mode=mode,state='pending',sources=sources)
    db.add(answer)
    db.commit()

    def stream():
        text=''
        complete=False
        usage={}
        try:
            yield event('meta',{'mode':mode,'sources':sources})
            if mode=='unknown':
                body='当前已发布资料中没有找到足够依据。请补充目标考试、地区或课程名称；涉及具体报名时间和报考资格，请核对对应年度官方公告，也可以转人工核实。'
                pieces=[body[i:i+24] for i in range(0,len(body),24)]
                for piece in pieces:
                    text+=piece
                    yield event('delta',{'text':piece})
            elif mode=='extractive':
                text='当前未配置 DeepSeek，以下为已发布资料摘录：\n\n'
                yield event('delta',{'text':text})
                for i,source in enumerate(sources[:2]):
                    piece=f'[{i+1}] {source["title"]}\n{source["text"][:650]}\n\n'
                    text+=piece
                    yield event('delta',{'text':piece})
                end='以上经营资料为演示设定。如需实际报价、开班日期或政策资格确认，请转人工核实。'
                text+=end
                yield event('delta',{'text':end})
            else:
                for index,(kind,piece) in enumerate(deepseek_stream(data.content,sources,[public_message_data(db,m,conv.analysis.get('knowledge_context',{})) for m in reversed(previous)])):
                    if kind=='usage':
                        usage=piece
                        continue
                    if index % 8 == 0:
                        db.refresh(conv)
                        if conv.status!='bot':
                            answer.state='cancelled'
                            yield event('error',{'message':'已切换人工服务，机器人回答已停止。'})
                            return
                    text+=piece
                    yield event('delta',{'text':piece})
                if not text.strip(): raise ValueError('empty model output')
            complete=True
            yield event('done',{'id':answer.id,'status':conv.status})
        except GeneratorExit:
            raise
        except Exception:
            yield event('error',{'message':'模型服务暂时未能完成回答，请稍后重试或转人工。可在下方查看已检索资料。'})
        finally:
            answer.content=text
            answer.usage=usage
            if answer.state!='cancelled': answer.state='completed' if complete else 'failed'
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
    conv=db.scalar(select(Conversation).where(Conversation.id==conv.id).with_for_update())
    limit('contact:'+conv.id,6,300)
    value=data.contact.strip()
    if not re.fullmatch(r'(1[3-9]\d{9}|[^\s@]+@[^\s@]+\.[^\s@]+)',value):
        raise HTTPException(422,'请输入有效的中国大陆手机号或邮箱')
    lead=db.scalar(select(Lead).where(Lead.conversation_id==conv.id))
    if not lead:
        lead=Lead(conversation_id=conv.id,name=data.name.strip() or '网站访客',contact=value,
                  consent_text=db.get(Company,TENANT).config['contact_notice'],is_demo=conv.is_demo,contact_allowed=True)
        db.add(lead)
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
    if conv.status!='human': conv.status='waiting'
    db.commit()
    return {'status':conv.status}
