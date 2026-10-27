"""Metric口径 consistency and lightweight synthetic benchmarks for item 13."""
import csv
import io
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Conversation, Lead, Message, Trial, CourseBatch

def _seed_chain(admin, marker, n=40):
    """Create n engaged conversations + leads; every 4th has a trial and agent reply.

    Timestamps stay slightly in the past so later tests' list pages (ordered by
    updated_at desc) are not dominated by far-future synthetic rows.
    """
    with SessionLocal() as db:
        batch = db.scalar(select(CourseBatch).where(CourseBatch.sale_status == 'open'))
        assert batch is not None, 'seed data must provide an open course batch'
        yesterday = datetime.now(ZoneInfo('Asia/Shanghai')).date() - timedelta(days=1)
        stamp = datetime.combine(yesterday, datetime.min.time(), tzinfo=ZoneInfo('Asia/Shanghai')).replace(hour=8).astimezone(timezone.utc)
        for i in range(n):
            c = Conversation(
                visitor_name=f'{marker}{i}', token_hash='test', source_page=marker,
                analysis={'active_exam_types': ['国考']},
                created_at=stamp + timedelta(minutes=i),
                status='human' if i % 4 == 0 else 'bot',
                waiting_since=(stamp + timedelta(minutes=i)) if i % 4 == 0 else None,
            )
            db.add(c); db.flush()
            db.add(Message(
                conversation_id=c.id, role='user', content=f'{marker}问题{i}',
                created_at=stamp + timedelta(minutes=i, seconds=1),
            ))
            if i % 4 == 0:
                db.add(Message(
                    conversation_id=c.id, role='agent', content='人工回复',
                    created_at=stamp + timedelta(minutes=i, seconds=31),
                ))
            stage = '已成交' if i % 10 == 0 else ('跟进中' if i % 2 == 0 else '新线索')
            level = '高意向' if i % 5 == 0 else '中意向'
            db.add(Lead(
                conversation_id=c.id, name=f'{marker}{i}', contact='test@example.invalid',
                consent_text='synthetic', tags=['国考'], stage=stage, level=level,
                created_at=stamp + timedelta(minutes=i),
            ))
            if i % 4 == 0:
                db.add(Trial(
                    conversation_id=c.id, batch_id=batch.id, client_id=f'{marker}-{i}',
                    requested_at=stamp + timedelta(minutes=i), status='pending',
                ))
        db.commit()

def test_list_export_overview_share_filter_totals(admin):
    marker = '口径一致' + uuid.uuid4().hex[:8]
    _seed_chain(admin, marker, n=48)
    from zoneinfo import ZoneInfo
    day = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=1)).date().isoformat()
    filters = {'q': marker, 'date_from': day, 'date_to': day, 'exam_type': '国考'}
    leads = admin.get('/api/admin/leads', params={**filters, 'page_size': 100}).json()
    conversations = admin.get('/api/admin/conversations', params={**filters, 'page_size': 100}).json()
    export = admin.get('/api/admin/leads-export', params=filters)
    rows = list(csv.reader(io.StringIO(export.content.decode('utf-8-sig'))))
    overview = admin.get('/api/admin/overview', params=filters).json()
    assert leads['total'] == overview['counts']['leads'] == len(rows) - 1 == 48
    assert conversations['total'] == overview['counts']['conversations'] == 48
    assert overview['counts']['contacted_conversations'] == 48
    assert overview['counts']['trials'] == 12
    assert overview['counts']['customers'] == 5  # i%10==0 → 0,10,20,30,40
    assert overview['counts']['high_intent'] == 10  # i%5==0
    assert overview['counts']['pending'] == 24  # odd i → 新线索
    assert 'metric_definitions' in overview
    assert overview['reply_latency']['sample_count'] == 12
    assert overview['reply_latency']['avg_seconds'] == 31.0

def test_reception_unread_matches_per_conversation_sum(admin):
    marker = '未读汇总' + uuid.uuid4().hex[:8]
    session = admin.post('/api/widget/sessions', json={}).json()
    sid, token = session['id'], session['token']
    headers = {'Authorization': 'Bearer ' + token}
    assert admin.post(f'/api/widget/sessions/{sid}/messages', headers=headers,
                      json={'content': marker + '第一条', 'client_id': str(uuid.uuid4())}).status_code == 200
    assert admin.post(f'/api/widget/sessions/{sid}/handoff', headers=headers).status_code == 200
    assert admin.post(f'/api/widget/sessions/{sid}/messages', headers=headers,
                      json={'content': marker + '第二条', 'client_id': str(uuid.uuid4())}).status_code == 200
    summary = admin.get('/api/admin/reception-summary').json()
    from app.models import Admin, Conversation
    from app.permissions import scope_conversations
    from app.reception import unread
    with SessionLocal() as db:
        user = db.scalar(select(Admin).where(Admin.email == 'admin@zhixing.local'))
        convs = db.scalars(scope_conversations(select(Conversation).where(Conversation.status != 'closed'), user)).all()
        expected = sum(unread(db, c, user) for c in convs)
    assert summary['unread'] == expected
    assert summary['waiting'] >= 1

def test_synthetic_overview_benchmark_under_budget(admin):
    """Reproducible micro-benchmark on ~600 synthetic rows (not production load)."""
    marker = '性能基准' + uuid.uuid4().hex[:8]
    _seed_chain(admin, marker, n=600)
    from zoneinfo import ZoneInfo
    day = (datetime.now(ZoneInfo('Asia/Shanghai')) - timedelta(days=1)).date().isoformat()
    filters = {'q': marker, 'date_from': day, 'date_to': day}
    assert admin.get('/api/admin/overview', params=filters).status_code == 200
    started = time.perf_counter()
    overview = admin.get('/api/admin/overview', params=filters).json()
    overview_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    listing = admin.get('/api/admin/leads', params={**filters, 'page_size': 50}).json()
    list_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    export = admin.get('/api/admin/leads-export', params=filters)
    export_ms = (time.perf_counter() - started) * 1000
    assert listing['total'] == overview['counts']['leads'] == 600
    assert export.status_code == 200
    # Soft budget for in-process SQLite microbench; documents the observed path.
    assert overview_ms < 8000, f'overview too slow: {overview_ms:.0f}ms'
    assert list_ms < 5000, f'list too slow: {list_ms:.0f}ms'
    assert export_ms < 8000, f'export too slow: {export_ms:.0f}ms'
    print(f'\n[bench] n=600 overview={overview_ms:.0f}ms list={list_ms:.0f}ms export={export_ms:.0f}ms')
