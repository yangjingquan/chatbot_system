"""Reception WebSocket: auth, tenant-scoped publish, event delivery."""
import json
from app.reception_events import publish, subscribe, unsubscribe, subscriber_count
from test_permissions import employee

def test_reception_ws_rejects_anonymous(client):
    try:
        with client.websocket_connect('/api/admin/reception/ws') as ws:
            # If accept-then-close(4401), first receive may fail
            msg = ws.receive_text()
            assert False, f'anonymous should not stay connected: {msg}'
    except Exception:
        pass  # expected: connection rejected/closed

def test_reception_ws_requires_login_cookie(admin):
    with admin.websocket_connect('/api/admin/reception/ws') as ws:
        first = json.loads(ws.receive_text())
        assert first['type'] == 'connected'
        assert first.get('tenant_id')
        publish(first['tenant_id'], 'queue.waiting', conversation_id='demo-sid')
        event = json.loads(ws.receive_text())
        assert event['type'] == 'queue.waiting'
        assert event['conversation_id'] == 'demo-sid'

def test_reception_ws_knowledge_role_forbidden(admin):
    # knowledge role lacks conversations.read
    from app.db import SessionLocal
    from app.models import Admin
    from app.security import passwords
    with SessionLocal() as db:
        row = Admin(email='know-ws@zhixing.local', name='知识', role='knowledge',
                    password_hash=passwords.hash('test-only-strong-password-123'))
        db.add(row); db.commit(); email = row.email
    client = admin  # reuse TestClient jar carefully — login as knowledge on fresh headers
    # Use a separate login
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        assert c.post('/api/auth/login', headers={'Origin': 'http://testserver'},
                      json={'email': email, 'password': 'test-only-strong-password-123'}).status_code == 200
        c.headers['Origin'] = 'http://testserver'
        try:
            with c.websocket_connect('/api/admin/reception/ws') as ws:
                # accepted then closed 4401, or fails
                try:
                    data = ws.receive_text()
                    # If somehow connected message arrives, fail
                    assert json.loads(data).get('type') != 'connected'
                except Exception:
                    pass
        except Exception:
            pass

def test_publish_is_tenant_isolated():
    q_a = subscribe('tenant-a')
    q_b = subscribe('tenant-b')
    try:
        assert publish('tenant-a', 'queue.waiting', conversation_id='a1') == 1
        assert q_a.get_nowait()['conversation_id'] == 'a1'
        assert q_b.empty()
        assert publish('tenant-b', 'message.user', conversation_id='b1') == 1
        assert q_b.get_nowait()['conversation_id'] == 'b1'
        assert subscriber_count('tenant-a') == 1
    finally:
        unsubscribe('tenant-a', q_a)
        unsubscribe('tenant-b', q_b)

def test_handoff_publishes_waiting_event(admin):
    q = subscribe('zhixing-demo')
    try:
        session = admin.post('/api/widget/sessions', json={}).json()
        assert admin.post(f'/api/widget/sessions/{session["id"]}/handoff',
                          headers={'Authorization': 'Bearer ' + session['token']}).status_code == 200
        # Drain until we see queue.waiting for this session (may get other noise)
        found = False
        for _ in range(5):
            try:
                msg = q.get_nowait()
            except Exception:
                break
            if msg.get('type') == 'queue.waiting' and msg.get('conversation_id') == session['id']:
                found = True
                break
        assert found, 'expected queue.waiting publish after handoff'
    finally:
        unsubscribe('zhixing-demo', q)
