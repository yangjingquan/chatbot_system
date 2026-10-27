"""Additive, idempotent upgrades with an explicit schema version gate.

Migrations stay additive (ADD COLUMN / CREATE INDEX IF NOT EXISTS / create_all).
SCHEMA_VERSION is recorded in schema_migrations after each successful apply so
release scripts can refuse to proceed when the database is behind the code, or
when a failed upgrade left the DB short of the expected revision.
"""
from sqlalchemy import inspect, text
from .db import Base

# Bump when additive schema steps change. Keep descriptions short and append-only
# in MIGRATION_NOTES so upgrade docs can cite a revision.
SCHEMA_VERSION = 14
MIGRATION_NOTES = {
    14: 'v0.4 ops: handoff_note, trial reason, schema_migrations gate',
}


def expected_schema_version() -> int:
    return SCHEMA_VERSION


def applied_schema_version(engine) -> int:
    """Highest recorded revision, or 0 when the version table is absent."""
    with engine.connect() as conn:
        if engine.dialect.name == 'sqlite':
            row = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            )).first()
            if not row:
                return 0
        else:
            row = conn.execute(text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name='schema_migrations'"
            )).first()
            if not row:
                return 0
        value = conn.execute(text('SELECT COALESCE(MAX(version), 0) FROM schema_migrations')).scalar()
        return int(value or 0)


def _ensure_version_table(conn, dialect_name: str) -> None:
    if dialect_name == 'postgresql':
        conn.execute(text(
            'CREATE TABLE IF NOT EXISTS schema_migrations ('
            'version INTEGER PRIMARY KEY,'
            "description TEXT NOT NULL DEFAULT '',"
            'applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()'
            ')'
        ))
    else:
        conn.execute(text(
            'CREATE TABLE IF NOT EXISTS schema_migrations ('
            'version INTEGER PRIMARY KEY,'
            "description TEXT NOT NULL DEFAULT '',"
            'applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP'
            ')'
        ))


def _record_version(conn, version: int) -> None:
    note = MIGRATION_NOTES.get(version, f'schema revision {version}')
    existing = conn.execute(
        text('SELECT 1 FROM schema_migrations WHERE version = :v'),
        {'v': version},
    ).first()
    if existing:
        return
    conn.execute(
        text('INSERT INTO schema_migrations (version, description) VALUES (:v, :d)'),
        {'v': version, 'd': note},
    )


def migrate_schema(engine):
    additions = {
        'audit_logs': {'details': "TEXT NOT NULL DEFAULT ''"},
        'admins': {
            'role': "VARCHAR(24) NOT NULL DEFAULT 'admin'",
            'is_active': 'BOOLEAN NOT NULL DEFAULT TRUE',
        },
        'leads': {
            'contact_allowed': 'BOOLEAN NOT NULL DEFAULT TRUE',
            'owner_id': 'VARCHAR(36)',
            'contact_hash': 'VARCHAR(64)',
            'student_id': 'VARCHAR(36)',
            'next_followup_at': 'TIMESTAMP',
        },
        'conversations': {
            'assigned_to_id': 'VARCHAR(36)',
            'waiting_since': (
                'TIMESTAMP WITH TIME ZONE'
                if engine.dialect.name == 'postgresql'
                else 'TIMESTAMP'
            ),
            'handoff_note': "TEXT NOT NULL DEFAULT ''",
        },
        'trial_requests': {'reason': "TEXT NOT NULL DEFAULT ''"},
        'documents': {
            'edit_sequence': 'INTEGER NOT NULL DEFAULT 0',
            'knowledge_type': "VARCHAR(24) NOT NULL DEFAULT 'general'",
            'exam_type': "VARCHAR(40) NOT NULL DEFAULT '通用'",
            'region': "VARCHAR(80) NOT NULL DEFAULT '全国'",
            'exam_year': 'INTEGER',
            'valid_from': 'TIMESTAMP',
            'valid_until': 'TIMESTAMP',
            'responsible_id': 'VARCHAR(36)',
            'policy_verified': 'BOOLEAN NOT NULL DEFAULT FALSE',
        },
    }
    with engine.begin() as conn:
        if engine.dialect.name == 'postgresql':
            conn.execute(text('SELECT pg_advisory_xact_lock(72102602)'))
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
        Base.metadata.create_all(conn)
        for table, columns in additions.items():
            existing = {c['name'] for c in inspect(conn).get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    if engine.dialect.name == 'postgresql' and name in (
                        'valid_from',
                        'valid_until',
                        'next_followup_at',
                    ):
                        definition = 'TIMESTAMP WITH TIME ZONE'
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))
        if engine.dialect.name == 'postgresql':
            conn.execute(text('ALTER TABLE leads ALTER COLUMN contact TYPE TEXT'))
            conn.execute(text('CREATE INDEX IF NOT EXISTS ix_leads_owner_id ON leads(owner_id)'))
            conn.execute(
                text(
                    'CREATE INDEX IF NOT EXISTS ix_conversations_assigned_to_id '
                    'ON conversations(assigned_to_id)'
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_chunks_terms_gin "
                    "ON chunks USING gin (to_tsvector('simple', terms))"
                )
            )
            for field in ('contact_hash', 'student_id', 'next_followup_at'):
                conn.execute(text(f'CREATE INDEX IF NOT EXISTS ix_leads_{field} ON leads({field})'))
        from .encryption import migrate_contacts

        migrate_contacts(conn)
        _ensure_version_table(conn, engine.dialect.name)
        # Restored older DBs that already have additive columns still get marked
        # current after a successful migrate_schema run.
        _record_version(conn, SCHEMA_VERSION)


def assert_schema_current(engine) -> dict:
    """Return version info; raise RuntimeError when DB is behind the running code."""
    applied = applied_schema_version(engine)
    expected = expected_schema_version()
    info = {
        'applied': applied,
        'expected': expected,
        'current': applied >= expected,
        'note': MIGRATION_NOTES.get(expected, ''),
    }
    if applied < expected:
        raise RuntimeError(
            f'数据库 schema 版本 {applied} 落后于代码期望 {expected}；'
            '请先完成升级前备份，再启动新版本以执行 migrate_schema，或回滚代码。'
        )
    return info
