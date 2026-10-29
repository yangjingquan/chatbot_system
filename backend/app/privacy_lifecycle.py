"""Contact withdrawal, retention cleanup, and privacy request fulfillment."""
from __future__ import annotations
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select, or_
from sqlalchemy.orm import Session
from .config import settings
from .models import (
    Company, Conversation, Message, Lead, FollowUp, Audit, PrivacyRequest,
    Student, StudentInterest, Trial, TENANT, now, uid,
)
from .monitoring import get_heartbeat, redact_log, touch_heartbeat

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
    for trial in db.scalars(select(Trial).where(Trial.conversation_id == lead.conversation_id, Trial.todo_due_at.is_not(None))):
        trial.todo_due_at = None
        trial.todo_kind = ''
        trial.version += 1
    db.add(Audit(tenant_id=lead.tenant_id, actor=actor_name, action='撤回联系许可',
                 target=lead.id, details=(note or '访客或员工撤回联系许可')[:1000]))

def anonymize_conversation(db: Session, conv: Conversation):
    conv.visitor_name = ANON_NAME
    conv.analysis = {'anonymized': True, 'purpose': '已清理', 'level': '不适用', 'score': 0, 'tags': [], 'evidence': [], 'demands': []}
    conv.token_hash = 'anonymized-' + conv.id[:18]
    messages = db.scalars(select(Message).where(Message.conversation_id == conv.id)).all()
    for msg in messages:
        msg.content = ANON_MESSAGE
        msg.sources = []
        msg.usage = {}
    return len(messages)

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
    for trial in db.scalars(select(Trial).where(Trial.conversation_id == lead.conversation_id, Trial.todo_due_at.is_not(None))):
        trial.todo_due_at = None
        trial.todo_kind = ''
        trial.version += 1
    lead.purpose = '已清理'
    followups = db.scalars(select(FollowUp).where(FollowUp.lead_id == lead.id)).all()
    for item in followups:
        item.content = ANON_MESSAGE
        item.author = ANON_NAME
    return len(followups)


def anonymize_student(db: Session, student: Student) -> int:
    """Remove profile details while retaining the row for relationship integrity."""
    student.name = ANON_NAME
    student.notes = ANON_MESSAGE
    student.exam_targets = []
    student.status = 'anonymized'
    interests = db.scalars(select(StudentInterest).where(StudentInterest.student_id == student.id)).all()
    for interest in interests:
        db.delete(interest)
    return len(interests)


def _privacy_scope(db: Session, tenant_id: str, lead_id: str | None,
                   conversation_id: str | None) -> tuple[list[Lead], list[Conversation], Student | None]:
    lead = db.scalar(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)) if lead_id else None
    conv = db.scalar(select(Conversation).where(
        Conversation.id == conversation_id, Conversation.tenant_id == tenant_id)) if conversation_id else None
    if lead and conv and lead.conversation_id != conv.id:
        raise HTTPException(422, '线索与会话不匹配')
    if lead and not conv:
        conv = db.scalar(select(Conversation).where(
            Conversation.id == lead.conversation_id, Conversation.tenant_id == tenant_id))
    if conv and not lead:
        lead = db.scalar(select(Lead).where(
            Lead.conversation_id == conv.id, Lead.tenant_id == tenant_id))
    student = db.scalar(select(Student).where(
        Student.id == lead.student_id, Student.tenant_id == tenant_id)) if lead and lead.student_id else None
    if student:
        leads = db.scalars(select(Lead).where(
            Lead.tenant_id == tenant_id, Lead.student_id == student.id
        ).order_by(Lead.id)).all()
    elif lead:
        leads = [lead]
    else:
        leads = []
    if lead and all(item.id != lead.id for item in leads):
        leads.append(lead)
    conversation_ids = {item.conversation_id for item in leads}
    if conv:
        conversation_ids.add(conv.id)
    conversations = db.scalars(select(Conversation).where(
        Conversation.tenant_id == tenant_id, Conversation.id.in_(conversation_ids)
    ).order_by(Conversation.id)).all() if conversation_ids else []
    return leads, conversations, student

def create_privacy_request(db: Session, *, kind: str, subject_name: str = '', contact: str = '',
                           conversation_id: str | None = None, lead_id: str | None = None,
                           note: str = '', actor=None) -> PrivacyRequest:
    if kind not in VALID_KINDS:
        raise HTTPException(422, '请求类型须为撤回联系许可、匿名化或删除')
    tenant_id = actor.tenant_id if actor else TENANT
    lead = db.get(Lead, lead_id) if lead_id else None
    if lead_id and (not lead or lead.tenant_id != tenant_id):
        raise HTTPException(404, '线索不存在')
    conv = db.get(Conversation, conversation_id) if conversation_id else None
    if conversation_id and (not conv or conv.tenant_id != tenant_id):
        raise HTTPException(404, '会话不存在')
    if lead and conv and lead.conversation_id != conversation_id:
        raise HTTPException(422, '线索与会话不匹配')
    if lead and not conversation_id:
        conversation_id = lead.conversation_id
    hint = mask_contact(contact) or (mask_contact(lead.contact) if lead else '')
    req = PrivacyRequest(
        tenant_id=tenant_id,
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
                             reject: bool = False, lead_id: str | None = None,
                             conversation_id: str | None = None) -> PrivacyRequest:
    if req.status in ('completed', 'rejected'):
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
    if lead_id:
        candidate = db.get(Lead, lead_id)
        if not candidate or candidate.tenant_id != req.tenant_id:
            raise HTTPException(404, '线索不存在')
    if conversation_id:
        candidate = db.get(Conversation, conversation_id)
        if not candidate or candidate.tenant_id != req.tenant_id:
            raise HTTPException(404, '会话不存在')
    selected_lead_id = lead_id or req.lead_id
    selected_conversation_id = conversation_id or req.conversation_id
    leads, conversations, student = _privacy_scope(
        db, req.tenant_id, selected_lead_id, selected_conversation_id)
    if not leads and not conversations:
        req.status = 'needs_review'
        req.resolution = '未找到关联会话或线索；请核实身份与处理范围，关联对象后再完成。'
        req.actor_id = actor.id
        req.actor_name = actor.name
        req.completed_at = None
        db.add(Audit(tenant_id=req.tenant_id, actor=actor.name, action='隐私请求待核实',
                     target=req.id, details=req.resolution[:1000]))
        return req
    if req.kind == 'withdraw_contact' and not leads:
        req.status = 'needs_review'
        req.resolution = '撤回联系许可需要关联线索；请核实对象并补充线索后再处理。'
        req.actor_id = actor.id
        req.actor_name = actor.name
        req.completed_at = None
        db.add(Audit(tenant_id=req.tenant_id, actor=actor.name, action='隐私请求待核实',
                     target=req.id, details=req.resolution[:1000]))
        return req
    if req.kind == 'withdraw_contact':
        for lead in leads:
            withdraw_lead_contact(db, lead, actor_name=actor.name, note=req.note or resolution)
        resolution = f'已停止联系；处理线索 {len(leads)} 条。'
    elif req.kind in ('anonymize', 'delete'):
        counts = {'会话': 0, '消息': 0, '线索': 0, '跟进记录': 0,
                  '学员档案': 0, '兴趣记录': 0}
        for lead in leads:
            counts['跟进记录'] += anonymize_lead(db, lead, wipe_contact=True)
            counts['线索'] += 1
            withdraw_lead_contact(db, lead, actor_name=actor.name, note='隐私删除/匿名化')
        for conv in conversations:
            counts['消息'] += anonymize_conversation(db, conv)
            counts['会话'] += 1
        if student:
            counts['兴趣记录'] = anonymize_student(db, student)
            counts['学员档案'] = 1
        # Remove free-form notes on matched records while preserving audit events and IDs.
        targets = [req.id, *(lead.id for lead in leads), *(conv.id for conv in conversations)]
        if student:
            targets.append(student.id)
        for row in db.scalars(select(Audit).where(
                Audit.tenant_id == req.tenant_id, Audit.target.in_(targets))).all():
            row.details = ANON_MESSAGE
        req.subject_name = ANON_NAME
        req.contact_hint = ANON_CONTACT
        req.note = '已按隐私请求处理'
        resolution = '处理范围：' + '、'.join(f'{key} {value} 条' for key, value in counts.items())
        req.lead_id = selected_lead_id if any(row.id == selected_lead_id for row in leads) else (leads[0].id if leads else req.lead_id)
        req.conversation_id = selected_conversation_id if any(row.id == selected_conversation_id for row in conversations) else (conversations[0].id if conversations else req.conversation_id)
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
        leads, conversations, student = _privacy_scope(db, tenant_id, req.lead_id, req.conversation_id)
        if req.kind == 'withdraw_contact':
            for lead in leads:
                if lead.contact_allowed:
                    withdraw_lead_contact(db, lead, actor_name='系统', note='恢复后重放撤回联系许可')
                    count += 1
        if req.kind in ('anonymize', 'delete'):
            for lead in leads:
                if lead.contact != ANON_CONTACT:
                    anonymize_lead(db, lead, wipe_contact=True)
                    count += 1
            for conv in conversations:
                if conv.visitor_name != ANON_NAME:
                    anonymize_conversation(db, conv)
                    count += 1
            if student and student.name != ANON_NAME:
                anonymize_student(db, student)
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


def retention_check_due(db: Session, tenant_id: str = TENANT) -> bool:
    row = get_heartbeat(db, 'privacy-retention', tenant_id)
    interval = max(60, min(settings().retention_interval_seconds, 86400))
    if row and row.status == 'fail':
        interval = min(interval, 900)
    if not row or not row.updated_at:
        return True
    stamp = now()
    updated = row.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=stamp.tzinfo)
    return (stamp - updated).total_seconds() >= interval


def execute_retention(db: Session, tenant_id: str = TENANT) -> dict:
    """Run one cleanup pass and persist a visible success/failure heartbeat."""
    stamp = now()
    try:
        result = apply_retention(db, tenant_id)
        detail = {
            'run_at': stamp.isoformat(),
            'conversations': result['conversations'],
            'leads': result['leads'],
            'audits': result['audits'],
        }
        touch_heartbeat(db, 'privacy-retention', 'ok', detail, tenant_id=tenant_id)
        return {'status': 'ok', **detail, 'config': result['config']}
    except Exception as exc:
        db.rollback()
        message = redact_log(f'{type(exc).__name__}: {exc}', 180)
        detail = {'run_at': stamp.isoformat(), 'last_error': message}
        touch_heartbeat(db, 'privacy-retention', 'fail', detail, tenant_id=tenant_id)
        return {'status': 'fail', **detail}


def retention_status(db: Session, tenant_id: str = TENANT) -> dict:
    row = get_heartbeat(db, 'privacy-retention', tenant_id)
    interval = max(60, min(settings().retention_interval_seconds, 86400))
    if not row or not row.updated_at:
        return {'status': 'unknown', 'detail': '尚无自动留存清理记录', 'run_at': None,
                'interval_seconds': interval}
    stamp = now()
    updated = row.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=stamp.tzinfo)
    age = max(0, int((stamp - updated).total_seconds()))
    status = row.status
    detail = dict(row.detail or {})
    if status == 'ok' and age > interval * 3:
        status = 'stale'
    return {'status': status, 'detail': ('最近一次自动清理记录已过期' if status == 'stale' else ''),
            'run_at': detail.get('run_at') or updated.isoformat(), 'age_seconds': age,
            'interval_seconds': interval, 'conversations': detail.get('conversations', 0),
            'leads': detail.get('leads', 0), 'audits': detail.get('audits', 0),
            'last_error': detail.get('last_error', '')}
