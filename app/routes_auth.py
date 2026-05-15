"""Auth routes – captcha, verification, register, login, me."""

import time
import secrets
from typing import Optional, List

from fastapi import Request, Depends, HTTPException
from fastapi.responses import JSONResponse, Response

from .config import (
    CAPTCHA_LENGTH,
    CAPTCHA_TTL_SECONDS,
    VERIFY_CODE_TTL_SECONDS,
    VERIFY_SEND_COOLDOWN_SECONDS,
    TERMS_VERSION,
    PRIVACY_VERSION,
    IS_PRODUCTION,
)
from .models import RegisterRequest, LoginRequest, VerifySendRequest, VerifyConfirmRequest, RegisterCheckRequest
from pydantic import BaseModel, Field
from .database import get_db_conn
from .utils import (
    normalize_email,
    normalize_phone,
    validate_username_or_raise,
    validate_password_or_raise,
    get_client_ip,
)
from .captcha import captcha_store, generate_captcha_text, captcha_image_bytes, verify_captcha_or_raise
from .auth import (
    authenticate_user,
    create_access_token,
    create_user,
    create_verification_code,
    delete_verification_code_row,
    consume_verification_code,
    is_smtp_configured,
    send_email_verification_code,
    get_user_by_username,
    get_user_by_email,
    get_user_by_phone,
    is_login_locked,
    clear_login_failures,
    record_login_failure,
    _login_failure_key_hash,
)
from .deps import (
    get_current_user,
    require_legal_acceptance_or_raise,
    record_legal_acceptance,
    is_admin_user,
    get_membership_effective,
    mask_email,
    mask_phone,
)
from .audit import write_audit_event
from .middleware import rate_limiter


def normalize_verify_target(channel: str, target: str) -> str:
    ch = (channel or "").strip().lower()
    if ch == "email":
        return normalize_email(target)
    if ch == "phone":
        return normalize_phone(target)
    raise HTTPException(status_code=400, detail="不支持的验证类型")


# ---------- Captcha ----------

async def get_captcha(request: Request):
    text = generate_captcha_text(CAPTCHA_LENGTH)
    captcha_id = secrets.token_urlsafe(24)
    expires_at = time.time() + CAPTCHA_TTL_SECONDS
    ct, img = captcha_image_bytes(text)
    captcha_store.put(captcha_id=captcha_id, answer=text, expires_at=expires_at, image_bytes=img, image_content_type=ct)
    return {"captcha_id": captcha_id, "image_url": f"/api/auth/captcha/image/{captcha_id}", "expires_in": CAPTCHA_TTL_SECONDS}


async def get_captcha_image(captcha_id: str):
    raw, ct = captcha_store.get_image(str(captcha_id).strip())
    if not raw or not ct:
        raise HTTPException(status_code=404, detail="验证码已过期")
    return Response(content=raw, media_type=ct, headers={"Cache-Control": "no-store"})


# ---------- Verification ----------

async def send_verify_code(payload: VerifySendRequest, request: Request):
    channel = (payload.channel or "").strip().lower()
    target = normalize_verify_target(channel, payload.target)
    client_ip = get_client_ip(request)
    if not rate_limiter.is_allowed(f"verify_send_ip_cooldown:{client_ip}", 1, window_seconds=VERIFY_SEND_COOLDOWN_SECONDS):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    if not rate_limiter.is_allowed(f"verify_send_target_cooldown:{channel}:{target}", 1, window_seconds=VERIFY_SEND_COOLDOWN_SECONDS):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    code, row_id = create_verification_code(channel=channel, target=target)
    if channel == "email":
        if IS_PRODUCTION and not is_smtp_configured():
            delete_verification_code_row(row_id)
            raise HTTPException(status_code=500, detail="邮件服务未配置，暂无法发送邮箱验证码")
        try:
            if is_smtp_configured():
                send_email_verification_code(target, code)
        except Exception:
            delete_verification_code_row(row_id)
            raise HTTPException(status_code=500, detail="邮箱验证码发送失败，请稍后重试")
    resp = {"status": "sent", "channel": channel, "target": target, "expires_in": VERIFY_CODE_TTL_SECONDS}
    if not IS_PRODUCTION:
        resp["dev_code"] = code
    masked = mask_email(target) if channel == "email" else mask_phone(target) if channel == "phone" else None
    write_audit_event(
        action="auth.verify.send",
        request=request,
        user=None,
        detail={"channel": channel, "target_masked": masked},
    )
    return resp


async def confirm_verify_code(payload: VerifyConfirmRequest, request: Request):
    channel = (payload.channel or "").strip().lower()
    target = normalize_verify_target(channel, payload.target)
    ok = consume_verification_code(channel=channel, target=target, code=payload.code)
    if not ok:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    masked = mask_email(target) if channel == "email" else mask_phone(target) if channel == "phone" else None
    write_audit_event(
        action="auth.verify.confirm",
        request=request,
        user=None,
        detail={"channel": channel, "target_masked": masked},
    )
    return {"status": "verified", "channel": channel, "target": target}


# ---------- Register Check ----------

async def check_register_exists(payload: RegisterCheckRequest):
    field = (payload.field or "").strip().lower()
    raw_value = (payload.value or "").strip()
    if field == "username":
        try:
            value = validate_username_or_raise(raw_value)
        except HTTPException as e:
            return {"field": field, "valid": False, "exists": False, "message": e.detail}
        exists = get_user_by_username(value) is not None
        return {"field": field, "valid": True, "exists": exists}
    if field == "email":
        try:
            value = normalize_email(raw_value)
        except HTTPException as e:
            return {"field": field, "valid": False, "exists": False, "message": e.detail}
        exists = get_user_by_email(value) is not None
        return {"field": field, "valid": True, "exists": exists}
    if field == "phone":
        try:
            value = normalize_phone(raw_value)
        except HTTPException as e:
            return {"field": field, "valid": False, "exists": False, "message": e.detail}
        exists = get_user_by_phone(value) is not None
        return {"field": field, "valid": True, "exists": exists}
    raise HTTPException(status_code=400, detail="不支持的检查字段")


# ---------- Register ----------

async def register(payload: RegisterRequest, request: Request):
    verify_captcha_or_raise(payload.captcha_id, payload.captcha_code)
    require_legal_acceptance_or_raise(payload.accept_terms, payload.accept_privacy)
    username = validate_username_or_raise(payload.username)
    password = validate_password_or_raise(payload.password)
    channel = (payload.register_channel or "").strip().lower()
    email = None
    phone = None
    email_verified = 0
    phone_verified = 0
    if get_user_by_username(username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    if channel == "email":
        if not payload.email or not payload.email_code:
            raise HTTPException(status_code=400, detail="邮箱注册需要填写邮箱与验证码")
        email = normalize_email(payload.email)
        if get_user_by_email(email):
            raise HTTPException(status_code=409, detail="邮箱已存在")
        if not consume_verification_code("email", email, payload.email_code):
            raise HTTPException(status_code=400, detail="邮箱验证码错误或已过期")
        email_verified = 1
    elif channel == "phone":
        if not payload.phone or not payload.phone_code:
            raise HTTPException(status_code=400, detail="手机注册需要填写手机号与验证码")
        phone = normalize_phone(payload.phone)
        if get_user_by_phone(phone):
            raise HTTPException(status_code=409, detail="手机号已存在")
        if not consume_verification_code("phone", phone, payload.phone_code):
            raise HTTPException(status_code=400, detail="手机验证码错误或已过期")
        phone_verified = 1
    else:
        raise HTTPException(status_code=400, detail="不支持的注册方式")

    user = create_user(username, password, email=email, phone=phone, email_verified=email_verified, phone_verified=phone_verified)
    write_audit_event(
        action="auth.register",
        request=request,
        user=user,
        detail={
            "register_channel": channel,
            "email_masked": mask_email(email) if email else None,
            "phone_masked": mask_phone(phone) if phone else None,
            "terms_version": TERMS_VERSION,
            "privacy_version": PRIVACY_VERSION,
        },
    )
    access_token = create_access_token(user_id=user["id"], username=user["username"])
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "created_at": user["created_at"],
        },
    }


# ---------- Login ----------

async def login(payload: LoginRequest, request: Request):
    verify_captcha_or_raise(payload.captcha_id, payload.captcha_code)
    require_legal_acceptance_or_raise(payload.accept_terms, payload.accept_privacy)
    password = validate_password_or_raise(payload.password)
    locked, remaining = is_login_locked(payload.identifier)
    if locked:
        write_audit_event(
            action="auth.login_locked",
            request=request,
            user=None,
            detail={"key_hash_prefix": _login_failure_key_hash(payload.identifier)[:12], "retry_after_s": remaining},
        )
        raise HTTPException(
            status_code=429,
            detail="登录失败次数过多，请稍后再试",
            headers={"Retry-After": str(remaining)},
        )
    try:
        user = authenticate_user(payload.identifier, password)
    except HTTPException:
        locked2, remaining2 = record_login_failure(payload.identifier)
        write_audit_event(
            action="auth.login_failed",
            request=request,
            user=None,
            detail={"identifier": (payload.identifier or "").strip()[:120]},
        )
        if locked2:
            write_audit_event(
                action="auth.login_locked",
                request=request,
                user=None,
                detail={"key_hash_prefix": _login_failure_key_hash(payload.identifier)[:12], "retry_after_s": remaining2},
            )
            raise HTTPException(
                status_code=429,
                detail="登录失败次数过多，请稍后再试",
                headers={"Retry-After": str(remaining2)},
            )
        raise

    clear_login_failures(payload.identifier)
    record_legal_acceptance(int(user["id"]))
    write_audit_event(
        action="auth.login",
        request=request,
        user=user,
        detail={
            "identifier": (payload.identifier or "").strip()[:120],
            "terms_version": TERMS_VERSION,
            "privacy_version": PRIVACY_VERSION,
        },
    )
    access_token = create_access_token(user_id=user["id"], username=user["username"])
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "created_at": user["created_at"],
        },
    }


# ---------- Me ----------

async def auth_me(current_user=Depends(get_current_user)):
    import logging
    logger = logging.getLogger(__name__)
    try:
        level, expires_ts = get_membership_effective(current_user)
        return {
            "id": current_user["id"],
            "username": current_user["username"],
            "created_at": current_user["created_at"],
            "email": current_user["email"],
            "phone": current_user["phone"],
            "email_verified": bool(current_user["email_verified"] or 0),
            "phone_verified": bool(current_user["phone_verified"] or 0),
            "is_admin": is_admin_user(current_user),
            "membership_level": level,
            "membership_expires_at": expires_ts,
            "is_member": level == "member",
        }
    except Exception as e:
        logger.error(f"获取用户信息失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"INTERNAL_ERROR: 获取用户信息失败 ({str(e)})")


# ── Password Reset ──

class ResetRequestModel(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    captcha_id: str = Field(..., min_length=8, max_length=80)
    captcha_code: str = Field(..., min_length=4, max_length=10)


class ResetConfirmModel(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    code: str = Field(..., min_length=4, max_length=10)
    new_password: str = Field(..., min_length=6, max_length=100)


async def password_reset_request(payload: ResetRequestModel, request: Request):
    """Request password reset email."""
    verify_captcha_or_raise(payload.captcha_id, payload.captcha_code)
    email = normalize_email(payload.email)
    user = get_user_by_email(email)
    # Always return success to avoid email enumeration
    if not user:
        masked = mask_email(email)
        write_audit_event(action="auth.reset.request_no_user", request=request, detail={"email_masked": masked})
        return {"status": "sent", "message": "若该邮箱已注册，重置邮件已发送"}

    if not is_smtp_configured():
        if not IS_PRODUCTION:
            # Dev mode: return code directly
            code, row_id = create_verification_code(channel="email", target=email)
            write_audit_event(action="auth.reset.request", request=request, user=user)
            return {"status": "sent", "dev_code": code, "expires_in": VERIFY_CODE_TTL_SECONDS}
        raise HTTPException(status_code=500, detail="邮件服务未配置")

    code, row_id = create_verification_code(channel="email", target=email)
    try:
        send_email_verification_code(email, code)
    except Exception:
        delete_verification_code_row(row_id)
        raise HTTPException(status_code=500, detail="邮件发送失败，请稍后重试")

    write_audit_event(action="auth.reset.request", request=request, user=user)
    resp = {"status": "sent", "expires_in": VERIFY_CODE_TTL_SECONDS}
    if not IS_PRODUCTION:
        resp["dev_code"] = code
    return resp


async def password_reset_confirm(payload: ResetConfirmModel, request: Request):
    """Confirm password reset with verification code."""
    new_password = validate_password_or_raise(payload.new_password)
    email = normalize_email(payload.email)

    if not consume_verification_code(channel="email", target=email, code=payload.code):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")

    user = get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    from .auth import get_password_hash
    new_hash = get_password_hash(new_password)
    with get_db_conn() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, int(user["id"])))
        conn.commit()

    # Clear login failures for this user
    clear_login_failures(str(user["username"]) or "")
    if email:
        clear_login_failures(email)

    write_audit_event(action="auth.reset.confirm", request=request, user=user)
    return {"status": "ok", "message": "密码已重置，请使用新密码登录"}
