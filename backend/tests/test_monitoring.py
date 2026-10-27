"""Fault monitoring: worker stale, job backlog, model errors, backup failure, log redaction."""
from datetime import timedelta
from pathlib import Path
from sqlalchemy import select
from app.db import SessionLocal
from app.models import DocumentJob, ModelCall, Message, Conversation, Alert, OpsHeartbeat, TENANT, now
from app.monitoring import (
    evaluate_alerts, touch_heartbeat, record_backup_result, redact_log, collect_status,
)


def test_redact_log_strips_secrets_and_phone():
    raw = 'Authorization: Bearer sk-abcdefghijklmnop password=super-secret 手机 13800138000'
    cleaned = redact_log(raw)
    assert 'sk-abcdefghijklmnop' not in cleaned
    assert 'super-secret' not in cleaned
    assert '13800138000' not in cleaned
    assert '***' in cleaned


def test_worker_stale_raises_and_clears(admin):
    with SessionLocal() as db:
        # Force stale heartbeat
        touch_heartbeat(db, 'worker', 'ok', {})
        row = db.get(OpsHeartbeat, 'worker')
        row.updated_at = now() - timedelta(minutes=10)
        db.add(DocumentJob(tenant_id=TENANT, kind='parse', status='queued', payload={'filename': 'a.md'}))
        db.commit()
        result = evaluate_alerts(db, TENANT)
        db.commit()
        assert result['status']['components']['worker']['status'] == 'fail'
        open_codes = {a.code for a in db.scalars(select(Alert).where(Alert.status.in_(['open', 'acked']))).all()}
        assert 'worker_stale' in open_codes

        touch_heartbeat(db, 'worker', 'ok', {'recovered': True})
        evaluate_alerts(db, TENANT)
        db.commit()
        open_codes = {a.code for a in db.scalars(select(Alert).where(Alert.status.in_(['open', 'acked']))).all()}
        assert 'worker_stale' not in open_codes

    status = admin.get('/api/admin/ops/status')
    assert status.status_code == 200
    body = status.json()
    assert body['components']['worker']['status'] == 'ok'
    assert 'open_alerts' in body


def test_job_backlog_and_failures_alert(admin):
    with SessionLocal() as db:
        for i in range(5):
            db.add(DocumentJob(tenant_id=TENANT, kind='index', status='queued', payload={'version': i}))
        for i in range(3):
            db.add(DocumentJob(
                tenant_id=TENANT, kind='parse', status='failed', attempts=3,
                error='模拟失败', stage='处理失败', payload={'filename': f'f{i}.md'},
            ))
        db.commit()
        result = evaluate_alerts(db, TENANT)
        db.commit()
        assert result['status']['components']['jobs']['status'] == 'fail'
        assert db.scalar(select(Alert).where(Alert.code == 'job_backlog', Alert.status == 'open'))


def test_model_timeout_error_rate_alert(admin):
    with SessionLocal() as db:
        conv = Conversation(token_hash='monitor-test-' + 'x' * 40, source_page='监控测试')
        db.add(conv)
        db.flush()
        for i in range(4):
            msg = Message(conversation_id=conv.id, role='assistant', content='x', state='failed')
            db.add(msg)
            db.flush()
            db.add(ModelCall(
                message_id=msg.id, tenant_id=TENANT, model='deepseek-flash',
                error_type='timeout', state='failed', latency_ms=60000,
            ))
        db.commit()
        result = evaluate_alerts(db, TENANT)
        db.commit()
        assert result['status']['components']['model']['status'] == 'fail'
        assert result['status']['components']['model']['timeouts_1h'] >= 2
        assert db.scalar(select(Alert).where(Alert.code == 'model_errors', Alert.status.in_(['open', 'acked'])))

    listed = admin.get('/api/admin/ops/alerts?status=open')
    assert listed.status_code == 200
    codes = {item['code'] for item in listed.json()['items']}
    assert 'model_errors' in codes


def test_backup_failure_alert_and_notify_stub(admin, tmp_path, monkeypatch):
    monkeypatch.setenv('BACKUP_DIR', str(tmp_path))
    from app.config import settings
    settings.cache_clear()
    settings().backup_dir = str(tmp_path)

    # No backup file -> check endpoint records failure
    r = admin.post('/api/admin/ops/backup-check')
    assert r.status_code == 200
    assert r.json()['ok'] is False

    with SessionLocal() as db:
        assert db.get(OpsHeartbeat, 'backup').status == 'fail'
        assert db.scalar(select(Alert).where(Alert.code == 'backup_failed', Alert.status.in_(['open', 'acked'])))

    # Notify channel remains off by default
    cfg = admin.get('/api/admin/ops/notify')
    assert cfg.status_code == 200
    assert cfg.json()['enabled'] is False
    put = admin.put('/api/admin/ops/notify', json={
        'enabled': True, 'channel': 'email', 'email_to': 'ops@example.com', 'webhook_url': '',
    })
    assert put.status_code == 200
    assert put.json()['enabled'] is True
    assert put.json()['channel'] == 'email'

    # Re-evaluate: dispatch stays queued/skipped-safe (no real outbound)
    with SessionLocal() as db:
        record_backup_result(db, False, {'reason': 'still_bad'})
        evaluate_alerts(db, TENANT)
        db.commit()
        alert = db.scalar(select(Alert).where(Alert.code == 'backup_failed', Alert.status.in_(['open', 'acked'])))
        assert alert.notify_state in ('queued', 'skipped', 'pending')

    # Clear failure
    with SessionLocal() as db:
        record_backup_result(db, True, {'file': 'ok.json.enc'})
        (tmp_path / 'ok.json.enc').write_bytes(b'placeholder')
        evaluate_alerts(db, TENANT)
        db.commit()
        open_backup = db.scalar(select(Alert).where(Alert.code == 'backup_failed', Alert.status.in_(['open', 'acked'])))
        assert open_backup is None


def test_public_health_includes_checks(client):
    r = client.get('/api/health')
    assert r.status_code == 200
    body = r.json()
    assert body['status'] in ('ok', 'degraded', 'fail')
    assert 'database' in body['checks']
    assert 'worker' in body['checks']
    assert 'schema' in body['checks']
    assert body['schema_version']['current'] is True
    assert body['schema_version']['applied'] >= 1


def test_ack_and_resolve_alert(admin):
    with SessionLocal() as db:
        touch_heartbeat(db, 'worker', 'ok', {})
        row = db.get(OpsHeartbeat, 'worker')
        row.updated_at = now() - timedelta(minutes=30)
        evaluate_alerts(db, TENANT)
        db.commit()
        alert = db.scalar(select(Alert).where(Alert.code == 'worker_stale', Alert.status == 'open'))
        assert alert
        aid = alert.id
    ack = admin.post(f'/api/admin/ops/alerts/{aid}/ack')
    assert ack.status_code == 200 and ack.json()['status'] == 'acked'
    # Manual resolve even if condition still true
    resolved = admin.post(f'/api/admin/ops/alerts/{aid}/resolve')
    assert resolved.status_code == 200 and resolved.json()['status'] == 'resolved'
