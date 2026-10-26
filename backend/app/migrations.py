"""Additive, idempotent upgrades for the existing local v0.1 database."""
from sqlalchemy import inspect, text
from .db import Base

def migrate_schema(engine):
    additions={
        'admins':{'role':"VARCHAR(24) NOT NULL DEFAULT 'admin'",'is_active':'BOOLEAN NOT NULL DEFAULT TRUE'},
        'leads':{'contact_allowed':'BOOLEAN NOT NULL DEFAULT TRUE','owner_id':'VARCHAR(36)'},
        'conversations':{'assigned_to_id':'VARCHAR(36)'},
        'documents':{'edit_sequence':'INTEGER NOT NULL DEFAULT 0',
                     'knowledge_type':"VARCHAR(24) NOT NULL DEFAULT 'general'",
                     'exam_type':"VARCHAR(40) NOT NULL DEFAULT '通用'",'region':"VARCHAR(80) NOT NULL DEFAULT '全国'",
                     'exam_year':'INTEGER','valid_from':'TIMESTAMP','valid_until':'TIMESTAMP',
                     'responsible_id':'VARCHAR(36)','policy_verified':'BOOLEAN NOT NULL DEFAULT FALSE'},
    }
    with engine.begin() as conn:
        if engine.dialect.name=='postgresql':
            conn.execute(text('SELECT pg_advisory_xact_lock(72102602)'))
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
        Base.metadata.create_all(conn)
        for table,columns in additions.items():
            existing={c['name'] for c in inspect(conn).get_columns(table)}
            for name,definition in columns.items():
                if name not in existing:
                    if engine.dialect.name=='postgresql' and name in ('valid_from','valid_until'):
                        definition='TIMESTAMP WITH TIME ZONE'
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))
        if engine.dialect.name=='postgresql':
            conn.execute(text('ALTER TABLE leads ALTER COLUMN contact TYPE TEXT'))
            conn.execute(text('CREATE INDEX IF NOT EXISTS ix_leads_owner_id ON leads(owner_id)'))
            conn.execute(text('CREATE INDEX IF NOT EXISTS ix_conversations_assigned_to_id ON conversations(assigned_to_id)'))
        from .encryption import migrate_contacts
        migrate_contacts(conn)
