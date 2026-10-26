from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./data/local.db'
    redis_url: str = ''
    admin_email: str = 'admin@zhixing.local'
    admin_password: str = ''
    session_secret: str = ''
    deepseek_api_key: str = ''
    deepseek_base_url: str = 'https://api.deepseek.com'
    deepseek_model: str = 'deepseek-flash'
    embedding_enabled: bool = False
    embedding_model: str = 'BAAI/bge-small-zh-v1.5'
    cookie_secure: bool = False
    public_origin: str = 'http://localhost:18080'
    knowledge_dir: str = '../knowledge_base'
    data_dir: str = './data'

@lru_cache
def settings():
    return Settings()
