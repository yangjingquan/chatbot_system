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
    embeddings = [None] * len(texts)
    if _model is not None and texts:
        with _model_lock:
            embeddings = [v.tolist() for v in _model.embed(texts)]
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
        with SessionLocal() as db:
            chunks = db.scalars(select(Chunk).where(Chunk.embedding.is_(None))).all()
            for i in range(0, len(chunks), 32):
                batch = chunks[i:i + 32]
                vectors = list(model.embed([c.text for c in batch]))
                for chunk, vector in zip(batch, vectors):
                    chunk.embedding = vector.tolist()
                db.commit()
        _model = model
        _model_status = 'ready'
    except Exception:
        _model_status = 'unavailable'

def retrieve(db, query: str, public_only: bool = True, tenant_id: str = TENANT, limit: int = 4) -> list[dict]:
    statement = select(Chunk, Document).join(Document, Chunk.document_id == Document.id).where(
        Chunk.tenant_id == tenant_id, Document.tenant_id == tenant_id, Document.status == 'published')
    if public_only:
        statement = statement.where(Document.visibility == 'public')
    # Bounded first-release corpus. Scale beyond this with DB-side lexical ranking.
    rows = db.execute(statement.limit(5000)).all()
    if not rows:
        return []
    q = set(terms(query))
    if not q:
        return []
    token_lists = [c.terms.split() for c, _ in rows]
    frequencies = Counter(t for tokens in token_lists for t in set(tokens))
    average = sum(map(len, token_lists)) / max(1, len(rows))
    lexical = {}
    for (chunk, doc), tokens in zip(rows, token_lists):
        counts, score = Counter(tokens), 0.0
        for token in q:
            if not counts[token]:
                continue
            inverse = math.log(1 + (len(rows) - frequencies[token] + .5) / (frequencies[token] + .5))
            score += inverse * counts[token] * 2.2 / (counts[token] + 1.2 * (.25 + .75 * len(tokens) / max(average, 1)))
            if token in doc.title:
                score += 1.5
        if score > 0:
            lexical[chunk.id] = score
    lex_rank = sorted(lexical, key=lexical.get, reverse=True)
    semantic = {}
    if _model is not None:
        with _model_lock:
            vector = next(iter(_model.query_embed(query)))
        import numpy as np
        for chunk, _ in rows:
            if chunk.embedding is not None:
                other = np.asarray(chunk.embedding)
                similarity = float(np.dot(vector, other) / (np.linalg.norm(vector) * np.linalg.norm(other) + 1e-9))
                if similarity >= .60:
                    semantic[chunk.id] = similarity
    sem_rank = sorted(semantic, key=semantic.get, reverse=True)
    candidates = set(lex_rank[:20]) | set(sem_rank[:12])
    if not candidates:
        return []
    scores = {key: (1 / (60 + lex_rank.index(key)) if key in lex_rank else 0) +
                   (1 / (60 + sem_rank.index(key)) if key in sem_rank else 0) for key in candidates}
    by_id = {c.id: (c, d) for c, d in rows}
    result = []
    for key in sorted(scores, key=scores.get, reverse=True)[:limit]:
        chunk, doc = by_id[key]
        result.append({'chunk_id': chunk.id, 'document_id': doc.id, 'title': doc.title, 'category': doc.category,
                       'text': chunk.text, 'version': doc.version, 'is_demo': doc.is_demo,
                       'source_url': doc.source_url, 'score': round(scores[key], 5)})
    return result

def parse_file(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, '文件不能超过 5 MB')
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
                raise HTTPException(422, '未提取到足够文字，可能是扫描 PDF；首版尚未启用 OCR，请上传可复制文字的 PDF 或文本')
        elif suffix == '.docx':
            from docx import Document as Word
            archive = zipfile.ZipFile(io.BytesIO(content))
            if len(archive.infolist()) > 1000 or sum(i.file_size for i in archive.infolist()) > 20 * 1024 * 1024:
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
