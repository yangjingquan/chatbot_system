import uuid
from fastapi.testclient import TestClient
from app.main import app

PASSWORD='test-only-employee-password-987'

def employee(admin,role):
    data={'email':f'{uuid.uuid4().hex}@example.invalid','name':role+'测试','role':role,'password':PASSWORD}
    response=admin.post('/api/admin/staff',json=data)
    assert response.status_code==200,response.text
    result=response.json()
    client=TestClient(app,headers={'Origin':'http://testserver'})
    assert client.post('/api/auth/login',json={'email':data['email'],'password':PASSWORD}).status_code==200
    return result,client

def new_lead(admin):
    session=admin.post('/api/widget/sessions',json={}).json()
    result=admin.post(f'/api/widget/sessions/{session["id"]}/contact',headers={'Authorization':'Bearer '+session['token']},json={'name':'权限测试','contact':'test@example.invalid','consent':True})
    assert result.status_code==200
    return result.json()['id'],session['id']

def test_knowledge_operator_cannot_read_business_data_or_manage_staff(admin):
    user,client=employee(admin,'knowledge')
    assert client.get('/api/admin/documents').status_code==200
    for path in ('/admin/leads','/admin/conversations','/admin/overview','/admin/leads-export','/admin/staff','/admin/audit'):
        assert client.get('/api'+path).status_code==403,path
    assert client.patch('/api/admin/settings',json={'company_name':'越权修改'}).status_code==403
    assert 'password_hash' not in str(admin.get('/api/admin/staff').json())

def test_advisor_scope_covers_detail_followup_conversation_and_metrics(admin):
    first,c1=employee(admin,'advisor')
    second,c2=employee(admin,'advisor')
    lid,sid=new_lead(admin)
    assert admin.patch('/api/admin/leads/'+lid,json={'owner_id':first['id']}).status_code==200
    assert any(l['id']==lid for l in c1.get('/api/admin/leads').json())
    assert all(l['id']!=lid for l in c2.get('/api/admin/leads').json())
    assert c1.get('/api/admin/leads/'+lid).status_code==200
    assert c2.get('/api/admin/leads/'+lid).status_code==404
    assert c2.get('/api/admin/conversations/'+sid).status_code==404
    assert c2.post('/api/admin/leads/'+lid+'/followups',json={'content':'越权'}).status_code==404
    assert c1.patch('/api/admin/leads/'+lid,json={'owner_id':second['id']}).status_code==403
    assert c1.get('/api/admin/leads-export').status_code==403
    assert c2.get('/api/admin/overview').json()['counts']['leads']==0
    assert c1.get('/api/admin/overview').json()['counts']['leads']==1

def test_disabled_employee_sessions_stop_immediately(admin):
    user,client=employee(admin,'support')
    assert client.get('/api/admin/conversations').status_code==200
    assert admin.patch('/api/admin/staff/'+user['id'],json={'is_active':False}).status_code==200
    assert client.get('/api/auth/me').status_code==401
    assert client.post('/api/auth/login',json={'email':user['email'],'password':PASSWORD}).status_code==401

def test_last_administrator_cannot_be_disabled(admin):
    current=admin.get('/api/auth/me').json()
    response=admin.patch('/api/admin/staff/'+current['id'],json={'is_active':False})
    assert response.status_code==409

def test_role_change_and_reset_password_revoke_sessions(admin):
    user,client=employee(admin,'support')
    admin.patch('/api/admin/staff/'+user['id'],json={'role':'knowledge'})
    assert client.get('/api/auth/me').status_code==401
    assert client.post('/api/auth/login',json={'email':user['email'],'password':PASSWORD}).status_code==200
    assert client.get('/api/admin/leads').status_code==403
    reset=admin.post('/api/admin/staff/'+user['id']+'/reset-password',json={'password':'new-employee-password-12345'})
    assert reset.status_code==200
    assert client.get('/api/auth/me').status_code==401

def test_unknown_owner_cannot_assign_by_unverified_name(admin):
    lid,sid=new_lead(admin)
    assert admin.patch('/api/admin/leads/'+lid,json={'owner':'随便输入的员工'}).status_code==422

def test_reassignment_revokes_old_advisor_and_preserves_owner_id_on_rename(admin):
    first,c1=employee(admin,'advisor')
    second,c2=employee(admin,'advisor')
    lid,sid=new_lead(admin)
    admin.patch('/api/admin/leads/'+lid,json={'owner_id':first['id']})
    assert c1.post('/api/admin/conversations/'+sid+'/takeover').status_code==200
    admin.patch('/api/admin/leads/'+lid,json={'owner_id':second['id']})
    for path in ('/api/admin/leads/'+lid,'/api/admin/conversations/'+sid):
        assert c1.get(path).status_code==404
        assert c2.get(path).status_code==200
    assert c1.post('/api/admin/conversations/'+sid+'/reply',json={'content':'旧顾问越权回复'}).status_code==404
    admin.patch('/api/admin/staff/'+second['id'],json={'name':'重新命名的顾问','role':'advisor','is_active':True})
    assert c2.get('/api/auth/me').status_code==200
    assert c2.get('/api/admin/leads/'+lid).json()['owner']=='重新命名的顾问'

def test_disabled_or_knowledge_employee_cannot_be_assigned(admin):
    person,client=employee(admin,'advisor')
    knowledge,kclient=employee(admin,'knowledge')
    lid,sid=new_lead(admin)
    admin.patch('/api/admin/staff/'+person['id'],json={'is_active':False})
    for owner in (person['id'],knowledge['id'],'unknown-id'):
        assert admin.patch('/api/admin/leads/'+lid,json={'owner_id':owner}).status_code==422
