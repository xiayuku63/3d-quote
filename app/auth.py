"""Authentication – JWT, password hashing, user CRUD, verification codes, SMTP email."""

import time
import re
import ssl
import smtplib
import hashlib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import jwt
from fastapi import HTTPException

from .config import (
    JWT_SECRET_KEY,
    JWT_ALGORITHM,
    JWT_EXPIRE_HOURS,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    SMTP_FROM,
    SMTP_USE_SSL,
    SMTP_USE_TLS,
    VERIFY_CODE_TTL_SECONDS,
    VERIFY_CODE_MAX_ATTEMPTS,
    VERIFY_SEND_COOLDOWN_SECONDS,
)
from .database import get_db_conn, get_app_defaults
from .utils import (
    normalize_email,
    normalize_phone,
    validate_username_or_raise,
    hash_verify_code,
    generate_numeric_code,
)
from .config import DEFAULT_MATERIALS, DEFAULT_COLORS, DEFAULT_PRICING_CONFIG


# ---------- password ----------

def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def verify_password(plain_password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))


# ---------- JWT ----------

def create_access_token(user_id: int, username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "username": username, "exp": expire}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


# ---------- user queries ----------

def get_user_by_username(username: str):
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at, email, phone, email_verified, phone_verified, membership_level, membership_expires_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    return row


def get_user_by_email(email: str):
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at, email, phone, email_verified, phone_verified, membership_level, membership_expires_at FROM users WHERE email = ?",
            (email,),
        ).fetchone()
    return row


def get_user_by_phone(phone: str):
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at, email, phone, email_verified, phone_verified, membership_level, membership_expires_at FROM users WHERE phone = ?",
            (phone,),
        ).fetchone()
    return row


def get_user_by_identifier(identifier: str):
    raw = (identifier or "").strip()
    if not raw:
        return None
    if "@" in raw:
        return get_user_by_email(normalize_email(raw))
    if re.fullmatch(r"[\d\+\-\s\(\)]+", raw or ""):
        return get_user_by_phone(normalize_phone(raw))
    return get_user_by_username(validate_username_or_raise(raw))


def get_user_by_id(user_id: int):
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT id, username, created_at, email, phone, email_verified, phone_verified, membership_level, membership_expires_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return row


def authenticate_user(identifier: str, password: str):
    user = get_user_by_identifier(identifier)
    if not user:
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    return user


# ---------- verification codes ----------

def create_verification_code(channel: str, target: str) -> tuple[str, int]:
    code = generate_numeric_code(6)
    now = time.time()
    expires_at = now + VERIFY_CODE_TTL_SECONDS
    created_at = datetime.now(timezone.utc).isoformat()
    with get_db_conn() as conn:
        cur = conn.execute(
            "INSERT INTO verification_codes (channel, target, code_hash, expires_at, created_at, used_at, attempts) VALUES (?, ?, ?, ?, ?, NULL, 0)",
            (channel, target, hash_verify_code(code), str(expires_at), created_at),
        )
        conn.commit()
        row_id = int(cur.lastrowid or 0)
    return code, row_id


def delete_verification_code_row(row_id: int) -> None:
    rid = int(row_id or 0)
    if rid <= 0:
        return
    with get_db_conn() as conn:
        conn.execute("DELETE FROM verification_codes WHERE id = ?", (rid,))
        conn.commit()


def consume_verification_code(channel: str, target: str, code: str) -> bool:
    now = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    supplied = hash_verify_code(code)
    with get_db_conn() as conn:
        row = conn.execute(
            """
            SELECT id, code_hash, expires_at, attempts
            FROM verification_codes
            WHERE channel = ? AND target = ? AND used_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (channel, target),
        ).fetchone()
        if not row:
            return False
        try:
            expires_at = float(row["expires_at"])
        except Exception:
            return False
        if now > expires_at:
            conn.execute("UPDATE verification_codes SET used_at = ? WHERE id = ?", (now_iso, row["id"]))
            conn.commit()
            return False
        attempts = int(row["attempts"] or 0) + 1
        if attempts > VERIFY_CODE_MAX_ATTEMPTS:
            conn.execute("UPDATE verification_codes SET attempts = ?, used_at = ? WHERE id = ?", (attempts, now_iso, row["id"]))
            conn.commit()
            return False
        if supplied != str(row["code_hash"] or ""):
            conn.execute("UPDATE verification_codes SET attempts = ? WHERE id = ?", (attempts, row["id"]))
            conn.commit()
            return False
        conn.execute("UPDATE verification_codes SET attempts = ?, used_at = ? WHERE id = ?", (attempts, now_iso, row["id"]))
        conn.commit()
        return True


# ---------- SMTP email ----------

def is_smtp_configured() -> bool:
    if not SMTP_HOST:
        return False
    if SMTP_USE_SSL and SMTP_PORT <= 0:
        return False
    if SMTP_USE_TLS and SMTP_PORT <= 0:
        return False
    return True


def send_email_verification_code(to_email: str, code: str) -> None:
    import os
    if not is_smtp_configured():
        raise RuntimeError("SMTP 未配置")
    from_addr = (SMTP_FROM or SMTP_USER or "").strip()
    if not from_addr:
        raise RuntimeError("SMTP_FROM 未配置")
    msg = EmailMessage()
    msg["Subject"] = "邮箱验证码 - 3D打印自动报价系统"
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.set_content(
        "\n".join(
            [
                "您正在注册 3D打印自动报价系统。",
                f"本次邮箱验证码：{code}",
                f"有效期：{int(VERIFY_CODE_TTL_SECONDS)} 秒",
                "",
                "如非本人操作，请忽略本邮件。",
            ]
        )
    )
    timeout_s = float(os.getenv("SMTP_TIMEOUT_SECONDS", "10") or "10")
    if SMTP_USE_SSL:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=timeout_s, context=context) as server:
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        return
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=timeout_s) as server:
        server.ehlo()
        if SMTP_USE_TLS:
            context = ssl.create_default_context()
            server.starttls(context=context)
            server.ehlo()
        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    return


# ---------- user creation ----------

def create_user(username: str, password: str, email: Optional[str], phone: Optional[str], email_verified: int, phone_verified: int):
    import json
    # Hard guard: block duplicates at application layer
    if get_user_by_username(username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    if email and get_user_by_email(email):
        raise HTTPException(status_code=409, detail="邮箱已存在")
    if phone and get_user_by_phone(phone):
        raise HTTPException(status_code=409, detail="手机号已存在")

    password_hash = get_password_hash(password)
    created_at = datetime.now(timezone.utc).isoformat()
    defaults = get_app_defaults()
    materials_json = json.dumps(defaults.get("materials") or DEFAULT_MATERIALS)
    colors_json = json.dumps(defaults.get("colors") or DEFAULT_COLORS)
    pricing_json = json.dumps(defaults.get("pricing_config") or DEFAULT_PRICING_CONFIG)
    membership_level = "free"
    membership_expires_at = None
    accepted_at = datetime.now(timezone.utc).isoformat()
    try:
        with get_db_conn() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at, materials, colors, pricing_config, email, phone, email_verified, phone_verified, membership_level, membership_expires_at, terms_accepted_at, privacy_accepted_at, terms_version, privacy_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    username,
                    password_hash,
                    created_at,
                    materials_json,
                    colors_json,
                    pricing_json,
                    email,
                    phone,
                    email_verified,
                    phone_verified,
                    membership_level,
                    membership_expires_at,
                    accepted_at,
                    accepted_at,
                    "v1",
                    "v1",
                ),
            )
            conn.commit()
            user = conn.execute("SELECT id, username, created_at FROM users WHERE id = last_insert_rowid()").fetchone()
    except Exception:
        raise HTTPException(status_code=409, detail="用户名或邮箱/手机号已存在")
    if not user:
        raise HTTPException(status_code=500, detail="REGISTRATION_FAILED")
    return dict(user)


# ---------- login failure tracking ----------

def _login_failure_key_hash(identifier: str) -> str:
    raw = (identifier or "").strip().lower()
    return hashlib.sha256((raw + "|" + JWT_SECRET_KEY).encode("utf-8")).hexdigest()


def is_login_locked(identifier: str) -> tuple[bool, int]:
    key_hash = _login_failure_key_hash(identifier)
    now = time.time()
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT locked_until FROM login_failures WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    if not row:
        return False, 0
    try:
        locked_until = float(row["locked_until"] or 0)
    except Exception:
        locked_until = 0.0
    if locked_until and now < locked_until:
        return True, max(1, int(locked_until - now))
    return False, 0


def clear_login_failures(identifier: str) -> None:
    key_hash = _login_failure_key_hash(identifier)
    with get_db_conn() as conn:
        conn.execute("DELETE FROM login_failures WHERE key_hash = ?", (key_hash,))
        conn.commit()


def record_login_failure(identifier: str) -> tuple[bool, int]:
    from .config import LOGIN_FAILED_MAX_ATTEMPTS, LOGIN_FAILED_WINDOW_SECONDS, LOGIN_LOCK_SECONDS
    key_hash = _login_failure_key_hash(identifier)
    now = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    window_start = now - LOGIN_FAILED_WINDOW_SECONDS
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT fail_count, first_failed_at, locked_until FROM login_failures WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
        if row:
            try:
                first_failed_at = float(row["first_failed_at"] or 0)
            except Exception:
                first_failed_at = 0.0
            try:
                locked_until = float(row["locked_until"] or 0)
            except Exception:
                locked_until = 0.0
            if locked_until and now < locked_until:
                return True, max(1, int(locked_until - now))
            if not first_failed_at or first_failed_at < window_start:
                fail_count = 1
                first_failed_at = now
            else:
                fail_count = int(row["fail_count"] or 0) + 1
            locked = False
            remaining = 0
            new_locked_until = 0.0
            if fail_count >= LOGIN_FAILED_MAX_ATTEMPTS:
                locked = True
                new_locked_until = now + LOGIN_LOCK_SECONDS
                remaining = int(LOGIN_LOCK_SECONDS)
            conn.execute(
                """
                UPDATE login_failures
                SET fail_count = ?, first_failed_at = ?, last_failed_at = ?, locked_until = ?
                WHERE key_hash = ?
                """,
                (int(fail_count), str(first_failed_at), str(now), str(new_locked_until), key_hash),
            )
            conn.commit()
            return locked, remaining
        else:
            conn.execute(
                """
                INSERT INTO login_failures (created_at, key_hash, fail_count, first_failed_at, last_failed_at, locked_until)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (now_iso, key_hash, 1, str(now), str(now), "0"),
            )
            conn.commit()
            return False, 0
