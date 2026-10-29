"""Periodic alert evaluation and SMTP delivery, isolated from document processing."""
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from sqlalchemy import select
from .alert_mail import MailConfigurationError, _mail_settings, claim_email, finish_email, send_alert_email
from .config import settings
from .db import SessionLocal, engine
from .models import Alert, Company
from .monitoring import evaluate_alerts, get_heartbeat, notify_config, redact_log, touch_heartbeat


def probe_api():
    try:
        cfg=settings()
        host=urlsplit(cfg.public_origin).netloc
        request=Request(cfg.alert_api_health_url,headers={'Host':host} if host else {})
        with urlopen(request, timeout=3) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def record_probe(db, tenant, reachable):
    row=get_heartbeat(db, 'api-probe', tenant)
    failures=0 if reachable else int((row.detail or {}).get('failures',0) if row and row.tenant_id==tenant else 0)+1
    status='ok' if reachable else 'fail' if failures>=2 else 'warn'
    return touch_heartbeat(db,'api-probe',status,{'failures':failures,
                           'detail':'API 探测正常' if reachable else f'API 探测连续失败 {failures} 次'},tenant_id=tenant)


def record_cycle_failure(exc):
    """Persist a safe error only if the database is still reachable."""
    try:
        with SessionLocal() as db:
            for tenant in db.scalars(select(Company.id)):
                previous=get_heartbeat(db,'alert-monitor',tenant)
                detail=dict(previous.detail or {}) if previous and previous.tenant_id==tenant else {}
                detail['last_error']=redact_log(type(exc).__name__,120)
                touch_heartbeat(db,'alert-monitor','fail',detail,tenant_id=tenant)
            db.commit()
    except Exception:
        pass


def run_cycle():
    with SessionLocal() as db:
        tenants = list(db.scalars(select(Company.id)))
    reachable = probe_api()
    for tenant in tenants:
        with SessionLocal() as db:
            record_probe(db, tenant, reachable)
            try:
                _mail_settings()
                configured = True
            except MailConfigurationError:
                configured = False
            touch_heartbeat(db, 'alert-monitor', 'ok', {'smtp_configured': configured}, tenant_id=tenant)
            evaluate_alerts(db, tenant)
            db.commit()
        for _ in range(20):
            with SessionLocal() as db:
                job = claim_email(db, tenant)
                db.commit()
            if not job:
                break
            error = None
            try:
                with SessionLocal() as db:
                    row = db.get(Alert, job['id'])
                    if not row or (row.status not in ('open', 'acked') and not (row.status == 'resolved' and row.notify_event == 'recovery')) or row.notify_token != job['token']:
                        continue
                    cfg = notify_config(db, tenant)
                    if not cfg['enabled'] or cfg['channel'] != 'email' or cfg['email_to'] != job['recipient']:
                        row.notify_state = 'pending'
                        row.notify_token = None
                        row.notify_lease_until = None
                        db.commit()
                        continue
                send_alert_email(job, job['recipient'])
            except Exception as exc:
                error = exc
            with SessionLocal() as db:
                finish_email(db, job, error)
                db.commit()


def main():
    from .migrations import migrate_schema
    migrate_schema(engine)
    interval = max(5, min(settings().alert_monitor_interval_seconds, 3600))
    while True:
        try:
            run_cycle()
        except Exception as exc:
            record_cycle_failure(exc)
            print('告警巡检暂不可用，将稍后重试：' + redact_log(type(exc).__name__, 120), flush=True)
        time.sleep(interval)


if __name__ == '__main__':
    main()
