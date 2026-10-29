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


def test_email_delivery_retries_and_recovery_is_queued(monkeypatch):
    import smtplib
    import uuid
    from app.models import Company
    from app.alert_mail import claim_email, finish_email
    from app.monitoring import _upsert_alert
    tenant = 'mail-test-' + uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(Company(id=tenant,config={'_ops_notify':{'enabled':True,'channel':'email','email_to':'ops@example.invalid'}}))
        row = _upsert_alert(db,tenant,'disk_low','空间不足',{},True)
        db.commit(); aid=row.id
    with SessionLocal() as db:
        job=claim_email(db,tenant);db.commit()
        assert job['id']==aid and job['event']=='alert'
    with SessionLocal() as db:
        assert claim_email(db,tenant) is None
        # A stale worker cannot acknowledge another lease.
        assert not finish_email(db,{**job,'token':'stale'},None)
        assert finish_email(db,job,smtplib.SMTPAuthenticationError(535,b'secret-password'))
        db.commit()
    with SessionLocal() as db:
        row=db.get(Alert,aid)
        assert row.notify_state=='failed' and row.notify_attempts==1
        assert 'secret-password' not in row.notify_last_error and row.notify_next_attempt_at
        assert claim_email(db,tenant) is None
        row.notify_next_attempt_at=now()-timedelta(seconds=1);db.commit()
    with SessionLocal() as db:
        job=claim_email(db,tenant);db.commit()
    with SessionLocal() as db:
        assert finish_email(db,job,None);db.commit()
        assert db.get(Alert,aid).notify_state=='sent'
        _upsert_alert(db,tenant,'disk_low','空间已恢复',{},False);db.commit()
        row=db.get(Alert,aid)
        assert row.status=='resolved' and row.notify_event=='recovery' and row.notify_state=='pending'
    with SessionLocal() as db:
        recovery=claim_email(db,tenant);db.commit()
        assert recovery['event']=='recovery'
    with SessionLocal() as db:
        assert finish_email(db,recovery,None);db.commit()
        assert db.get(Alert,aid).notify_state=='sent'


def test_smtp_uses_tls_and_does_not_embed_private_data(monkeypatch):
    import ssl
    from app.config import settings
    from app.alert_mail import send_alert_email
    cfg=settings()
    for key,value in {'smtp_host':'smtp.example.invalid','smtp_port':587,'smtp_security':'starttls',
                      'smtp_from':'sender@example.invalid','smtp_username':'sender','smtp_password':'secret-password'}.items():
        monkeypatch.setattr(cfg,key,value)
    calls=[]
    class SMTP:
        def __init__(self,host,port,timeout):calls.append(('connect',host,port))
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def ehlo(self):calls.append(('ehlo',))
        def starttls(self,context):assert isinstance(context,ssl.SSLContext);calls.append(('tls',))
        def login(self,user,password):calls.append(('login',user,password))
        def send_message(self,msg,from_addr,to_addrs):
            calls.append(('send',msg,from_addr,to_addrs));return {}
    monkeypatch.setattr('smtplib.SMTP',SMTP)
    send_alert_email({'id':'alert-1','event':'alert','title':'故障','severity':'critical',
                      'message':'password=very-secret 手机 13800138000','first_seen_at':'2026-10-28T00:00:00Z'},'ops@example.invalid')
    assert [call[0] for call in calls]==['connect','ehlo','tls','ehlo','login','send']
    message=calls[-1][1]
    assert 'very-secret' not in message.get_content() and '13800138000' not in message.get_content()
    assert calls[-1][3]==['ops@example.invalid']


def test_email_configuration_and_test_request_permissions(admin):
    from test_permissions import employee
    assert admin.put('/api/admin/ops/notify',json={'enabled':True,'channel':'email','email_to':'a@example.invalid,b@example.invalid'}).status_code==422
    assert admin.put('/api/admin/ops/notify',json={'enabled':True,'channel':'email','email_to':'ops@example.invalid'}).status_code==200
    _,client=employee(admin,'support')
    assert client.post('/api/admin/ops/notify/test').status_code==403
    response=admin.post('/api/admin/ops/notify/test')
    assert response.status_code==200 and response.json()['state']=='pending'
    assert admin.post('/api/admin/ops/notify/test').status_code==409
    # Reset shared tenant configuration so later tests never schedule real mail.
    admin.put('/api/admin/ops/notify',json={'enabled':False,'channel':'none'})


def test_smtp_ssl_delivery_to_local_tls_server(client, tmp_path, monkeypatch):
    """Exercise the actual SMTP protocol and certificate validation without external mail."""
    import ipaddress
    import socket
    import ssl
    import threading
    import uuid
    from datetime import datetime, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from app.config import settings
    from app.alert_mail import claim_email, finish_email, send_alert_email
    from app.models import Company

    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    current=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(current-timedelta(minutes=5))
          .not_valid_after(current+timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
          .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
          .sign(key,hashes.SHA256()))
    cert_path=tmp_path/'test-cert.pem';key_path=tmp_path/'test-key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert_path,key_path)
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(10)
    received=[];errors=[]
    def server():
        try:
            raw,_=listener.accept()
            with tls.wrap_socket(raw,server_side=True) as connection:
                connection.settimeout(10);reader=connection.makefile('rb')
                connection.sendall(b'220 localhost test SMTP\r\n')
                while line:=reader.readline():
                    command=line.upper()
                    if command.startswith((b'EHLO',b'HELO')):connection.sendall(b'250-localhost\r\n250 SIZE 1024000\r\n')
                    elif command.startswith((b'MAIL FROM:',b'RCPT TO:')):connection.sendall(b'250 OK\r\n')
                    elif command.startswith(b'DATA'):
                        connection.sendall(b'354 End data with dot\r\n')
                        parts=[]
                        while True:
                            line=reader.readline()
                            if not line or line==b'.\r\n':break
                            parts.append(line)
                        received.append(b''.join(parts));connection.sendall(b'250 Accepted\r\n')
                    elif command.startswith(b'QUIT'):
                        connection.sendall(b'221 Bye\r\n');break
                    else:connection.sendall(b'250 OK\r\n')
        except Exception as exc:errors.append(exc)
        finally:listener.close()
    thread=threading.Thread(target=server,daemon=True);thread.start()
    monkeypatch.setenv('SSL_CERT_FILE',str(cert_path))
    cfg=settings()
    for field,value in {'smtp_host':'localhost','smtp_port':listener.getsockname()[1],'smtp_security':'ssl',
                        'smtp_from':'sender@example.invalid','smtp_username':'','smtp_password':''}.items():
        monkeypatch.setattr(cfg,field,value)
    tenant='smtp-local-'+uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(Company(id=tenant,config={'_ops_notify':{'enabled':True,'channel':'email','email_to':'receiver@example.invalid'}}))
        alert=Alert(tenant_id=tenant,code='local-test',title='磁盘告警',severity='critical',message='隔离测试')
        db.add(alert);db.commit();aid=alert.id
    with SessionLocal() as db:
        job=claim_email(db,tenant);db.commit()
    send_alert_email(job,job['recipient'])
    with SessionLocal() as db:
        assert finish_email(db,job,None);db.commit()
        assert db.get(Alert,aid).notify_state=='sent'
    thread.join(10)
    assert not thread.is_alive() and not errors
    assert len(received)==1 and b'receiver@example.invalid' in received[0]
