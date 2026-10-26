from datetime import datetime, timezone
import uuid
from sqlalchemy import String, Text, JSON, DateTime, Boolean, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector
from .db import Base
from .encryption import EncryptedContact

TENANT = 'zhixing-demo'

def uid(): return str(uuid.uuid4())
def now(): return datetime.now(timezone.utc)

class Company(Base):
    __tablename__ = 'companies'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=TENANT)
    config: Mapped[dict] = mapped_column(JSON, default=dict)

class Admin(Base):
    __tablename__ = 'admins'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(100), default='机构管理员')

class AdminSession(Base):
    __tablename__ = 'admin_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_id: Mapped[str] = mapped_column(ForeignKey('admins.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Document(Base):
    __tablename__ = 'documents'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    title: Mapped[str] = mapped_column(String(240))
    category: Mapped[str] = mapped_column(String(100), default='未分类')
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default='draft', index=True)
    visibility: Mapped[str] = mapped_column(String(24), default='public')
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class Chunk(Base):
    __tablename__ = 'chunks'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    text: Mapped[str] = mapped_column(Text)
    terms: Mapped[str] = mapped_column(Text, default='')
    position: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list | None] = mapped_column(Vector(512).with_variant(JSON, 'sqlite'), nullable=True)

class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    visitor_name: Mapped[str] = mapped_column(String(100), default='网站访客')
    source_page: Mapped[str] = mapped_column(String(300), default='官网')
    status: Mapped[str] = mapped_column(String(24), default='bot')
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class Message(Base):
    __tablename__ = 'messages'
    __table_args__ = (UniqueConstraint('conversation_id', 'client_id', name='uq_message_idempotency'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    client_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reply_to: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(24), default='')
    state: Mapped[str] = mapped_column(String(24), default='completed')
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Lead(Base):
    __tablename__ = 'leads'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    contact: Mapped[str] = mapped_column(EncryptedContact())
    contact_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_text: Mapped[str] = mapped_column(Text)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    level: Mapped[str] = mapped_column(String(24), default='待判定')
    score: Mapped[int] = mapped_column(Integer, default=0)
    purpose: Mapped[str] = mapped_column(String(40), default='课程咨询')
    tags: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    stage: Mapped[str] = mapped_column(String(24), default='新线索')
    owner: Mapped[str] = mapped_column(String(80), default='未分配')
    manual_level: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class FollowUp(Base):
    __tablename__ = 'followups'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    lead_id: Mapped[str] = mapped_column(ForeignKey('leads.id'), index=True)
    author: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Audit(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(120))
    target: Mapped[str] = mapped_column(String(240), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
