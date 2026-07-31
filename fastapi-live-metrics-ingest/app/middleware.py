import logging
import time

logger = logging.getLogger(__name__)

class RequestTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        start = time.monotonic()

        async def send_with_timing(message):
            if message["type"] == "http.response.start":
                duration_ms = int((time.monotonic() - start) * 1000)
                message.setdefault("headers", []).append(
                    (b"x-request-duration-ms", str(duration_ms).encode())
                )
                if duration_ms > 1000:
                    logger.warning(
                        "Slow request: %s %s took %dms",
                        scope["method"],
                        scope["path"],
                        duration_ms,
                    )
            await send(message)

        await self.app(scope, receive, send_with_timing)

class TenantHeaderMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        tenant_id = headers.get(b"x-tenant-id")
        if tenant_id:
            scope.setdefault("state", {})["tenant_id"] = tenant_id.decode()
        await self.app(scope, receive, send)
