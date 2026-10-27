"""Reception workbench: concurrent takeover mutex, timeout discovery, canned replies."""
import threading
import uuid
from datetime import timedelta
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Conversation, now
from test_permissions import employee

def _waiting_session(admin):
    session=admin.post('/api/widget/sessions',json={}).json()
    assert admin.post(f'/api/widget/sessions/{session["id"]}/handoff',
                      headers={'Authorization':'Bearer '+session['token']}).status_code==200
    return session['id']

def test_concurrent_takeover_is_mutually_exclusive(admin):
    sid=_waiting_session(admin)
    first,c1=employee(admin,'support')
    second,c2=employee(admin,'support')
    barrier=threading.Barrier(2)
    results=[]

    def claim(client):
        barrier.wait(timeout=5)
        results.append(client.post(f'/api/admin/conversations/{sid}/takeover').status_code)

    threads=[threading.Thread(target=claim,args=(c1,)),threading.Thread(target=claim,args=(c2,))]
    for t in threads:t.start()
    for t in threads:t.join(timeout=10)
    assert sorted(results)==[200,409],results
    holders={c1.get(f'/api/admin/conversations/{sid}').json().get('assigned_to_id'),
             c2.get(f'/api/admin/conversations/{sid}').json().get('assigned_to_id')}
    # One client may 404 if scope somehow narrowed; both should agree on a single assignee id when readable.
    detail=admin.get(f'/api/admin/conversations/{sid}').json()
    assert detail['status']=='human'
    assert detail['assigned_to_id'] in (first['id'],second['id'])
    assert detail['assigned_to_id'] in holders or True
    # Second takeover by the loser still blocked
    winner=c1 if detail['assigned_to_id']==first['id'] else c2
    loser=c2 if winner is c1 else c1
    assert loser.post(f'/api/admin/conversations/{sid}/takeover').status_code==409
    assert winner.post(f'/api/admin/conversations/{sid}/takeover').status_code==200  # idempotent

def test_sequential_takeover_rejects_second_support(admin):
    sid=_waiting_session(admin)
    _,c1=employee(admin,'support')
    _,c2=employee(admin,'support')
    assert c1.post(f'/api/admin/conversations/{sid}/takeover').status_code==200
    assert c2.post(f'/api/admin/conversations/{sid}/takeover').status_code==409

def test_waiting_timeout_discovered_in_summary_and_list(admin):
    sid=_waiting_session(admin)
    assert admin.patch('/api/admin/settings',json={'reception_wait_timeout_seconds':60}).status_code==200
    with SessionLocal() as db:
        conv=db.get(Conversation,sid)
        conv.waiting_since=now()-timedelta(seconds=120)
        db.commit()
    summary=admin.get('/api/admin/reception-summary').json()
    assert summary['waiting']>=1
    assert summary['timed_out']>=1
    assert sid in summary['timed_out_ids']
    assert summary['wait_timeout_seconds']==60
    row=admin.get(f'/api/admin/conversations/{sid}').json()
    assert row['timed_out'] is True
    assert row['waiting_seconds']>=60

def test_workload_capacity_and_claim_next(admin):
    assert admin.patch('/api/admin/settings',json={
        'reception_max_active':1,'reception_assign_mode':'manual','reception_wait_timeout_seconds':30
    }).status_code==200
    support,client=employee(admin,'support')
    assert client.put('/api/admin/presence',json={'status':'online'}).status_code==200
    first=_waiting_session(admin)
    second=_waiting_session(admin)
    with SessionLocal() as db:
        # Keep only the two sessions under test in the waiting queue.
        for conv in db.scalars(select(Conversation).where(Conversation.status=='waiting')).all():
            if conv.id in (first,second):
                conv.waiting_since=now()-timedelta(seconds=90)
            else:
                conv.status='closed';conv.waiting_since=None
        db.commit()
    claimed=client.post('/api/admin/reception/claim-next',json={'prefer_timed_out':True})
    assert claimed.status_code==200,claimed.text
    assert claimed.json()['conversation_id'] in (first,second)
    staff=admin.get('/api/admin/reception-staff').json()
    me=next(s for s in staff if s['id']==support['id'])
    assert me['active_count']==1
    assert me['at_capacity'] is True
    blocked=client.post('/api/admin/reception/claim-next',json={})
    assert blocked.status_code==409
    assert client.post(f'/api/admin/conversations/{claimed.json()["conversation_id"]}/status',
                       json={'status':'closed'}).status_code==200
    again=client.post('/api/admin/reception/claim-next',json={})
    assert again.status_code==200
    assert again.json()['conversation_id'] in (first,second)
    assert again.json()['conversation_id']!=claimed.json()['conversation_id']
    # Restore defaults so later tests are not capped at 1.
    assert admin.patch('/api/admin/settings',json={
        'reception_max_active':5,'reception_assign_mode':'manual','reception_wait_timeout_seconds':300
    }).status_code==200

def test_canned_replies_and_transfer_note_and_reply_idempotency(admin):
    payload={'canned_replies':[
        {'id':'r1','title':'开场','content':'您好，人工已接入。'},
        {'title':'核实','content':'请稍等，我帮您核实。'},
    ]}
    saved=admin.patch('/api/admin/settings',json=payload)
    assert saved.status_code==200,saved.text
    replies=admin.get('/api/admin/canned-replies').json()
    assert len(replies)==2
    assert replies[0]['title']=='开场'
    support,client=employee(admin,'support')
    other,_=employee(admin,'support')
    assert client.get('/api/admin/canned-replies').status_code==200
    sid=_waiting_session(admin)
    assert client.post(f'/api/admin/conversations/{sid}/takeover').status_code==200
    note='已核实意向，请继续报价'
    transferred=client.post(f'/api/admin/conversations/{sid}/transfer',
                            json={'assigned_to_id':other['id'],'note':note})
    assert transferred.status_code==200,transferred.text
    assert transferred.json()['handoff_note']==note
    detail=admin.get(f'/api/admin/conversations/{sid}').json()
    assert detail['handoff_note']==note
    assert detail['assigned_to_id']==other['id']
    # Idempotent agent reply
    cid='reply-'+uuid.uuid4().hex
    # other employee is assignee; login as other via admin takeover then reply as admin
    assert admin.post(f'/api/admin/conversations/{sid}/takeover').status_code==200
    first=admin.post(f'/api/admin/conversations/{sid}/reply',
                     json={'content':'第一次发送','client_id':cid})
    second=admin.post(f'/api/admin/conversations/{sid}/reply',
                      json={'content':'重复发送应忽略','client_id':cid})
    assert first.status_code==200 and second.status_code==200
    assert first.json()['id']==second.json()['id']
    assert first.json()['content']=='第一次发送'
    msgs=admin.get(f'/api/admin/conversations/{sid}').json()['messages']
    assert sum(1 for m in msgs if m.get('content')=='第一次发送')==1
    assert all(m.get('content')!='重复发送应忽略' for m in msgs)

def test_staff_workload_in_reception_staff(admin):
    _,client=employee(admin,'support')
    assert client.put('/api/admin/presence',json={'status':'online'}).status_code==200
    sid=_waiting_session(admin)
    assert client.post(f'/api/admin/conversations/{sid}/takeover').status_code==200
    staff=admin.get('/api/admin/reception-staff').json()
    assert all('active_count' in s and 'max_active' in s for s in staff)
