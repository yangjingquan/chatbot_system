"""Conservative, evidence-based business rules. Scores are not probabilities."""
import re

EXAMS = ('公安联考', '国考', '省考', '选调生', '事业单位', '事业编', '社会招警', '辅警', '社区工作者')
NEGATIVE = re.compile(r'(不想|不需要|不用|不打算|不考虑|不要|暂不|不是|没打算|不感兴趣|取消)')
WITHDRAW_CONTACT = re.compile(r'(不用|不要|别|停止|不需要|不再).{0,5}联系|撤回.{0,8}(同意|授权|联系)')
REJECT_PURCHASE = re.compile(r'(不打算|不想|不考虑|没打算|不再|暂不|取消).{0,6}(报名|报班|购买|采购|试听)')

def analyze_intent(text: str, contactable: bool = False) -> dict:
    latest = next((line for line in reversed(text.split('\n')) if line.strip()),'')
    if WITHDRAW_CONTACT.search(latest): contactable=False
    if REJECT_PURCHASE.search(latest):
        return {'purpose':'课程咨询','score':0,'level':'一般咨询','tags':['暂不考虑'],
                'evidence':[{'label':'最新明确拒绝','quote':latest[:180],'points':0}],
                'contactable':contactable,'rule_version':'training-v2'}
    clauses = [s.strip() for s in re.split(r'[，。！？；\n,!?;]', text) if s.strip()]
    positive = [s for s in clauses if not NEGATIVE.search(s)]
    evidence, tags, score = [], [], 0
    def add(label, points, pattern):
        nonlocal score
        for clause in positive:
            if re.search(pattern, clause):
                tags.append(label)
                evidence.append({'label': label, 'quote': clause[:180], 'points': points})
                score += points
                return True
        return False
    for exam in EXAMS:
        if exam in text:
            tags.append('事业单位' if exam == '事业编' else exam)
    service = any(re.search(r'退费|退款|投诉|无法听课|课件打不开|转班|发票|已经报[过了]名|已购', s) for s in positive)
    if service:
        return {'purpose': '售后服务', 'score': 0, 'level': '不适用', 'tags': list(dict.fromkeys(tags + ['售后服务'])),
                'evidence': [{'label': '售后服务', 'quote': s[:180], 'points': 0} for s in positive if re.search(r'退费|退款|投诉|听课|转班|发票|已购', s)],
                'contactable': contactable, 'rule_version': 'training-v1'}
    add('明确需求', 25, r'(想报|准备|备考|想学|要考|想了解).{0,20}(联考|国考|省考|选调|事业|招警|辅警|社区)|零基础.{0,10}(课程|班)')
    action = add('主动询价', 25, r'报价|多少钱|怎么收费|价格多少|报名|预约|试听|顾问联系')
    if any(re.search(r'试听|预约', s) for s in positive):
        tags.append('预约试听')
    add('近期计划', 20, r'下个?月|本月|这个月|下周|近期|尽快|马上|今年.{0,5}(考试|报考)|\d+月.{0,6}(考试|报考)')
    add('预算明确', 15, r'预算.{0,10}\d|\d+\s*(元|块).{0,6}(预算|以内)|\d+\s*人.{0,5}(报名|团报)')
    if contactable:
        tags.append('可联系')
        evidence.append({'label': '联系授权', 'quote': '访客已提交本次需求联系许可，联系方式尚待人工核实', 'points': 15})
        score += 15
    if not evidence or (contactable and len(evidence) == 1):
        level = '一般咨询' if tags else '待判定'
    elif not action:
        level = '中意向' if score >= 40 else '低意向'
    else:
        level = '高意向' if score >= 70 else ('中意向' if score >= 40 else '低意向')
    return {'purpose': '课程咨询', 'score': min(score, 100), 'level': level, 'tags': list(dict.fromkeys(tags)),
            'evidence': evidence, 'contactable': contactable, 'rule_version': 'training-v1'}
