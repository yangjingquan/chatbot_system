import hashlib
import hmac
import ipaddress
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
from .models import Admin, AdminSession, Audit, Company, Conversation, TENANT

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

def _parse_networks(raw: str):
    nets = []
    for part in (raw or '').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return nets

def client_ip(request: Request) -> str:
    """Prefer peer address; only honor X-Forwarded-* when the peer is a configured trusted proxy."""
    peer = request.client.host if request.client else 'local'
    trusted = settings().trusted_proxies.strip()
    if not trusted or peer in ('local', 'testclient'):
        return peer
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if not any(peer_ip in net for net in _parse_networks(trusted)):
        return peer
    forwarded = (request.headers.get('x-forwarded-for') or '').split(',')[0].strip()
    for candidate in (forwarded, (request.headers.get('x-real-ip') or '').strip()):
        if not candidate:
            continue
        try:
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            continue
    return peer

def normalize_origin(value: str) -> str:
    text = (value or '').strip()
    if not text:
        return ''
    if '://' not in text:
        text = 'https://' + text
    parts = urlsplit(text.rstrip('/'))
    if parts.scheme not in ('http', 'https') or not parts.netloc or parts.username or parts.password:
        return ''
    host = parts.hostname
    if not host or ' ' in parts.netloc or '/' in host:
        return ''
    # Reject obvious placeholders without a dot, except localhost / IPs.
    if host not in ('localhost',) and not host.replace('.', '').isdigit() and '.' not in host:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return ''
    port = f':{parts.port}' if parts.port else ''
    return f'{parts.scheme}://{host.lower()}{port}'

def allowed_origins():
    origin = normalize_origin(settings().public_origin) or settings().public_origin.rstrip('/')
    origins = {origin} if origin else set()
    if settings().database_url.startswith('sqlite'):
        origins.add('http://testserver')
    parsed = urlsplit(origin or 'http://localhost')
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

def record_security_event(db: Session, action: str, target: str = '', details: str = '', actor: str = '系统'):
    db.add(Audit(actor=actor[:100], tenant_id=TENANT, action=action[:120], target=(target or '')[:240], details=(details or '')[:2000]))

def configured_embed_origins(db: Session) -> set[str]:
    company = db.get(Company, TENANT)
    raw = (company.config if company else {}).get('allowed_embed_origins') or []
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.splitlines() if part.strip()]
    origins = {normalize_origin(item) for item in raw}
    origins.discard('')
    public = normalize_origin(settings().public_origin)
    if public:
        origins.add(public)
    if settings().database_url.startswith('sqlite'):
        origins.add('http://testserver')
    return origins

def resolve_embed_origin(request: Request, claimed: str | None = None) -> str:
    for candidate in (claimed, request.headers.get('origin'), settings().public_origin):
        origin = normalize_origin(candidate or '')
        if origin:
            return origin
    return ''

def assert_embed_allowed(db: Session, request: Request, claimed: str | None = None) -> str:
    origin = resolve_embed_origin(request, claimed)
    allowed = configured_embed_origins(db)
    if not origin or origin not in allowed:
        record_security_event(
            db, '拒绝嵌入来源', target=origin or '(空)',
            details=f'ip={client_ip(request)}; path={request.url.path}',
            actor='系统',
        )
        db.commit()
        raise HTTPException(403, '当前网站未获准接入客服组件，请联系管理员配置允许域名')
    return origin

def validate_embed_origin_list(values: list[str]) -> list[str]:
    if len(values) > 30:
        raise HTTPException(422, '允许嵌入域名最多 30 个')
    cleaned = []
    seen = set()
    for item in values:
        origin = normalize_origin(item)
        if not origin:
            raise HTTPException(422, f'域名格式无效：{item[:80]}')
        if origin in seen:
            continue
        seen.add(origin)
        cleaned.append(origin)
    return cleaned

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
