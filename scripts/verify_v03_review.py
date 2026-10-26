"""PostgreSQL race regressions in the existing disposable rehearsal database only."""
import os,uuid,time,threading,json
from concurrent.futures import ThreadPoolExecutor
os.environ['DATABASE_URL']=os.environ['DATABASE_URL'].rsplit('/',1)[0]+'/zhixing_v03_rehearsal'
os.environ['DEEPSEEK_API_KEY']='';os.environ['EMBEDDING_ENABLED']='false';os.environ['REDIS_URL']=''
from fastapi.testclient import TestClient
from sqlalchemy import select,text
from app.main import app
from app.db import SessionLocal,engine
from app.models import Lead,Student,Audit
import app.admin as administration

def ok(r):
    assert r.status_code==200,(r.status_code,r.text[:150])
    return r.json()
def lock_wait(fragment):
    with engine.connect() as db:
        return bool(db.execute(text("SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query ILIKE :needle AND pid<>pg_backend_pid()"),{'needle':'%'+fragment+'%'}).first())
def wait_until(condition):
    end=time.monotonic()+8
    while time.monotonic()<end:
        if condition():return
        time.sleep(.02)
    raise AssertionError('Expected concurrent request did not reach its synchronization point')

origin={'Origin':os.environ['PUBLIC_ORIGIN']}
with TestClient(app,headers=origin) as admin:
    ok(admin.post('/api/auth/login',json={'email':os.environ['ADMIN_EMAIL'],'password':os.environ['ADMIN_PASSWORD']}))
    password='test-only-'+uuid.uuid4().hex
    people=[]
    for index in range(2):people.append(ok(admin.post('/api/admin/staff',json={'name':'PG竞态顾问'+str(index),'email':uuid.uuid4().hex+'@example.invalid','password':password,'role':'advisor'})))
    advisor=TestClient(app,headers=origin)
    ok(advisor.post('/api/auth/login',json={'email':people[0]['email'],'password':password}))
    def lead():
        session=ok(admin.post('/api/widget/sessions',json={}))
        return ok(admin.post('/api/widget/sessions/'+session['id']+'/contact',headers={'Authorization':'Bearer '+session['token']},json={'name':'PG竞态合成学员','contact':'race@example.invalid','consent':True}))['id']
    lid=lead();ok(admin.patch('/api/admin/leads/'+lid,json={'owner_id':people[0]['id']}))
    student=ok(admin.post('/api/admin/leads/'+lid+'/student',json={}))
    # Force a wait on the student row after the request's initial MVCC permission read.
    with SessionLocal() as blocker,ThreadPoolExecutor(max_workers=1) as pool:
        s=blocker.scalar(select(Student).where(Student.id==student['id']).with_for_update())
        s.owner_id=people[1]['id'];blocker.get(Lead,lid).owner_id=people[1]['id'];blocker.flush()
        future=pool.submit(lambda:advisor.post('/api/admin/students/'+s.id+'/interests',json={'exam_type':'国考','notes':'旧顾问不应成功写入'}))
        try:wait_until(lambda:lock_wait('students'))
        finally:blocker.commit()
        assert future.result(timeout=8).status_code==404

    def relationship_race(change):
        key=lead();entered=threading.Event();resume=threading.Event();original=administration.owned
        def paused(db,model,identity,user):
            result=original(db,model,identity,user)
            if model is Lead and identity==key and not entered.is_set():
                entered.set();assert resume.wait(8)
            return result
        administration.owned=paused
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                a=pool.submit(lambda:admin.patch('/api/admin/leads/'+key,json=change))
                assert entered.wait(5)
                b=pool.submit(lambda:admin.post('/api/admin/leads/'+key+'/student',json={}))
                try:wait_until(lambda:b.done() or lock_wait('companies'))
                finally:resume.set()
                first,second=ok(a.result(timeout=8)),ok(b.result(timeout=8))
            with SessionLocal() as db:
                row=db.get(Lead,key);profile=db.get(Student,row.student_id)
                assert profile.id==second['id'] and row.owner_id==profile.owner_id
                if change.get('stage')=='已成交':assert first['student_id']==second['id']
        finally:resume.set();administration.owned=original
    relationship_race({'stage':'已成交'})
    relationship_race({'owner_id':people[1]['id']})
    a=ok(admin.post('/api/admin/leads/'+lead()+'/student',json={}))
    b=ok(admin.post('/api/admin/leads/'+lead()+'/student',json={}))
    reason='已通过人工核验确认身份，保留各次联系许可。'*30
    merged=ok(admin.post('/api/admin/students/'+a['id']+'/merge',json={'target_id':b['id'],'source_version':a['version'],'target_version':b['version'],'identity_confirmed':True,'reason':reason}))
    with SessionLocal() as db:
        entry=db.scalars(select(Audit).where(Audit.action=='合并学员档案').order_by(Audit.created_at.desc())).first()
        assert entry.details==reason
    print(json.dumps({'status':'passed','checks':['post-lock-advisor-scope','lead-conversion-link-race','lead-owner-link-race','full-length-merge-audit'],'merge_reason_characters':len(reason)},ensure_ascii=False))
