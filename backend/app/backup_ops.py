"""Logical encrypted backup / restore for isolation drills (not a full pg_dump replacement).

Production hosts should still run scripts/backup_db.sh (pg_dump -Fc) and keep copies off-box.
This module proves session / lead / knowledge round-trips in tests and SQLite, and can write
a Fernet-protected JSON archive under BACKUP_DIR.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .config import settings
from .encryption import cipher, PREFIX
from .models import (
    Conversation, Message, Lead, FollowUp, Document, Chunk, DocumentRevision,
    Company, Audit, PrivacyRequest, TENANT, now,
)

LOGICAL_VERSION = 1
TABLE_ORDER = (
    'companies', 'documents', 'document_revisions', 'chunks',
    'conversations', 'messages', 'leads', 'followups', 'privacy_requests', 'audit_logs',
)

def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value

def _backup_root() -> Path:
    root = Path(settings().backup_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root

def export_logical(db: Session, tenant_id: str = TENANT) -> dict:
    """Snapshot tenant business rows needed to restore sessions, leads and knowledge."""
    company = db.get(Company, tenant_id)
    documents = db.scalars(select(Document).where(Document.tenant_id == tenant_id).order_by(Document.id)).all()
    doc_ids = [d.id for d in documents]
    revisions = db.scalars(select(DocumentRevision).where(DocumentRevision.tenant_id == tenant_id).order_by(DocumentRevision.id)).all() if doc_ids else []
    chunks = db.scalars(select(Chunk).where(Chunk.tenant_id == tenant_id).order_by(Chunk.document_id, Chunk.position)).all() if doc_ids else []
    conversations = db.scalars(select(Conversation).where(Conversation.tenant_id == tenant_id).order_by(Conversation.id)).all()
    conv_ids = [c.id for c in conversations]
    messages = db.scalars(select(Message).where(Message.conversation_id.in_(conv_ids)).order_by(Message.created_at, Message.id)).all() if conv_ids else []
    leads = db.scalars(select(Lead).where(Lead.tenant_id == tenant_id).order_by(Lead.id)).all()
    lead_ids = [l.id for l in leads]
    followups = db.scalars(select(FollowUp).where(FollowUp.lead_id.in_(lead_ids)).order_by(FollowUp.created_at, FollowUp.id)).all() if lead_ids else []
    privacy = db.scalars(select(PrivacyRequest).where(PrivacyRequest.tenant_id == tenant_id).order_by(PrivacyRequest.created_at, PrivacyRequest.id)).all()
    audits = db.scalars(select(Audit).where(Audit.tenant_id == tenant_id).order_by(Audit.created_at, Audit.id)).all()

    # Contacts stay ciphertext as stored; restore does not need the ORM type decorator.
    lead_rows = []
    for lead in leads:
        raw = db.execute(text('SELECT contact FROM leads WHERE id=:id'), {'id': lead.id}).scalar()
        lead_rows.append({
            'id': lead.id, 'tenant_id': lead.tenant_id, 'conversation_id': lead.conversation_id,
            'name': lead.name, 'contact_cipher': raw, 'contact_hash': lead.contact_hash,
            'student_id': lead.student_id, 'next_followup_at': _iso(lead.next_followup_at),
            'contact_verified': lead.contact_verified, 'contact_allowed': lead.contact_allowed,
            'consent_text': lead.consent_text, 'consent_at': _iso(lead.consent_at),
            'level': lead.level, 'score': lead.score, 'purpose': lead.purpose,
            'tags': lead.tags, 'evidence': lead.evidence, 'stage': lead.stage,
            'owner': lead.owner, 'owner_id': lead.owner_id, 'manual_level': lead.manual_level,
            'is_demo': lead.is_demo, 'created_at': _iso(lead.created_at), 'updated_at': _iso(lead.updated_at),
        })

    payload = {
        'format': 'zhixing-logical',
        'version': LOGICAL_VERSION,
        'tenant_id': tenant_id,
        'created_at': now().isoformat(),
        'companies': [{'id': company.id, 'config': company.config}] if company else [],
        'documents': [{
            'id': d.id, 'tenant_id': d.tenant_id, 'title': d.title, 'category': d.category,
            'content': d.content, 'status': d.status, 'visibility': d.visibility,
            'source_url': d.source_url, 'is_demo': d.is_demo, 'version': d.version,
            'edit_sequence': d.edit_sequence, 'knowledge_type': d.knowledge_type,
            'exam_type': d.exam_type, 'region': d.region, 'exam_year': d.exam_year,
            'valid_from': _iso(d.valid_from), 'valid_until': _iso(d.valid_until),
            'responsible_id': d.responsible_id, 'policy_verified': d.policy_verified,
            'created_at': _iso(d.created_at), 'updated_at': _iso(d.updated_at),
        } for d in documents],
        'document_revisions': [{
            'id': r.id, 'document_id': r.document_id, 'tenant_id': r.tenant_id, 'number': r.number,
            'status': r.status, 'data': r.data, 'author_id': r.author_id, 'author_name': r.author_name,
            'reviewer_name': r.reviewer_name, 'review_note': r.review_note,
            'created_at': _iso(r.created_at), 'updated_at': _iso(r.updated_at),
        } for r in revisions],
        'chunks': [{
            'id': c.id, 'document_id': c.document_id, 'tenant_id': c.tenant_id,
            'text': c.text, 'terms': c.terms, 'position': c.position,
            # embeddings omitted: large and rebuildable by worker after restore
        } for c in chunks],
        'conversations': [{
            'id': c.id, 'tenant_id': c.tenant_id, 'token_hash': c.token_hash,
            'visitor_name': c.visitor_name, 'source_page': c.source_page, 'status': c.status,
            'assigned_to_id': c.assigned_to_id, 'waiting_since': _iso(c.waiting_since), 'handoff_note': getattr(c,'handoff_note','') or '',
            'analysis': c.analysis, 'is_demo': c.is_demo,
            'created_at': _iso(c.created_at), 'updated_at': _iso(c.updated_at),
        } for c in conversations],
        'messages': [{
            'id': m.id, 'conversation_id': m.conversation_id, 'role': m.role, 'content': m.content,
            'client_id': m.client_id, 'reply_to': m.reply_to, 'sources': m.sources,
            'mode': m.mode, 'state': m.state, 'usage': m.usage, 'created_at': _iso(m.created_at),
        } for m in messages],
        'leads': lead_rows,
        'followups': [{
            'id': f.id, 'lead_id': f.lead_id, 'author': f.author, 'content': f.content,
            'created_at': _iso(f.created_at),
        } for f in followups],
        'privacy_requests': [{
            'id': p.id, 'tenant_id': p.tenant_id, 'kind': p.kind, 'status': p.status,
            'subject_name': p.subject_name, 'contact_hint': p.contact_hint,
            'conversation_id': p.conversation_id, 'lead_id': p.lead_id,
            'note': p.note, 'resolution': p.resolution, 'actor_id': p.actor_id,
            'actor_name': p.actor_name, 'created_at': _iso(p.created_at),
            'completed_at': _iso(p.completed_at),
        } for p in privacy],
        'audit_logs': [{
            'id': a.id, 'tenant_id': a.tenant_id, 'actor': a.actor, 'action': a.action,
            'details': a.details, 'target': a.target, 'created_at': _iso(a.created_at),
        } for a in audits],
    }
    return payload

def write_encrypted_backup(db: Session, tenant_id: str = TENANT, label: str = '') -> Path:
    payload = export_logical(db, tenant_id)
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    token = cipher().encrypt(raw)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    name = f'logical-{stamp}{("-" + label) if label else ""}.json.enc'
    path = _backup_root() / name
    path.write_bytes(PREFIX.encode() + token)
    path.chmod(0o600)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(path.suffix + '.sha256').write_text(digest + '  ' + path.name + '\n', encoding='utf-8')
    return path

def read_encrypted_backup(path: Path) -> dict:
    data = Path(path).read_bytes()
    if data.startswith(PREFIX.encode()):
        data = data[len(PREFIX):]
    plain = cipher().decrypt(data)
    payload = json.loads(plain.decode())
    if payload.get('format') != 'zhixing-logical':
        raise ValueError('不是本系统逻辑备份格式')
    return payload

def _parse_dt(value):
    if not value:
        return None
    return datetime.fromisoformat(value)

def _delete_ids(db: Session, model, ids):
    ids = [i for i in ids if i]
    if not ids:
        return
    for row in db.scalars(select(model).where(model.id.in_(ids))).all():
        db.delete(row)

def replace_payload_rows(db: Session, payload: dict):
    """Delete existing rows that will be replaced by this backup payload."""
    from .models import DocumentJob, AnswerFeedback, KnowledgeGap, ModelCall, Trial, ConversationRead
    lead_ids = [r['id'] for r in payload.get('leads') or []]
    conv_ids = [r['id'] for r in payload.get('conversations') or []]
    doc_ids = [r['id'] for r in payload.get('documents') or []]
    msg_ids = [r['id'] for r in payload.get('messages') or []]
    follow_ids = [r['id'] for r in payload.get('followups') or []]
    priv_ids = [r['id'] for r in payload.get('privacy_requests') or []]
    chunk_ids = [r['id'] for r in payload.get('chunks') or []]
    rev_ids = [r['id'] for r in payload.get('document_revisions') or []]

    if lead_ids:
        for item in db.scalars(select(FollowUp).where(FollowUp.lead_id.in_(lead_ids))).all():
            db.delete(item)
    if msg_ids:
        for model in (AnswerFeedback, KnowledgeGap, ModelCall):
            col = getattr(model, 'message_id')
            for item in db.scalars(select(model).where(col.in_(msg_ids))).all():
                db.delete(item)
    if conv_ids:
        for item in db.scalars(select(Message).where(Message.conversation_id.in_(conv_ids))).all():
            db.delete(item)
        for item in db.scalars(select(Trial).where(Trial.conversation_id.in_(conv_ids))).all():
            db.delete(item)
        for item in db.scalars(select(ConversationRead).where(ConversationRead.conversation_id.in_(conv_ids))).all():
            db.delete(item)
        for item in db.scalars(select(Lead).where(Lead.conversation_id.in_(conv_ids))).all():
            # Drop leads tied to these conversations even if lead id list omitted.
            for fu in db.scalars(select(FollowUp).where(FollowUp.lead_id == item.id)).all():
                db.delete(fu)
            db.delete(item)
    if doc_ids:
        for item in db.scalars(select(DocumentJob).where(DocumentJob.document_id.in_(doc_ids))).all():
            db.delete(item)
        for item in db.scalars(select(Chunk).where(Chunk.document_id.in_(doc_ids))).all():
            db.delete(item)
        for item in db.scalars(select(DocumentRevision).where(DocumentRevision.document_id.in_(doc_ids))).all():
            db.delete(item)
        # Knowledge gaps pointing at documents
        for item in db.scalars(select(KnowledgeGap).where(KnowledgeGap.document_id.in_(doc_ids))).all():
            item.document_id = None
    _delete_ids(db, FollowUp, follow_ids)
    _delete_ids(db, Lead, lead_ids)
    _delete_ids(db, Conversation, conv_ids)
    _delete_ids(db, Chunk, chunk_ids)
    _delete_ids(db, DocumentRevision, rev_ids)
    _delete_ids(db, Document, doc_ids)
    _delete_ids(db, PrivacyRequest, priv_ids)
    db.flush()

def restore_logical(db: Session, payload: dict, *, clear_first: bool = True):
    tenant_id = payload.get('tenant_id') or TENANT
    if clear_first:
        replace_payload_rows(db, payload)
    for row in payload.get('companies') or []:
        company = db.get(Company, row['id'])
        if company:
            company.config = row.get('config') or company.config
        else:
            db.add(Company(id=row['id'], config=row.get('config') or {}))
    for row in payload.get('documents') or []:
        db.add(Document(
            id=row['id'], tenant_id=row.get('tenant_id', tenant_id), title=row['title'],
            category=row.get('category', '未分类'), content=row.get('content', ''),
            status=row.get('status', 'draft'), visibility=row.get('visibility', 'public'),
            source_url=row.get('source_url'), is_demo=bool(row.get('is_demo')),
            version=int(row.get('version') or 1), edit_sequence=int(row.get('edit_sequence') or 0),
            knowledge_type=row.get('knowledge_type') or 'general',
            exam_type=row.get('exam_type') or '通用', region=row.get('region') or '全国',
            exam_year=row.get('exam_year'), valid_from=_parse_dt(row.get('valid_from')),
            valid_until=_parse_dt(row.get('valid_until')), responsible_id=row.get('responsible_id'),
            policy_verified=bool(row.get('policy_verified')),
            created_at=_parse_dt(row.get('created_at')) or now(),
            updated_at=_parse_dt(row.get('updated_at')) or now(),
        ))
    db.flush()
    for row in payload.get('document_revisions') or []:
        db.add(DocumentRevision(
            id=row['id'], document_id=row['document_id'], tenant_id=row.get('tenant_id', tenant_id),
            number=int(row['number']), status=row.get('status', 'draft'), data=row.get('data') or {},
            author_id=row.get('author_id'), author_name=row.get('author_name') or '历史资料',
            reviewer_name=row.get('reviewer_name'), review_note=row.get('review_note') or '',
            created_at=_parse_dt(row.get('created_at')) or now(),
            updated_at=_parse_dt(row.get('updated_at')) or now(),
        ))
    for row in payload.get('chunks') or []:
        db.add(Chunk(
            id=row['id'], document_id=row['document_id'], tenant_id=row.get('tenant_id', tenant_id),
            text=row.get('text', ''), terms=row.get('terms') or '', position=int(row.get('position') or 0),
            embedding=None,
        ))
    for row in payload.get('conversations') or []:
        db.add(Conversation(
            id=row['id'], tenant_id=row.get('tenant_id', tenant_id), token_hash=row['token_hash'],
            visitor_name=row.get('visitor_name') or '网站访客', source_page=row.get('source_page') or '官网',
            status=row.get('status') or 'bot', assigned_to_id=row.get('assigned_to_id'),
            waiting_since=_parse_dt(row.get('waiting_since')), handoff_note=row.get('handoff_note') or '', analysis=row.get('analysis') or {},
            is_demo=bool(row.get('is_demo')),
            created_at=_parse_dt(row.get('created_at')) or now(),
            updated_at=_parse_dt(row.get('updated_at')) or now(),
        ))
    db.flush()
    for row in payload.get('messages') or []:
        db.add(Message(
            id=row['id'], conversation_id=row['conversation_id'], role=row['role'],
            content=row.get('content') or '', client_id=row.get('client_id'),
            reply_to=row.get('reply_to'), sources=row.get('sources') or [],
            mode=row.get('mode') or '', state=row.get('state') or 'completed',
            usage=row.get('usage') or {}, created_at=_parse_dt(row.get('created_at')) or now(),
        ))
    from .encryption import decrypt as decrypt_contact
    for row in payload.get('leads') or []:
        cipher_text = row.get('contact_cipher') or row.get('contact') or ''
        try:
            plain = decrypt_contact(cipher_text) if cipher_text else ''
        except Exception:
            plain = cipher_text if cipher_text and not str(cipher_text).startswith(PREFIX) else '[恢复失败]'
        db.add(Lead(
            id=row['id'], tenant_id=row.get('tenant_id', tenant_id),
            conversation_id=row['conversation_id'], name=row.get('name') or '访客',
            contact=plain or '[空]', contact_hash=row.get('contact_hash'),
            student_id=None, next_followup_at=_parse_dt(row.get('next_followup_at')),
            contact_verified=bool(row.get('contact_verified')),
            contact_allowed=bool(row.get('contact_allowed', True)),
            consent_text=row.get('consent_text') or '',
            consent_at=_parse_dt(row.get('consent_at')) or now(),
            level=row.get('level') or '待判定', score=int(row.get('score') or 0),
            purpose=row.get('purpose') or '课程咨询',
            tags=row.get('tags') or [], evidence=row.get('evidence') or [],
            stage=row.get('stage') or '新线索', owner=row.get('owner') or '未分配',
            owner_id=row.get('owner_id'), manual_level=bool(row.get('manual_level')),
            is_demo=bool(row.get('is_demo')),
            created_at=_parse_dt(row.get('created_at')) or now(),
            updated_at=_parse_dt(row.get('updated_at')) or now(),
        ))
    db.flush()
    for row in payload.get('followups') or []:
        db.add(FollowUp(
            id=row['id'], lead_id=row['lead_id'], author=row.get('author') or '',
            content=row.get('content') or '', created_at=_parse_dt(row.get('created_at')) or now(),
        ))
    for row in payload.get('privacy_requests') or []:
        db.add(PrivacyRequest(
            id=row['id'], tenant_id=row.get('tenant_id', tenant_id), kind=row['kind'],
            status=row.get('status') or 'open', subject_name=row.get('subject_name') or '',
            contact_hint=row.get('contact_hint') or '', conversation_id=row.get('conversation_id'),
            lead_id=row.get('lead_id'), note=row.get('note') or '',
            resolution=row.get('resolution') or '', actor_id=row.get('actor_id'),
            actor_name=row.get('actor_name') or '',
            created_at=_parse_dt(row.get('created_at')) or now(),
            completed_at=_parse_dt(row.get('completed_at')),
        ))
    for row in payload.get('audit_logs') or []:
        if db.get(Audit, row['id']):
            continue
        db.add(Audit(
            id=row['id'], tenant_id=row.get('tenant_id', tenant_id), actor=row.get('actor') or '',
            action=row.get('action') or '', details=row.get('details') or '',
            target=row.get('target') or '', created_at=_parse_dt(row.get('created_at')) or now(),
        ))
    db.flush()
    return {
        'documents': len(payload.get('documents') or []),
        'conversations': len(payload.get('conversations') or []),
        'leads': len(payload.get('leads') or []),
        'messages': len(payload.get('messages') or []),
        'privacy_requests': len(payload.get('privacy_requests') or []),
    }

def verify_backup_file(path: Path) -> dict:
    path = Path(path)
    digest_file = path.with_suffix(path.suffix + '.sha256')
    if not digest_file.exists():
        # also accept name.sha256 next to name
        alt = Path(str(path) + '.sha256')
        digest_file = alt if alt.exists() else digest_file
    payload = read_encrypted_backup(path)
    result = {
        'path': str(path),
        'ok': True,
        'tenant_id': payload.get('tenant_id'),
        'created_at': payload.get('created_at'),
        'counts': {k: len(payload.get(k) or []) for k in (
            'documents', 'conversations', 'messages', 'leads', 'followups', 'privacy_requests')},
    }
    if digest_file.exists():
        expected = digest_file.read_text(encoding='utf-8').split()[0]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        result['checksum_ok'] = expected == actual
        result['ok'] = result['ok'] and result['checksum_ok']
    return result
