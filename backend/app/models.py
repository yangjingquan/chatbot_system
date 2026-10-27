from datetime import datetime, timezone
import uuid
from sqlalchemy import String, Text, JSON, DateTime, Boolean, ForeignKey, Integer, UniqueConstraint, Numeric
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
    role: Mapped[str] = mapped_column(String(24), default='admin', server_default='admin')
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default='true')

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
    edit_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    knowledge_type: Mapped[str] = mapped_column(String(24), default='general', server_default='general')
    exam_type: Mapped[str] = mapped_column(String(40), default='通用', server_default='通用')
    region: Mapped[str] = mapped_column(String(80), default='全国', server_default='全国')
    exam_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responsible_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    policy_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class DocumentRevision(Base):
    __tablename__ = 'document_revisions'
    __table_args__ = (UniqueConstraint('document_id','number',name='uq_document_revision'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id'), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default='draft')
    data: Mapped[dict] = mapped_column(JSON)
    author_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    author_name: Mapped[str] = mapped_column(String(100), default='历史资料')
    reviewer_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    review_note: Mapped[str] = mapped_column(Text, default='')
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
    assigned_to_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    handoff_note: Mapped[str] = mapped_column(Text, default='')
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
    contact_hash: Mapped[str | None] = mapped_column(String(64), nullable=True,index=True)
    student_id: Mapped[str | None] = mapped_column(ForeignKey('students.id'),nullable=True,index=True)
    next_followup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),nullable=True,index=True)
    contact_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    contact_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    consent_text: Mapped[str] = mapped_column(Text)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    level: Mapped[str] = mapped_column(String(24), default='待判定')
    score: Mapped[int] = mapped_column(Integer, default=0)
    purpose: Mapped[str] = mapped_column(String(40), default='课程咨询')
    tags: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    stage: Mapped[str] = mapped_column(String(24), default='新线索')
    owner: Mapped[str] = mapped_column(String(80), default='未分配')
    owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
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

class Presence(Base):
    __tablename__ = 'staff_presence'
    admin_id: Mapped[str] = mapped_column(ForeignKey('admins.id'), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default='offline')
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class AnswerFeedback(Base):
    __tablename__ = 'answer_feedback'
    message_id: Mapped[str] = mapped_column(ForeignKey('messages.id'), primary_key=True)
    rating: Mapped[str] = mapped_column(String(12))
    comment: Mapped[str] = mapped_column(Text, default='')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class KnowledgeGap(Base):
    __tablename__ = 'knowledge_gaps'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey('messages.id'), unique=True)
    question: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), default='open', index=True)
    note: Mapped[str] = mapped_column(Text, default='')
    document_id: Mapped[str | None] = mapped_column(ForeignKey('documents.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class ModelCall(Base):
    __tablename__ = 'model_calls'
    message_id: Mapped[str] = mapped_column(ForeignKey('messages.id'), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    model: Mapped[str] = mapped_column(String(100))
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    pricing: Mapped[dict] = mapped_column(JSON, default=dict)
    estimated_cny: Mapped[float | None] = mapped_column(Numeric(20,8), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_type: Mapped[str] = mapped_column(String(40), default='')
    state: Mapped[str] = mapped_column(String(24), default='pending')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)

class Evaluation(Base):
    __tablename__ = 'quality_evaluations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    cases: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class DocumentJob(Base):
    __tablename__ = 'document_jobs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey('admins.id'), nullable=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey('documents.id'), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default='queued', index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(100), default='等待处理')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default='')
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class ConversationRead(Base):
    __tablename__ = 'conversation_reads'
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), primary_key=True)
    admin_id: Mapped[str] = mapped_column(ForeignKey('admins.id'), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message_id: Mapped[str] = mapped_column(String(36))

class Trial(Base):
    __tablename__ = 'trial_requests'
    __table_args__ = (UniqueConstraint('conversation_id','client_id',name='uq_trial_request'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey('course_batches.id'))
    client_id: Mapped[str] = mapped_column(String(80))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    advisor_id: Mapped[str | None] = mapped_column(ForeignKey('admins.id'), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default='pending', index=True)
    note: Mapped[str] = mapped_column(Text, default='')
    reason: Mapped[str] = mapped_column(Text, default='')  # cancel / no_show reason
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class Audit(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(120))
    details:Mapped[str]=mapped_column(Text,default='',server_default='')
    target: Mapped[str] = mapped_column(String(240), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Course(Base):
    __tablename__='courses'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id:Mapped[str]=mapped_column(String(64),default=TENANT,index=True)
    name:Mapped[str]=mapped_column(String(160))
    exam_type:Mapped[str]=mapped_column(String(40),index=True)
    hours:Mapped[int]=mapped_column(Integer,default=0)
    class_type:Mapped[str]=mapped_column(String(80),default='基础班')
    description:Mapped[str]=mapped_column(Text,default='')
    status:Mapped[str]=mapped_column(String(24),default='active')
    version:Mapped[int]=mapped_column(Integer,default=1)
    is_demo:Mapped[bool]=mapped_column(Boolean,default=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,onupdate=now)

class CourseBatch(Base):
    __tablename__='course_batches'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id:Mapped[str]=mapped_column(String(64),default=TENANT,index=True)
    course_id:Mapped[str]=mapped_column(ForeignKey('courses.id'),index=True)
    name:Mapped[str]=mapped_column(String(160))
    mode:Mapped[str]=mapped_column(String(24),default='online')
    location:Mapped[str]=mapped_column(String(240),default='')
    schedule:Mapped[str]=mapped_column(Text,default='待人工确认')
    starts_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    ends_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    price_cents:Mapped[int|None]=mapped_column(Integer,nullable=True)
    price_from:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    price_until:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    sale_status:Mapped[str]=mapped_column(String(24),default='draft')
    version:Mapped[int]=mapped_column(Integer,default=1)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,onupdate=now)

class Student(Base):
    __tablename__='students'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    tenant_id:Mapped[str]=mapped_column(String(64),default=TENANT,index=True)
    name:Mapped[str]=mapped_column(String(100))
    owner_id:Mapped[str|None]=mapped_column(String(36),nullable=True,index=True)
    exam_targets:Mapped[list]=mapped_column(JSON,default=list)
    notes:Mapped[str]=mapped_column(Text,default='')
    status:Mapped[str]=mapped_column(String(24),default='active',index=True)
    merged_into_id:Mapped[str|None]=mapped_column(String(36),nullable=True)
    version:Mapped[int]=mapped_column(Integer,default=1)
    is_demo:Mapped[bool]=mapped_column(Boolean,default=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,onupdate=now)

class StudentInterest(Base):
    __tablename__='student_interests'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    student_id:Mapped[str]=mapped_column(ForeignKey('students.id'),index=True)
    exam_type:Mapped[str]=mapped_column(String(40))
    course_id:Mapped[str|None]=mapped_column(ForeignKey('courses.id'),nullable=True)
    target_year:Mapped[int|None]=mapped_column(Integer,nullable=True)
    status:Mapped[str]=mapped_column(String(24),default='exploring')
    notes:Mapped[str]=mapped_column(Text,default='')


class OpsHeartbeat(Base):
    """Last-seen signal for worker / backup / other ops components."""
    __tablename__ = 'ops_heartbeats'
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    status: Mapped[str] = mapped_column(String(24), default='ok')
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Alert(Base):
    """Persistent runtime alerts for the admin ops console."""
    __tablename__ = 'alerts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    code: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(16), default='warning')  # info | warning | critical
    status: Mapped[str] = mapped_column(String(16), default='open', index=True)  # open | acked | resolved
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text, default='')
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ack_by: Mapped[str] = mapped_column(String(100), default='')
    notify_state: Mapped[str] = mapped_column(String(24), default='pending')  # pending | skipped | queued | sent | failed


class KnowledgeTodo(Base):
    """Expiry / review reminders for knowledge owners."""
    __tablename__ = 'knowledge_todos'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id'), index=True)
    responsible_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # expiring_soon | expired
    status: Mapped[str] = mapped_column(String(16), default='open', index=True)  # open | done | dismissed
    title: Mapped[str] = mapped_column(String(240))
    message: Mapped[str] = mapped_column(Text, default='')
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[str] = mapped_column(String(100), default='')


class PrivacyRequest(Base):
    """Staff-handled contact withdrawal, deletion or anonymization requests."""
    __tablename__ = 'privacy_requests'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(64), default=TENANT, index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # withdraw_contact | anonymize | delete
    status: Mapped[str] = mapped_column(String(24), default='open', index=True)  # open | completed | rejected
    subject_name: Mapped[str] = mapped_column(String(100), default='')
    contact_hint: Mapped[str] = mapped_column(String(120), default='')  # masked hint only, not full contact
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    lead_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    note: Mapped[str] = mapped_column(Text, default='')
    resolution: Mapped[str] = mapped_column(Text, default='')
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(100), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

