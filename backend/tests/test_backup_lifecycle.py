"""Backup restore, contact key rotation, retention and privacy requests."""
import uuid
from datetime import timedelta
from pathlib import Path
from sqlalchemy import text
from app.db import SessionLocal, engine
from app.models import Conversation, Message, Lead, Document, Chunk, Company, TENANT, now
from app.config import settings
from app.encryption import encrypt, decrypt, rotate_contacts, PREFIX
from app.backup_ops import export_logical, write_encrypted_backup, read_encrypted_backup, restore_logical, verify_backup_file
from app.privacy_lifecycle import apply_retention, anonymize_lead, ANON_CONTACT, ANON_NAME, ANON_MESSAGE

def _session(client):
    response = client.post('/api/widget/sessions', json={'source_page': '备份测试', 'embed_origin': 'http://testserver'})
    assert response.status_code == 200, response.text
    body = response.json()
    return body['id'], {'Authorization': 'Bearer ' + body['token']}


def test_contact_key_rotation_keeps_history_readable(client):
    sid, headers = _session(client)
    phone = '13900001111'
    assert client.post(f'/api/widget/sessions/{sid}/contact', headers=headers,
                       json={'name': '轮换测试', 'contact': phone, 'consent': True}).status_code == 200
    with engine.connect() as conn:
        raw = conn.execute(text('SELECT contact FROM leads WHERE conversation_id=:id'), {'id': sid}).scalar()
    assert raw.startswith(PREFIX)
    old_secret = settings().contact_secret or settings().session_secret
    settings().contact_secret = 'new-contact-secret-for-rotation-tests-0001'
    settings().contact_secret_previous = old_secret
    assert decrypt(raw) == phone
    with engine.begin() as conn:
        assert rotate_contacts(conn) >= 1
    settings().contact_secret_previous = ''
    with engine.connect() as conn:
        raw2 = conn.execute(text('SELECT contact FROM leads WHERE conversation_id=:id'), {'id': sid}).scalar()
    assert decrypt(raw2) == phone
    assert raw2 != raw
    # Re-encrypt with SESSION_SECRET so later tests (same SQLite file) still decrypt.
    settings().contact_secret_previous = 'new-contact-secret-for-rotation-tests-0001'
    settings().contact_secret = ''
    with engine.begin() as conn:
        rotate_contacts(conn)
    settings().contact_secret_previous = ''
    assert decrypt(engine.connect().execute(text('SELECT contact FROM leads WHERE conversation_id=:id'), {'id': sid}).scalar()) == phone


def test_logical_backup_restore_sessions_leads_knowledge(client, tmp_path, monkeypatch):
    monkeypatch.setenv('BACKUP_DIR', str(tmp_path))
    settings.cache_clear()
    settings().backup_dir = str(tmp_path)

    sid, headers = _session(client)
    assert client.post(f'/api/widget/sessions/{sid}/contact', headers=headers,
                       json={'name': '备份访客', 'contact': '13800002222', 'consent': True}).status_code == 200
    doc_id = None
    with SessionLocal() as db:
        doc = Document(title='备份知识样例', content='公安联考基础班备份演练内容。', status='published',
                       visibility='public', category='测试', is_demo=True)
        db.add(doc)
        db.flush()
        db.add(Chunk(document_id=doc.id, text=doc.content, terms='公安联考 基础班', position=0))
        doc_id = doc.id
        path = write_encrypted_backup(db, label='pytest')
        db.commit()
    assert path.exists()
    info = verify_backup_file(path)
    assert info['ok'] and info['counts']['leads'] >= 1 and info['counts']['documents'] >= 1

    from sqlalchemy import select
    with SessionLocal() as db:
        lead = db.scalar(select(Lead).where(Lead.conversation_id == sid))
        assert lead is not None
        lead.name = '已被篡改'
        lead.contact = '13000000000'
        conv = db.get(Conversation, sid)
        conv.visitor_name = '篡改访客'
        for msg in db.scalars(select(Message).where(Message.conversation_id == sid)).all():
            msg.content = '已被清空'
        doc = db.get(Document, doc_id)
        doc.content = '错误内容'
        db.commit()

    payload = read_encrypted_backup(path)
    with SessionLocal() as db:
        restored = restore_logical(db, payload, clear_first=True)
        db.commit()
    assert restored['leads'] >= 1 and restored['documents'] >= 1 and restored['conversations'] >= 1

    with SessionLocal() as db:
        lead = db.scalar(select(Lead).where(Lead.conversation_id == sid))
        assert lead.name == '备份访客'
        assert lead.contact == '13800002222'
        assert db.get(Conversation, sid).visitor_name != '篡改访客'
        assert '备份演练内容' in db.get(Document, doc_id).content


def test_privacy_withdraw_anonymize_and_retention(admin, client):
    sid, headers = _session(client)
    assert client.post(f'/api/widget/sessions/{sid}/contact', headers=headers,
                       json={'name': '隐私访客', 'contact': '13700003333', 'consent': True}).status_code == 200
    from sqlalchemy import select
    with SessionLocal() as db:
        lead = db.scalar(select(Lead).where(Lead.conversation_id == sid))
        lead_id = lead.id

    withdrawn = admin.post(f'/api/admin/leads/{lead_id}/withdraw-contact')
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()['contact_allowed'] is False
    detail = admin.get(f'/api/admin/leads/{lead_id}').json()
    assert detail['contact_allowed'] is False

    created = admin.post('/api/admin/privacy/requests', json={
        'kind': 'anonymize', 'lead_id': lead_id, 'conversation_id': sid, 'note': '测试匿名化'})
    assert created.status_code == 200, created.text
    rid = created.json()['id']
    done = admin.post(f'/api/admin/privacy/requests/{rid}/complete', json={'resolution': '已匿名'})
    assert done.status_code == 200, done.text
    assert done.json()['status'] == 'completed'

    with SessionLocal() as db:
        lead = db.get(Lead, lead_id)
        conv = db.get(Conversation, sid)
        assert lead.name == ANON_NAME and lead.contact == ANON_CONTACT
        assert conv.visitor_name == ANON_NAME
        for msg in db.scalars(select(Message).where(Message.conversation_id == sid)).all():
            assert msg.content == ANON_MESSAGE

    # Retention: plant an old conversation and run cleanup.
    with SessionLocal() as db:
        company = db.get(Company, TENANT)
        company.config = {**company.config, 'retention_conversation_days': 30, 'retention_lead_days': 30}
        old = now() - timedelta(days=60)
        conv = Conversation(visitor_name='过期访客', source_page='留存', token_hash='hash-retention-1',
                            created_at=old, updated_at=old, is_demo=True)
        db.add(conv)
        db.flush()
        db.add(Message(conversation_id=conv.id, role='user', content='过期消息', created_at=old))
        db.add(Lead(conversation_id=conv.id, name='过期线索', contact='13600004444',
                    consent_text='t', created_at=old, updated_at=old, is_demo=True))
        db.commit()
        old_sid = conv.id

    ran = admin.post('/api/admin/privacy/retention/run')
    assert ran.status_code == 200, ran.text
    assert ran.json()['conversations'] >= 1
    with SessionLocal() as db:
        assert db.get(Conversation, old_sid).visitor_name == ANON_NAME


def test_retention_settings_roundtrip(admin):
    previous = admin.get('/api/admin/privacy/retention').json()
    try:
        updated = admin.patch('/api/admin/privacy/retention', json={
            'retention_conversation_days': 180, 'retention_lead_days': 400,
            'retention_audit_days': 800, 'retention_backup_days': 60})
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body['retention_conversation_days'] == 180
        assert body['retention_backup_days'] == 60
    finally:
        admin.patch('/api/admin/privacy/retention', json={
            k: previous[k] for k in (
                'retention_conversation_days', 'retention_lead_days',
                'retention_audit_days', 'retention_backup_days')})


def test_backup_api_creates_verifiable_file(admin, tmp_path, monkeypatch):
    monkeypatch.setenv('BACKUP_DIR', str(tmp_path))
    settings.cache_clear()
    settings().backup_dir = str(tmp_path)
    response = admin.post('/api/admin/privacy/backup')
    assert response.status_code == 200, response.text
    body = response.json()
    assert Path(body['path']).exists()
    assert body['counts']['documents'] >= 0
