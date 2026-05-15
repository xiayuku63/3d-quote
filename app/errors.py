"""Unified error handling — consistent {code, message, data} responses."""

import logging
from typing import Any, Optional

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("pricer3d")


# ── Custom exceptions ──

class AppError(HTTPException):
    """Application-level error with code and message."""
    def __init__(self, code: int, message: str, status_code: int = 400):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message


class NotFoundError(AppError):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(code=40400, message=message, status_code=404)


class ForbiddenError(AppError):
    def __init__(self, message: str = "无权限"):
        super().__init__(code=40300, message=message, status_code=403)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "未登录或登录已过期"):
        super().__init__(code=40100, message=message, status_code=401)


class ValidationError(AppError):
    def __init__(self, message: str = "请求参数不合法"):
        super().__init__(code=42200, message=message, status_code=422)


class ConflictError(AppError):
    def __init__(self, message: str = "资源冲突"):
        super().__init__(code=40900, message=message, status_code=409)


class RateLimitError(AppError):
    def __init__(self, message: str = "请求过于频繁，请稍后再试"):
        super().__init__(code=42900, message=message, status_code=429)


class InternalError(AppError):
    def __init__(self, message: str = "服务器内部错误"):
        super().__init__(code=50000, message=message, status_code=500)


class ServiceUnavailableError(AppError):
    def __init__(self, message: str = "服务暂不可用"):
        super().__init__(code=50300, message=message, status_code=503)


# ── Response helpers ──

def success_response(data: Any = None, message: str = "ok") -> JSONResponse:
    """Wrap a successful response in {code: 0, message, data}."""
    return JSONResponse(
        status_code=200,
        content={"code": 0, "message": message, "data": data},
    )


def error_response(code: int, message: str, status_code: int = 400) -> JSONResponse:
    """Return an error in {code, message, data: null} format."""
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "data": None},
    )


# ── Exception handlers ──

def register_exception_handlers(app):
    """Register global exception handlers on the FastAPI app."""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return error_response(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Convert standard HTTPException to unified format."""
        code_map = {
            400: 40000,
            401: 40100,
            403: 40300,
            404: 40400,
            409: 40900,
            422: 42200,
            429: 42900,
            500: 50000,
            503: 50300,
        }
        error_code = code_map.get(exc.status_code, exc.status_code * 100)
        return error_response(
            code=error_code,
            message=str(exc.detail),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Convert Pydantic validation errors."""
        messages: list[str] = []
        for err in exc.errors():
            loc = " → ".join(str(p) for p in err.get("loc", []))
            msg = err.get("msg", "校验失败")
            messages.append(f"{loc}: {msg}" if loc else msg)
        detail = "；".join(messages[:5]) if messages else "请求参数不合法"
        if len(messages) > 5:
            detail += f"（共 {len(messages)} 项）"
        return error_response(
            code=42200,
            message=detail,
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all for unhandled errors."""
        logger.exception(
            "Unhandled error on %s %s",
            request.method,
            request.url.path,
        )
        # Don't leak internal details in production
        from .config import IS_PRODUCTION
        message = "服务器内部错误" if IS_PRODUCTION else f"服务器内部错误: {str(exc)}"
        return error_response(
            code=50000,
            message=message,
            status_code=500,
        )

    return app
