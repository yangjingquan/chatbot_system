"""Replay explicit user events; cancellation and service context persist across neutral turns."""
import re

RULE_VERSION = 'training-v4'
EXAMS = ('公安联考','国考','省考','选调生','事业单位','社会招警','辅警','社区工作者')
EXAM_PATTERN = re.compile(r'公安联考|(?:公务员)?国考|(?:公务员)?省考|选调生|事业单位|事业编|社会招警|辅警|社区工作者')
NEGATIVE = re.compile(r'不想|不需要|不用|不打算|不考虑|不要|暂不|不是|没打算|不感兴趣|取消|不考')
WITHDRAW_CONTACT = re.compile(r'(不用|不要|别|停止|不需要|不再).{0,5}联系|撤回.{0,8}(同意|授权|联系)')
REJECT_PURCHASE = re.compile(r'(?:不打算|不想|不考虑|没打算|不再|暂不|取消|不准备|不需要|不要).{0,5}(?:报名|报班|购买|采购|报考)|不(?:报名|报班|报了|考了)|不考.{0,10}(?:联考|国考|省考|选调|事业|招警|辅警|社区)|暂时不报|不感兴趣')
NEED = re.compile(r'想报|准备|备考|想学|要考|想了解|想考|打算|改考|转考|零基础')
ACTION = re.compile(r'报价|多少钱|怎么收费|价格多少|想报名|要报名|准备报名|想报|还是想报名|预约|试听|顾问联系')
SERVICE = re.compile(r'退费|退款|投诉|无法听课|课件打不开|转班|发票|已经报[过了]名|已购')
SWITCH = re.compile(r'改考|转考|换成|改成|改为|转为')

def exam_names(text):
    return list(dict.fromkeys('事业单位' if m=='事业编' else m.replace('公务员','') for m in EXAM_PATTERN.findall(text)))

def _level(score, action):
    if score>=70 and action:return '高意向'
    if score>=40:return '中意向'
    return '低意向'

def analyze_messages(messages: list[dict], contactable: bool=False, respect_withdrawal: bool=True) -> dict:
    needs, services, focus, events, purposes = {}, {}, [], [], []
    purpose='课程咨询'
    def need(exam):
        return needs.setdefault(exam,{'exam_type':exam,'purpose':'课程咨询','state':'active','signals':{},'actions':{},'rejection':None})
    for message in messages:
        content=message['content']
        mid=message.get('id')
        if respect_withdrawal and WITHDRAW_CONTACT.search(content):contactable=False
        for clause in re.split(r'[，。！？；\n,!?;]|但是|不过|另外|但',content):
            clause=clause.strip()
            if not clause:continue
            names=exam_names(clause)
            if names:
                if SWITCH.search(clause):
                    for key,value in needs.items():
                        if key not in names and value['state']=='active':value['state']='switched'
                focus=names
            targets=names or focus or ['未指定考试']
            proof={'quote':clause[:180],'message_id':mid}
            # "不要取消报名" is not a new cancellation.
            rejection=REJECT_PURCHASE.search(clause) and not re.search(r'(?:不要|不用|不是|并非).{0,3}(?:取消|不报|不考)',clause)
            if rejection:
                cancelled=names or list(needs) or targets
                for target in cancelled:
                    need(target).update(state='declined',signals={},actions={},rejection={**proof,'label':'最新明确拒绝','points':0})
                events.append({**proof,'kind':'purchase_declined','exam_types':cancelled})
                purpose='课程咨询'
                continue
            cancel_trial=re.search(r'(取消|不想|不用|不需要|不要).{0,5}(试听|预约)',clause)
            cancel_quote=re.search(r'(取消|不想|不用|不需要|不要).{0,5}(询价|报价)',clause)
            for target in targets:
                if target in needs:
                    if cancel_trial:needs[target]['actions'].pop('trial',None)
                    if cancel_quote:needs[target]['actions'].pop('quote',None)
            if NEGATIVE.search(clause):continue
            if SERVICE.search(clause):
                purpose='售后服务'
                if purpose not in purposes:purposes.append(purpose)
                for target in targets:
                    services[target]={'exam_type':target,'purpose':purpose,'state':'active','evidence':[{**proof,'label':purpose,'points':0}]}
                continue
            explicit_need=bool(NEED.search(clause)) and (bool(names) or bool(focus) or '零基础' in clause)
            explicit_action=bool(ACTION.search(clause))
            new_course=(explicit_need or explicit_action) and (bool(names) or bool(re.search(r'课程|培训|报班|报名|备考|考班|基础班|笔试班|面试班|试听',clause)))
            if purpose=='售后服务':
                if not new_course:continue
                # A new course after a service episode must provide its own purchase evidence.
                for target in targets:
                    if target in needs:needs[target].update(signals={},actions={},state='active',rejection=None)
                purpose='课程咨询'
            if names:
                for target in names:need(target)
            if explicit_need or explicit_action:
                purpose='课程咨询'
                if purpose not in purposes:purposes.append(purpose)
            for target in targets:
                if target not in needs and not (explicit_need or explicit_action):continue
                n=need(target)
                if n['state']!='active':
                    if not (explicit_need or explicit_action):continue
                    n.update(state='active',signals={},actions={},rejection=None)
                    events.append({**proof,'kind':'purchase_reopened','exam_types':[target]})
                if explicit_need:n['signals']['need']={**proof,'label':'明确需求','points':25}
                if explicit_action:
                    if re.search(r'报价|多少钱|收费|价格',clause):n['actions']['quote']={**proof,'label':'主动询价','kind':'quote'}
                    if re.search(r'试听|预约',clause):n['actions']['trial']={**proof,'label':'预约试听','kind':'trial'}
                    if re.search(r'想报|要报名|准备报名|顾问联系',clause):n['actions']['enroll']={**proof,'label':'主动咨询课程','kind':'enroll'}
                if re.search(r'下个?月|本月|这个月|下周|近期|尽快|马上|今年.{0,5}(考试|报考)|\d+月.{0,6}(考试|报考)',clause):
                    n['signals']['time']={**proof,'label':'近期计划','points':20}
                if re.search(r'预算.{0,10}\d|\d+\s*(元|块).{0,6}(预算|以内)|\d+\s*人.{0,5}(报名|团报)',clause):
                    n['signals']['budget']={**proof,'label':'预算明确','points':15}
    active=[n for n in needs.values() if n['state']=='active']
    evidence=[]
    tags=[n['exam_type'] for n in active if n['exam_type']!='未指定考试']
    score=0
    for n in needs.values():
        action_evidence=[{**p,'points':25 if i==0 else 0,'group':'咨询行动（同组不重复计分）'} for i,p in enumerate(n['actions'].values())]
        n['evidence']=list(n['signals'].values())+action_evidence if n['state']=='active' else ([n['rejection']] if n['rejection'] else [])
        n['score']=min(100,sum(e['points'] for e in n['evidence'])+(15 if contactable and n['state']=='active' else 0))
        n['level']=_level(n['score'],bool(n['actions'])) if n['state']=='active' and (n['signals'] or n['actions']) else '一般咨询'
        if n['state']=='active':
            score=max(score,n['score'])
            evidence+=n['evidence']
            tags.extend(e['label'] for e in n['evidence'])
        n.pop('signals');n.pop('actions');n.pop('rejection')
    declined=bool(needs) and not active
    if purpose=='售后服务':
        level='不适用';score=0
        evidence=[e for service in services.values() for e in service['evidence']]
        tags.append('售后服务')
        purchase_state='declined' if declined else ('interested' if active else 'unknown')
    elif declined:
        level='一般咨询';score=0;purchase_state='declined';tags.append('暂不考虑')
        evidence=[e for n in needs.values() for e in n['evidence']]
    elif active:
        level=max(active,key=lambda n:n['score'])['level']
        purchase_state='interested' if evidence else 'exploring'
    else:
        level='一般咨询' if contactable else '待判定';purchase_state='unknown'
    if contactable:
        tags.append('可联系')
        if not declined and purpose!='售后服务':
            evidence.append({'label':'联系授权','quote':'访客已提交本次需求联系许可，联系方式尚待人工核实','points':15,'message_id':None,'source':'contact_consent'})
    if not purposes:purposes=['课程咨询']
    return {'purpose':purpose,'purposes':purposes,'purchase_state':purchase_state,'score':score,'level':level,
            'tags':list(dict.fromkeys(tags)),'evidence':evidence,'contactable':contactable,
            'needs':list(needs.values())+list(services.values()),
            'active_exam_types':[n['exam_type'] for n in active if n['exam_type']!='未指定考试'],
            'state_events':events,'rule_version':RULE_VERSION}

def analyze_intent(text: str, contactable: bool=False) -> dict:
    return analyze_messages([{'id':None,'content':line} for line in text.split('\n') if line.strip()],contactable)
