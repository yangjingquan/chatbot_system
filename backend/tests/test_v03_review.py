import uuid
import pytest
from app.db import SessionLocal
from app.models import Lead,Student,Course
from test_permissions import employee,new_lead

@pytest.mark.parametrize('text',['学费：¥999','售价：999','人民币九百九十九元','课程费：￥999','优惠价998'])
def test_all_legacy_course_quote_formats_are_excluded(text):
    from app.knowledge import historical_commercial_text
    assert historical_commercial_text({'knowledge_type':'general','text':text})

def test_currency_in_public_catalog_free_text_rejected(admin):
    for field in ('name','class_type','description'):
        payload={'name':'课程名称','class_type':'基础班','description':'教学介绍','exam_type':'国考','hours':40,field:'学费¥999'}
        assert admin.post('/api/admin/courses',json=payload).status_code==422

def test_legacy_bad_catalog_name_cannot_advertise_old_price_when_paused(admin):
    from app.catalog import public_source
    with SessionLocal() as db:
        c=Course(name='999元国考班',exam_type='国考',hours=40,status='paused')
        db.add(c);db.flush();source=public_source(db,c)
        assert '999' not in source['title']+source['text']
        db.rollback()

def test_student_lock_rechecks_advisor_scope_after_transfer(admin,monkeypatch):
    import app.students as module
    first,c1=employee(admin,'advisor');second,c2=employee(admin,'advisor')
    lid,_=new_lead(admin);admin.patch('/api/admin/leads/'+lid,json={'owner_id':first['id']})
    student=admin.post('/api/admin/leads/'+lid+'/student',json={}).json()
    original=module.scoped_object
    switched=False
    def gate(db,model,key,user):
        nonlocal switched
        result=original(db,model,key,user)
        if model is Student and key==student['id'] and not switched:
            switched=True
            with SessionLocal() as writer:
                writer.get(Student,key).owner_id=second['id'];writer.get(Lead,lid).owner_id=second['id'];writer.commit()
        return result
    monkeypatch.setattr(module,'scoped_object',gate)
    response=c1.post('/api/admin/students/'+student['id']+'/interests',json={'exam_type':'国考','notes':'旧顾问等待锁后越权写入'})
    assert response.status_code==404

def test_lead_patch_refreshes_relationship_before_conversion_and_assignment(admin,monkeypatch):
    import app.admin as module
    owner,c1=employee(admin,'advisor')
    original=module.owned
    for change in ({'stage':'已成交'},{'owner_id':owner['id']}):
        lid,_=new_lead(admin);inserted=[]
        def gate(db,model,key,user):
            result=original(db,model,key,user)
            if model is Lead and key==lid and not inserted:
                with SessionLocal() as writer:
                    s=Student(name='并发建档'+uuid.uuid4().hex[:6],owner_id=None)
                    writer.add(s);writer.flush();writer.get(Lead,key).student_id=s.id;writer.commit();inserted.append(s.id)
            return result
        monkeypatch.setattr(module,'owned',gate)
        response=admin.patch('/api/admin/leads/'+lid,json=change)
        assert response.status_code==200,response.text
        assert response.json()['student_id']==inserted[0]
        student=admin.get('/api/admin/students/'+inserted[0]).json()
        assert student['owner_id']==response.json()['owner_id']
        monkeypatch.setattr(module,'owned',original)

def test_merge_chain_transfers_all_archived_nodes_and_keeps_full_reason(admin):
    first,c1=employee(admin,'advisor');second,c2=employee(admin,'advisor')
    rows=[]
    for person in (first,first,second):
        lid,_=new_lead(admin);admin.patch('/api/admin/leads/'+lid,json={'owner_id':person['id']})
        rows.append(admin.post('/api/admin/leads/'+lid+'/student',json={}).json())
    a,b,c=rows
    def merge(source,target,reason):
        response=admin.post('/api/admin/students/'+source['id']+'/merge',json={'target_id':target['id'],'source_version':source['version'],'target_version':target['version'],'identity_confirmed':True,'reason':reason})
        assert response.status_code==200,response.text
        return response.json()
    b=merge(a,b,'第一次合并');c=merge(b,c,'核验原因'*150)
    assert c1.get('/api/admin/students/'+a['id']).status_code==404
    assert c2.get('/api/admin/students/'+a['id']).json()['merged_into_id']==c['id']
    from sqlalchemy import select
    from app.models import Audit
    with SessionLocal() as db:
        audit=db.scalars(select(Audit).where(Audit.action=='合并学员档案').order_by(Audit.created_at.desc())).first()
        assert audit and audit.details=='核验原因'*150
