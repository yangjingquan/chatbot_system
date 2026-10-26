import uuid
from datetime import timedelta
from app.models import now
from test_permissions import employee, new_lead

def linked(admin,student_id=None):
    lid,sid=new_lead(admin)
    response=admin.post('/api/admin/leads/'+lid+'/student',json={'student_id':student_id,'identity_confirmed':bool(student_id)})
    assert response.status_code==200,response.text
    return lid,sid,response.json()

def test_independent_student_links_many_inquiries_and_interests(admin):
    lid,sid,s=linked(admin)
    other,osid,same=linked(admin,s['id'])
    detail=admin.get('/api/admin/students/'+s['id']).json()
    assert {l['id'] for l in detail['leads']}=={lid,other}
    response=admin.post('/api/admin/students/'+s['id']+'/interests',json={'exam_type':'省考','target_year':2027,'status':'interested','notes':'准备面试课程'})
    assert response.status_code==200
    assert any(i['exam_type']=='省考' for i in response.json()['interests'])
    assert admin.get('/api/admin/students',params={'q':s['name']}).json()['total']>=1

def test_same_unverified_contact_only_suggests_duplicates_never_auto_links(admin):
    lid,sid,one=linked(admin)
    other,osid=new_lead(admin)
    lead=admin.get('/api/admin/leads/'+other).json()
    assert lead.get('student_id') is None
    candidates=admin.get('/api/admin/leads/'+other+'/student-candidates').json()
    assert any(c['id']==one['id'] for c in candidates)
    assert admin.post('/api/admin/leads/'+other+'/student',json={'student_id':one['id']}).status_code==422
    result=admin.post('/api/admin/leads/'+other+'/student',json={}).json()
    assert result['id']!=one['id']

def test_merge_requires_identity_confirmation_and_preserves_contact_withdrawal(admin):
    a,_,source=linked(admin);b,_,target=linked(admin)
    from app.db import SessionLocal
    from app.models import Lead
    with SessionLocal() as db:db.get(Lead,a).contact_allowed=False;db.commit()
    body={'target_id':target['id'],'source_version':source['version'],'target_version':target['version'],'identity_confirmed':False,'reason':'已人工核验身份'}
    assert admin.post('/api/admin/students/'+source['id']+'/merge',json=body).status_code==422
    body['identity_confirmed']=True
    response=admin.post('/api/admin/students/'+source['id']+'/merge',json=body)
    assert response.status_code==200,response.text
    detail=response.json()
    assert {l['id'] for l in detail['leads']}=={a,b}
    assert next(l for l in detail['leads'] if l['id']==a)['contact_allowed'] is False
    assert admin.get('/api/admin/students/'+source['id']).json()['merged_into_id']==target['id']
    assert admin.post('/api/admin/students/'+source['id']+'/merge',json=body).status_code==409

def test_student_scope_and_reassignment_include_all_linked_leads(admin):
    first,c1=employee(admin,'advisor');second,c2=employee(admin,'advisor')
    lid,sid=new_lead(admin)
    admin.patch('/api/admin/leads/'+lid,json={'owner_id':first['id']})
    s=admin.post('/api/admin/leads/'+lid+'/student',json={}).json()
    another,another_sid=new_lead(admin)
    admin.patch('/api/admin/leads/'+another,json={'owner_id':first['id']})
    assert admin.post('/api/admin/leads/'+another+'/student',json={'student_id':s['id'],'identity_confirmed':True}).status_code==200
    assert c1.get('/api/admin/students/'+s['id']).status_code==200
    assert c2.get('/api/admin/students/'+s['id']).status_code==404
    assert c2.get('/api/admin/students').json()['total']==0
    assert c1.post('/api/admin/students/'+s['id']+'/merge',json={'target_id':s['id'],'source_version':1,'target_version':1,'identity_confirmed':True,'reason':'越权'}).status_code==403
    admin.patch('/api/admin/leads/'+lid,json={'owner_id':second['id']})
    assert c1.get('/api/admin/students/'+s['id']).status_code==404
    assert c2.get('/api/admin/students/'+s['id']).status_code==200
    assert c1.get('/api/admin/leads/'+another).status_code==404
    assert c2.get('/api/admin/leads/'+another).status_code==200

def test_duplicate_candidates_do_not_reveal_another_advisors_student(admin):
    first,c1=employee(admin,'advisor');second,c2=employee(admin,'advisor')
    lid,sid=new_lead(admin);admin.patch('/api/admin/leads/'+lid,json={'owner_id':first['id']})
    s=admin.post('/api/admin/leads/'+lid+'/student',json={}).json()
    other,osid=new_lead(admin);admin.patch('/api/admin/leads/'+other,json={'owner_id':second['id']})
    assert all(candidate['id']!=s['id'] for candidate in c2.get('/api/admin/leads/'+other+'/student-candidates').json())

def test_todos_exclude_withdrawn_contacts_and_completion_is_persisted(admin):
    lid,sid=new_lead(admin);other,osid=new_lead(admin)
    due=(now()-timedelta(hours=1)).isoformat()
    for key in (lid,other):assert admin.patch('/api/admin/leads/'+key,json={'next_followup_at':due}).status_code==200
    from app.db import SessionLocal
    from app.models import Lead
    with SessionLocal() as db:db.get(Lead,other).contact_allowed=False;db.commit()
    result=admin.get('/api/admin/todos',params={'state':'overdue'}).json()
    assert any(l['id']==lid for l in result['items'])
    assert all(l['id']!=other for l in result['items'])
    assert admin.post('/api/admin/leads/'+lid+'/complete-followup',json={'content':'已完成本次跟进'}).status_code==200
    assert admin.get('/api/admin/leads/'+lid).json()['next_followup_at'] is None
