"""Restored PostgreSQL v0.2 -> v0.3 rehearsal. No paid model calls, no live DB writes."""
import os,uuid,json,csv,io
from concurrent.futures import ThreadPoolExecutor
os.environ['DATABASE_URL']=os.environ['DATABASE_URL'].rsplit('/',1)[0]+'/zhixing_v03_rehearsal'
os.environ['DEEPSEEK_API_KEY']='';os.environ['EMBEDDING_ENABLED']='false';os.environ['REDIS_URL']=''
from sqlalchemy import text,select
from fastapi.testclient import TestClient
from app.db import engine,SessionLocal
from app.main import app
from app.models import Lead,Conversation,now
from app.migrations import migrate_schema

tables=('admins','documents','conversations','messages','leads','followups')
with engine.connect() as db:before={t:db.execute(text(f'SELECT COUNT(*) FROM {t}')).scalar() for t in tables}
def ok(response):
    assert response.status_code==200,(response.status_code,response.text[:300])
    return response.json()
with TestClient(app,headers={'Origin':os.environ['PUBLIC_ORIGIN']}) as client:
    with engine.connect() as db:after={t:db.execute(text(f'SELECT COUNT(*) FROM {t}')).scalar() for t in tables}
    assert before==after
    ok(client.post('/api/auth/login',json={'email':os.environ['ADMIN_EMAIL'],'password':os.environ['ADMIN_PASSWORD']}))
    c=ok(client.post('/api/admin/courses',json={'name':'PG课程'+uuid.uuid4().hex[:8],'exam_type':'国考','hours':48}))
    b=ok(client.post('/api/admin/courses/'+c['id']+'/batches',json={'name':'PG班次','price_cents':234500,'sale_status':'open'}))
    def change(index):return client.patch('/api/admin/batches/'+b['id'],json={'expected_version':b['version'],'price_cents':234500+index}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=sorted(pool.map(change,range(2)))
    assert statuses==[200,409]
    students=[]
    for i in range(2):
        session=ok(client.post('/api/widget/sessions',json={}))
        l=ok(client.post('/api/widget/sessions/'+session['id']+'/contact',headers={'Authorization':'Bearer '+session['token']},json={'name':'PG学员'+str(i),'contact':'same@example.invalid','consent':True}))
        s=ok(client.post('/api/admin/leads/'+l['id']+'/student',json={}))
        students.append((s,l))
    source,target=students[0][0],students[1][0]
    candidates=ok(client.get('/api/admin/leads/'+students[1][1]['id']+'/student-candidates'))
    assert any(row['id']==source['id'] for row in candidates)
    body={'target_id':target['id'],'source_version':source['version'],'target_version':target['version'],'identity_confirmed':True,'reason':'PostgreSQL身份核验演练'}
    def merge(index):return client.post('/api/admin/students/'+source['id']+'/merge',json=body).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:merge_statuses=sorted(pool.map(merge,range(2)))
    assert merge_statuses==[200,409],merge_statuses
    assert len(ok(client.get('/api/admin/students/'+target['id']))['leads'])==2
    marker='PG分页'+uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        for index in range(525):
            cv=Conversation(visitor_name=marker+str(index),token_hash='synthetic',analysis={'active_exam_types':['国考']})
            db.add(cv);db.flush();db.add(Lead(conversation_id=cv.id,name=marker+str(index),contact='test@example.invalid',consent_text='synthetic',tags=['国考'],stage='跟进中'))
        db.commit()
    params={'q':marker,'exam_type':'国考','stage':'跟进中'}
    page=ok(client.get('/api/admin/leads',params={**params,'page':11,'page_size':50}))
    assert page['total']==525 and len(page['items'])==25
    assert ok(client.get('/api/admin/conversations',params=params))['total']==525
    assert ok(client.get('/api/admin/overview',params=params))['counts']['leads']==525
    export=client.get('/api/admin/leads-export',params=params);export.raise_for_status()
    assert len(list(csv.reader(io.StringIO(export.content.decode('utf-8-sig')))))-1==525
    migrate_schema(engine)
    print(json.dumps({'status':'passed','preserved_rows':before,'price_edit_statuses':statuses,'merge_statuses':merge_statuses,'filtered_rows':525,'checks':['additive-upgrade','course-price-cas','manual-merge-cas','contact-candidate-only','postgres-json-filters','pagination-over-old-cap','csv-and-dashboard-consistency','repeat-migration']},ensure_ascii=False))
