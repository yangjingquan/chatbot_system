from datetime import timedelta
import uuid
from app.models import now
from test_permissions import employee

def course(admin,**extra):
    payload={'name':'星河国考课程'+uuid.uuid4().hex[:6],'exam_type':'国考','hours':40,'class_type':'基础班','description':'课程业务数据验收',**extra}
    response=admin.post('/api/admin/courses',json=payload)
    assert response.status_code==200,response.text
    return response.json()

def batch(admin,c,**extra):
    response=admin.post('/api/admin/courses/'+c['id']+'/batches',json={'name':'春季班','mode':'online','schedule':'具体开课时间由人工确认','price_cents':110000,'sale_status':'open',**extra})
    assert response.status_code==200,response.text
    return response.json()

def test_catalog_price_overrides_conflicting_document_and_changes_invalidate_history(admin):
    c=course(admin);b=batch(admin,c)
    admin.post('/api/admin/documents',json={'title':c['name']+'旧报价','content':c['name']+'旧价格为999元，99课时。','status':'published'})
    s=admin.post('/api/widget/sessions',json={}).json();h={'Authorization':'Bearer '+s['token']}
    def send():return admin.post(f'/api/widget/sessions/{s["id"]}/messages',headers=h,json={'content':c['name']+'多少钱','client_id':str(uuid.uuid4())})
    first=send()
    assert '1100' in first.text and '999元' not in first.text
    updated=admin.patch('/api/admin/batches/'+b['id'],json={'expected_version':b['version'],'price_cents':130000})
    assert updated.status_code==200
    history=admin.get(f'/api/widget/sessions/{s["id"]}',headers=h).json()
    assert all('1100' not in m['content'] for m in history['messages'] if m['role']=='assistant')
    assert '1300' in send().text

def test_paused_and_expired_price_cannot_be_quoted(admin,monkeypatch):
    import app.catalog as catalog
    c=course(admin);b=batch(admin,c,price_until=(now()+timedelta(days=1)).isoformat())
    moment=now()+timedelta(days=2);monkeypatch.setattr(catalog,'now',lambda:moment)
    sources=admin.post('/api/admin/retrieval-test',json={'query':c['name']+'价格'}).json()['sources']
    assert any(s.get('kind')=='catalog' for s in sources)
    assert all('1100' not in s['text'] for s in sources)
    changed=admin.patch('/api/admin/courses/'+c['id'],json={'expected_version':c['version'],'status':'paused'})
    assert changed.status_code==200
    sources=admin.post('/api/admin/retrieval-test',json={'query':c['name']+'多少钱'}).json()['sources']
    assert any('停售' in s['text'] for s in sources)
    assert all('1100' not in s['text'] for s in sources)

def test_course_permissions_and_optimistic_edits(admin):
    person,client=employee(admin,'knowledge')
    assert client.get('/api/admin/courses').status_code==200
    assert client.post('/api/admin/courses',json={'name':'越权改价','exam_type':'国考','hours':40}).status_code==403
    c=course(admin)
    assert admin.patch('/api/admin/courses/'+c['id'],json={'expected_version':c['version'],'hours':48}).status_code==200
    assert admin.patch('/api/admin/courses/'+c['id'],json={'expected_version':c['version'],'hours':99}).status_code==409
    assert admin.post('/api/admin/courses/'+c['id']+'/batches',json={'name':'错误日期','price_cents':-1}).status_code==422
    assert admin.patch('/api/admin/courses/'+c['id'],json={'expected_version':2,'description':'另有旧价格999元'}).status_code==422
