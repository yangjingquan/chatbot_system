"""Shared metric definitions and database-side aggregates for dashboards.

Lists, CSV export and /admin/overview must share BusinessFilters so totals match.
Reply-latency averages are computed from a compact (waiting_since, first_agent) query,
not by loading every message into the application.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, and_, or_, cast, Integer, Date
from sqlalchemy.orm import Session
from .models import (
    Admin, Conversation, ConversationRead, Document, Lead, Message, Trial, now,
)
from .querying import BusinessFilters, conversation_query, lead_query, count, json_has, conversation_exam
from .scoring import EXAMS

SHANGHAI = ZoneInfo('Asia/Shanghai')

# Stable definitions exposed to API clients and docs. Keep in sync with docs/统计口径与系统性能.md.
METRIC_DEFINITIONS = {
    'conversations': '有效咨询：筛选范围内至少发过一条用户消息，或提交过联系请求的会话；默认排除演示。仅打开面板不计。',
    'leads': '留资线索：筛选范围内自愿提交联系方式形成的线索条数（与线索列表 total、导出行数一致）。',
    'contacted_conversations': '留资会话：有效咨询中已存在线索记录的会话数；留资率 = 本值 ÷ 有效咨询。',
    'trials': '试听：筛选范围内有效咨询关联的试听申请条数（含各状态）。',
    'customers': '成交：筛选范围内跟进阶段为「已成交」的线索数（需顾问回填，非机器人推断）。',
    'qualified': '合格及以上：阶段为已合格、商机推进或已成交的线索数。',
    'high_intent': '高意向：规则/人工等级为「高意向」的线索数；仅作跟进辅助，非成交概率。',
    'pending': '待跟进：阶段为新线索或待分配的线索数。',
    'handoffs': '转人工排队：有效咨询中当前状态为 waiting 的会话数。',
    'reply_latency_first_agent': '人工首次回复时长：有 waiting_since 且存在客服消息的会话，从排队时刻到首条 role=agent 消息的秒数平均值；无样本时为 null。',
}

def dialect(db: Session) -> str:
    return db.bind.dialect.name

def shanghai_day(db: Session, column):
    """Calendar date in Asia/Shanghai for trend buckets.

    PostgreSQL returns `date`; SQLite `date(...)` returns 'YYYY-MM-DD' text — do not
    cast SQLite to Date or the result processor may choke on non-str values.
    """
    if dialect(db) == 'postgresql':
        return func.timezone('Asia/Shanghai', column).cast(Date)
    return func.date(func.datetime(column, '+8 hours'))

def token_total_expr(db: Session, usage_column):
    if dialect(db) == 'postgresql':
        return func.coalesce(usage_column['total_tokens'].as_integer(), 0)
    return func.coalesce(cast(func.json_extract(usage_column, '$.total_tokens'), Integer), 0)

def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

def business_date(value: datetime) -> date:
    return aware(value).astimezone(SHANGHAI).date()

def lead_stage_counts(db: Session, user: Admin, filters: BusinessFilters) -> dict:
    base = lead_query(db, user, filters).with_only_columns(Lead.stage, func.count()).group_by(Lead.stage)
    rows = {stage: n for stage, n in db.execute(base).all()}
    pending = rows.get('新线索', 0) + rows.get('待分配', 0)
    customers = rows.get('已成交', 0)
    qualified = customers + rows.get('已合格', 0) + rows.get('商机推进', 0)
    return {'pending': pending, 'customers': customers, 'qualified': qualified, 'by_stage': rows}

def lead_level_counts(db: Session, user: Admin, filters: BusinessFilters) -> list[dict]:
    base = lead_query(db, user, filters).with_only_columns(Lead.level, func.count()).group_by(Lead.level)
    return [{'name': level, 'value': n} for level, n in db.execute(base).all()]

def exam_lead_counts(db: Session, user: Admin, filters: BusinessFilters) -> list[dict]:
    """Count filtered leads whose tags or linked conversation analysis mention each exam."""
    result = []
    for exam in EXAMS:
        linked = select(Conversation.id).where(
            Conversation.id == Lead.conversation_id, conversation_exam(db, exam)
        ).correlate(Lead).exists()
        query = lead_query(db, user, filters).where(or_(json_has(db, Lead.tags, exam), linked))
        n = count(db, query)
        if n:
            result.append({'name': exam, 'value': n})
    return result

def trend_series(db: Session, user: Admin, filters: BusinessFilters, days: int = 7) -> list[dict]:
    today = business_date(now())
    start_day = today - timedelta(days=days - 1)
    # Respect explicit date filters: only emit days that intersect the filter window.
    if filters.date_from and filters.date_from > start_day:
        start_day = filters.date_from
    end_day = filters.date_to if filters.date_to and filters.date_to < today else today
    if start_day > end_day:
        return []

    conv_day = shanghai_day(db, Conversation.created_at)
    lead_day = shanghai_day(db, Lead.created_at)
    def normalize_day(value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str) and value:
            return date.fromisoformat(value[:10])
        return value

    conv_rows = {
        normalize_day(day): n
        for day, n in db.execute(
            conversation_query(db, user, filters).with_only_columns(conv_day, func.count()).group_by(conv_day)
        ).all()
    }
    lead_rows = {
        normalize_day(day): n
        for day, n in db.execute(
            lead_query(db, user, filters).with_only_columns(lead_day, func.count()).group_by(lead_day)
        ).all()
    }

    series = []
    cursor = start_day
    while cursor <= end_day:
        series.append({
            'date': cursor.isoformat(),
            'conversations': int(conv_rows.get(cursor) or 0),
            'leads': int(lead_rows.get(cursor) or 0),
        })
        cursor += timedelta(days=1)
    return series[-days:]

def message_usage(db: Session, user: Admin, filters: BusinessFilters) -> dict:
    conv_ids = conversation_query(db, user, filters).with_only_columns(Conversation.id).order_by(None).subquery()
    tokens = db.scalar(
        select(func.coalesce(func.sum(token_total_expr(db, Message.usage)), 0)).where(
            Message.conversation_id.in_(select(conv_ids.c.id))
        )
    ) or 0
    deepseek = db.scalar(
        select(func.count()).select_from(Message).where(
            Message.conversation_id.in_(select(conv_ids.c.id)), Message.mode == 'deepseek'
        )
    ) or 0
    return {'total_tokens': int(tokens), 'deepseek_answers': int(deepseek)}

def knowledge_gap_rows(db: Session, user: Admin, filters: BusinessFilters, limit: int = 20) -> list[Message]:
    conv_ids = conversation_query(db, user, filters).with_only_columns(Conversation.id).order_by(None).subquery()
    unknown = select(Message.reply_to).where(
        Message.mode == 'unknown', Message.reply_to.isnot(None),
        Message.conversation_id.in_(select(conv_ids.c.id)),
    )
    return list(db.scalars(
        select(Message).where(
            Message.role == 'user',
            Message.id.in_(unknown),
            Message.conversation_id.in_(select(conv_ids.c.id)),
        ).order_by(Message.created_at.desc()).limit(limit)
    ))

def document_counts(db: Session, tenant_id: str) -> dict:
    moment = aware(now())
    docs = select(Document).where(Document.tenant_id == tenant_id)
    total = count(db, docs)
    active_public = and_(
        Document.status == 'published',
        Document.visibility == 'public',
        or_(Document.valid_from.is_(None), Document.valid_from <= moment),
        or_(Document.valid_until.is_(None), Document.valid_until >= moment),
    )
    published = db.scalar(select(func.count()).select_from(Document).where(Document.tenant_id == tenant_id, active_public)) or 0
    expired = db.scalar(select(func.count()).select_from(Document).where(
        Document.tenant_id == tenant_id,
        Document.status == 'published',
        Document.valid_until.isnot(None),
        Document.valid_until < moment,
    )) or 0
    return {'documents': total, 'published': int(published), 'expired_documents': int(expired)}

def trial_count(db: Session, user: Admin, filters: BusinessFilters) -> int:
    conv_ids = conversation_query(db, user, filters).with_only_columns(Conversation.id).order_by(None).subquery()
    return db.scalar(
        select(func.count()).select_from(Trial).where(
            Trial.tenant_id == user.tenant_id,
            Trial.conversation_id.in_(select(conv_ids.c.id)),
        )
    ) or 0

def first_agent_reply_latency(db: Session, user: Admin, filters: BusinessFilters) -> dict:
    """Average seconds from waiting_since to the first agent message among filtered conversations."""
    conv_ids = conversation_query(db, user, filters).with_only_columns(Conversation.id).order_by(None).subquery()
    first_agent = (
        select(func.min(Message.created_at))
        .where(Message.conversation_id == Conversation.id, Message.role == 'agent')
        .correlate(Conversation)
        .scalar_subquery()
    )
    rows = db.execute(
        select(Conversation.waiting_since, first_agent).where(
            Conversation.id.in_(select(conv_ids.c.id)),
            Conversation.waiting_since.isnot(None),
        )
    ).all()
    samples = []
    for waiting, first in rows:
        if waiting is None or first is None:
            continue
        seconds = (aware(first) - aware(waiting)).total_seconds()
        if seconds >= 0:
            samples.append(seconds)
    if not samples:
        return {'avg_seconds': None, 'sample_count': 0}
    return {'avg_seconds': round(sum(samples) / len(samples), 1), 'sample_count': len(samples)}

def contacted_conversation_count(db: Session, user: Admin, filters: BusinessFilters) -> int:
    conv_ids = conversation_query(db, user, filters).with_only_columns(Conversation.id).order_by(None).subquery()
    return db.scalar(
        select(func.count()).select_from(conv_ids).where(
            select(Lead.id).where(Lead.conversation_id == conv_ids.c.id).exists()
        )
    ) or 0

def high_intent_count(db: Session, user: Admin, filters: BusinessFilters) -> int:
    return count(db, lead_query(db, user, filters).where(Lead.level == '高意向'))

def handoff_waiting_count(db: Session, user: Admin, filters: BusinessFilters) -> int:
    return count(db, conversation_query(db, user, filters, status='waiting'))

def build_overview_counts(db: Session, user: Admin, filters: BusinessFilters) -> dict:
    stages = lead_stage_counts(db, user, filters)
    docs = document_counts(db, user.tenant_id)
    return {
        'conversations': count(db, conversation_query(db, user, filters)),
        'leads': count(db, lead_query(db, user, filters)),
        'contacted_conversations': contacted_conversation_count(db, user, filters),
        'high_intent': high_intent_count(db, user, filters),
        'documents': docs['documents'],
        'published': docs['published'],
        'pending': stages['pending'],
        'handoffs': handoff_waiting_count(db, user, filters),
        'customers': stages['customers'],
        'qualified': stages['qualified'],
        'trials': trial_count(db, user, filters),
    }

def unread_total(db: Session, user: Admin) -> int:
    """Total unread visitor messages across non-closed conversations in scope (one SQL)."""
    open_ids = scope_open_conversation_ids(db, user).subquery()
    return db.scalar(
        select(func.count()).select_from(Message).outerjoin(
            ConversationRead,
            and_(
                ConversationRead.conversation_id == Message.conversation_id,
                ConversationRead.admin_id == user.id,
            ),
        ).where(
            Message.conversation_id.in_(select(open_ids.c.id)),
            Message.role == 'user',
            or_(
                ConversationRead.read_at.is_(None),
                Message.created_at > ConversationRead.read_at,
                and_(Message.created_at == ConversationRead.read_at, Message.id > ConversationRead.message_id),
            ),
        )
    ) or 0

def scope_open_conversation_ids(db: Session, user: Admin):
    from .permissions import scope_conversations
    return scope_conversations(
        select(Conversation.id).where(Conversation.status != 'closed'), user
    )

def reception_queue_stats(db: Session, user: Admin, wait_timeout_seconds: int) -> dict:
    from .permissions import scope_conversations
    waiting_q = scope_conversations(select(Conversation).where(Conversation.status == 'waiting'), user)
    waiting_n = count(db, waiting_q)
    cutoff = aware(now()) - timedelta(seconds=wait_timeout_seconds)
    timed_q = waiting_q.where(Conversation.waiting_since.isnot(None), Conversation.waiting_since <= cutoff)
    timed_n = count(db, timed_q)
    oldest = db.scalar(
        scope_conversations(
            select(func.min(Conversation.waiting_since)).where(Conversation.status == 'waiting'), user
        )
    )
    oldest_wait = None
    if oldest is not None:
        oldest_wait = max(0, int((aware(now()) - aware(oldest)).total_seconds()))
    timed_ids = list(db.scalars(
        timed_q.order_by(Conversation.waiting_since.asc().nulls_last(), Conversation.id).limit(20)
    ))
    return {
        'waiting': waiting_n,
        'timed_out': timed_n,
        'oldest_wait_seconds': oldest_wait,
        'timed_out_ids': [c.id for c in timed_ids],
    }

def last_message_preview(db: Session, conversation_id: str) -> tuple[str, int, str | None]:
    """Return preview, message count and role without loading the full transcript."""
    total = db.scalar(
        select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id)
    ) or 0
    if not total:
        return '', 0, None
    last = db.scalar(
        select(Message).where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc()).limit(1)
    )
    from .privacy import redact_text
    preview = redact_text(last.content)[:120] if last else ''
    return preview, int(total), last.role if last else None
