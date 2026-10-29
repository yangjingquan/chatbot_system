"""One-time-safe cleanup for the customer-facing seeded knowledge corpus."""
import re
from sqlalchemy import func, select
from .models import Audit, Company, Document, DocumentRevision, TENANT, now


DISCLAIMER = (
    "> 开发演示资料：知行公考为虚构机构；机构、课程、价格、服务安排均为演示设定，"
    "不构成真实销售或招录承诺。\n"
)

REPLACEMENTS = (
    (DISCLAIMER, ""),
    ("知行公考是本地开发演示中的考试培训机构", "知行公考专注公职考试备考咨询与课程服务"),
    ("八类备考咨询。提供课程介绍", "八类备考方向。提供课程介绍"),
    ("演示人工咨询时间", "人工咨询时间"),
    ("暂无真实校区地址、联系电话和师资名单，请勿编造；",
     "校区地址、联系电话和师资信息以机构正式公布的信息为准；"),
    ("## 演示基础班价格", "## 基础班价格"),
    ("以下均为虚构演示价，不是正式报价，每课时 45 分钟。",
     "以下为基础班价格，每课时 45 分钟。"),
    ("| 课程 | 演示参考价 | 演示课时 |", "| 课程 | 价格 | 课时 |"),
    ("演示基础班费用包含设定课时的", "基础班费用包含相应课时的"),
    ("真实交易必须以经确认的课程清单和合同为准。", "具体服务范围以课程清单和合同为准。"),
    ("暂不设定优惠活动、付款账户及开票主体", "优惠活动、付款账户及开票主体以机构正式通知为准"),
    ("演示课程以线上直播和回放为主，具体课程是否提供回放、回放截止时间、作业答疑和资料形式，以正式课程说明为准。暂无真实线下校区安排。",
     "课程以线上直播和回放为主，具体班次的回放期限、作业答疑和资料形式以课程安排为准。线下教学信息请咨询课程顾问。"),
    ("暂定演示服务为 1 次线上体验课，不承诺真实日期和可预约名额。",
     "每人可预约 1 次线上体验课，日期与名额以顾问确认为准。"),
    ("当前演示知识库没有真实合同、退费比例或计算公式。是否可以退费、扣费项目、办理时限及所需资料，应按有效合同、已履行服务及适用规则由人工核实。",
     "退费比例、扣费项目、办理时限及所需资料以已签署合同、已履行服务及适用规则为准，由工作人员核实。"),
    ("当前是本地开发演示系统，请使用合成数据测试。", ""),
    ("演示接待时间为每日 09:00–21:00，不虚构当前在线人数或保证等待分钟数。",
     "接待时间为每日 09:00–21:00。客服状态与排队情况以系统显示为准，不承诺固定等待时长。"),
    ("演示课程采用", "课程采用"),
    ("演示服务支持线上直播与回放，具体开班日期、授课老师、回放期限及线下安排尚未设置，需要人工核实。提供 1 次线上体验课预约，预约不等于已确认开课。",
     "课程支持线上直播与回放，具体开班日期、授课老师、回放期限及线下安排请联系顾问确认。可预约 1 次线上体验课，预约提交后由顾问确认时间。"),
    ("当前演示资料没有实际开班日期，请预约人工确认。", "当前班次的开班日期请联系顾问确认。"),
    ("# 内部顾问分配与跟进示例", "# 内部顾问分配与跟进规范"),
    ("## 内部资料禁止对访客公开\n这是权限隔离测试资料。内部测试识别码为 ZX-INTERNAL-4827，不应出现在公开客服回答或引用中。",
     "## 资料范围\n本资料供内部运营人员参考，不对访客公开。公开客服回答只使用已发布的公开资料。"),
    ("## 跟进\n", "## 跟进原则\n"),
    ("基于具体需求、主动报价或试听请求、采购或学习时间、自愿联系信息判断优先级。纯售后不进入购买意向分级；信息不足保持待判定；不从姓名推断经济情况。人工修改等级优先于后续自动规则。",
     "基于访客明确表达的需求、主动提出的报价或试听请求、预计学习时间及联系许可安排后续工作。纯售后问题不进入购买意向分级；信息不足时保持待判定；不从姓名推断经济情况。人工确认的等级优先于自动规则。"),
    ("## 演示归属\n种子线索分配至课程顾问A、课程顾问B，均为虚构员工。实际团队需在正式上线前确认。",
     "## 线索归属\n按照企业设置的接待分配方式和员工权限分配线索。负责人变更、转交和跟进结果应在系统中及时记录。"),
)

COURSE_PRICE = re.compile(
    r"本地演示的(.+?)参考价为 (\d+) 元，(\d+) 课时，每课时 45 分钟。"
    r"该价格仅供系统演示，不是实际对外报价。"
)


def clean_text(value: str) -> str:
    cleaned = value or ""
    for old, new in REPLACEMENTS:
        cleaned = cleaned.replace(old, new)
    cleaned = COURSE_PRICE.sub(r"\1价格为 \2 元，\3 课时，每课时 45 分钟。", cleaned)
    return cleaned


def prepare_customer_data(db) -> dict:
    """Promote current records and publish normalized knowledge without rewriting history."""
    # Serialize concurrent app starts so the unique document revision sequence stays safe.
    db.scalar(select(Company).where(Company.id == TENANT).with_for_update())
    changed_docs = 0
    cleaned_revisions = 0
    for doc in db.scalars(select(Document).where(Document.tenant_id == TENANT).order_by(Document.id)):
        old_title, old_content = doc.title, doc.content
        doc.title = clean_text(doc.title)
        doc.content = clean_text(doc.content)
        changed = (doc.title != old_title or doc.content != old_content or doc.is_demo)
        if changed:
            doc.is_demo = False
            doc.updated_at = now()
            if doc.status in ('published', 'archived'):
                latest = db.scalar(select(func.max(DocumentRevision.number)).where(
                    DocumentRevision.document_id == doc.id,
                )) or 0
                number = latest + 1
                for revision in db.scalars(select(DocumentRevision).where(
                    DocumentRevision.document_id == doc.id,
                    DocumentRevision.status == 'published',
                )):
                    revision.status = 'superseded'
                doc.version = number
                doc.edit_sequence += 1
                from .document_service import snapshot
                revision = DocumentRevision(
                    document_id=doc.id,
                    tenant_id=doc.tenant_id,
                    number=number,
                    status='published',
                    data=snapshot(doc),
                    author_name='上线资料整理',
                    reviewer_name='系统',
                    review_note='整理客户可见资料并更新检索内容。',
                )
                db.add(revision)
                if doc.status == 'published':
                    from .knowledge import ingest_document
                    ingest_document(db, doc)
                    from .jobs import enqueue_index
                    enqueue_index(db, doc)
                changed_docs += 1
            else:
                # Keep draft state intact while ensuring the working copy and its latest
                # pending revision both use customer-ready wording.
                from .document_service import snapshot
                pending = db.scalar(select(DocumentRevision).where(
                    DocumentRevision.document_id == doc.id,
                    DocumentRevision.status.in_(('draft', 'submitted')),
                ).order_by(DocumentRevision.number.desc()))
                if pending:
                    data = dict(pending.data)
                    data['title'] = clean_text(data.get('title', ''))
                    data['content'] = clean_text(data.get('content', ''))
                    data['is_demo'] = False
                    if data != pending.data:
                        pending.data = data
                        pending.updated_at = now()
                        cleaned_revisions += 1
                elif doc.status == 'draft':
                    latest = db.scalar(select(func.max(DocumentRevision.number)).where(
                        DocumentRevision.document_id == doc.id,
                    )) or 0
                    db.add(DocumentRevision(
                        document_id=doc.id,
                        tenant_id=doc.tenant_id,
                        number=latest + 1,
                        status='draft',
                        data=snapshot(doc),
                        author_name='上线资料整理',
                        review_note='整理资料文案。',
                    ))
                    doc.edit_sequence += 1
                    cleaned_revisions += 1

    # Preserve authored historical revisions as audit history; clean all current drafts.
    for revision in db.scalars(select(DocumentRevision).where(
        DocumentRevision.tenant_id == TENANT,
        DocumentRevision.status.in_(('draft', 'submitted')),
    )):
        data = dict(revision.data)
        title = clean_text(data.get('title', ''))
        content = clean_text(data.get('content', ''))
        if title != data.get('title') or content != data.get('content') or data.get('is_demo'):
            data.update(title=title, content=content, is_demo=False)
            revision.data = data
            revision.updated_at = now()
            cleaned_revisions += 1

    if changed_docs:
        db.add(Audit(
            tenant_id=TENANT,
            actor='系统',
            action='整理上线知识资料',
            details=f'已更新 {changed_docs} 份发布资料，重建检索内容。',
            target='customer-readiness',
        ))
    return {'documents_updated': changed_docs, 'drafts_updated': cleaned_revisions}
