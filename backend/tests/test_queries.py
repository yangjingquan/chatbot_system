import csv
import io
import uuid
from datetime import datetime, timezone
from app.db import SessionLocal
from app.models import Conversation, Lead, Document
from test_permissions import employee

def test_over_old_caps_pages_export_and_metrics_share_filters(admin):
    marker='分页验收'+uuid.uuid4().hex[:8]
    stamp=datetime(2027,2,2,2,tzinfo=timezone.utc)
    with SessionLocal() as db:
        for index in range(525):
            c=Conversation(visitor_name=marker+str(index),token_hash='test',analysis={'active_exam_types':['国考']},created_at=stamp)
            db.add(c);db.flush()
            db.add(Lead(conversation_id=c.id,name=marker+str(index),contact='test@example.invalid',consent_text='synthetic',tags=['国考'],stage='跟进中',created_at=stamp))
        db.commit()
    filters={'q':marker,'date_from':'2027-02-02','date_to':'2027-02-02','exam_type':'国考','stage':'跟进中'}
    ids=[]
    for page in range(1,12):
        result=admin.get('/api/admin/leads',params={**filters,'page':page,'page_size':50}).json()
        assert result['total']==525
        ids.extend(item['id'] for item in result['items'])
    assert len(ids)==len(set(ids))==525
    conversations=admin.get('/api/admin/conversations',params={**filters,'page':18,'page_size':30}).json()
    assert conversations['total']==525 and len(conversations['items'])==15
    response=admin.get('/api/admin/leads-export',params=filters)
    rows=list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert len(rows)-1==525
    overview=admin.get('/api/admin/overview',params=filters).json()
    assert overview['counts']['leads']==overview['counts']['conversations']==525

def test_beijing_inclusive_dates_and_contact_status(admin):
    marker='日期验收'+uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        for hour,allowed in [(15,True),(16,False),(23,False)]:
            stamp=datetime(2027,3,1,hour,tzinfo=timezone.utc)
            c=Conversation(visitor_name=marker,token_hash='test',created_at=stamp)
            db.add(c);db.flush()
            db.add(Lead(conversation_id=c.id,name=marker,contact='test@example.invalid',consent_text='synthetic',contact_allowed=allowed,created_at=stamp))
        db.commit()
    result=admin.get('/api/admin/leads',params={'q':marker,'date_from':'2027-03-02','date_to':'2027-03-02','contact_state':'withdrawn'}).json()
    assert result['total']==2 and all(not l['contact_allowed'] for l in result['items'])
    assert admin.get('/api/admin/leads',params={'date_from':'2027-03-02','date_to':'2027-03-01'}).status_code==422
    assert admin.get('/api/admin/leads',params={'page_size':101}).status_code==422

def test_paging_does_not_allow_owner_filter_to_expand_advisor_scope(admin):
    other,client=employee(admin,'advisor')
    result=client.get('/api/admin/leads',params={'owner_id':'unassigned','page_size':100}).json()
    assert result['total']==0
    assert client.get('/api/admin/overview',params={'owner_id':'unassigned'}).json()['counts']['leads']==0

def test_knowledge_pagination_searches_saved_drafts(admin):
    doc=admin.post('/api/admin/documents',json={'title':'分页初版','content':'测试正文','status':'published'}).json()
    draft=admin.patch('/api/admin/documents/'+doc['id'],json={'expected_sequence':doc['edit_sequence'],'title':'草稿搜索独有标记'}).json()
    result=admin.get('/api/admin/documents',params={'q':'草稿搜索独有标记','status':'draft','page_size':1}).json()
    assert result['total']==1 and result['items'][0]['id']==doc['id']
