"""Contact withdrawal, retention cleanup, and privacy request fulfillment."""
from __future__ import annotations
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select, or_
from sqlalchemy.orm import Session
from .config import settings
from .models import (
    Company, Conversation, Message, Lead, FollowUp, Audit, PrivacyRequest,
    Student, TENANT, now, uid,
)

ANON_NAME = '已匿名'
ANON_CONTACT = '[已删除]'
ANON_MESSAGE = '[已按隐私或留存策略清理]'
VALID_KINDS = {'withdraw_contact', 'anonymize', 'delete'}

def retention_config(db: Session, tenant_id: str = TENANT) -> dict:
    company = db.get(Company, tenant_id)
    cfg = (company.config if company else {}) or {}
    s = settings()
    def days(key, default):
        value = cfg.get(key, default)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = default
        return max(0, value)
    return {
        'retention_conversation_days': days('retention_conversation_days', s.retention_conversation_days),
        'retention_lead_days': days('retention_lead_days', s.retention_lead_days),
        'retention_audit_days': days('retention_audit_days', s.retention_audit_days),
        'retention_backup_days': days('retention_backup_days', 90),
    }

def mask_contact(value: str | None) -> str:
    if not value:
        return ''
    text = value.strip()
    if '@' in text:
        local, _, domain = text.partition('@')
        return (local[:1] + '***@' + domain) if domain else '***'
    digits = ''.join(ch for ch in text if ch.isdigit())
    if len(digits) >= 7:
        return digits[:3] + '****' + digits[-4:]
    if len(text) <= 2:
        return '*' * len(text)
    return text[:1] + '***' + text[-1:]

def withdraw_lead_contact(db: Session, lead: Lead, *, actor_name: str = '系统', note: str = ''):
    lead.contact_allowed = False
    lead.next_followup_at = None
    db.add(Audit(tenant_id=lead.tenant_id, actor=actor_name, action='撤回联系许可',
                 target=lead.id, details=(note or '访客或员工撤回联系许可')[:1000]))

def anonymize_conversation(db: Session, conv: Conversation):
    conv.visitor_name = ANON_NAME
    conv.analysis = {'anonymized': True, 'purpose': '已清理', 'level': '不适用', 'score': 0, 'tags': [], 'evidence': [], 'demands': []}
    conv.token_hash = 'anonymized-' + conv.id[:18]
    for msg in db.scalars(select(Message).where(Message.conversation_id == conv.id)).all():
        msg.content = ANON_MESSAGE
        msg.sources = []
        msg.usage = {}

def anonymize_lead(db: Session, lead: Lead, *, wipe_contact: bool = True):
    lead.name = ANON_NAME
    if wipe_contact:
        lead.contact = ANON_CONTACT
        lead.contact_hash = None
    lead.contact_allowed = False
    lead.contact_verified = False
    lead.consent_text = '已按隐私请求清理'
    lead.tags = []
    lead.evidence = []
    lead.next_followup_at = None
    lead.purpose = '已清理'
    for item in db.scalars(select(FollowUp).where(FollowUp.lead_id == lead.id)).all():
        item.content = ANON_MESSAGE
        item.author = ANON_NAME

def create_privacy_request(db: Session, *, kind: str, subject_name: str = '', contact: str = '',
                           conversation_id: str | None = None, lead_id: str | None = None,
                           note: str = '', actor=None) -> PrivacyRequest:
    if kind not in VALID_KINDS:
        raise HTTPException(422, '请求类型须为撤回联系许可、匿名化或删除')
    lead = db.get(Lead, lead_id) if lead_id else None
    if lead_id and not lead:
        raise HTTPException(404, '线索不存在')
    if conversation_id and not db.get(Conversation, conversation_id):
        raise HTTPException(404, '会话不存在')
    if lead and conversation_id and lead.conversation_id != conversation_id:
        raise HTTPException(422, '线索与会话不匹配')
    if lead and not conversation_id:
        conversation_id = lead.conversation_id
    hint = mask_contact(contact) or (mask_contact(lead.contact) if lead else '')
    req = PrivacyRequest(
        tenant_id=(actor.tenant_id if actor else TENANT),
        kind=kind, status='open',
        subject_name=(subject_name or (lead.name if lead else ''))[:100],
        contact_hint=hint[:120],
        conversation_id=conversation_id, lead_id=lead_id,
        note=note[:2000],
        actor_id=(actor.id if actor else None),
        actor_name=(actor.name if actor else ''),
    )
    db.add(req)
    db.flush()
    return req

def complete_privacy_request(db: Session, req: PrivacyRequest, *, actor, resolution: str = '',
                             reject: bool = False) -> PrivacyRequest:
    if req.status == 'completed':
        return req
    if reject:
        req.status = 'rejected'
        req.resolution = (resolution or '已拒绝')[:2000]
        req.actor_id = actor.id
        req.actor_name = actor.name
        req.completed_at = now()
        db.add(Audit(tenant_id=req.tenant_id, actor=actor.name, action='拒绝隐私请求',
                     target=req.id, details=req.resolution[:1000]))
        return req
    lead = db.get(Lead, req.lead_id) if req.lead_id else None
    conv = db.get(Conversation, req.conversation_id) if req.conversation_id else None
    if req.kind == 'withdraw_contact':
        if not lead:
            raise HTTPException(422, '撤回联系许可需要关联线索')
        withdraw_lead_contact(db, lead, actor_name=actor.name, note=req.note or resolution)
    elif req.kind in ('anonymize', 'delete'):
        if lead:
            anonymize_lead(db, lead, wipe_contact=True)
            withdraw_lead_contact(db, lead, actor_name=actor.name, note='隐私删除/匿名化')
        if conv:
            anonymize_conversation(db, conv)
        # delete 与 anonymize 在本版本均做就地匿名化，避免破坏外键；物理删除留给备份过期。
    else:
        raise HTTPException(422, '不支持的隐私请求类型')
    req.status = 'completed'
    req.resolution = (resolution or f'已执行{req.kind}')[:2000]
    req.actor_id = actor.id
    req.actor_name = actor.name
    req.completed_at = now()
    db.add(Audit(tenant_id=req.tenant_id, actor=actor.name, action='完成隐私请求',
                 target=req.id, details=f'{req.kind};{req.resolution}'[:1000]))
    return req

def reapply_completed_privacy(db: Session, tenant_id: str = TENANT) -> int:
    """After restoring an older backup, re-apply completed privacy outcomes still recorded online."""
    count = 0
    rows = db.scalars(select(PrivacyRequest).where(
        PrivacyRequest.tenant_id == tenant_id, PrivacyRequest.status == 'completed')).all()
    for req in rows:
        lead = db.get(Lead, req.lead_id) if req.lead_id else None
        conv = db.get(Conversation, req.conversation_id) if req.conversation_id else None
        if req.kind == 'withdraw_contact' and lead and lead.contact_allowed:
            lead.contact_allowed = False
            lead.next_followup_at = None
            count += 1
        if req.kind in ('anonymize', 'delete'):
            if lead and lead.contact != ANON_CONTACT:
                anonymize_lead(db, lead, wipe_contact=True)
                count += 1
            if conv and conv.visitor_name != ANON_NAME:
                anonymize_conversation(db, conv)
                count += 1
    return count

def apply_retention(db: Session, tenant_id: str = TENANT) -> dict:
    """Anonymize visitor data past configured retention. 0 days means skip that category."""
    cfg = retention_config(db, tenant_id)
    stamp = now()
    result = {'conversations': 0, 'leads': 0, 'audits': 0, 'config': cfg}
    conv_days = cfg['retention_conversation_days']
    if conv_days > 0:
        cutoff = stamp - timedelta(days=conv_days)
        for conv in db.scalars(select(Conversation).where(
                Conversation.tenant_id == tenant_id, Conversation.created_at < cutoff,
                Conversation.visitor_name != ANON_NAME)).all():
            anonymize_conversation(db, conv)
            result['conversations'] += 1
    lead_days = cfg['retention_lead_days']
    if lead_days > 0:
        cutoff = stamp - timedelta(days=lead_days)
        for lead in db.scalars(select(Lead).where(
                Lead.tenant_id == tenant_id, Lead.created_at < cutoff,
                Lead.contact != ANON_CONTACT)).all():
            anonymize_lead(db, lead, wipe_contact=True)
            result['leads'] += 1
    audit_days = cfg['retention_audit_days']
    if audit_days > 0:
        cutoff = stamp - timedelta(days=audit_days)
        old = db.scalars(select(Audit).where(Audit.tenant_id == tenant_id, Audit.created_at < cutoff)).all()
        for row in old:
            # Keep the row for integrity but strip potentially sensitive details/targets.
            if row.details or (row.target and '@' in row.target):
                row.details = ''
                if '@' in (row.target or ''):
                    row.target = mask_contact(row.target)
                result['audits'] += 1
    if result['conversations'] or result['leads'] or result['audits']:
        db.add(Audit(tenant_id=tenant_id, actor='系统', action='执行数据留存清理',
                     target=tenant_id,
                     details=f"conversations={result['conversations']};leads={result['leads']};audits={result['audits']}"))
    return result
