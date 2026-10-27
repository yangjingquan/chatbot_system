"""Knowledge expiry reminders and owner todos. OCR remains intentionally disabled."""
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select, or_
from .models import Admin, Document, KnowledgeTodo, now
from .document_service import aware

# Remind owners this many days before valid_until.
EXPIRY_WARN_DAYS = 14

def needed_kinds(doc, moment=None):
    moment = aware(moment or now())
    if doc.status != 'published' or not doc.valid_until:
        return []
    until = aware(doc.valid_until)
    if until < moment:
        return ['expired']
    if until <= moment + timedelta(days=EXPIRY_WARN_DAYS):
        return ['expiring_soon']
    return []

def todo_copy(doc, kind):
    until = aware(doc.valid_until).isoformat() if doc.valid_until else ''
    if kind == 'expired':
        return f'资料已过期：{doc.title}', f'「{doc.title}」已于 {until} 失效，已退出公开回答。请核对官方来源后更新并重新发布，或确认下架。'
    return f'资料即将到期：{doc.title}', f'「{doc.title}」将于 {until} 失效（提前 {EXPIRY_WARN_DAYS} 天提醒）。请资料负责人核对并更新。'

def sync_expiry_todos(db, tenant_id: str) -> int:
    """Create/update/close open expiry todos for published docs. Idempotent per document+kind."""
    moment = aware(now())
    docs = db.scalars(select(Document).where(Document.tenant_id == tenant_id, Document.status == 'published', Document.valid_until.is_not(None))).all()
    created = 0
    open_rows = db.scalars(select(KnowledgeTodo).where(KnowledgeTodo.tenant_id == tenant_id, KnowledgeTodo.status == 'open')).all()
    open_map = {(t.document_id, t.kind): t for t in open_rows}
    seen = set()
    for doc in docs:
        for kind in needed_kinds(doc, moment):
            seen.add((doc.id, kind))
            title, message = todo_copy(doc, kind)
            due = aware(doc.valid_until) if kind == 'expired' else aware(doc.valid_until) - timedelta(days=EXPIRY_WARN_DAYS)
            existing = open_map.get((doc.id, kind))
            if existing:
                existing.title = title
                existing.message = message
                existing.due_at = due
                existing.responsible_id = doc.responsible_id
                continue
            db.add(KnowledgeTodo(tenant_id=tenant_id, document_id=doc.id, responsible_id=doc.responsible_id,
                                 kind=kind, title=title, message=message, due_at=due))
            created += 1
    for key, row in open_map.items():
        if key not in seen and row.kind in ('expiring_soon', 'expired'):
            # Validity extended, archived elsewhere, or no longer published → auto close.
            doc = db.get(Document, row.document_id)
            if not doc or doc.status != 'published' or not needed_kinds(doc, moment):
                row.status = 'done'
                row.completed_at = moment
                row.completed_by = '系统'
    # If expired todo exists, close the prior expiring_soon open todo for the same doc.
    for doc in docs:
        if 'expired' in needed_kinds(doc, moment):
            soon = open_map.get((doc.id, 'expiring_soon'))
            if soon and soon.status == 'open':
                soon.status = 'done'
                soon.completed_at = moment
                soon.completed_by = '系统'
    db.flush()
    return created

def todo_data(db, todo):
    owner = db.get(Admin, todo.responsible_id) if todo.responsible_id else None
    doc = db.get(Document, todo.document_id)
    return {
        'id': todo.id, 'document_id': todo.document_id, 'document_title': doc.title if doc else '',
        'responsible_id': todo.responsible_id, 'responsible_name': owner.name if owner else '未指定负责人',
        'kind': todo.kind, 'status': todo.status, 'title': todo.title, 'message': todo.message,
        'due_at': todo.due_at.isoformat() if todo.due_at else None,
        'created_at': todo.created_at.isoformat(),
        'completed_at': todo.completed_at.isoformat() if todo.completed_at else None,
        'completed_by': todo.completed_by,
        'document_status': doc.status if doc else '',
        'valid_until': doc.valid_until.isoformat() if doc and doc.valid_until else None,
    }

def list_knowledge_todos(db, user, status='open', kind='', mine=False):
    query = select(KnowledgeTodo).where(KnowledgeTodo.tenant_id == user.tenant_id)
    if status: query = query.where(KnowledgeTodo.status == status)
    if kind in ('expiring_soon', 'expired'): query = query.where(KnowledgeTodo.kind == kind)
    if mine or user.role == 'knowledge':
        # Knowledge operators mainly see their own assignments; admins see all unless mine=true.
        if mine or user.role != 'admin':
            query = query.where(or_(KnowledgeTodo.responsible_id == user.id, KnowledgeTodo.responsible_id.is_(None)))
    return query.order_by(KnowledgeTodo.due_at, KnowledgeTodo.id)

def complete_knowledge_todo(db, todo_id, user, action='done'):
    if action not in ('done', 'dismissed'):
        raise HTTPException(422, '仅支持完成或忽略')
    todo = db.scalar(select(KnowledgeTodo).where(KnowledgeTodo.id == todo_id, KnowledgeTodo.tenant_id == user.tenant_id).with_for_update())
    if not todo:
        raise HTTPException(404, '知识待办不存在')
    if todo.status != 'open':
        raise HTTPException(409, '待办已处理')
    if user.role == 'knowledge' and todo.responsible_id and todo.responsible_id != user.id:
        raise HTTPException(403, '只能处理本人负责的资料待办')
    todo.status = action
    todo.completed_at = now()
    todo.completed_by = user.name
    return todo
