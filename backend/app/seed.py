import json
from pathlib import Path
from sqlalchemy import select
from .config import settings
from .models import Company, Admin, Document, TENANT
from .security import passwords
from .knowledge import ingest_document
from .privacy import DEFAULT_PRIVACY_NOTICE

DEFAULT_CONFIG = {
    'company_name': '知行公考', 'bot_name': '知行学习顾问',
    'description': '专注公职考试备考咨询与课程服务',
    'welcome': '您好，我是知行学习顾问。您正在准备哪一类考试？我可以帮您了解课程、学习方式或预约试听。课程与价格以机构公布的信息为准。',
    'service_hours': '每日 09:00–21:00',
    'suggested_questions': ['公安联考基础班多少钱？', '国考零基础怎么学习？', '如何预约试听？', '辅警和社会招警有什么区别？'],
    'contact_notice': '我同意机构使用此联系方式，就本次课程咨询与我联系。联系方式尚待核验，不用于其他营销。',
    'privacy_notice': DEFAULT_PRIVACY_NOTICE,
    'allowed_embed_origins': ['http://localhost:18080', 'http://127.0.0.1:18080'],
    'retention_conversation_days': 365,
    'retention_lead_days': 730,
    'retention_audit_days': 1095,
    'retention_backup_days': 90,
    'reception_assign_mode': 'least_busy',
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
        # Product default flipped to least_busy; operators may switch back to manual in 机器人配置.
        if int(company.config.get('reception_defaults_revision') or 1) < 2:
            company.config={**company.config,'reception_assign_mode':'least_busy','reception_defaults_revision':2}
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
                               visibility=record['visibility'], source_url=record.get('source_url'), is_demo=False)
                db.add(doc)
                ingest_document(db, doc)
    company.config = {**company.config, 'knowledge_seeded': True}
    db.commit()
