"""Privacy redaction before DeepSeek, history limits, and non-essential surfaces."""
import json
import uuid
from app import llm
from app.privacy import (
    MAX_HISTORY_MESSAGES, MAX_MESSAGE_CHARS, DEFAULT_PRIVACY_NOTICE,
    redact_text, prepare_model_history, prepare_model_question,
)

PHONE = '13800138000'
ID_CARD = '110101199001011234'
EMAIL = 'visitor.test@example.com'

def test_redact_phone_id_email_and_preserve_other_text():
    raw = f'请回电 {PHONE}，证件 {ID_CARD}，邮箱 {EMAIL}，预算2000元国考班'
    out = redact_text(raw)
    assert PHONE not in out
    assert ID_CARD not in out
    assert EMAIL not in out
    assert '138****8000' in out
    assert '1101**********1234' in out
    assert 'v***@example.com' in out
    assert '预算2000元国考班' in out
    assert redact_text('') == ''
    assert redact_text(None) == ''

def test_plus86_phone_and_id_with_x():
    assert '138****8000' in redact_text('+86-13800138000 咨询')
    assert '1101**********123X' in redact_text('证件11010119900101123X')

def test_history_limit_and_truncation():
    history = [{'role':'user','content':f'第{i}轮 {PHONE} ' + ('长'*2000)} for i in range(8)]
    history += [{'role':'system','content':'应忽略'}]
    prepared = prepare_model_history(history)
    assert len(prepared) == MAX_HISTORY_MESSAGES
    assert all(m['role']=='user' for m in prepared)
    assert all(PHONE not in m['content'] for m in prepared)
    assert all(len(m['content']) <= MAX_MESSAGE_CHARS for m in prepared)
    assert prepare_model_question(PHONE + '多少钱').startswith('138****8000')

class CaptureStream:
    def __init__(self):
        self.payload=None
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def raise_for_status(self):pass
    def iter_lines(self):
        yield 'data: '+json.dumps({'choices':[{'delta':{'content':'已收到'},'finish_reason':None}]})
        yield 'data: '+json.dumps({'choices':[{'delta':{},'finish_reason':'stop'}]})
        yield 'data: [DONE]'

def test_deepseek_payload_redacts_question_and_history(monkeypatch):
    captured={}
    stream=CaptureStream()
    class Client:
        def __init__(self,*a,**k):pass
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def stream(self,method,url,headers=None,json=None):
            captured['json']=json
            return stream
    monkeypatch.setattr(llm.httpx,'Client',Client)
    from app.config import settings
    monkeypatch.setattr(settings(),'deepseek_api_key','fake-key')
    history=[{'role':'user','content':f'上次留了 {PHONE}'},{'role':'assistant','content':'好的'}]
    assert list(llm.deepseek_stream(f'我的证件是{ID_CARD}，联系{PHONE}',[],history))==[('delta','已收到')]
    messages=captured['json']['messages']
    user_msgs=[m['content'] for m in messages if m['role']=='user']
    assert user_msgs
    blob='\n'.join(m['content'] for m in messages if m['role']!='system')
    assert PHONE not in blob
    assert ID_CARD not in blob
    assert '138****8000' in blob
    assert '1101**********1234' in blob
    # system knowledge path untouched here (empty sources)

def test_widget_config_exposes_privacy_notice(admin):
    data=admin.get('/api/widget/config').json()
    assert data.get('privacy_notice')
    assert 'DeepSeek' in data['privacy_notice'] or '模型' in data['privacy_notice']

def test_settings_can_update_privacy_notice(admin):
    text='机构确认后的隐私说明 '+uuid.uuid4().hex[:6]
    updated=admin.patch('/api/admin/settings',json={'privacy_notice':text})
    assert updated.status_code==200,updated.text
    assert updated.json()['privacy_notice']==text
    assert admin.get('/api/widget/config').json()['privacy_notice']==text

def test_gap_and_list_preview_redact_but_message_kept(admin,monkeypatch):
    from app.config import settings
    from app.db import SessionLocal
    from app.models import Message
    monkeypatch.setattr(settings(),'deepseek_api_key','')
    c=admin.post('/api/widget/sessions',json={}).json()
    headers={'Authorization':'Bearer '+c['token']}
    # Unknown answer creates a knowledge gap from the user question.
    result=admin.post(f'/api/widget/sessions/{c["id"]}/messages',headers=headers,
                      json={'content':f'火星跃迁口诀请回电{PHONE}','client_id':uuid.uuid4().hex})
    assert result.status_code==200
    history=admin.get(f'/api/widget/sessions/{c["id"]}',headers=headers).json()
    user=next(m for m in history['messages'] if m['role']=='user')
    assert PHONE in user['content']  # original kept for staff/CRM conversation detail
    answer=next(m for m in history['messages'] if m['role']=='assistant')
    # Make the latest list preview a user message that contains PII.
    # Unique q= marker keeps the row visible after large synthetic waiting queues.
    marker='隐私列表'+uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        db.add(Message(conversation_id=c['id'],role='user',content=f'补充联系方式{PHONE} {marker}'))
        db.commit()
    listing=admin.get('/api/admin/conversations',params={'page_size':100,'q':marker}).json()
    row=next(item for item in listing['items'] if item['id']==c['id'])
    assert PHONE not in row['last_message']
    assert '138****8000' in row['last_message']
    detail=admin.get(f'/api/admin/conversations/{c["id"]}').json()
    assert any(PHONE in m['content'] for m in detail['messages'] if m['role']=='user')
    gaps=admin.get('/api/admin/quality/gaps').json()['items']
    gap=next(g for g in gaps if g['message_id']==answer['id'])
    assert PHONE not in gap['question']
    assert '138****8000' in gap['question']

def test_default_privacy_notice_marks_confirmation_needed():
    assert '确认' in DEFAULT_PRIVACY_NOTICE
