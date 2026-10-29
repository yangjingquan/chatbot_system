"""Item 9: consultation → lead → trial → follow-up chain."""
import uuid
from datetime import datetime, timedelta
from sqlalchemy import select
from app.document_service import aware
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
    assert lead['next_followup_at'] is None
    detail=admin.get('/api/admin/leads/'+lead['id']).json()
    assert any('试听' in f['content'] for f in detail['followups'])
    assert any(t['id']==body['id'] for t in detail['trials'])
    row=admin.get('/api/admin/trials/'+body['id']).json()
    assert row['lead_id']==lead['id']
    assert row['lead_name']=='业务链同学'
    assert row['auto_todo_rule']
    assert row['todo_kind']=='确认试听' and row['todo_due_at']
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
    assert conf['todo_due_at']  # the reminder belongs to the trial
    lead_id=conf['lead_id']
    # link student (manual)
    student=admin.post('/api/admin/leads/'+lead_id+'/student',json={'identity_confirmed':False})
    # creating new student without existing id does not need identity_confirmed
    assert student.status_code==200,student.text
    sid=student.json()['id']
    # no_show requires reason
    assert admin.patch('/api/admin/trials/'+conf['id'],json={'version':conf['version'],'status':'no_show'}).status_code==422
    # attend instead
    attended=admin.patch('/api/admin/trials/'+conf['id'],json={'version':conf['version'],'status':'attended','note':'到场','confirm_early':True,'early_reason':'试听提前完成'})
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
    assert any(item['id']==lead_id and item['trial_id']==created['id'] for item in todos['items'])

def test_multiple_trial_todos_and_manual_reminder_are_independent(admin):
    session,headers=_visitor_with_lead(admin,'multiple@example.invalid')
    lead=next(l for l in admin.get('/api/admin/leads',params={'q':'业务链同学','page_size':100}).json()['items']
              if l['conversation_id']==session['id'])
    manual=(now()+timedelta(hours=6)).isoformat()
    assert admin.patch('/api/admin/leads/'+lead['id'],json={'next_followup_at':manual}).status_code==200
    path=f'/api/widget/sessions/{session["id"]}/trials'
    batch_id=_open_batch()
    def create(days):
        response=admin.post(path,headers=headers,json={'batch_id':batch_id,'requested_at':(now()+timedelta(days=days)).isoformat(),
                                                       'client_id':uuid.uuid4().hex})
        assert response.status_code==200,response.text
        return response.json()
    first,second=create(6),create(7)
    def tasks():
        response=admin.get('/api/admin/todos',params={'q':'业务链同学','page_size':100})
        assert response.status_code==200,response.text
        return [row for row in response.json()['items'] if row['id']==lead['id']]
    initial=tasks()
    assert len(initial)==3
    original_second=next(row for row in initial if row['trial_id']==second['id'])
    uid=admin.get('/api/auth/me').json()['id']
    confirmed=admin.patch('/api/admin/trials/'+first['id'],json={
        'version':first['version'],'status':'confirmed','advisor_id':uid,'scheduled_at':first['requested_at']})
    assert confirmed.status_code==200,confirmed.text
    after=tasks()
    assert len(after)==3
    assert next(row for row in after if row['trial_id']==second['id'])['next_followup_at']==original_second['next_followup_at']
    assert next(row for row in after if row['trial_id']==first['id'])['todo_label']=='接待试听'
    assert aware(datetime.fromisoformat(admin.get('/api/admin/leads/'+lead['id']).json()['next_followup_at']))==datetime.fromisoformat(manual)
    first_todo=next(row for row in after if row['trial_id']==first['id'])
    assert admin.post('/api/admin/trials/'+first['id']+'/complete-followup',json={'version':first_todo['todo_version']}).status_code==200
    assert admin.post('/api/admin/trials/'+first['id']+'/complete-followup',json={'version':first_todo['todo_version']}).status_code==409
    remaining=tasks()
    assert len(remaining)==2 and {r['trial_id'] for r in remaining}=={None,second['id']}
    assert admin.post('/api/admin/leads/'+lead['id']+'/complete-followup',json={}).status_code==200
    assert [r['trial_id'] for r in tasks()]==[second['id']]
    assert admin.post('/api/admin/leads/'+lead['id']+'/withdraw-contact',json={}).status_code==200
    assert tasks()==[]

def test_trial_todo_obeys_lead_scope(admin):
    session,headers=_visitor_with_lead(admin,'todo-scope@example.invalid')
    requested=(now()+timedelta(days=5)).isoformat()
    result=admin.post(f'/api/widget/sessions/{session["id"]}/trials',headers=headers,
                      json={'batch_id':_open_batch(),'requested_at':requested,'client_id':uuid.uuid4().hex})
    assert result.status_code==200,result.text
    trial=result.json()
    lead=next(l for l in admin.get('/api/admin/leads',params={'q':'业务链同学','page_size':100}).json()['items']
              if l['conversation_id']==session['id'])
    owner,owner_client=employee(admin,'advisor')
    _,other_client=employee(admin,'advisor')
    assert admin.patch('/api/admin/leads/'+lead['id'],json={'owner_id':owner['id']}).status_code==200
    assert any(row['trial_id']==trial['id'] for row in owner_client.get('/api/admin/todos',params={'page_size':100}).json()['items'])
    assert not any(row['trial_id']==trial['id'] for row in other_client.get('/api/admin/todos',params={'page_size':100}).json()['items'])
    assert other_client.post('/api/admin/trials/'+trial['id']+'/complete-followup',json={'version':trial['version']}).status_code==404
    assert owner_client.post('/api/admin/trials/'+trial['id']+'/complete-followup',json={'version':trial['version']}).status_code==200

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

def test_early_attendance_requires_reason_and_terminal_correction_rebuilds_todo(admin):
    session,headers=_visitor_with_lead(admin,'correction@example.invalid')
    requested=(now()+timedelta(days=4)).isoformat()
    created=admin.post(f'/api/widget/sessions/{session["id"]}/trials',headers=headers,
                       json={'batch_id':_open_batch(),'requested_at':requested,'client_id':uuid.uuid4().hex}).json()
    uid=admin.get('/api/auth/me').json()['id']
    confirmed=admin.patch('/api/admin/trials/'+created['id'],json={'version':created['version'],'status':'confirmed','advisor_id':uid,'scheduled_at':requested}).json()
    path='/api/admin/trials/'+created['id']
    assert admin.patch(path,json={'version':confirmed['version'],'status':'attended'}).status_code==422
    assert admin.patch(path,json={'version':confirmed['version'],'status':'attended','confirm_early':True}).status_code==422
    done=admin.patch(path,json={'version':confirmed['version'],'status':'attended','confirm_early':True,'early_reason':'已提前上课'})
    assert done.status_code==200,done.text
    assert done.json()['todo_kind']=='试听后跟进'
    assert any('已提前上课' in f['content'] for f in done.json()['followups'])
    assert admin.patch(path,json={'version':done.json()['version'],'status':'pending'}).status_code==422
    corrected=admin.patch(path,json={'version':done.json()['version'],'status':'pending','correction_reason':'误点了已参加'})
    assert corrected.status_code==200,corrected.text
    assert corrected.json()['status']=='pending' and corrected.json()['todo_kind']=='确认试听'
    assert corrected.json()['scheduled_at'] is None
    assert any('误点了已参加' in f['content'] for f in corrected.json()['followups'])
    assert admin.patch(path,json={'version':done.json()['version'],'status':'pending','correction_reason':'旧版本'}).status_code==409

def test_visitor_reschedule_cancel_and_persistent_notices(admin):
    session,headers=_visitor_with_lead(admin,'selfserve@example.invalid')
    base=f'/api/widget/sessions/{session["id"]}'
    created=admin.post(base+'/trials',headers=headers,json={
        'batch_id':_open_batch(),'requested_at':(now()+timedelta(days=5)).isoformat(),'client_id':uuid.uuid4().hex}).json()
    change=base+'/trials/'+created['id']
    other,other_headers=_visitor_with_lead(admin,'other@example.invalid')
    assert admin.patch(f'/api/widget/sessions/{other["id"]}/trials/{created["id"]}',headers=other_headers,
                       json={'version':created['version'],'action':'cancel'}).status_code==404
    assert admin.patch(change,headers=headers,json={'version':created['version'],'action':'reschedule','requested_at':created['requested_at']}).status_code==422
    requested=(now()+timedelta(days=6)).isoformat()
    changed=admin.patch(change,headers=headers,json={'version':created['version'],'action':'reschedule','requested_at':requested})
    assert changed.status_code==200,changed.text
    assert changed.json()['status']=='pending' and changed.json()['id']==created['id']
    assert admin.patch(change,headers=headers,json={'version':created['version'],'action':'cancel'}).status_code==409
    uid=admin.get('/api/auth/me').json()['id']
    confirmed=admin.patch('/api/admin/trials/'+created['id'],json={'version':changed.json()['version'],'status':'confirmed','advisor_id':uid,'scheduled_at':requested})
    assert confirmed.status_code==200,confirmed.text
    history=admin.get(base,headers=headers).json()['messages']
    assert any(m['mode']=='trial_update' and '已确认' in m['content'] for m in history)
    cancelled=admin.patch(change,headers=headers,json={'version':confirmed.json()['version'],'action':'cancel'})
    assert cancelled.status_code==200 and cancelled.json()['status']=='cancelled'
    assert admin.patch(change,headers=headers,json={'version':cancelled.json()['version'],'action':'cancel'}).status_code==409
    assert any(m['mode']=='trial_update' and '已取消' in m['content'] for m in admin.get(base,headers=headers).json()['messages'])
