"""Commercial facts belong to the course catalog, not free-form reference documents."""
import re

NUMBER=r'[零〇一二三四五六七八九十百千万两\d,.]+'
AMOUNT=re.compile(r'(?:[¥￥]\s*'+NUMBER+r'|(?:RMB|CNY|人民币)\s*[:：]?\s*'+NUMBER+r'|'+NUMBER+r'\s*(?:元|块|课时|学时)|(?:学费|课程费|售价|原价|优惠价|价格|收费|报价|费用)\s*[:：]?\s*'+NUMBER+r')',re.I)
PRICE_WORDS=re.compile(r'学费|课程费|售价|原价|优惠价|价格|收费|报价|费用|课时|学时|开班|人民币|[¥￥]|\bRMB\b|\bCNY\b',re.I)

def has_amount(text):return bool(AMOUNT.search(text or ''))

def course_question(text):
    return bool(re.search(r'课程|培训|报班|班型|班次|基础班|笔试班|面试班|多少钱|报价|学费|课时|线上课|线下课|上课|晚上的课|开课|开班|价格|试听|怎么报名',text))

def reference_is_commercial(source):
    if source.get('knowledge_type')=='policy':return False
    return source.get('knowledge_type')=='course' or bool(PRICE_WORDS.search(source.get('text',''))) or has_amount(source.get('text',''))
