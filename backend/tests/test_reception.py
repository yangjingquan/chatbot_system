from datetime import timedelta, timezone
import uuid
from sqlalchemy import select
from app.db import SessionLocal
from app.models import CourseBatch, now
from test_permissions import employee, new_lead

def test_presence_read_watermark_and_transfer_scope(admin):
    support,client=employee(admin,'support')
    advisor,other=employee(admin,'advisor')
    assert client.put('/api/admin/presence',json={'status':'online'}).status_code==200
    staff=admin.get('/api/admin/reception-staff').json()
    assert next(s for s in staff if s['id']==support['id'])['status']=='online'
    assert 'email' not in str(staff)
    lead,sid=new_lead(admin)
    with SessionLocal() as db:
        from app.models import Message
        db.add(Message(conversation_id=sid,role='user',content='人工接待测试'))
        db.commit()
    row=admin.get('/api/admin/conversations/'+sid).json()
    assert row['unread_count']==1
    assert admin.post('/api/admin/conversations/'+sid+'/read',json={'message_id':row['messages'][-1]['id']}).status_code==200
    assert admin.get('/api/admin/conversations/'+sid).json()['unread_count']==0
    assert client.post('/api/admin/conversations/'+sid+'/takeover').status_code==200
    assert other.post('/api/admin/conversations/'+sid+'/takeover').status_code==404
    assert client.post('/api/admin/conversations/'+sid+'/transfer',json={'assigned_to_id':advisor['id']}).status_code==422
    assert client.post('/api/admin/conversations/'+sid+'/transfer',json={'assigned_to_id':admin.get('/api/auth/me').json()['id']}).status_code==200
    assert client.post('/api/admin/conversations/'+sid+'/reply',json={'content':'已转交不能发'}).status_code==409

def test_trial_visitor_isolation_and_state_machine(admin):
    session=admin.post('/api/widget/sessions',json={}).json()
    headers={'Authorization':'Bearer '+session['token']}
    with SessionLocal() as db:
        batch=db.scalar(select(CourseBatch).where(CourseBatch.sale_status=='open'))
        batch_id=batch.id
    data={'batch_id':batch_id,'requested_at':(now()+timedelta(days=7)).isoformat(),'client_id':'trial-request-test'}
    path=f'/api/widget/sessions/{session["id"]}/trials'
    assert admin.post(path,headers=headers,json=data).status_code==422
    admin.post(f'/api/widget/sessions/{session["id"]}/contact',headers=headers,json={'name':'试听测试','contact':'trial@example.invalid','consent':True})
    response=admin.post(path,headers=headers,json=data)
    assert response.status_code==200,response.text
    trial=response.json()
    assert trial['status']=='pending'
    assert admin.post(path,headers=headers,json=data).json()['id']==trial['id']
    assert admin.get(path).status_code==404
    assert admin.patch('/api/admin/trials/'+trial['id'],json={'version':trial['version'],'status':'attended'}).status_code==409
    uid=admin.get('/api/auth/me').json()['id']
    confirmed=admin.patch('/api/admin/trials/'+trial['id'],json={'version':trial['version'],'status':'confirmed','advisor_id':uid,'scheduled_at':data['requested_at']})
    assert confirmed.status_code==200,confirmed.text
    assert admin.patch('/api/admin/trials/'+trial['id'],json={'version':trial['version'],'status':'cancelled'}).status_code==409
    assert admin.patch('/api/admin/trials/'+trial['id'],json={'version':confirmed.json()['version'],'status':'attended','confirm_early':True,'early_reason':'试听提前完成'}).status_code==200
    _,advisor=employee(admin,'advisor')
    assert all(t['id']!=trial['id'] for t in advisor.get('/api/admin/trials').json()['items'])

def test_trial_business_dedup_and_rebooking(admin):
    session=admin.post('/api/widget/sessions',json={}).json()
    headers={'Authorization':'Bearer '+session['token']}
    assert admin.post(f'/api/widget/sessions/{session["id"]}/contact',headers=headers,
                      json={'name':'预约去重测试','contact':'dedup@example.invalid','consent':True}).status_code==200
    with SessionLocal() as db:
        batch_id=db.scalar(select(CourseBatch).where(CourseBatch.sale_status=='open')).id
    requested=now()+timedelta(days=7)
    payload={'batch_id':batch_id,'requested_at':requested.isoformat(),'client_id':uuid.uuid4().hex}
    path=f'/api/widget/sessions/{session["id"]}/trials'
    created=admin.post(path,headers=headers,json=payload)
    assert created.status_code==200,created.text
    first=created.json()
    assert first['already_exists'] is False
    # A new request ID and an equivalent time in a different zone still refer to one appointment.
    duplicate=admin.post(path,headers=headers,json={**payload,'client_id':uuid.uuid4().hex,
                          'requested_at':requested.astimezone(timezone(timedelta(hours=8))).isoformat()})
    assert duplicate.status_code==200,duplicate.text
    assert duplicate.json()['id']==first['id']
    assert duplicate.json()['already_exists'] is True
    assert len(admin.get(path,headers=headers).json())==1
    lead=admin.get('/api/admin/trials/'+first['id']).json()
    assert sum('访客申请试听' in f['content'] for f in lead['followups'])==1
    # A different requested time remains a distinct booking.
    other=admin.post(path,headers=headers,json={**payload,'client_id':uuid.uuid4().hex,
                     'requested_at':(requested+timedelta(days=1)).isoformat()})
    assert other.status_code==200 and other.json()['id']!=first['id']
    uid=admin.get('/api/auth/me').json()['id']
    confirmed=admin.patch('/api/admin/trials/'+first['id'],json={
        'version':first['version'],'status':'confirmed','advisor_id':uid,
        'scheduled_at':payload['requested_at']})
    assert confirmed.status_code==200,confirmed.text
    repeat_confirmed=admin.post(path,headers=headers,json={**payload,'client_id':uuid.uuid4().hex})
    assert repeat_confirmed.status_code==200 and repeat_confirmed.json()['id']==first['id']
    assert repeat_confirmed.json()['status']=='confirmed'
    cancelled=admin.patch('/api/admin/trials/'+first['id'],json={
        'version':confirmed.json()['version'],'status':'cancelled','reason':'测试重新预约'})
    assert cancelled.status_code==200,cancelled.text
    rebooked=admin.post(path,headers=headers,json={**payload,'client_id':uuid.uuid4().hex})
    assert rebooked.status_code==200 and rebooked.json()['id']!=first['id']
    assert rebooked.json()['already_exists'] is False

def test_transfer_anonymous_conversation_to_advisor_and_presence_expires(admin):
    from app.models import Presence
    person,client=employee(admin,'advisor')
    assert client.put('/api/admin/presence',json={'status':'online'}).status_code==200
    with SessionLocal() as db:
        db.get(Presence,person['id']).heartbeat_at=now()-timedelta(seconds=91);db.commit()
    assert next(s for s in admin.get('/api/admin/reception-staff').json() if s['id']==person['id'])['status']=='offline'
    c=admin.post('/api/widget/sessions',json={}).json()
    assert admin.post('/api/admin/conversations/'+c['id']+'/takeover').status_code==200
    result=admin.post('/api/admin/conversations/'+c['id']+'/transfer',json={'assigned_to_id':person['id']})
    assert result.status_code==200,result.text
    assert client.get('/api/admin/conversations/'+c['id']).status_code==200

def test_presence_heartbeat_keeps_explicit_choice_and_expires(admin):
    from app.models import Presence
    person,client=employee(admin,'support')
    assert client.post('/api/admin/presence/heartbeat').json()['status']=='offline'
    assert client.put('/api/admin/presence',json={'status':'online'}).json()['status']=='online'
    assert client.post('/api/admin/presence/heartbeat').json()['status']=='online'
    assert client.put('/api/admin/presence',json={'status':'away'}).json()['status']=='away'
    # A heartbeat from another tab has no cached status to overwrite the choice.
    assert client.post('/api/admin/presence/heartbeat').json()['status']=='away'
    with SessionLocal() as db:
        db.get(Presence,person['id']).heartbeat_at=now()-timedelta(seconds=91);db.commit()
    assert client.post('/api/admin/presence/heartbeat').json()['status']=='offline'
