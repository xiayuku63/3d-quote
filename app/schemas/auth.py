"""Auth schemas — login, register, token responses."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator
from ..config import USERNAME_PATTERN, EMAIL_PATTERN, PHONE_PATTERN, PASSWORD_MIN_LENGTH


class LoginRequest(BaseModel):
    account: str = Field(..., min_length=1, max_length=255, description="用户名/邮箱/手机号")
    password: str = Field(..., min_length=1, max_length=100, description="密码")
    captcha_id: str = Field(..., min_length=1, description="验证码ID")
    captcha_code: str = Field(..., min_length=1, max_length=8, description="验证码")

    @field_validator("account")
    @classmethod
    def account_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("账号不能为空")
        return v.strip()


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, max_length=100, description="密码")
    channel: str = Field(default="email", description="注册渠道: email / phone")
    email: Optional[str] = Field(default=None, description="邮箱")
    phone: Optional[str] = Field(default=None, description="手机号")
    email_code: Optional[str] = Field(default=None, description="邮箱验证码")
    phone_code: Optional[str] = Field(default=None, description="手机验证码")
    accept_terms: bool = Field(default=False)
    accept_privacy: bool = Field(default=False)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not USERNAME_PATTERN.match(v.strip()):
            raise ValueError("用户名只能包含字母、数字、下划线、点和连字符，长度3-50")
        return v.strip()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class CaptchaResponse(BaseModel):
    captcha_id: str
    image_base64: Optional[str] = None


class VerifyCodeRequest(BaseModel):
    channel: str = Field(..., description="email / phone")
    target: str = Field(..., min_length=3, max_length=255, description="邮箱或手机号")


class VerifyCodeConfirmRequest(BaseModel):
    channel: str = Field(..., description="email / phone")
    target: str = Field(..., min_length=3, max_length=255)
    code: str = Field(..., min_length=4, max_length=10)


class PasswordResetRequest(BaseModel):
    channel: str = Field(..., description="email")
    target: str = Field(..., min_length=3, max_length=255)


class PasswordResetConfirmRequest(BaseModel):
    channel: str = Field(..., description="email")
    target: str = Field(..., min_length=3, max_length=255)
    code: str = Field(..., min_length=4, max_length=10)
    new_password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, max_length=100)
