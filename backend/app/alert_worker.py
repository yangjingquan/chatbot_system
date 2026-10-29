"""Periodic alert evaluation and SMTP delivery, isolated from document processing."""
import time
from sqlalchemy import select
from .alert_mail import MailConfigurationError, _mail_settings, claim_email, finish_email, send_alert_email
from .config import settings
from .db import SessionLocal, engine
from .models import Alert, Company
from .monitoring import evaluate_alerts, notify_config, redact_log, touch_heartbeat


def run_cycle():
    with SessionLocal() as db:
        tenants = list(db.scalars(select(Company.id)))
    for tenant in tenants:
        with SessionLocal() as db:
            # API latency samples are process-local: only the API may evaluate them.
            evaluate_alerts(db, tenant, include_latency=False)
            try:
                _mail_settings()
                configured = True
            except MailConfigurationError:
                configured = False
            touch_heartbeat(db, 'alert-monitor', 'ok', {'smtp_configured': configured}, tenant_id=tenant)
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
            print('告警巡检暂不可用，将稍后重试：' + redact_log(type(exc).__name__, 120), flush=True)
        time.sleep(interval)


if __name__ == '__main__':
    main()
