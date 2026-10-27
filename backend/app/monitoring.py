"""Runtime health probes, alert evaluation and optional notify hooks.

Metrics land in the DB (alerts / heartbeats) and in an in-process latency ring.
External email / webhook channels are configurable but default to disabled —
operators must set them explicitly; this module never forces outbound delivery.
"""
from __future__ import annotations
import re
import shutil
import threading
import time
from collections import deque
from datetime import timedelta
from pathlib import Path
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session
from .config import settings
from .models import (
    Alert, OpsHeartbeat, DocumentJob, ModelCall, Company, TENANT, now,
)
from .privacy import redact_text
from .document_service import aware

# --- in-process request latency samples (API process only) ---
_latency_guard = threading.Lock()
_latency_samples: deque[float] = deque(maxlen=200)

WORKER_STALE_SECONDS = 180
JOB_BACKLOG_WARN = 5
JOB_FAILED_WINDOW = timedelta(hours=1)
MODEL_WINDOW = timedelta(hours=1)
MODEL_ERROR_MIN_CALLS = 3
MODEL_ERROR_RATE = 0.3
MODEL_TIMEOUT_MIN = 2
LATENCY_WARN_MS = 8000
DISK_WARN_PCT = 90
BACKUP_STALE_HOURS = 48

SECRET_RE = re.compile(
    r'(?i)((?:authorization\s*[:=]\s*bearer\s+)|(?:api[_-]?key\s*[:=]\s*)|(?:password\s*[:=]\s*)|(?:(?:session_secret|contact_secret|postgres_password)\s*[:=]\s*))\S+'
    r'|(sk-[A-Za-z0-9]{8,})'
)


def redact_log(text_value: str | None, limit: int = 400) -> str:
    """Strip secrets and common PII before any ops log / alert evidence string."""
    if not text_value:
        return ''
    def _mask(match: re.Match) -> str:
        if match.group(1):
            return match.group(1) + '***'
        return 'sk-***'
    cleaned = SECRET_RE.sub(_mask, str(text_value))
    cleaned = redact_text(cleaned)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + '…'
    return cleaned


def record_request_latency(ms: float):
    with _latency_guard:
        _latency_samples.append(float(ms))


def latency_stats() -> dict:
    with _latency_guard:
        values = list(_latency_samples)
    if not values:
        return {'count': 0, 'p50_ms': None, 'p95_ms': None, 'max_ms': None}
    ordered = sorted(values)
    def pct(p):
        idx = min(len(ordered) - 1, max(0, int(round((p / 100) * (len(ordered) - 1)))))
        return round(ordered[idx], 1)
    return {
        'count': len(ordered),
        'p50_ms': pct(50),
        'p95_ms': pct(95),
        'max_ms': round(ordered[-1], 1),
    }


def touch_heartbeat(db: Session, name: str, status: str = 'ok', detail: dict | None = None,
                    tenant_id: str = TENANT) -> OpsHeartbeat:
    row = db.get(OpsHeartbeat, name)
    if not row:
        row = OpsHeartbeat(name=name, tenant_id=tenant_id)
        db.add(row)
    row.tenant_id = tenant_id
    row.status = status
    row.detail = detail or {}
    row.updated_at = now()
    db.flush()
    return row


def _iso(value):
    return value.isoformat() if value is not None else None


def check_database(db: Session) -> dict:
    try:
        db.execute(text('SELECT 1'))
        return {'status': 'ok', 'detail': 'database reachable'}
    except Exception as exc:
        return {'status': 'fail', 'detail': redact_log(str(exc), 160)}


def check_redis() -> dict:
    url = settings().redis_url
    if not url:
        return {'status': 'skipped', 'detail': 'REDIS_URL not configured; in-memory rate limit is used'}
    try:
        from .security import redis_client
        client = redis_client()
        if client is None:
            return {'status': 'skipped', 'detail': 'redis client unavailable'}
        pong = client.ping()
        return {'status': 'ok' if pong else 'fail', 'detail': 'PONG' if pong else 'no PONG'}
    except Exception as exc:
        return {'status': 'fail', 'detail': redact_log(str(exc), 160)}


def check_worker(db: Session, tenant_id: str = TENANT) -> dict:
    row = db.get(OpsHeartbeat, 'worker')
    if not row or row.tenant_id != tenant_id:
        queued = db.scalar(
            select(func.count()).select_from(DocumentJob).where(
                DocumentJob.tenant_id == tenant_id, DocumentJob.status.in_(['queued', 'running']))
        ) or 0
        return {
            'status': 'unknown' if queued == 0 else 'fail',
            'detail': '尚无 worker 心跳' + (f'，且有 {queued} 个排队/处理中任务' if queued else ''),
            'updated_at': None,
            'queued_or_running': queued,
        }
    age = (now() - aware(row.updated_at)).total_seconds() if row.updated_at else 1e9
    status = 'ok' if age <= WORKER_STALE_SECONDS else 'fail'
    return {
        'status': status,
        'detail': f'heartbeat {int(age)}s ago' if status == 'ok' else f'worker 心跳已过期（{int(age)}s）',
        'updated_at': _iso(row.updated_at),
        'age_seconds': int(age),
        'heartbeat_status': row.status,
        'heartbeat_detail': row.detail or {},
    }


def check_jobs(db: Session, tenant_id: str = TENANT) -> dict:
    queued = db.scalar(
        select(func.count()).select_from(DocumentJob).where(
            DocumentJob.tenant_id == tenant_id, DocumentJob.status == 'queued')
    ) or 0
    running = db.scalar(
        select(func.count()).select_from(DocumentJob).where(
            DocumentJob.tenant_id == tenant_id, DocumentJob.status == 'running')
    ) or 0
    since = now() - JOB_FAILED_WINDOW
    failed = db.scalar(
        select(func.count()).select_from(DocumentJob).where(
            DocumentJob.tenant_id == tenant_id, DocumentJob.status == 'failed',
            DocumentJob.updated_at >= since)
    ) or 0
    recent_fail = db.scalars(
        select(DocumentJob).where(
            DocumentJob.tenant_id == tenant_id, DocumentJob.status == 'failed',
            DocumentJob.updated_at >= since
        ).order_by(DocumentJob.updated_at.desc()).limit(5)
    ).all()
    status = 'ok'
    if queued >= JOB_BACKLOG_WARN or failed >= 3:
        status = 'fail'
    elif queued > 0 or failed > 0:
        status = 'warn'
    return {
        'status': status,
        'queued': queued,
        'running': running,
        'failed_1h': failed,
        'recent_failures': [
            {'id': j.id, 'kind': j.kind, 'stage': j.stage, 'error': redact_log(j.error, 120),
             'updated_at': _iso(j.updated_at)}
            for j in recent_fail
        ],
    }


def check_model(db: Session, tenant_id: str = TENANT) -> dict:
    since = now() - MODEL_WINDOW
    rows = db.scalars(
        select(ModelCall).where(ModelCall.tenant_id == tenant_id, ModelCall.created_at >= since)
    ).all()
    total = len(rows)
    errors = [r for r in rows if r.error_type or r.state not in ('completed', 'pending')]
    timeouts = [r for r in errors if r.error_type == 'timeout']
    rate = (len(errors) / total) if total else 0.0
    latencies = [r.latency_ms for r in rows if r.latency_ms and r.state == 'completed']
    avg_ms = round(sum(latencies) / len(latencies), 1) if latencies else None
    status = 'ok'
    if total >= MODEL_ERROR_MIN_CALLS and rate >= MODEL_ERROR_RATE:
        status = 'fail'
    elif len(timeouts) >= MODEL_TIMEOUT_MIN:
        status = 'fail'
    elif errors:
        status = 'warn'
    return {
        'status': status,
        'calls_1h': total,
        'errors_1h': len(errors),
        'timeouts_1h': len(timeouts),
        'error_rate': round(rate, 3),
        'avg_latency_ms': avg_ms,
        'error_types': sorted({r.error_type for r in errors if r.error_type}),
    }


def check_disk() -> dict:
    path = Path(settings().data_dir)
    try:
        path.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(path)
        used_pct = round(100 * usage.used / usage.total, 1) if usage.total else 0
        status = 'fail' if used_pct >= DISK_WARN_PCT else 'ok'
        return {
            'status': status,
            'path': str(path),
            'used_pct': used_pct,
            'free_gb': round(usage.free / (1024 ** 3), 2),
            'total_gb': round(usage.total / (1024 ** 3), 2),
        }
    except Exception as exc:
        return {'status': 'fail', 'detail': redact_log(str(exc), 160)}


def check_backup(db: Session, tenant_id: str = TENANT) -> dict:
    row = db.get(OpsHeartbeat, 'backup')
    root = Path(settings().backup_dir)
    files = []
    try:
        if root.is_dir():
            files = sorted(
                [p for p in root.iterdir() if p.is_file() and p.suffix in ('.bin', '.enc', '.gz', '.dump', '.sql')],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
    except Exception as exc:
        return {'status': 'fail', 'detail': redact_log(str(exc), 160)}

    latest = files[0] if files else None
    heartbeat = {
        'status': row.status if row else 'unknown',
        'updated_at': _iso(row.updated_at) if row else None,
        'detail': (row.detail if row else {}) or {},
    }
    if row and row.status == 'fail':
        return {
            'status': 'fail',
            'detail': '最近一次备份检查失败',
            'heartbeat': heartbeat,
            'latest_file': latest.name if latest else None,
        }
    if latest:
        age_h = (time.time() - latest.stat().st_mtime) / 3600
        status = 'ok' if age_h <= BACKUP_STALE_HOURS else 'warn'
        return {
            'status': status,
            'detail': f'最新备份 {latest.name}（约 {age_h:.1f} 小时前）',
            'latest_file': latest.name,
            'age_hours': round(age_h, 1),
            'heartbeat': heartbeat,
        }
    if row and row.status == 'ok':
        return {'status': 'ok', 'detail': '有备份心跳记录', 'heartbeat': heartbeat, 'latest_file': None}
    return {
        'status': 'warn',
        'detail': '尚未发现备份文件；请配置主机定时备份或在后台创建逻辑备份',
        'heartbeat': heartbeat,
        'latest_file': None,
    }


def collect_status(db: Session, tenant_id: str = TENANT) -> dict:
    lat = latency_stats()
    lat_status = 'fail' if (lat.get('p95_ms') or 0) >= LATENCY_WARN_MS else 'ok'
    components = {
        'api': {'status': 'ok', 'detail': 'API process responding'},
        'database': check_database(db),
        'redis': check_redis(),
        'worker': check_worker(db, tenant_id),
        'jobs': check_jobs(db, tenant_id),
        'model': check_model(db, tenant_id),
        'latency': {**lat, 'status': lat_status},
        'disk': check_disk(),
        'backup': check_backup(db, tenant_id),
    }
    ranks = {'ok': 0, 'skipped': 0, 'unknown': 1, 'warn': 2, 'fail': 3}
    worst = max((ranks.get(c.get('status', 'ok'), 0) for c in components.values()), default=0)
    overall = {0: 'ok', 1: 'ok', 2: 'degraded', 3: 'fail'}[worst]
    return {
        'status': overall,
        'version': '0.4.0',
        'checked_at': _iso(now()),
        'components': components,
    }


ALERT_DEFS = {
    'worker_stale': ('critical', '文档任务 Worker 无心跳或已过期'),
    'job_backlog': ('warning', '文档任务积压或近期失败偏多'),
    'model_errors': ('critical', '模型调用错误率偏高或超时增多'),
    'latency_high': ('warning', 'API 请求耗时偏高'),
    'disk_low': ('critical', '数据盘可用空间不足'),
    'backup_failed': ('critical', '备份检查失败或备份过旧'),
    'redis_down': ('warning', 'Redis 不可用，已回退内存限流'),
    'database_down': ('critical', '数据库连接异常'),
}


def _upsert_alert(db: Session, tenant_id: str, code: str, message: str, evidence: dict,
                  active: bool) -> Alert | None:
    severity, title = ALERT_DEFS[code]
    existing = db.scalar(
        select(Alert).where(
            Alert.tenant_id == tenant_id, Alert.code == code, Alert.status.in_(['open', 'acked'])
        ).order_by(Alert.first_seen_at.desc()).limit(1)
    )
    if active:
        if existing:
            existing.last_seen_at = now()
            existing.message = message
            existing.evidence = evidence
            existing.severity = severity
            return existing
        row = Alert(
            tenant_id=tenant_id, code=code, severity=severity, status='open',
            title=title, message=message, evidence=evidence, notify_state='pending',
        )
        db.add(row)
        db.flush()
        return row
    if existing and existing.status == 'open':
        existing.status = 'resolved'
        existing.resolved_at = now()
        existing.last_seen_at = now()
        existing.message = message or existing.message
        existing.evidence = evidence or existing.evidence
        return existing
    return existing


def evaluate_alerts(db: Session, tenant_id: str = TENANT) -> dict:
    status = collect_status(db, tenant_id)
    c = status['components']
    raised = []

    def bump(code, active, message, evidence):
        row = _upsert_alert(db, tenant_id, code, message, evidence, active)
        if active and row:
            raised.append(row)

    bump('database_down', c['database']['status'] == 'fail',
         c['database'].get('detail', '数据库异常'), c['database'])
    bump('redis_down', c['redis']['status'] == 'fail',
         c['redis'].get('detail', 'Redis 异常'), c['redis'])
    bump('worker_stale', c['worker']['status'] == 'fail',
         c['worker'].get('detail', 'Worker 异常'), c['worker'])
    jobs = c['jobs']
    bump('job_backlog', jobs['status'] == 'fail',
         f"排队 {jobs['queued']}，近 1 小时失败 {jobs['failed_1h']}", jobs)
    model = c['model']
    bump('model_errors', model['status'] == 'fail',
         f"近 1 小时错误 {model['errors_1h']}/{model['calls_1h']}，超时 {model['timeouts_1h']}", model)
    lat = c['latency']
    bump('latency_high', lat.get('status') == 'fail',
         f"p95={lat.get('p95_ms')}ms（样本 {lat.get('count')}）", lat)
    disk = c['disk']
    bump('disk_low', disk['status'] == 'fail',
         f"磁盘已用 {disk.get('used_pct')}%，剩余 {disk.get('free_gb')} GB", disk)
    backup = c['backup']
    bump('backup_failed', backup['status'] == 'fail',
         backup.get('detail', '备份异常'), backup)

    for row in raised:
        if row.notify_state == 'pending':
            result = dispatch_alert(db, row)
            row.notify_state = result['state']
            evidence = dict(row.evidence or {})
            evidence['notify'] = result
            row.evidence = evidence

    db.flush()
    open_count = db.scalar(
        select(func.count()).select_from(Alert).where(
            Alert.tenant_id == tenant_id, Alert.status.in_(['open', 'acked']))
    ) or 0
    return {'status': status, 'open_alerts': open_count, 'raised': len(raised)}


def notify_config(db: Session, tenant_id: str = TENANT) -> dict:
    company = db.get(Company, tenant_id)
    cfg = (company.config if company else {}) or {}
    raw = cfg.get('_ops_notify') or {}
    return {
        'enabled': bool(raw.get('enabled')),
        'channel': raw.get('channel') or 'none',  # none | email | webhook
        'email_to': raw.get('email_to') or '',
        'webhook_url': raw.get('webhook_url') or '',
        'note': '默认不外发。启用前请确认收件邮箱或企业 Webhook，并核对是否允许出站。',
    }


def save_notify_config(db: Session, tenant_id: str, data: dict) -> dict:
    company = db.get(Company, tenant_id)
    clean = {
        'enabled': bool(data.get('enabled')),
        'channel': data.get('channel') if data.get('channel') in ('none', 'email', 'webhook') else 'none',
        'email_to': (data.get('email_to') or '')[:200],
        'webhook_url': (data.get('webhook_url') or '')[:500],
    }
    company.config = {**company.config, '_ops_notify': clean}
    return notify_config(db, tenant_id)


def dispatch_alert(db: Session, alert: Alert) -> dict:
    """Reserve email / webhook channel. Default: skip outbound delivery."""
    cfg = notify_config(db, alert.tenant_id)
    if not cfg['enabled'] or cfg['channel'] in ('', 'none'):
        return {
            'state': 'skipped',
            'reason': '通知渠道未启用（需管理员在运维告警配置中打开）',
            'channel': cfg['channel'],
        }
    # Intentionally do not send yet: keep a safe stub so production can wire SMTP/webhook later.
    return {
        'state': 'queued',
        'reason': '渠道已配置，本版仅登记待发送，不自动外发（需用户配置并验收渠道）',
        'channel': cfg['channel'],
        'target': redact_log(cfg.get('email_to') or cfg.get('webhook_url') or '', 80),
    }


def alert_data(row: Alert) -> dict:
    return {
        'id': row.id,
        'code': row.code,
        'severity': row.severity,
        'status': row.status,
        'title': row.title,
        'message': row.message,
        'evidence': row.evidence,
        'first_seen_at': _iso(row.first_seen_at),
        'last_seen_at': _iso(row.last_seen_at),
        'resolved_at': _iso(row.resolved_at),
        'ack_by': row.ack_by,
        'notify_state': row.notify_state,
    }


def record_backup_result(db: Session, ok: bool, detail: dict | None = None, tenant_id: str = TENANT):
    safe = {k: (redact_log(str(v), 120) if isinstance(v, str) else v) for k, v in (detail or {}).items()}
    touch_heartbeat(db, 'backup', 'ok' if ok else 'fail', safe, tenant_id=tenant_id)
