"""Published-source retrieval. No request may bypass document visibility filters."""
import io
import math
import re
import threading
import zipfile
from collections import Counter
from pathlib import Path
import jieba
from sqlalchemy import select, delete
from fastapi import HTTPException
from .config import settings
from .models import Chunk, Document, TENANT

STOP = set('的 了 吗 呢 啊 我 你 您 们 是 有 和 与 在 就 请 可以 什么 怎么 如何 一下 想 要 了解 这个 那个 还有 以及'.split())
_model = None
_model_status = 'disabled'
_model_lock = threading.Lock()

def terms(text: str) -> list[str]:
    return [w.lower() for w in jieba.lcut_for_search(text) if len(w.strip()) > 1 and w not in STOP and re.search(r'[\w\u4e00-\u9fff]', w)]

def split_content(text: str) -> list[str]:
    text = re.sub(r'^>.*$', '', text, flags=re.M)
    parts, current = [], ''
    for para in re.split(r'\n\s*\n|(?=^## )', text, flags=re.M):
        if len(current) + len(para) > 600 and current.strip():
            parts.append(current.strip())
            current = ''
        while len(para) > 650:
            parts.append(para[:600])
            para = para[540:]
        current += para + '\n'
    if current.strip():
        parts.append(current.strip())
    return parts

def ingest_document(db, doc: Document) -> int:
    db.flush()
    db.execute(delete(Chunk).where(Chunk.document_id == doc.id, Chunk.tenant_id == doc.tenant_id))
    texts = split_content(doc.content)
    # Publication switches text chunks atomically; vectors are prepared by a durable worker.
    embeddings = [None] * len(texts)
    for i, (text, vector) in enumerate(zip(texts, embeddings)):
        db.add(Chunk(document_id=doc.id, tenant_id=doc.tenant_id, text=text,
                     terms=' '.join(terms(doc.title + '\n' + text)), position=i, embedding=vector))
    db.flush()
    return len(texts)

def retrieval_status():
    return {'mode': 'hybrid' if _model is not None else 'keyword', 'embedding_status': _model_status,
            'model': settings().embedding_model if settings().embedding_enabled else None}

def warm_embeddings():
    global _model, _model_status
    if not settings().embedding_enabled:
        return
    _model_status = 'loading'
    try:
        from fastembed import TextEmbedding
        from .db import SessionLocal
        model = TextEmbedding(settings().embedding_model, cache_dir='/models', threads=2)
        _model = model
        _model_status = 'ready'
    except Exception:
        _model_status = 'unavailable'

def _retrieve_documents(db, query: str, public_only: bool = True, tenant_id: str = TENANT, limit: int = 4, context: dict | None = None) -> list[dict]:
    from .search import search_documents
    return search_documents(db,query,public_only,tenant_id,limit,context)

def historical_commercial_text(source):
    from .commercial import reference_is_commercial
    return reference_is_commercial(source)

def retrieve(db,query:str,public_only:bool=True,tenant_id:str=TENANT,limit:int=4,context:dict|None=None):
    from .catalog import catalog_sources
    from .document_service import infer_context
    scope=context if context is not None else infer_context(query)
    courses=catalog_sources(db,query,scope,tenant_id)
    docs=_retrieve_documents(db,query,public_only,tenant_id,limit,scope)
    if public_only:
        docs=[s for s in docs if not historical_commercial_text(s)]
        # Known course questions use only the catalog's commercial facts. Policy evidence
        # remains separate; generic documents cannot override a course's current state.
        if courses:docs=[s for s in docs if s.get('knowledge_type')=='policy']
    # Course suggestions must not crowd out matching policy or other knowledge evidence.
    if courses and docs:return courses[:min(2,limit)]+docs[:max(0,limit-min(2,len(courses)))]
    return (courses+docs)[:limit]

# Text/PDF/DOCX with tables often exceed 15 MB; scanned PDFs use OCR (tesseract).
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_FILE_MB = MAX_FILE_BYTES // (1024 * 1024)
ALLOWED_SUFFIXES = {'.md', '.txt', '.pdf', '.docx'}
OCR_ENABLED = True
OCR_MAX_PAGES = 50
OCR_DPI = 200
OCR_LANG = 'chi_sim+eng'
OCR_TEXT_MIN_CHARS = 20
OCR_LOW_CONFIDENCE = 50


def _ocr_pdf_pages(content: bytes, page_numbers: list[int], *, dpi: int = OCR_DPI,
                   lang: str = OCR_LANG) -> dict[int, dict]:
    """OCR only selected pages, preserving page numbers and per-page confidence."""
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
    except Exception as exc:
        raise HTTPException(503, f'OCR 依赖未就绪（pdf2image/pytesseract）：{exc}') from exc
    pages = {}
    for page_number in page_numbers:
        try:
            images = convert_from_bytes(content, dpi=dpi, first_page=page_number,
                                        last_page=page_number)
        except Exception as exc:
            raise HTTPException(422, f'第 {page_number} 页转图失败，无法 OCR：{exc}') from exc
        if not images:
            raise HTTPException(422, f'第 {page_number} 页无法转图，未生成完整草稿')
        try:
            data = pytesseract.image_to_data(images[0], lang=lang,
                                             output_type=pytesseract.Output.DICT)
        except Exception as exc:
            raise HTTPException(422, f'第 {page_number} 页 OCR 失败：{exc}') from exc
        words, confidence = [], []
        for token, raw_confidence in zip(data.get('text', []), data.get('conf', [])):
            token = (token or '').strip()
            if not token:
                continue
            words.append(token)
            try:
                value = float(raw_confidence)
                if value >= 0:
                    confidence.append(value)
            except (TypeError, ValueError):
                pass
        pages[page_number] = {
            'text': ' '.join(words),
            'confidence': round(sum(confidence) / len(confidence)) if confidence else None,
        }
    return pages


def ocr_pdf_bytes(content: bytes, *, max_pages: int = OCR_MAX_PAGES, dpi: int = OCR_DPI,
                  lang: str = OCR_LANG) -> str:
    """Compatibility helper; reject truncation instead of implying a full parse."""
    if max_pages < 1:
        raise HTTPException(422, 'OCR 页数上限必须大于 0')
    if max_pages > OCR_MAX_PAGES:
        raise HTTPException(422, f'OCR 单次最多处理 {OCR_MAX_PAGES} 页')
    try:
        from pypdf import PdfReader
        page_count = len(PdfReader(io.BytesIO(content)).pages)
    except Exception as exc:
        raise HTTPException(422, 'PDF 无法解析，未生成不完整草稿') from exc
    if page_count > max_pages:
        raise HTTPException(422, f'PDF 共 {page_count} 页，超过本次 OCR 上限 {max_pages} 页；请拆分文件后重试')
    pages = _ocr_pdf_pages(content, list(range(1, page_count + 1)), dpi=dpi, lang=lang)
    return '\n\n'.join(f'第 {page_number} 页\n{data["text"]}'
                       for page_number, data in pages.items())


def parse_file_result(filename: str, content: bytes) -> tuple[str, dict]:
    suffix = Path(filename).suffix.lower()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, f'文件不能超过 {MAX_FILE_MB} MB')
    metadata = {'page_count': None, 'ocr_pages': [], 'ocr_warnings': []}
    try:
        if suffix in ('.md', '.txt'):
            text = content.decode('utf-8-sig')
        elif suffix == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise HTTPException(422, 'PDF 已加密，请解除密码保护后重新上传')
            if len(reader.pages) > 200:
                raise HTTPException(422, 'PDF 超过单文件 200 页上限，请按页数拆分后重试')
            metadata['page_count'] = len(reader.pages)
            page_texts = [(page.extract_text() or '').strip() for page in reader.pages]
            ocr_pages = [number for number, page_text in enumerate(page_texts, 1)
                         if len(re.sub(r'\s', '', page_text)) < OCR_TEXT_MIN_CHARS]
            if ocr_pages and not OCR_ENABLED:
                raise HTTPException(422, f'第 {ocr_pages[0]} 页文字不足且 OCR 已关闭；请上传可复制文字的 PDF、Markdown、TXT 或 DOCX')
            if len(ocr_pages) > OCR_MAX_PAGES:
                sample = '、'.join(str(number) for number in ocr_pages[:10])
                suffix_hint = '等' if len(ocr_pages) > 10 else ''
                raise HTTPException(422, f'有 {len(ocr_pages)} 页需要 OCR（第 {sample} 页{suffix_hint}），超过单文件 {OCR_MAX_PAGES} 页上限；请拆分文件后重试，本次未生成不完整草稿')
            recognized = _ocr_pdf_pages(content, ocr_pages) if ocr_pages else {}
            metadata['ocr_pages'] = ocr_pages
            for page_number in ocr_pages:
                result = recognized[page_number]
                page_text = result['text'].strip()
                if len(re.sub(r'\s', '', page_text)) < OCR_TEXT_MIN_CHARS:
                    raise HTTPException(422, f'第 {page_number} 页 OCR 未识别到足够文字，本次未生成不完整草稿')
                confidence = result.get('confidence')
                if confidence is None or confidence < OCR_LOW_CONFIDENCE:
                    metadata['ocr_warnings'].append(
                        f'第 {page_number} 页 OCR 置信度{confidence if confidence is not None else "未知"}，请人工核对')
                page_texts[page_number - 1] = page_text
            text = '\n\n'.join(f'第 {i + 1} 页\n{page_text}'
                                for i, page_text in enumerate(page_texts))
        elif suffix == '.docx':
            from docx import Document as Word
            archive = zipfile.ZipFile(io.BytesIO(content))
            if len(archive.infolist()) > 1000 or sum(i.file_size for i in archive.infolist()) > 80 * 1024 * 1024:
                raise ValueError('document expands beyond limit')
            document = Word(io.BytesIO(content))
            text = '\n'.join(p.text for p in document.paragraphs)
            text += '\n' + '\n'.join(' | '.join(c.text for c in row.cells) for table in document.tables for row in table.rows)
        else:
            raise HTTPException(415, '支持 UTF-8 的 Markdown、TXT、文本 PDF 和 DOCX')
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, '文件无法解析，请检查文件内容、编码或是否加密')
    if not text.strip() or len(text) > 200000 or '\x00' in text:
        raise HTTPException(422, '文件为空、内容过大或包含无效字符')
    return text, metadata


def parse_file(filename: str, content: bytes) -> str:
    return parse_file_result(filename, content)[0]
