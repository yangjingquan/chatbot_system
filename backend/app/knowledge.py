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


def ocr_pdf_bytes(content: bytes, *, max_pages: int = OCR_MAX_PAGES, dpi: int = OCR_DPI, lang: str = OCR_LANG) -> str:
    """Rasterize PDF pages and run Tesseract. Overridable in tests via monkeypatch."""
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
    except Exception as exc:
        raise HTTPException(503, f'OCR 依赖未就绪（pdf2image/pytesseract）：{exc}') from exc
    try:
        images = convert_from_bytes(content, dpi=dpi, first_page=1, last_page=max_pages)
    except Exception as exc:
        raise HTTPException(422, f'扫描 PDF 转图像失败，无法 OCR：{exc}') from exc
    if not images:
        raise HTTPException(422, '扫描 PDF 无可识别页面，OCR 未提取到文字')
    parts = []
    for i, image in enumerate(images):
        try:
            page_text = pytesseract.image_to_string(image, lang=lang) or ''
        except Exception as exc:
            raise HTTPException(422, f'第 {i + 1} 页 OCR 失败：{exc}') from exc
        parts.append(f'第 {i + 1} 页\n{page_text}')
    return '\n\n'.join(parts)


def parse_file(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, f'文件不能超过 {MAX_FILE_MB} MB')
    try:
        if suffix in ('.md', '.txt'):
            text = content.decode('utf-8-sig')
        elif suffix == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted or len(reader.pages) > 200:
                raise ValueError('encrypted or too many pages')
            text = '\n\n'.join(f'第 {i + 1} 页\n{p.extract_text() or ""}' for i, p in enumerate(reader.pages))
            if len(re.sub(r'第 \d+ 页|\s', '', text)) < 20:
                if not OCR_ENABLED:
                    raise HTTPException(422, '未提取到足够文字，可能是扫描 PDF；OCR 已关闭，请上传可复制文字的 PDF、Markdown、TXT 或 DOCX')
                text = ocr_pdf_bytes(content)
                if len(re.sub(r'第 \d+ 页|\s', '', text)) < 20:
                    raise HTTPException(422, 'OCR 未识别到足够文字，请检查扫描清晰度或改传可复制文字的 PDF / Markdown / TXT / DOCX')
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
    return text
