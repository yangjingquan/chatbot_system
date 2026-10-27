"""Redact common PII before model calls and in non-essential surfaces.

Coverage is heuristic only: mainland mobile numbers, 18-digit resident ID
numbers, and emails. It cannot catch every sensitive phrase, nicknames,
partial numbers, screenshots, or free-form secrets.
"""
import re

# History sent to the model: recent turns only, each truncated after redaction.
MAX_HISTORY_MESSAGES = 4
MAX_MESSAGE_CHARS = 1200

DEFAULT_PRIVACY_NOTICE = (
    '智能回答会把您在本窗口输入的问题及近期对话内容（经常见敏感信息脱敏后）'
    '发送至 DeepSeek 模型服务以生成回复；请勿在对话中发送身份证号、完整银行卡号'
    '等敏感信息。正式留资请使用下方「请顾问联系我」表单。本提示文案需机构按'
    '实际数据处理规则确认后使用。'
)

PHONE_RE = re.compile(r'(?<!\d)(?:\+?86[-\s]?)?(1[3-9]\d{9})(?!\d)')
# Rough mainland 18-digit ID shape (includes birth date range); checksum not verified.
ID_RE = re.compile(
    r'(?<!\d)([1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx])(?!\d)'
)
EMAIL_RE = re.compile(r'\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b')


def _mask_phone(match: re.Match) -> str:
    number = match.group(1)
    return number[:3] + '****' + number[-4:]


def _mask_id(match: re.Match) -> str:
    value = match.group(1)
    return value[:4] + '**********' + value[-4:]


def _mask_email(match: re.Match) -> str:
    local, domain = match.group(1), match.group(2)
    keep = local[:1] if local else ''
    return f'{keep}***@{domain}'


def redact_text(text: str | None) -> str:
    """Replace common PII patterns; safe on empty/None."""
    if not text:
        return ''
    result = PHONE_RE.sub(_mask_phone, text)
    result = ID_RE.sub(_mask_id, result)
    result = EMAIL_RE.sub(_mask_email, result)
    return result


def truncate_text(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def prepare_model_history(history: list[dict], max_messages: int = MAX_HISTORY_MESSAGES,
                          max_chars: int = MAX_MESSAGE_CHARS) -> list[dict]:
    """Keep recent user/assistant turns, redact, then truncate each body."""
    selected = [m for m in history if m.get('role') in ('user', 'assistant')][-max_messages:]
    return [{'role': m['role'], 'content': truncate_text(redact_text(m.get('content') or ''), max_chars)}
            for m in selected]


def prepare_model_question(question: str, max_chars: int = MAX_MESSAGE_CHARS) -> str:
    return truncate_text(redact_text(question), max_chars)
