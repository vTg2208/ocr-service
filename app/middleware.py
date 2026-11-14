"""Request identity, access logging, and bounded in-process rate limiting."""

from collections import defaultdict, deque
import hashlib
import logging
import time
import uuid
import re
import jwt

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("app.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, requests: int, window_seconds: int, redis_url: str = ""):
        super().__init__(app)
        self.limit, self.window = requests, window_seconds
        self.hits = defaultdict(deque)
        from app.services.rate_limit import RedisRateLimiter
        self.shared = RedisRateLimiter(redis_url) if redis_url else None

    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", request_id):
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        protected = request.url.path.startswith(
            ("/api/",)
        )
        if protected:
            peer = request.client.host if request.client else "unknown"
            identity = peer
            token = request.headers.get("Authorization", "").removeprefix("Bearer ") or request.cookies.get("parcel_registry_session")
            if token and len(token) <= 8192:
                from app.config import get_settings
                s = get_settings()
                try:
                    claims = jwt.decode(token, s.auth_secret, algorithms=["HS256"], issuer=s.auth_issuer, audience=s.auth_audience,
                                        options={"require": ["sub", "exp", "iat", "iss", "aud"]})
                    identity = "subject:" + str(claims["sub"])
                except jwt.PyJWTError:
                    pass
            if request.url.path.startswith("/api/auth"):
                identity = "auth:" + (request.client.host if request.client else "unknown")
            key = hashlib.sha256(identity.encode()).hexdigest()
            if self.shared:
                try:
                    allowed, retry = await self.shared.allow(key, limit=self.limit, window=self.window)
                except Exception:
                    return JSONResponse({"success": False, "message": "Request protection is unavailable; retry later."}, status_code=503,
                        headers={"X-Request-ID": request_id, "Retry-After": "5", "Cache-Control": "no-store"})
            else:
                now = time.monotonic(); queue = self.hits[key]
                while queue and queue[0] <= now - self.window: queue.popleft()
                allowed, retry = len(queue) < self.limit, self.window
                if allowed: queue.append(now)
                if len(self.hits) > 10000:
                    for old in list(self.hits):
                        if not self.hits[old] or self.hits[old][-1] <= now-self.window: self.hits.pop(old, None)
            if not allowed:
                return JSONResponse(
                    {"success": False, "message": "Rate limit exceeded."}, status_code=429,
                    headers={"X-Request-ID": request_id, "Retry-After": str(retry), "Cache-Control": "no-store"},
                )
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/api/"):
            if "no-store" not in response.headers.get("Cache-Control", ""):
                response.headers["Cache-Control"] = "no-store"
            response.headers["Vary"] = "Cookie, Authorization"
        logger.info(
            "request_complete method=%s path=%s status=%s duration_ms=%.2f request_id=%s",
            request.method, request.url.path, response.status_code,
            (time.perf_counter() - started) * 1000, request_id,
        )
        return response
