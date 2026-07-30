import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)

class RequestTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        response.headers["X-Request-Duration-Ms"] = str(int(duration_ms))
        if duration_ms > 1000:
            logger.warning("Slow request: %s %s took %.0fms", request.method, request.url.path, duration_ms)
        return response

class TenantHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        tenant_id = request.headers.get("X-Tenant-Id")
        if tenant_id:
            request.state.tenant_id = tenant_id
        return await call_next(request)
