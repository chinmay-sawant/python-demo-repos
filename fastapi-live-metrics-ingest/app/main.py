from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from app.config import Settings
from app.database import create_engine, create_session_factory
from app.middleware import RequestTimingMiddleware, TenantHeaderMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.engine = engine
    yield
    await engine.dispose()

app = FastAPI(lifespan=lifespan)

app.add_middleware(RequestTimingMiddleware)
app.add_middleware(TenantHeaderMiddleware)

from app.routers import ingest as ingest_router
from app.routers import dashboard as dashboard_router
app.include_router(ingest_router.router)
app.include_router(dashboard_router.router)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/v1/debug/tenant-context")
async def debug_tenant_context(request: Request):
    return {"tenant_id": getattr(request.state, "tenant_id", None)}
