"""Real-container smoke test. Run in backend container; prints no credentials."""
import json
import os
import uuid
import httpx

def main():
    base=os.environ.get('SMOKE_BASE_URL','http://localhost:8000')
    with httpx.Client(base_url=base,timeout=90,headers={'Origin':os.environ.get('PUBLIC_ORIGIN','http://localhost:18080')}) as client:
        def verify(response):
            response.raise_for_status()
            return response
        verify(client.get('/api/health'))
        verify(client.post('/api/auth/login',json={'email':os.environ['ADMIN_EMAIL'],'password':os.environ['ADMIN_PASSWORD']}))
        config=verify(client.get('/api/admin/settings')).json()
        assert 'deepseek_api_key' not in config
        docs=verify(client.get('/api/admin/documents?page_size=100')).json()['items']
        assert len(docs)>=19
        for query in ('公安联考基础班多少钱','辅警一定是公务员吗','今年国考报名时间'):
            result=verify(client.post('/api/admin/retrieval-test',json={'query':query})).json()
            assert result['sources'],query
            assert all(s['document_id']!='19-sales-playbook' for s in result['sources'])
        sid=verify(client.post('/api/widget/sessions',json={'source_page':'系统冒烟验证'})).json()
        headers={'Authorization':'Bearer '+sid['token']}
        try:
            # Unknown question verifies refusal without an additional paid model call.
            answer=verify(client.post(f'/api/widget/sessions/{sid["id"]}/messages',headers=headers,
                json={'content':'木星卫星XYZQ化学成分','client_id':str(uuid.uuid4())}))
            assert 'event: done' in answer.text
            verify(client.post(f'/api/widget/sessions/{sid["id"]}/contact',headers=headers,json={'name':'系统测试访客','contact':'test@example.invalid','consent':True}))
            verify(client.post(f'/api/widget/sessions/{sid["id"]}/handoff',headers=headers))
            verify(client.post(f'/api/admin/conversations/{sid["id"]}/takeover'))
            verify(client.post(f'/api/admin/conversations/{sid["id"]}/reply',json={'content':'本条为系统自动验证的人工接待消息。'}))
            history=verify(client.get(f'/api/widget/sessions/{sid["id"]}',headers=headers)).json()
            assert any(m['role']=='agent' for m in history['messages'])
            assert client.get(f'/api/widget/sessions/{sid["id"]}',headers={'Authorization':'Bearer invalid'}).status_code==404
        finally:
            verify(client.post(f'/api/admin/conversations/{sid["id"]}/mark-demo'))
        print(json.dumps({'status':'passed','documents':len(docs),'model_configured':config['model_configured'],
                          'retrieval':config['retrieval'],'checks':['health','login','published-retrieval','private-source-filter','unknown-answer','contact','handoff','agent-reply','session-isolation','exclude-test-records']},ensure_ascii=False))

if __name__=='__main__':main()
