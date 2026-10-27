"""Item 11: batch import, filters, batch retry, expiry todos; public answers exclude expired/internal."""
from datetime import timedelta
from app.db import SessionLocal
from app.models import Document, Chunk, DocumentJob, KnowledgeTodo, now
from app.knowledge import MAX_FILE_BYTES, MAX_FILE_MB, parse_file
from fastapi import HTTPException
import pytest

def test_file_limit_is_fifty_mb_and_rejects_oversize():
    assert MAX_FILE_MB == 50
    with pytest.raises(HTTPException) as err:
        parse_file('big.txt', b'x' * (MAX_FILE_BYTES + 1))
    assert err.value.status_code == 413
    assert '50' in err.value.detail

def test_batch_upload_partial_failure_keeps_other_files(admin):
    from app.worker import run_one
    response = admin.post(
        '/api/admin/document-jobs/upload-batch',
        files=[
            ('files', ('good.txt', '批量成功资料 MARKER-BATCH-OK'.encode(), 'text/plain')),
            ('files', ('bad.exe', b'MZfake', 'application/octet-stream')),
            ('files', ('empty.txt', b'', 'text/plain')),
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['total'] == 3
    assert body['queued'] == 1
    assert body['failed'] == 2
    statuses = {item['filename']: item['status'] for item in body['items']}
    assert statuses['good.txt'] == 'queued'
    assert statuses['bad.exe'] == 'failed'
    assert statuses['empty.txt'] == 'failed'
    assert all(item.get('batch_id') == body['batch_id'] for item in body['items'])
    good = next(i for i in body['items'] if i['filename'] == 'good.txt')
    assert run_one(good['id'])
    finished = admin.get('/api/admin/document-jobs/' + good['id']).json()
    assert finished['status'] == 'succeeded'
    doc = admin.get('/api/admin/documents/' + finished['document_id']).json()
    assert doc['status'] == 'draft'
    assert 'MARKER-BATCH-OK' in doc['content']

def test_job_filters_by_file_type_and_failure_reason(admin):
    admin.post('/api/admin/document-jobs/upload-batch', files=[
        ('files', ('policy.pdf', b'%PDF-1.4 not-really', 'application/pdf')),
        ('files', ('note.md', b'# ok', 'text/markdown')),
        ('files', ('virus.exe', b'x', 'application/octet-stream')),
    ])
    typed = admin.get('/api/admin/document-jobs?file_type=.md&page_size=50').json()
    assert typed['items'] and all(i['file_type'] == '.md' for i in typed['items'] if i['filename'] == 'note.md' or i['file_type'] == '.md')
    assert any(i['filename'] == 'note.md' for i in typed['items'])
    failed = admin.get('/api/admin/document-jobs?status=failed&error_code=type&page_size=50').json()
    assert any(i['filename'] == 'virus.exe' for i in failed['items'])

def test_batch_retry_retries_eligible_and_skips_validation(admin):
    from app.worker import run_one
    from app.models import DocumentJob
    import base64
    created = admin.post('/api/admin/document-jobs/upload-batch', files=[
        ('files', ('retry-me.txt', b'bad\x00text', 'text/plain')),
        ('files', ('nope.exe', b'x', 'application/octet-stream')),
    ]).json()
    parse_job = next(i for i in created['items'] if i['filename'] == 'retry-me.txt')
    validation = next(i for i in created['items'] if i['filename'] == 'nope.exe')
    assert run_one(parse_job['id'])
    failed = admin.get('/api/admin/document-jobs/' + parse_job['id']).json()
    assert failed['status'] == 'failed'
    with SessionLocal() as db:
        row = db.get(DocumentJob, parse_job['id'])
        row.payload = {**row.payload, 'content': base64.b64encode('批量重试后的正文'.encode()).decode()}
        db.commit()
    result = admin.post('/api/admin/document-jobs/retry-batch', json={'job_ids': [parse_job['id'], validation['id']]}).json()
    assert result['succeeded'] == 1
    assert result['failed'] == 1
    assert run_one(parse_job['id'])
    assert admin.get('/api/admin/document-jobs/' + parse_job['id']).json()['status'] == 'succeeded'

def test_expired_and_internal_never_enter_public_answers(admin):
    marker = 'PUB-VIS-EXPIRE-9911'
    pub = admin.post('/api/admin/documents', json={
        'title': '公开有效 ' + marker, 'content': f'公开有效内容 {marker}', 'status': 'published', 'visibility': 'public',
    }).json()
    internal = admin.post('/api/admin/documents', json={
        'title': '内部 ' + marker, 'content': f'内部机密内容 {marker}', 'status': 'published', 'visibility': 'internal',
    }).json()
    expired = admin.post('/api/admin/documents', json={
        'title': '过期 ' + marker, 'content': f'过期内容 {marker}', 'status': 'published', 'visibility': 'public',
        'valid_until': (now() - timedelta(hours=1)).isoformat(),
    }).json()
    result = admin.post('/api/admin/retrieval-test', json={'query': marker, 'public_only': True}).json()
    ids = {s['document_id'] for s in result['sources']}
    assert pub['id'] in ids
    assert internal['id'] not in ids
    assert expired['id'] not in ids
    # Widget path also respects filters via retrieve(public_only=True)
    session = admin.post('/api/widget/sessions', json={}).json()
    headers = {'Authorization': 'Bearer ' + session['token']}
    # Ensure chunks exist for keyword search
    from sqlalchemy import select
    with SessionLocal() as db:
        for doc_id, text in ((pub['id'], f'公开有效内容 {marker}'), (internal['id'], f'内部机密内容 {marker}'), (expired['id'], f'过期内容 {marker}')):
            if not db.scalar(select(Chunk.id).where(Chunk.document_id == doc_id)):
                db.add(Chunk(document_id=doc_id, text=text, terms=marker + ' 内容', position=0))
        db.commit()
    public = admin.post('/api/admin/retrieval-test', json={'query': marker + ' 内容', 'public_only': True}).json()
    assert all(s['document_id'] != internal['id'] and s['document_id'] != expired['id'] for s in public['sources'])

def test_knowledge_expiry_todos_for_owner(admin):
    owner = admin.get('/api/auth/me').json()
    soon = admin.post('/api/admin/documents', json={
        'title': '即将到期资料 TODO-SOON',
        'content': '即将到期正文 TODO-SOON',
        'status': 'published',
        'responsible_id': owner['id'],
        'valid_until': (now() + timedelta(days=3)).isoformat(),
    }).json()
    past = admin.post('/api/admin/documents', json={
        'title': '已过期资料 TODO-PAST',
        'content': '已过期正文 TODO-PAST',
        'status': 'published',
        'responsible_id': owner['id'],
        'valid_until': (now() - timedelta(days=1)).isoformat(),
    }).json()
    todos = admin.get('/api/admin/knowledge-todos?status=open&page_size=50').json()
    kinds = {(t['document_id'], t['kind']) for t in todos['items']}
    assert (soon['id'], 'expiring_soon') in kinds
    assert (past['id'], 'expired') in kinds
    target = next(t for t in todos['items'] if t['document_id'] == past['id'] and t['kind'] == 'expired')
    done = admin.post('/api/admin/knowledge-todos/' + target['id'] + '/complete', json={'action': 'done'})
    assert done.status_code == 200
    assert done.json()['status'] == 'done'
    # Extending validity auto-closes open soon todo on next sync
    draft = admin.patch('/api/admin/documents/' + soon['id'], json={
        'expected_sequence': soon['edit_sequence'],
        'valid_until': (now() + timedelta(days=120)).isoformat(),
    }).json()
    admin.post(f'/api/admin/documents/{soon["id"]}/submit', json={'expected_sequence': draft['edit_sequence']})
    submitted = admin.get('/api/admin/documents/' + soon['id']).json()
    admin.post(f'/api/admin/documents/{soon["id"]}/publish', json={'expected_sequence': submitted['edit_sequence'], 'note': '延期'})
    again = admin.get('/api/admin/knowledge-todos?status=open&page_size=50').json()
    assert (soon['id'], 'expiring_soon') not in {(t['document_id'], t['kind']) for t in again['items']}

def test_ocr_used_when_pdf_has_no_extractable_text(monkeypatch):
    from pypdf import PdfWriter
    import io
    import app.knowledge as knowledge
    calls = {}
    def fake_ocr(content, **kwargs):
        calls['ok'] = True
        return '第 1 页\n这是扫描件识别出的公考课程说明文字足够长，用于测试OCR成功路径'
    monkeypatch.setattr(knowledge, 'ocr_pdf_bytes', fake_ocr)
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(buffer)
    text = parse_file('scan.pdf', buffer.getvalue())
    assert calls.get('ok')
    assert '扫描件识别' in text

def test_ocr_failure_message_when_mock_returns_empty(monkeypatch):
    from pypdf import PdfWriter
    import io
    import app.knowledge as knowledge
    monkeypatch.setattr(knowledge, 'ocr_pdf_bytes', lambda content, **kwargs: '第 1 页\n')
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(buffer)
    with pytest.raises(HTTPException) as err:
        parse_file('scan.pdf', buffer.getvalue())
    assert err.value.status_code == 422
    assert 'OCR' in err.value.detail
