"""Admin APIs for runtime status, alerts and notification channel settings."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import get_db
from .models import Admin, Alert
from .permissions import require
from .monitoring import (
    collect_status, evaluate_alerts, alert_data, notify_config, save_notify_config,
    record_backup_result, redact_log,
)
from .backup_ops import verify_backup_file
from .config import settings
from pathlib import Path

router = APIRouter()


def _record(db, user, action, target='', details=''):
    from .models import Audit
    db.add(Audit(actor=user.name, tenant_id=user.tenant_id, action=action,
                 target=(target or '')[:240], details=redact_log(details or '', 1000)))


@router.get('/admin/ops/status')
def ops_status(user: Admin = Depends(require('settings.read')), db: Session = Depends(get_db)):
    result = evaluate_alerts(db, user.tenant_id)
    db.commit()
    return result['status'] | {'open_alerts': result['open_alerts']}


@router.post('/admin/ops/evaluate')
def ops_evaluate(user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    result = evaluate_alerts(db, user.tenant_id)
    _record(db, user, '执行运维巡检', details=f"status={result['status']['status']}")
    db.commit()
    return {
        'status': result['status']['status'],
        'open_alerts': result['open_alerts'],
        'raised': result['raised'],
        'components': result['status']['components'],
        'checked_at': result['status']['checked_at'],
    }


@router.get('/admin/ops/alerts')
def list_alerts(status: str = 'open', user: Admin = Depends(require('settings.read')),
                db: Session = Depends(get_db)):
    evaluate_alerts(db, user.tenant_id)
    db.commit()
    query = select(Alert).where(Alert.tenant_id == user.tenant_id)
    if status == 'open':
        query = query.where(Alert.status.in_(['open', 'acked']))
    elif status:
        query = query.where(Alert.status == status)
    rows = db.scalars(query.order_by(Alert.last_seen_at.desc(), Alert.id.desc()).limit(200)).all()
    return {'items': [alert_data(r) for r in rows], 'total': len(rows)}


@router.post('/admin/ops/alerts/{aid}/ack')
def ack_alert(aid: str, user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    row = db.get(Alert, aid)
    if not row or row.tenant_id != user.tenant_id:
        raise HTTPException(404, '告警不存在')
    if row.status == 'resolved':
        raise HTTPException(409, '告警已恢复，无需确认')
    row.status = 'acked'
    row.ack_by = user.name
    _record(db, user, '确认运维告警', aid, row.code)
    db.commit()
    return alert_data(row)


@router.post('/admin/ops/alerts/{aid}/resolve')
def resolve_alert(aid: str, user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    row = db.get(Alert, aid)
    if not row or row.tenant_id != user.tenant_id:
        raise HTTPException(404, '告警不存在')
    from .models import now
    row.status = 'resolved'
    row.resolved_at = now()
    row.ack_by = row.ack_by or user.name
    _record(db, user, '手动关闭运维告警', aid, row.code)
    db.commit()
    return alert_data(row)


@router.get('/admin/ops/notify')
def get_notify(user: Admin = Depends(require('settings.read')), db: Session = Depends(get_db)):
    return notify_config(db, user.tenant_id)


class NotifyPatch(BaseModel):
    enabled: bool = False
    channel: Literal['none', 'email', 'webhook'] = 'none'
    email_to: str = Field('', max_length=200)
    webhook_url: str = Field('', max_length=500)


@router.put('/admin/ops/notify')
def put_notify(data: NotifyPatch, user: Admin = Depends(require('settings.write')),
               db: Session = Depends(get_db)):
    if data.enabled and data.channel == 'email':
        from .alert_mail import valid_address
        if not valid_address(data.email_to):
            raise HTTPException(422, '请填写一个有效的收件邮箱；多人接收可使用邮件组')
    cfg = save_notify_config(db, user.tenant_id, data.model_dump())
    _record(db, user, '更新运维通知渠道', details=f"enabled={cfg['enabled']} channel={cfg['channel']}")
    db.commit()
    return cfg


@router.post('/admin/ops/notify/test')
def test_email(user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    from .alert_mail import valid_address
    cfg = notify_config(db, user.tenant_id)
    if not cfg['enabled'] or cfg['channel'] != 'email' or not valid_address(cfg['email_to']):
        raise HTTPException(422, '请先保存并启用有效的邮件通知配置')
    existing = db.scalar(select(Alert).where(Alert.tenant_id==user.tenant_id, Alert.code=='smtp_test',
                                              Alert.status=='open', Alert.notify_state.in_(['pending','sending','failed'])))
    if existing:
        raise HTTPException(409, '已有渠道验证邮件待发送，请查看告警列表')
    row = Alert(tenant_id=user.tenant_id,code='smtp_test',severity='info',status='open',
                title='告警通知渠道验证',message='此邮件用于确认告警通知收件配置可正常接收。',notify_state='pending')
    db.add(row);db.flush()
    _record(db,user,'发送告警渠道验证邮件',row.id)
    db.commit()
    return {'id':row.id,'state':row.notify_state}


@router.post('/admin/ops/backup-check')
def backup_check(user: Admin = Depends(require('settings.write')), db: Session = Depends(get_db)):
    """Verify the newest logical/encrypted backup under BACKUP_DIR and record a heartbeat."""
    root = Path(settings().backup_dir)
    candidates = []
    if root.is_dir():
        candidates = sorted(
            [p for p in root.iterdir() if p.is_file() and p.name.endswith('.json.enc')],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    if not candidates:
        record_backup_result(db, False, {'reason': 'no_backup_file'}, user.tenant_id)
        evaluate_alerts(db, user.tenant_id)
        _record(db, user, '备份检查失败', details='no_backup_file')
        db.commit()
        return {'ok': False, 'detail': '逻辑备份目录中没有备份文件'}
    path = candidates[0]
    try:
        info = verify_backup_file(path)
        ok = bool(info.get('ok'))
        record_backup_result(db, ok, {'file': path.name, **{k: info.get(k) for k in ('ok', 'checksum_ok', 'counts')}},
                             user.tenant_id)
        evaluate_alerts(db, user.tenant_id)
        _record(db, user, '备份检查' + ('通过' if ok else '失败'), path.name)
        db.commit()
        return {'ok': ok, 'file': path.name, 'info': {k: info.get(k) for k in ('ok', 'checksum_ok', 'counts', 'error') if k in info}}
    except Exception as exc:
        record_backup_result(db, False, {'file': path.name, 'error': redact_log(str(exc))}, user.tenant_id)
        evaluate_alerts(db, user.tenant_id)
        _record(db, user, '备份检查失败', path.name, redact_log(str(exc)))
        db.commit()
        return {'ok': False, 'file': path.name, 'detail': '校验失败，详见告警证据'}
