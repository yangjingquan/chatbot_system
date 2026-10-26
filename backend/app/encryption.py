"""Authenticated encryption for contact values at rest; secret changes need a migration."""
import base64
import hashlib
from cryptography.fernet import Fernet
from sqlalchemy.types import TypeDecorator, Text
from .config import settings

PREFIX='enc:v1:'

def cipher():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings().session_secret.encode()).digest()))

def encrypt(value: str) -> str:
    return PREFIX+cipher().encrypt(value.encode()).decode()

class EncryptedContact(TypeDecorator):
    impl=Text
    cache_ok=True
    def process_bind_param(self,value,dialect):
        return encrypt(value) if value is not None else None
    def process_result_value(self,value,dialect):
        if value is None:return None
        if value.startswith(PREFIX):return cipher().decrypt(value[len(PREFIX):].encode()).decode()
        return value

def migrate_contacts(connection):
    from sqlalchemy import text
    for row in connection.execute(text('SELECT id,contact FROM leads')).all():
        if row.contact and not row.contact.startswith(PREFIX):
            connection.execute(text('UPDATE leads SET contact=:contact WHERE id=:id'),{'contact':encrypt(row.contact),'id':row.id})
