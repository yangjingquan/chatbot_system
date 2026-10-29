"""Durable, leased delivery of operational alerts through encrypted SMTP."""
from __future__ import annotations

import re
import smtplib
import ssl
import uuid
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .models import Alert, TENANT, now
from .monitoring import notify_config, redact_log

_ADDRESS = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
LEASE_SECONDS = 300


def valid_address(value: str) -> bool:
    value = value.strip()
    if len(value) > 200 or not _ADDRESS.fullmatch(value):
        return False
    local, domain = value.rsplit('@', 1)
    return not (local.startswith('.') or local.endswith('.') or '..' in local or '..' in domain)


class MailConfigurationError(Exception):
    pass


def _mail_settings():
    cfg = settings()
    if not cfg.smtp_host.strip() or not valid_address(cfg.smtp_from):
        raise MailConfigurationError('SMTP 服务器地址或发件邮箱未配置')
    if cfg.smtp_security not in ('ssl', 'starttls') or not 1 <= cfg.smtp_port <= 65535:
        raise MailConfigurationError('SMTP 端口或加密方式无效')
    if bool(cfg.smtp_username) != bool(cfg.smtp_password):
        raise MailConfigurationError('SMTP 用户名和授权码需同时配置')
    return cfg


def send_alert_email(alert: dict, recipient: str):
    cfg = _mail_settings()
    if not valid_address(recipient):
        raise MailConfigurationError('收件邮箱无效')
    msg = EmailMessage()
    msg['From'] = cfg.smtp_from.strip()
    msg['To'] = recipient.strip()
    prefix = '知行客服恢复' if alert['event'] == 'recovery' else '知行客服告警'
    msg['Subject'] = f"[{prefix}] {redact_log(alert['title'], 120)}"
    msg['Date'] = formatdate(localtime=False)
    msg['Message-ID'] = make_msgid(idstring=alert['id'])
    msg.set_content(
        f"{'已恢复' if alert['event'] == 'recovery' else '告警'}：{redact_log(alert['title'], 200)}\n"
        f"级别：{alert['severity']}\n"
        f"详情：{redact_log(alert['message'], 500)}\n"
        f"首次发现（UTC）：{alert['first_seen_at']}\n"
        f"告警编号：{alert['id']}\n\n"
        '请登录工作台的运行监控查看并处理。\n'
    )
    context = ssl.create_default_context()
    timeout = max(1, min(cfg.smtp_timeout_seconds, 30))
    if cfg.smtp_security == 'ssl':
        client = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=timeout, context=context)
    else:
        client = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=timeout)
    with client:
        if cfg.smtp_security == 'starttls':
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        if cfg.smtp_username:
            client.login(cfg.smtp_username, cfg.smtp_password)
        refused = client.send_message(msg, from_addr=cfg.smtp_from.strip(), to_addrs=[recipient.strip()])
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)


def _failure_message(exc: Exception) -> str:
    # Never persist raw SMTP responses: servers can echo credentials or addresses.
    if isinstance(exc, MailConfigurationError):
        return str(exc)
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return 'SMTP 身份验证失败，请检查账号和授权码'
    if isinstance(exc, ssl.SSLError):
        return 'SMTP TLS 连接或证书验证失败'
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return 'SMTP 服务器拒绝收件地址'
    if isinstance(exc, (TimeoutError, OSError, smtplib.SMTPServerDisconnected)):
        return 'SMTP 连接失败或超时，请检查网络和服务器配置'
    if isinstance(exc, smtplib.SMTPException):
        return 'SMTP 服务器拒绝发送，请检查服务配置'
    return '邮件发送失败，请检查服务器配置'


def claim_email(db: Session, tenant_id: str = TENANT) -> dict | None:
    cfg = notify_config(db, tenant_id)
    if not cfg['enabled'] or cfg['channel'] != 'email':
        return None
    stamp = now()
    query = select(Alert).where(
        Alert.tenant_id == tenant_id,
        or_(Alert.status.in_(['open', 'acked']), and_(Alert.status == 'resolved', Alert.notify_event == 'recovery')),
        or_(
            (Alert.notify_state.in_(['pending', 'queued', 'failed']) &
             or_(Alert.notify_next_attempt_at.is_(None), Alert.notify_next_attempt_at <= stamp)),
            (Alert.notify_state == 'sending') & (Alert.notify_lease_until <= stamp),
        ),
    ).order_by(Alert.first_seen_at, Alert.id).limit(1).with_for_update(skip_locked=True)
    row = db.scalar(query)
    if row is None:
        return None
    token = uuid.uuid4().hex
    row.notify_state = 'sending'
    row.notify_token = token
    row.notify_lease_until = stamp + timedelta(seconds=LEASE_SECONDS)
    row.notify_attempts = (row.notify_attempts or 0) + 1
    db.flush()
    return {'id': row.id, 'token': token, 'tenant_id': tenant_id, 'recipient': cfg['email_to'], 'event': row.notify_event,
            'title': row.title, 'severity': row.severity, 'message': row.message,
            'first_seen_at': row.first_seen_at.isoformat()}


def finish_email(db: Session, job: dict, error: Exception | None) -> bool:
    row = db.scalar(select(Alert).where(Alert.id == job['id'], Alert.tenant_id == job['tenant_id'],
                                       Alert.notify_state == 'sending', Alert.notify_token == job['token']).with_for_update())
    if row is None:
        return False
    row.notify_token = None
    row.notify_lease_until = None
    if error:
        if row.status == 'resolved' and job['event'] == 'alert':
            row.notify_state = 'skipped'
            row.notify_last_error = ''
            row.notify_next_attempt_at = None
        else:
            row.notify_state = 'failed'
            row.notify_last_error = _failure_message(error)
            row.notify_next_attempt_at = now() + timedelta(seconds=min(1800, 60 * 2 ** min(row.notify_attempts - 1, 5)))
    else:
        row.notify_state = 'sent'
        row.notify_sent_at = now()
        row.notify_last_error = ''
        row.notify_next_attempt_at = None
        if row.code == 'smtp_test':
            row.status = 'resolved'
            row.resolved_at = now()
        elif row.status == 'resolved' and job['event'] == 'alert':
            # Recovery raced with SMTP acceptance: queue the recovery as well.
            row.notify_event = 'recovery'
            row.notify_state = 'pending'
    db.flush()
    return True
