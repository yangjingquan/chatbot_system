import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy.orm import Session
from .config import settings
from .db import get_db
from .models import Admin, AdminSession, Conversation, TENANT

passwords = PasswordHash.recommended()
_memory = {}
_guard = threading.Lock()
_redis = None

def digest(token: str) -> str:
    return hmac.new(settings().session_secret.encode(), token.encode(), hashlib.sha256).hexdigest()

def redis_client():
    global _redis
    if not settings().redis_url:
        return None
    if _redis is None:
        import redis
        _redis = redis.Redis.from_url(settings().redis_url, socket_connect_timeout=1, socket_timeout=1, decode_responses=True)
    return _redis

def limit(key: str, maximum: int, seconds: int = 60):
    client = redis_client()
    if client:
        try:
            count = client.eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n", 1, f'rate:{key}', seconds)
            if count > maximum:
                raise HTTPException(429, '请求过于频繁，请稍后再试')
            return
        except HTTPException:
            raise
        except Exception:
            pass
    with _guard:
        moment = time.monotonic()
        if len(_memory) > 5000:
            _memory.clear()
        count, end = _memory.get(key, (0, moment + seconds))
        if moment >= end:
            count, end = 0, moment + seconds
        _memory[key] = (count + 1, end)
        if count >= maximum:
            raise HTTPException(429, '请求过于频繁，请稍后再试')

def allowed_origins():
    origin = settings().public_origin.rstrip('/')
    parsed = urlsplit(origin)
    origins = {origin}
    if settings().database_url.startswith('sqlite'):
        origins.add('http://testserver')
    if parsed.hostname in ('localhost', '127.0.0.1'):
        port = f':{parsed.port}' if parsed.port else ''
        origins.update({f'{parsed.scheme}://localhost{port}', f'{parsed.scheme}://127.0.0.1{port}'})
    return origins

def check_origin(request: Request):
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return
    origin = request.headers.get('origin')
    # Browser writes require an explicit trusted Origin. Non-browser clients use same header.
    if origin not in allowed_origins():
        raise HTTPException(403, '来源校验失败，请从本站管理后台操作')

def admin_user(request: Request, db: Session = Depends(get_db)) -> Admin:
    check_origin(request)
    token = request.cookies.get('admin_session', '')
    login = db.get(AdminSession, digest(token)) if token else None
    if not login:
        raise HTTPException(401, '请先登录管理后台')
    created = login.created_at.replace(tzinfo=timezone.utc) if login.created_at.tzinfo is None else login.created_at
    if (datetime.now(timezone.utc) - created).total_seconds() > 43200:
        db.delete(login)
        db.commit()
        raise HTTPException(401, '登录已过期，请重新登录')
    user = db.get(Admin, login.admin_id)
    if not user or user.tenant_id != TENANT or not user.is_active:
        raise HTTPException(401, '登录无效')
    return user

def visitor(request: Request, session_id: str, db: Session) -> Conversation:
    token = request.headers.get('authorization', '').removeprefix('Bearer ')
    conv = db.get(Conversation, session_id)
    if not conv or conv.tenant_id != TENANT or not token or not secrets.compare_digest(conv.token_hash, digest(token)):
        raise HTTPException(404, '会话不存在或访问凭据无效')
    created = conv.created_at.replace(tzinfo=timezone.utc) if conv.created_at.tzinfo is None else conv.created_at
    if (datetime.now(timezone.utc) - created).total_seconds() > 7 * 86400:
        raise HTTPException(410, '会话已过期，请新建咨询')
    return conv
