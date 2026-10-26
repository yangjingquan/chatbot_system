import os
from pathlib import Path
import tempfile
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(tempfile.mkdtemp()) / 'test.db')
os.environ['REDIS_URL'] = ''
os.environ['ADMIN_PASSWORD'] = 'test-only-strong-password-123'
os.environ['SESSION_SECRET'] = 'test-session-secret-' * 4
os.environ['EMBEDDING_ENABLED'] = 'false'
os.environ['DEEPSEEK_API_KEY'] = ''
os.environ['KNOWLEDGE_DIR'] = str(Path(__file__).resolve().parents[2] / 'knowledge_base')
import pytest
from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture
def client():
    # Each case gets an isolated limiter clock/counter. Limits still apply within a case.
    from app.security import _memory, _guard
    with _guard:
        _memory.clear()
    with TestClient(app) as test:
        yield test

@pytest.fixture
def admin(client):
    response = client.post('/api/auth/login', headers={'Origin': 'http://testserver'},
                           json={'email': 'admin@zhixing.local', 'password': 'test-only-strong-password-123'})
    assert response.status_code == 200
    client.headers['Origin'] = 'http://testserver'
    return client
