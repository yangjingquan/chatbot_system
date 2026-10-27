import json
import secrets
from datetime import timedelta
from pathlib import Path
from sqlalchemy import select
from .config import settings
from .models import Company, Admin, Document, Conversation, Message, Lead, FollowUp, TENANT, now
from .security import passwords, digest
from .knowledge import ingest_document
from .scoring import analyze_intent
from .privacy import DEFAULT_PRIVACY_NOTICE

DEFAULT_CONFIG = {
    'company_name': '知行公考', 'bot_name': '知行学习顾问',
    'description': '专注公职考试备考咨询的演示培训机构',
    'welcome': '您好，我是知行学习顾问。您正在准备哪一类考试？我可以帮您了解课程、学习方式或预约试听。当前机构与课程资料均为演示设定。',
    'service_hours': '每日 09:00–21:00',
    'suggested_questions': ['公安联考基础班多少钱？', '国考零基础怎么学习？', '如何预约试听？', '辅警和社会招警有什么区别？'],
    'contact_notice': '我同意机构使用此联系方式，就本次课程咨询与我联系。联系方式尚待核验，不用于其他营销。',
    'privacy_notice': DEFAULT_PRIVACY_NOTICE,
    'allowed_embed_origins': ['http://localhost:18080', 'http://127.0.0.1:18080'],
    'retention_conversation_days': 365,
    'retention_lead_days': 730,
    'retention_audit_days': 1095,
    'retention_backup_days': 90,
    'reception_assign_mode': 'manual',
    'reception_max_active': 5,
    'reception_wait_timeout_seconds': 300,
    'canned_replies': [
        {'id': 'canned-welcome', 'title': '接待开场', 'content': '您好，我是人工客服，已接替机器人继续为您服务，请问还需要补充哪些信息？'},
        {'id': 'canned-wait', 'title': '稍等核实', 'content': '好的，我先帮您核实一下，请稍等片刻。'},
        {'id': 'canned-trial', 'title': '试听引导', 'content': '您可以在对话中申请试听，留下本次咨询的联系方式后，顾问会与您确认时间。'},
    ],
    'knowledge_seeded': False,
}

def seed(db):
    company = db.get(Company, TENANT)
    if not company:
        company = Company(id=TENANT, config=DEFAULT_CONFIG.copy())
        db.add(company)
    else:
        missing={k:v for k,v in DEFAULT_CONFIG.items() if k not in company.config}
        if missing:
            company.config={**company.config,**missing}
    if not db.scalar(select(Admin).where(Admin.tenant_id == TENANT)):
        if len(settings().admin_password) < 16 or len(settings().session_secret) < 32:
            raise RuntimeError('请先运行 scripts/bootstrap.py 生成安全的本地密码与密钥')
        db.add(Admin(email=settings().admin_email, password_hash=passwords.hash(settings().admin_password)))
    db.flush()
    if company.config.get('knowledge_seeded'):
        db.commit()
        return
    root = Path(settings().knowledge_dir)
    manifest = root / 'manifest.json'
    if manifest.exists():
        for record in json.loads(manifest.read_text()):
            if not db.get(Document, record['id']):
                doc = Document(id=record['id'], title=record['title'], category=record['category'],
                               content=(root / record['filename']).read_text(), status=record['status'],
                               visibility=record['visibility'], source_url=record.get('source_url'), is_demo=True)
                db.add(doc)
                ingest_document(db, doc)
    examples = [
        ('林同学', '想报公安联考班，预算2000元，下个月考试，想预约试听和报价', '公安联考课程页', '课程顾问A', '跟进中'),
        ('周同学', '准备国考，零基础，想了解线上课程和怎么收费', '国考课程页', '未分配', '新线索'),
        ('陈同学', '想考社区工作者，想了解学习课程，有晚上的课吗', '社区工作者课程页', '课程顾问B', '已合格'),
        ('吴同学', '已经报名了，课件打不开，请人工帮助', '课务服务', '课程顾问A', '跟进中'),
        ('王同学', '省考想预约试听，预算1500元，下周可以联系我', '省考课程页', '未分配', '新线索'),
    ]
    for i, (name, question, page, owner, stage) in enumerate(examples):
        stamp = now() - timedelta(days=i, hours=1)
        analysis = analyze_intent(question, True)
        waiting = i == 3
        conv = Conversation(visitor_name=name, source_page=page, token_hash=digest(secrets.token_urlsafe(32)),
                            analysis=analysis, status='waiting' if waiting else 'bot',
                            waiting_since=stamp if waiting else None, is_demo=True, created_at=stamp, updated_at=stamp)
        db.add(conv)
        db.flush()
        db.add(Message(conversation_id=conv.id, role='user', content=question, created_at=stamp))
        db.add(Message(conversation_id=conv.id, role='assistant', content='这是合成演示对话，用于展示咨询管理。请在官网演示页测试真实知识检索。', mode='sample', created_at=stamp))
        lead = Lead(conversation_id=conv.id, name=name, contact=f'1380000000{i}',
                    consent_text='合成演示数据，无真实联系授权或个人信息', level=analysis['level'], score=analysis['score'],
                    purpose=analysis['purpose'], tags=analysis['tags'], evidence=analysis['evidence'],
                    owner=owner, stage=stage, is_demo=True, created_at=stamp, updated_at=stamp)
        db.add(lead)
        db.flush()
        if i == 0:
            db.add(FollowUp(lead_id=lead.id, author='课程顾问A', content='演示记录：已了解备考基础，待确认试听时间。'))
    company.config = {**company.config, 'knowledge_seeded': True}
    db.commit()
