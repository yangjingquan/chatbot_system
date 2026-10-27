"""Schema version gate and idempotent migrate_schema."""
import pytest
from sqlalchemy import text
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
