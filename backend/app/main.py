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
from . import admin, chat, staff, documents, catalog, students, reception, quality, jobs, privacy_admin, ops_admin
from .migrations import migrate_schema
from .config import settings

@asynccontextmanager
async def lifespan(app):
    migrate_schema(engine)
    with SessionLocal() as db:
        seed(db)
        from .document_service import ensure_history
        ensure_history(db)
        catalog.seed_catalog(db)
        from .customer_readiness import prepare_customer_data
        prepare_customer_data(db)
        chat.backfill_analysis(db)
        students.backfill_crm(db)
        if settings().embedding_enabled:jobs.backfill_index_jobs(db)
        db.commit()
    thread=threading.Thread(target=warm_embeddings,daemon=True)
    thread.start()
    # Publish aggregates from this API process so the independent monitor can
    # evaluate request latency without anyone opening the operations page.
    stop_metrics=threading.Event()
    def publish_metrics():
        from .monitoring import publish_api_latency
        interval=max(5,min(settings().api_metric_flush_seconds,300))
        while not stop_metrics.wait(interval):
            try:
                with SessionLocal() as db:
                    publish_api_latency(db)
                    db.commit()
            except Exception:
                pass  # A later heartbeat reports recovery; no sensitive data is logged.
    metrics_thread=threading.Thread(target=publish_metrics,daemon=True)
    metrics_thread.start()
    yield
    stop_metrics.set()
    metrics_thread.join(timeout=1)

app=FastAPI(title='知行客服 API',version='0.4.0',lifespan=lifespan,docs_url='/api/docs',openapi_url='/api/openapi.json',redoc_url=None)
hosts=[h.strip() for h in settings().allowed_hosts.split(',') if h.strip()]
if hosts:
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
app.include_router(admin.router,prefix='/api')
app.include_router(chat.router,prefix='/api')
app.include_router(staff.router,prefix='/api')
app.include_router(documents.router,prefix='/api')
app.include_router(catalog.router,prefix='/api')
app.include_router(students.router,prefix='/api')
app.include_router(reception.router,prefix='/api')
app.include_router(quality.router,prefix='/api')
app.include_router(jobs.router,prefix='/api')
app.include_router(privacy_admin.router,prefix='/api')
app.include_router(ops_admin.router,prefix='/api')

@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Do not echo submitted passwords or contact details in validation errors.
    return JSONResponse(status_code=422,content={'detail':'输入内容不符合要求，请检查必填项、长度、格式及授权确认。'})

@app.middleware('http')
async def safety_headers(request: Request, call_next):
    length=request.headers.get('content-length')
    # Allow knowledge uploads up to ~50 MB (+ multipart overhead); keep other writes smaller.
    uploadish = request.url.path.startswith('/api/admin/document-jobs') or request.url.path.startswith('/api/admin/documents')
    limit = 55*1024*1024 if uploadish else 6*1024*1024
    if length and (not length.isdigit() or int(length)>limit):
        return JSONResponse(status_code=413,content={'detail':'请求内容超过大小限制'})
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['Cache-Control']='no-store'
    return response

@app.middleware('http')
async def record_latency(request: Request, call_next):
    import time as _time
    started = _time.perf_counter()
    response = await call_next(request)
    try:
        from .monitoring import record_request_latency
        # Skip static noise; still count API work including /api/health.
        if request.url.path.startswith('/api'):
            record_request_latency((_time.perf_counter() - started) * 1000)
    except Exception:
        pass
    return response

@app.get('/api/health')
def health():
    """Public liveness/readiness. Avoids leaking secrets; richer detail is under /admin/ops/status."""
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
    except Exception:
        db_ok = False
    redis_status = 'skipped'
    try:
        from .monitoring import check_redis
        redis_status = check_redis().get('status', 'skipped')
    except Exception:
        redis_status = 'fail'
    worker_status = 'unknown'
    try:
        from .db import SessionLocal
        from .monitoring import check_worker
        with SessionLocal() as db:
            worker_status = check_worker(db).get('status', 'unknown')
    except Exception:
        worker_status = 'fail'
    schema_info = {'applied': 0, 'expected': 0, 'current': False}
    try:
        from .migrations import applied_schema_version, expected_schema_version
        applied = applied_schema_version(engine) if db_ok else 0
        expected = expected_schema_version()
        schema_info = {
            'applied': applied,
            'expected': expected,
            'current': bool(db_ok and applied >= expected),
        }
        if db_ok and applied < expected:
            overall_schema_lag = True
        else:
            overall_schema_lag = False
    except Exception:
        overall_schema_lag = False
    overall = 'ok' if db_ok and redis_status != 'fail' else 'fail'
    if overall == 'ok' and (worker_status == 'fail' or overall_schema_lag):
        overall = 'degraded'
    return {
        'status': overall,
        'version': '0.4.0',
        'schema_version': schema_info,
        'retrieval': retrieval_status(),
        'checks': {
            'database': 'ok' if db_ok else 'fail',
            'redis': redis_status,
            'worker': worker_status,
            'schema': 'ok' if schema_info.get('current') else ('fail' if db_ok else 'unknown'),
        },
    }
