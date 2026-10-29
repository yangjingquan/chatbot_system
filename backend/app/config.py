from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./data/local.db'
    redis_url: str = ''
    admin_email: str = 'admin@zhixing.local'
    admin_password: str = ''
    session_secret: str = ''
    # Preferred contact encryption secret; falls back to session_secret when empty.
    contact_secret: str = ''
    # Previous contact secret kept only during a rotation window.
    contact_secret_previous: str = ''
    deepseek_api_key: str = ''
    deepseek_base_url: str = 'https://api.deepseek.com'
    deepseek_model: str = 'deepseek-flash'
    embedding_enabled: bool = False
    embedding_model: str = 'BAAI/bge-small-zh-v1.5'
    cookie_secure: bool = False
    public_origin: str = 'http://localhost:18080'
    # Comma-separated hosts for TrustedHostMiddleware; empty disables the check.
    allowed_hosts: str = ''
    # Comma-separated IPs/CIDRs allowed to set X-Forwarded-*; empty trusts request.client only.
    # Uvicorn FORWARDED_ALLOW_IPS should match the reverse-proxy hop; see docs/对外部署与访问控制.md.
    trusted_proxies: str = ''
    knowledge_dir: str = '../knowledge_base'
    data_dir: str = './data'
    # Directory for logical/encrypted backups written by scripts (host path or container /data/backups).
    backup_dir: str = './data/backups'
    # Full pg_dump backups are distinct from the limited logical JSON backup.
    physical_backup_dir: str = './backups'
    physical_backup_max_age_minutes: int = 75
    smtp_host: str = ''
    smtp_port: int = 587
    smtp_security: str = 'starttls'
    smtp_username: str = ''
    smtp_password: str = ''
    smtp_from: str = ''
    smtp_timeout_seconds: int = 15
    alert_monitor_interval_seconds: int = 60
    alert_api_health_url: str = 'http://backend:8000/api/health'
    api_metric_flush_seconds: int = 15
    # Default retention days when company config omits the field. 0 = do not auto-clean.
    retention_conversation_days: int = 365
    retention_lead_days: int = 730
    retention_audit_days: int = 1095
    retention_interval_seconds: int = 3600
    # Public widget rate limits (per client IP).
    public_session_limit: int = 30
    public_session_window: int = 60
    public_chat_limit: int = 20
    public_chat_window: int = 60

@lru_cache
def settings():
    return Settings()
