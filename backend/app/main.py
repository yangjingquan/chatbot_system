from contextlib import asynccontextmanager
import threading
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from .db import Base, engine, SessionLocal
from .seed import seed
from .knowledge import warm_embeddings, retrieval_status
from .encryption import migrate_contacts
from . import admin, chat

@asynccontextmanager
async def lifespan(app):
    if engine.dialect.name=='postgresql':
        with engine.begin() as conn:
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        # Upgrade initial local records to encrypted contacts without losing data.
        if engine.dialect.name=='postgresql':
            conn.execute(text('ALTER TABLE leads ALTER COLUMN contact TYPE TEXT'))
        migrate_contacts(conn)
    with SessionLocal() as db:
        seed(db)
    thread=threading.Thread(target=warm_embeddings,daemon=True)
    thread.start()
    yield

app=FastAPI(title='知行客服 API',version='0.1.0',lifespan=lifespan,docs_url='/api/docs',openapi_url='/api/openapi.json',redoc_url=None)
app.include_router(admin.router,prefix='/api')
app.include_router(chat.router,prefix='/api')

@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Do not echo submitted passwords or contact details in validation errors.
    return JSONResponse(status_code=422,content={'detail':'输入内容不符合要求，请检查必填项、长度、格式及授权确认。'})

@app.middleware('http')
async def safety_headers(request: Request, call_next):
    length=request.headers.get('content-length')
    if length and (not length.isdigit() or int(length)>6*1024*1024):
        return JSONResponse(status_code=413,content={'detail':'请求内容超过大小限制'})
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['Cache-Control']='no-store'
    return response

@app.get('/api/health')
def health():
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
    return {'status':'ok','version':'0.1.0','retrieval':retrieval_status()}
