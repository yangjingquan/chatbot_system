"""Admin APIs for retention settings, privacy requests, backup drills and key rotation checks."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import get_db, engine
from .models import Admin, PrivacyRequest, Lead, now
from .permissions import require, has_permission
from .privacy_lifecycle import (
    retention_config, create_privacy_request, complete_privacy_request,
    execute_retention, retention_status,
    withdraw_lead_contact, reapply_completed_privacy, mask_contact,
)
from .backup_ops import write_encrypted_backup, read_encrypted_backup, restore_logical, verify_backup_file
from .monitoring import record_backup_result
from .encryption import rotate_contacts
from pathlib import Path

router = APIRouter()

def _record(db, user, action, target='', details=''):
    from .models import Audit
    db.add(Audit(actor=user.name, tenant_id=user.tenant_id, action=action, target=target[:240], details=(details or '')[:1000]))

def request_data(row: PrivacyRequest):
    return {
        'id': row.id, 'kind': row.kind, 'status': row.status,
        'subject_name': row.subject_name, 'contact_hint': row.contact_hint,
        'conversation_id': row.conversation_id, 'lead_id': row.lead_id,
        'note': row.note, 'resolution': row.resolution,
        'actor_name': row.actor_name,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'completed_at': row.completed_at.isoformat() if row.completed_at else None,
    }

@router.get('/admin/privacy/retention')
def get_retention(user: Admin = Depends(require('settings.read')), db: Session = Depends(get_db)):
    return retention_config(db, user.tenant_id)

class RetentionPatch(BaseModel):
    retention_conversation_days: int | None = Field(None, ge=0, le=3650)
    retention_lead_days: int | None = Field(None, ge=0, le=3650)
    retention_audit_days: int | None = Field(None, ge=0, le=3650)
    retention_backup_days: int | None = Field(None, ge=0, le=3650)

@router.patch('/admin/privacy/retention')
def patch_retention(data: RetentionPatch, user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    from .models import Company
    values = data.model_dump(exclude_none=True)
    company = db.get(Company, user.tenant_id)
    company.config = {**company.config, **values}
    _record(db, user, '更新数据留存策略', details=str(values))
    db.commit()
    return retention_config(db, user.tenant_id)

@router.get('/admin/privacy/retention/status')
def get_retention_status(user: Admin = Depends(require('settings.read')), db: Session = Depends(get_db)):
    return retention_status(db, user.tenant_id)

class PrivacyCreate(BaseModel):
    kind: Literal['withdraw_contact', 'anonymize', 'delete']
    subject_name: str = Field('', max_length=100)
    contact: str = Field('', max_length=200)
    conversation_id: str | None = Field(None, max_length=36)
    lead_id: str | None = Field(None, max_length=36)
    note: str = Field('', max_length=2000)

@router.get('/admin/privacy/requests')
def list_requests(status: str = '', user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    query = select(PrivacyRequest).where(PrivacyRequest.tenant_id == user.tenant_id)
    if status:
        query = query.where(PrivacyRequest.status == status)
    rows = db.scalars(query.order_by(PrivacyRequest.created_at.desc(), PrivacyRequest.id.desc()).limit(200)).all()
    return {'items': [request_data(r) for r in rows], 'total': len(rows)}

@router.post('/admin/privacy/requests')
def create_request(data: PrivacyCreate, user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    req = create_privacy_request(
        db, kind=data.kind, subject_name=data.subject_name, contact=data.contact,
        conversation_id=data.conversation_id, lead_id=data.lead_id, note=data.note, actor=user)
    _record(db, user, '登记隐私请求', req.id, data.kind)
    db.commit()
    return request_data(req)

class PrivacyComplete(BaseModel):
    resolution: str = Field('', max_length=2000)
    reject: bool = False
    lead_id: str | None = Field(None, max_length=36)
    conversation_id: str | None = Field(None, max_length=36)

@router.post('/admin/privacy/requests/{rid}/complete')
def finish_request(rid: str, data: PrivacyComplete, user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    req = db.get(PrivacyRequest, rid)
    if not req or req.tenant_id != user.tenant_id:
        raise HTTPException(404, '隐私请求不存在')
    complete_privacy_request(db, req, actor=user, resolution=data.resolution, reject=data.reject,
                             lead_id=data.lead_id, conversation_id=data.conversation_id)
    db.commit()
    return request_data(req)

@router.post('/admin/leads/{lead_id}/withdraw-contact')
def withdraw_contact(lead_id: str, user: Admin = Depends(require('leads.write')), db: Session = Depends(get_db)):
    from .permissions import scoped_object
    lead = scoped_object(db, Lead, lead_id, user)
    withdraw_lead_contact(db, lead, actor_name=user.name, note='员工登记停止联系')
    req = create_privacy_request(db, kind='withdraw_contact', subject_name=lead.name,
                                 conversation_id=lead.conversation_id, lead_id=lead.id,
                                 note='员工一键撤回联系许可', actor=user)
    req.status = 'completed'
    req.resolution = '已停止联系'
    req.completed_at = now()
    db.commit()
    return {'contact_allowed': False, 'privacy_request_id': req.id, 'contact_hint': mask_contact(lead.contact)}

@router.post('/admin/privacy/retention/run')
def run_retention(user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    result = execute_retention(db, user.tenant_id)
    _record(db, user, '手动执行数据留存清理', user.tenant_id,
            details=f"status={result['status']};conversations={result.get('conversations', 0)};leads={result.get('leads', 0)};audits={result.get('audits', 0)}")
    db.commit()
    if result['status'] == 'fail':
        raise HTTPException(503, '留存清理失败，请查看最近运行状态与服务日志。')
    return result

@router.post('/admin/privacy/backup')
def create_backup(user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    path = write_encrypted_backup(db, user.tenant_id, label='manual')
    info = verify_backup_file(path)
    ok = bool(info.get('ok', True) and info.get('checksum_ok', True))
    record_backup_result(db, ok, {'file': path.name, 'counts': info.get('counts')}, user.tenant_id)
    _record(db, user, '创建逻辑备份', path.name)
    db.commit()
    return {'path': str(path), 'checksum_ok': info.get('checksum_ok', True), 'counts': info['counts']}

class RestoreInput(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    clear_first: bool = True
    reapply_privacy: bool = True

@router.post('/admin/privacy/backup/restore-drill')
def restore_drill(data: RestoreInput, user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    """Restore a logical backup into the current database for isolation drills.

    Intended for empty rehearsal databases. On the live trial DB this overwrites
    tenant business rows for conversations/leads/knowledge — use with care.
    """
    path = Path(data.path)
    if not path.is_file():
        raise HTTPException(404, '备份文件不存在')
    # Only allow files under configured backup_dir.
    from .config import settings
    root = Path(settings().backup_dir).resolve()
    try:
        path.resolve().relative_to(root)
    except ValueError:
        raise HTTPException(403, '只能从配置的备份目录恢复')
    payload = read_encrypted_backup(path)
    counts = restore_logical(db, payload, clear_first=data.clear_first)
    reapplied = 0
    if data.reapply_privacy:
        reapplied = reapply_completed_privacy(db, user.tenant_id)
    _record(db, user, '执行备份恢复演练', path.name, details=str(counts))
    db.commit()
    return {'restored': counts, 'reapplied_privacy': reapplied}

@router.post('/admin/privacy/rotate-contacts')
def rotate_contact_keys(user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    """Re-encrypt contacts with the current CONTACT_SECRET.

    Operator must set CONTACT_SECRET (new) and CONTACT_SECRET_PREVIOUS (old) in the
    environment, recreate backend, then call this endpoint or scripts/rotate_contact_secret.py.
    """
    with engine.begin() as conn:
        count = rotate_contacts(conn)
    _record(db, user, '轮换联系方式加密密钥', details=f'reencrypted={count}')
    db.commit()
    return {'reencrypted': count, 'hint': '轮换成功后请清空 CONTACT_SECRET_PREVIOUS 并重建 backend/worker'}
