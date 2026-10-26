from datetime import timedelta
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
    assert admin.patch('/api/admin/trials/'+trial['id'],json={'version':confirmed.json()['version'],'status':'attended'}).status_code==200
    _,advisor=employee(admin,'advisor')
    assert all(t['id']!=trial['id'] for t in advisor.get('/api/admin/trials').json()['items'])

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
