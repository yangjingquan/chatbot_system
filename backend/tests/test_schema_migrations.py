"""Schema version gate and idempotent migrate_schema."""
import pytest
from datetime import timedelta
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from app.db import engine
from app import migrations as mig
from app.migrations import (
    SCHEMA_VERSION,
    applied_schema_version,
    assert_schema_current,
    expected_schema_version,
    migrate_schema,
)


def test_migrate_records_schema_version():
    migrate_schema(engine)
    assert applied_schema_version(engine) == SCHEMA_VERSION
    assert expected_schema_version() == SCHEMA_VERSION
    info = assert_schema_current(engine)
    assert info['current'] is True
    assert info['applied'] == SCHEMA_VERSION


def test_migrate_schema_is_idempotent():
    migrate_schema(engine)
    migrate_schema(engine)
    with engine.connect() as conn:
        count = conn.execute(
            text('SELECT count(*) FROM schema_migrations WHERE version = :v'),
            {'v': SCHEMA_VERSION},
        ).scalar()
    assert count == 1


def test_assert_schema_current_detects_lag(monkeypatch):
    migrate_schema(engine)
    monkeypatch.setattr(mig, 'SCHEMA_VERSION', SCHEMA_VERSION + 99)
    with pytest.raises(RuntimeError) as exc:
        mig.assert_schema_current(engine)
    assert '落后' in str(exc.value)


def test_existing_active_trials_are_recovered_once(tmp_path):
    from app.models import Trial, now
    local_engine=create_engine('sqlite:///'+str(tmp_path/'old.db'))
    migrate_schema(local_engine)
    with Session(local_engine) as db:
        created=now()-timedelta(hours=2)
        db.add_all([
            Trial(id='legacy-pending',conversation_id='legacy-session',batch_id='legacy-batch',client_id='legacy-req-1',status='pending',created_at=created,requested_at=created+timedelta(days=4)),
            Trial(id='legacy-confirmed',conversation_id='legacy-session',batch_id='legacy-batch',client_id='legacy-req-2',status='confirmed',created_at=created,requested_at=created+timedelta(days=4),scheduled_at=created+timedelta(days=4)),
            Trial(id='legacy-completed',conversation_id='legacy-session',batch_id='legacy-batch',client_id='legacy-req-3',status='attended',created_at=created,requested_at=created+timedelta(days=4)),
        ])
        db.execute(text('DELETE FROM schema_migrations WHERE version=15'))
        db.commit()
    with local_engine.begin() as conn:
        conn.execute(text('DROP INDEX IF EXISTS ix_trial_requests_todo_due_at'))
        conn.execute(text('ALTER TABLE trial_requests DROP COLUMN todo_due_at'))
        conn.execute(text('ALTER TABLE trial_requests DROP COLUMN todo_kind'))
    migrate_schema(local_engine)
    with Session(local_engine) as db:
        pending=db.get(Trial,'legacy-pending');confirmed=db.get(Trial,'legacy-confirmed')
        assert pending.todo_kind=='确认试听' and pending.todo_due_at==created.replace(tzinfo=None)+timedelta(hours=24)
        assert confirmed.todo_kind=='接待试听' and confirmed.todo_due_at is not None
        assert db.get(Trial,'legacy-completed').todo_due_at is None
        pending.todo_due_at=None
        db.commit()
    migrate_schema(local_engine)
    with Session(local_engine) as db:
        assert db.get(Trial,'legacy-pending').todo_due_at is None
    local_engine.dispose()


def test_alert_mail_columns_upgrade_existing_database(tmp_path):
    local_engine=create_engine('sqlite:///'+str(tmp_path/'v15.db'))
    migrate_schema(local_engine)
    with local_engine.begin() as conn:
        conn.execute(text('DELETE FROM schema_migrations WHERE version=16'))
        for column in ('notify_event','notify_attempts','notify_next_attempt_at','notify_lease_until',
                       'notify_token','notify_last_error','notify_sent_at'):
            conn.execute(text(f'ALTER TABLE alerts DROP COLUMN {column}'))
    migrate_schema(local_engine)
    migrate_schema(local_engine)
    with local_engine.connect() as conn:
        columns={row[1] for row in conn.execute(text('PRAGMA table_info(alerts)'))}
        assert {'notify_event','notify_attempts','notify_next_attempt_at','notify_lease_until',
                'notify_token','notify_last_error','notify_sent_at'} <= columns
        assert conn.execute(text('SELECT count(*) FROM schema_migrations WHERE version=16')).scalar()==1
    local_engine.dispose()
