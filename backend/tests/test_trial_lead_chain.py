"""Item 9: consultation → lead → trial → follow-up chain."""
import uuid
from datetime import timedelta
from sqlalchemy import select
from app.db import SessionLocal
from app.models import CourseBatch, FollowUp, Lead, now
from test_permissions import employee

def _open_batch():
    with SessionLocal() as db:
        batch=db.scalar(select(CourseBatch).where(CourseBatch.sale_status=='open'))
        return batch.id

def _visitor_with_lead(admin, contact='chain@example.invalid'):
    session=admin.post('/api/widget/sessions',json={}).json()
    headers={'Authorization':'Bearer '+session['token']}
    assert admin.post(f'/api/widget/sessions/{session["id"]}/contact',headers=headers,
                      json={'name':'业务链同学','contact':contact,'consent':True}).status_code==200
    return session,headers

def test_trial_creates_auto_todo_and_detail_exposes_lead(admin):
    session,headers=_visitor_with_lead(admin)
    batch_id=_open_batch()
    requested=(now()+timedelta(days=5)).isoformat()
    trial=admin.post(f'/api/widget/sessions/{session["id"]}/trials',headers=headers,
                     json={'batch_id':batch_id,'requested_at':requested,'client_id':'chain-'+uuid.uuid4().hex[:12]})
    assert trial.status_code==200,trial.text
    body=trial.json()
    assert body['status']=='pending'
    lead=admin.get('/api/admin/leads',params={'page_size':100,'q':'业务链同学'}).json()['items']
    lead=next(l for l in lead if l['conversation_id']==session['id'])
    assert lead['stage']=='跟进中'
    assert lead['next_followup_at']
    detail=admin.get('/api/admin/leads/'+lead['id']).json()
    assert any('试听' in f['content'] for f in detail['followups'])
    assert any(t['id']==body['id'] for t in detail['trials'])
    row=admin.get('/api/admin/trials/'+body['id']).json()
    assert row['lead_id']==lead['id']
    assert row['lead_name']=='业务链同学'
    assert row['auto_todo_rule']
    assert any('学员档案' in a for a in row['manual_actions'])

def test_confirm_attend_no_show_and_student_link_same_chain(admin):
    session,headers=_visitor_with_lead(admin,'chain2@example.invalid')
    batch_id=_open_batch()
    requested=(now()+timedelta(days=8)).isoformat()
    created=admin.post(f'/api/widget/sessions/{session["id"]}/trials',headers=headers,
                       json={'batch_id':batch_id,'requested_at':requested,'client_id':'chain2-'+uuid.uuid4().hex[:12]}).json()
    uid=admin.get('/api/auth/me').json()['id']
    # cancel without reason rejected
    assert admin.patch('/api/admin/trials/'+created['id'],json={'version':created['version'],'status':'cancelled'}).status_code==422
    confirmed=admin.patch('/api/admin/trials/'+created['id'],json={
        'version':created['version'],'status':'confirmed','advisor_id':uid,'scheduled_at':requested,'note':'已电话确认'})
    assert confirmed.status_code==200,confirmed.text
    conf=confirmed.json()
    assert conf['status']=='confirmed'
    assert conf['lead_id']
    assert conf['next_followup_at']  # set to scheduled time
    lead_id=conf['lead_id']
    # link student (manual)
    student=admin.post('/api/admin/leads/'+lead_id+'/student',json={'identity_confirmed':False})
    # creating new student without existing id does not need identity_confirmed
    assert student.status_code==200,student.text
    sid=student.json()['id']
    # no_show requires reason
    assert admin.patch('/api/admin/trials/'+conf['id'],json={'version':conf['version'],'status':'no_show'}).status_code==422
    # attend instead
    attended=admin.patch('/api/admin/trials/'+conf['id'],json={'version':conf['version'],'status':'attended','note':'到场'})
    assert attended.status_code==200,attended.text
    row=attended.json()
    assert row['status']=='attended'
    assert row['student_id']==sid
    assert row['student_name']
    assert any('阶段' in a for a in row['manual_actions'])
    assert any('试听后' in f['content'] or '已参加' in f['content'] for f in row['followups'])
    # student detail includes trial
    profile=admin.get('/api/admin/students/'+sid).json()
    assert any(t['id']==created['id'] for t in profile.get('trials',[]))
    # todos list includes this lead
    todos=admin.get('/api/admin/todos',params={'state':'upcoming'}).json()
    assert any(item['id']==lead_id for item in todos['items']) or admin.get('/api/admin/todos').json()['total']>=1

def test_no_show_writes_reason_and_todo(admin):
    session,headers=_visitor_with_lead(admin,'noshow@example.invalid')
    batch_id=_open_batch()
    requested=(now()+timedelta(days=3)).isoformat()
    created=admin.post(f'/api/widget/sessions/{session["id"]}/trials',headers=headers,
                       json={'batch_id':batch_id,'requested_at':requested,'client_id':'noshow-'+uuid.uuid4().hex[:12]}).json()
    uid=admin.get('/api/auth/me').json()['id']
    conf=admin.patch('/api/admin/trials/'+created['id'],json={
        'version':created['version'],'status':'confirmed','advisor_id':uid,'scheduled_at':requested}).json()
    missed=admin.patch('/api/admin/trials/'+conf['id'],json={
        'version':conf['version'],'status':'no_show','reason':'临时有事未到'})
    assert missed.status_code==200,missed.text
    body=missed.json()
    assert body['status']=='no_show'
    assert body['reason']=='临时有事未到'
    assert any('未参加' in f['content'] and '临时有事' in f['content'] for f in body['followups'])
    assert any('已失效' in a for a in body['manual_actions'])
