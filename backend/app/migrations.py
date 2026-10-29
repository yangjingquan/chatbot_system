"""Additive, idempotent upgrades with an explicit schema version gate.

Migrations stay additive (ADD COLUMN / CREATE INDEX IF NOT EXISTS / create_all).
SCHEMA_VERSION is recorded in schema_migrations after each successful apply so
release scripts can refuse to proceed when the database is behind the code, or
when a failed upgrade left the DB short of the expected revision.
"""
from datetime import timedelta
from sqlalchemy import inspect, select, text, update
from .db import Base

# Bump when additive schema steps change. Keep descriptions short and append-only
# in MIGRATION_NOTES so upgrade docs can cite a revision.
SCHEMA_VERSION = 17
MIGRATION_NOTES = {
    14: 'v0.4 ops: handoff_note, trial reason, schema_migrations gate',
    15: 'independent trial follow-up reminders',
    16: 'email alert delivery tracking',
    17: 'customer preview: promote current records and remove internal demo labels',
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
        'trial_requests': {
            'reason': "TEXT NOT NULL DEFAULT ''",
            'todo_due_at': 'TIMESTAMP WITH TIME ZONE' if engine.dialect.name == 'postgresql' else 'TIMESTAMP',
            'todo_kind': "VARCHAR(40) NOT NULL DEFAULT ''",
        },
        'alerts': {
            'notify_event': "VARCHAR(16) NOT NULL DEFAULT 'alert'",
            'notify_attempts': 'INTEGER NOT NULL DEFAULT 0',
            'notify_next_attempt_at': 'TIMESTAMP WITH TIME ZONE' if engine.dialect.name == 'postgresql' else 'TIMESTAMP',
            'notify_lease_until': 'TIMESTAMP WITH TIME ZONE' if engine.dialect.name == 'postgresql' else 'TIMESTAMP',
            'notify_token': 'VARCHAR(36)',
            'notify_last_error': "VARCHAR(300) NOT NULL DEFAULT ''",
            'notify_sent_at': 'TIMESTAMP WITH TIME ZONE' if engine.dialect.name == 'postgresql' else 'TIMESTAMP',
        },
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
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_trial_requests_todo_due_at ON trial_requests(todo_due_at)'))
        if not conn.execute(text('SELECT 1 FROM schema_migrations WHERE version = 15')).first():
            # Legacy records did not track completion per trial. Recover active
            # appointments only, without guessing or clearing manual lead reminders.
            from .models import Trial
            for row in conn.execute(select(Trial.id, Trial.status, Trial.created_at, Trial.scheduled_at)
                                    .where(Trial.todo_due_at.is_(None), Trial.status.in_(['pending', 'confirmed']))):
                due = row.scheduled_at if row.status == 'confirmed' else row.created_at + timedelta(hours=24)
                if due:
                    conn.execute(update(Trial).where(Trial.id == row.id).values(
                        todo_due_at=due, todo_kind='接待试听' if row.status == 'confirmed' else '确认试听'))
            _record_version(conn, 15)
        # The alert columns above are additive and always ensured. Keep revision 16
        # recorded when upgrading databases that were restored or partially repaired.
        if not conn.execute(text('SELECT 1 FROM schema_migrations WHERE version = 16')).first():
            _record_version(conn, 16)
        if not conn.execute(text('SELECT 1 FROM schema_migrations WHERE version = 17')).first():
            from .models import (
                Alert, Audit, Company, Conversation, Course, CourseBatch, Document,
                FollowUp, Lead, Message, Student, TENANT,
            )
            conn.execute(update(Document).where(Document.tenant_id == TENANT).values(is_demo=False))
            conn.execute(update(Course).where(Course.tenant_id == TENANT).values(is_demo=False))
            conn.execute(update(Conversation).where(Conversation.tenant_id == TENANT).values(is_demo=False))
            conn.execute(update(Lead).where(Lead.tenant_id == TENANT).values(is_demo=False))
            conn.execute(update(Student).where(Student.tenant_id == TENANT).values(is_demo=False))
            conn.execute(update(CourseBatch).where(
                CourseBatch.tenant_id == TENANT,
                CourseBatch.name == '演示基础班',
            ).values(name='基础班'))
            conn.execute(update(Course).where(
                Course.tenant_id == TENANT,
                Course.description == '线上教学和电子练习资料；考试报名费、交通食宿不包含。实际交易须人工确认。',
            ).values(description='线上教学和电子练习资料；考试报名费、交通食宿不包含。'))
            conn.execute(update(CourseBatch).where(
                CourseBatch.tenant_id == TENANT,
                CourseBatch.schedule == '线上直播与回放；实际开班日期由顾问确认',
            ).values(schedule='线上直播与回放；具体开班日期由顾问确认'))
            conn.execute(update(Conversation).where(
                Conversation.tenant_id == TENANT,
                Conversation.source_page == '官网演示',
            ).values(source_page='官网'))
            conn.execute(update(Message).where(
                Message.content == '这是合成演示对话，用于展示咨询管理。请在官网演示页测试真实知识检索。',
            ).values(content='您好，我想了解课程与试听安排，请顾问协助。'))
            conn.execute(update(FollowUp).where(
                FollowUp.content == '演示记录：已了解备考基础，待确认试听时间。',
            ).values(content='已了解备考基础，待确认试听时间。'))
            conn.execute(update(Alert).where(
                Alert.tenant_id == TENANT,
                Alert.code == 'smtp_test',
                Alert.title == '告警邮件测试',
            ).values(
                title='告警通知渠道验证',
                message='此邮件用于确认告警通知收件配置可正常接收。',
            ))
            conn.execute(update(Audit).where(
                Audit.tenant_id == TENANT,
                Audit.action == '标记测试记录',
            ).values(action='移入隔离数据'))
            conn.execute(update(Audit).where(
                Audit.tenant_id == TENANT,
                Audit.action == '发送告警测试邮件',
            ).values(action='发送告警渠道验证邮件'))
            no_consent = '合成演示数据，无真实联系授权或个人信息'
            conn.execute(update(Lead).where(
                Lead.tenant_id == TENANT,
                Lead.consent_text == no_consent,
            ).values(
                consent_text='此记录没有联系授权，不安排后续联系。',
                contact_allowed=False,
                contact_verified=False,
                contact='未提供',
                contact_hash=None,
            ))
            config = conn.execute(select(Company.config).where(Company.id == TENANT)).scalar_one_or_none()
            if isinstance(config, dict):
                updated = dict(config)
                if updated.get('description') == '专注公职考试备考咨询的演示培训机构':
                    updated['description'] = '专注公职考试备考咨询与课程服务'
                old_welcome = '您好，我是知行学习顾问。您正在准备哪一类考试？我可以帮您了解课程、学习方式或预约试听。当前机构与课程资料均为演示设定。'
                if updated.get('welcome') == old_welcome:
                    updated['welcome'] = '您好，我是知行学习顾问。您正在准备哪一类考试？我可以帮您了解课程、学习方式或预约试听。课程与价格以机构公布的信息为准。'
                if updated != config:
                    conn.execute(update(Company).where(Company.id == TENANT).values(config=updated))
            _record_version(conn, 17)
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
