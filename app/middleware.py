"""Security middleware – rate limiting, request IDs, security headers, access logging."""

import uuid
import time
import logging

from fastapi import Request

from .config import (
    IS_PRODUCTION,
    AUTH_RATE_LIMIT_PER_MIN,
    QUOTE_RATE_LIMIT_PER_MIN,
    CAPTCHA_RATE_LIMIT_PER_MIN,
    VERIFY_SEND_RATE_LIMIT_PER_10MIN,
)
from .rate_limiter import PersistentRateLimiter
from .metrics import InMemoryMetrics
from .utils import get_client_ip
from .logging_config import log_request
from .errors import error_response

rate_limiter = PersistentRateLimiter()
metrics = InMemoryMetrics()
logger = logging.getLogger("pricer3d")


async def security_middleware(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex
    path = request.url.path
    method = request.method.upper()
    client_ip = get_client_ip(request)

    # Rate limiting by endpoint
    if path in {"/api/auth/login", "/api/auth/register"} and method == "POST":
        if not rate_limiter.is_allowed(f"auth:{client_ip}", AUTH_RATE_LIMIT_PER_MIN):
            resp = error_response(42900, "请求过于频繁，请稍后再试", 429)
            resp.headers["X-Request-ID"] = request.state.request_id
            log_request(logger, method, path, 429, 0, client_ip, request.state.request_id)
            return resp
    if path == "/api/auth/register/check" and method == "POST":
        if not rate_limiter.is_allowed(f"auth_check:{client_ip}", AUTH_RATE_LIMIT_PER_MIN):
            resp = error_response(42900, "请求过于频繁，请稍后再试", 429)
            resp.headers["X-Request-ID"] = request.state.request_id
            log_request(logger, method, path, 429, 0, client_ip, request.state.request_id)
            return resp
    if path == "/api/auth/verify/send" and method == "POST":
        if not rate_limiter.is_allowed(f"verify_send_ip:{client_ip}", VERIFY_SEND_RATE_LIMIT_PER_10MIN, window_seconds=600):
            resp = error_response(42900, "请求过于频繁，请稍后再试", 429)
            resp.headers["X-Request-ID"] = request.state.request_id
            log_request(logger, method, path, 429, 0, client_ip, request.state.request_id)
            return resp
    if path == "/api/auth/captcha" and method == "GET":
        if not rate_limiter.is_allowed(f"captcha:{client_ip}", CAPTCHA_RATE_LIMIT_PER_MIN):
            resp = error_response(42900, "请求过于频繁，请稍后再试", 429)
            resp.headers["X-Request-ID"] = request.state.request_id
            log_request(logger, method, path, 429, 0, client_ip, request.state.request_id)
            return resp
    if path == "/api/quote" and method == "POST":
        if not rate_limiter.is_allowed(f"quote:{client_ip}", QUOTE_RATE_LIMIT_PER_MIN):
            resp = error_response(42900, "报价请求过于频繁，请稍后再试", 429)
            resp.headers["X-Request-ID"] = request.state.request_id
            log_request(logger, method, path, 429, 0, client_ip, request.state.request_id)
            return resp

    started = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - started) * 1000.0
    status_code = int(getattr(response, "status_code", 0) or 0)

    try:
        metrics.record(path=path, status_code=status_code, duration_ms=duration_ms)
    except Exception:
        pass

    # Structured access log
    log_request(logger, method, path, status_code, duration_ms, client_ip, request.state.request_id)

    # Security headers
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["X-Download-Options"] = "noopen"
    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response
