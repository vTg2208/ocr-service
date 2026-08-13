"""Request identity, access logging, and bounded in-process rate limiting."""

from collections import defaultdict, deque
import hashlib
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("app.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, requests: int, window_seconds: int):
        super().__init__(app)
        self.limit, self.window = requests, window_seconds
        self.hits = defaultdict(deque)

    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        protected = request.url.path.startswith(("/api/pattas", "/api/claims", "/api/parcels/resolve"))
        if protected:
            identity = request.headers.get("Authorization") or (request.client.host if request.client else "unknown")
            key = hashlib.sha256(identity.encode()).hexdigest()
            now = time.monotonic(); queue = self.hits[key]
            while queue and queue[0] <= now - self.window: queue.popleft()
            if len(queue) >= self.limit:
                return JSONResponse(
                    {"success": False, "message": "Rate limit exceeded."}, status_code=429,
                    headers={"X-Request-ID": request_id, "Retry-After": str(self.window)},
                )
            queue.append(now)
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_complete method=%s path=%s status=%s duration_ms=%.2f request_id=%s",
            request.method, request.url.path, response.status_code,
            (time.perf_counter() - started) * 1000, request_id,
        )
        return response
