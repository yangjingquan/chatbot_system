"""Authenticated encryption for contact values at rest.

Prefer CONTACT_SECRET so SESSION_SECRET can rotate independently for cookies.
CONTACT_SECRET_PREVIOUS allows decrypting old ciphertext during a rotation window.
Never overwrite the live secret without first re-encrypting stored contacts.
"""
import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import TypeDecorator, Text
from .config import settings

PREFIX = 'enc:v1:'

def _fernet(secret: str) -> Fernet:
    if not secret:
        raise RuntimeError('缺少联系方式加密密钥；请配置 CONTACT_SECRET 或 SESSION_SECRET')
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))

def contact_secrets():
    cfg = settings()
    primary = (cfg.contact_secret or cfg.session_secret or '').strip()
    previous = (cfg.contact_secret_previous or '').strip()
    return primary, previous

def cipher(secret: str | None = None):
    primary, _ = contact_secrets()
    return _fernet(primary if secret is None else secret)

def encrypt(value: str) -> str:
    return PREFIX + cipher().encrypt(value.encode()).decode()

def decrypt(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.startswith(PREFIX):
        return value
    token = value[len(PREFIX):].encode()
    primary, previous = contact_secrets()
    tried = []
    for secret in [primary] + ([previous] if previous and previous != primary else []):
        if not secret or secret in tried:
            continue
        tried.append(secret)
        try:
            return _fernet(secret).decrypt(token).decode()
        except InvalidToken:
            continue
    raise InvalidToken('联系方式密文无法用当前或上一版密钥解密')

class EncryptedContact(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value) if value is not None else None

    def process_result_value(self, value, dialect):
        return decrypt(value)

def migrate_contacts(connection):
    """Encrypt any leftover plaintext contact rows (idempotent)."""
    from sqlalchemy import text
    for row in connection.execute(text('SELECT id, contact FROM leads')).all():
        if row.contact and not str(row.contact).startswith(PREFIX):
            connection.execute(
                text('UPDATE leads SET contact=:contact WHERE id=:id'),
                {'contact': encrypt(row.contact), 'id': row.id},
            )

def rotate_contacts(connection) -> int:
    """Re-encrypt every contact with the current primary secret.

    During rotation set CONTACT_SECRET to the new value and CONTACT_SECRET_PREVIOUS
    to the previous value (or the old SESSION_SECRET if contacts were derived from it).
    Returns the number of rows rewritten.
    """
    from sqlalchemy import text
    count = 0
    for row in connection.execute(text('SELECT id, contact FROM leads')).all():
        if not row.contact:
            continue
        plain = decrypt(row.contact)
        connection.execute(
            text('UPDATE leads SET contact=:contact WHERE id=:id'),
            {'contact': encrypt(plain), 'id': row.id},
        )
        count += 1
    return count
