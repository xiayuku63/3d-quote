"""Common response schemas — shared across all API endpoints."""

from typing import Any, Optional, TypeVar, Generic
from pydantic import BaseModel, Field

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    """Unified API response wrapper."""
    code: int = Field(default=0, description="业务状态码，0=成功，4xxxx=客户端错误，5xxxx=服务端错误")
    message: str = Field(default="ok", description="提示信息")
    data: Optional[T] = Field(default=None, description="响应数据")


class ErrorResponse(BaseModel):
    """Standard error response."""
    code: int = Field(description="错误码")
    message: str = Field(description="错误信息")
    data: None = Field(default=None)


class PaginatedData(BaseModel, Generic[T]):
    """Paginated list with total count."""
    items: list[T]
    total: int
    limit: int
    offset: int
