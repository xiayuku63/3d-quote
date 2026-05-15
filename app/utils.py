"""Utility functions – filenames, dirs, email/phone normalization, materials."""

import os
import re
import hashlib
import secrets
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import HTTPException, Request

from .config import (
    JWT_SECRET_KEY,
    DEFAULT_MATERIALS,
    DEFAULT_COLORS,
    EMAIL_PATTERN,
    PHONE_PATTERN,
    USERNAME_PATTERN,
    PASSWORD_MIN_LENGTH,
    PASSWORD_MAX_LENGTH,
)

FILENAME_DISALLOWED_CHARS = set('\\/:*?"<>|')


def _sanitize_filename_component(value: str, fallback: str, max_len: int = 120) -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    out_chars: list[str] = []
    for ch in raw:
        code = ord(ch)
        if code < 32 or code == 127:
            out_chars.append("_")
            continue
        if ch in FILENAME_DISALLOWED_CHARS:
            out_chars.append("_")
            continue
        out_chars.append(ch)
    cleaned = re.sub(r"\s+", " ", "".join(out_chars)).strip()
    if not cleaned:
        cleaned = fallback
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned or fallback


def _user_base_dir() -> str:
    return _ensure_dir(os.getenv("USER_DATA_DIR", "user").strip() or "user")


def _ensure_dir(path: str) -> str:
    p = str(path or "").strip() or "."
    os.makedirs(p, exist_ok=True)
    return p


def _date_folder_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _uploads_base_dir() -> str:
    return _ensure_dir(os.getenv("UPLOADS_DIR", "uploads").strip() or "uploads")


def _outputs_base_dir() -> str:
    return _ensure_dir(os.getenv("OUTPUTS_DIR", "outputs").strip() or "outputs")


def _configs_base_dir() -> str:
    return _ensure_dir(os.getenv("CONFIGS_DIR", "configs").strip() or "configs")


def normalize_email(value: str) -> str:
    email = (value or "").strip().lower()
    if not email or len(email) > 254 or not EMAIL_PATTERN.match(email) or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="邮箱格式不合法")
    return email


def normalize_phone(value: str) -> str:
    raw = (value or "").strip()
    cleaned = re.sub(r"[\s\-\(\)]", "", raw)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not cleaned or not PHONE_PATTERN.match(cleaned):
        raise HTTPException(status_code=400, detail="手机号格式不合法")
    return cleaned


def hash_verify_code(code: str) -> str:
    return hashlib.sha256((str(code).strip() + JWT_SECRET_KEY).encode("utf-8")).hexdigest()


def generate_numeric_code(length: int = 6) -> str:
    length = max(4, min(int(length), 8))
    upper = 10 ** length
    return str(secrets.randbelow(upper)).zfill(length)


def get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def validate_username_or_raise(username: str) -> str:
    cleaned = (username or "").strip()
    if not USERNAME_PATTERN.match(cleaned):
        raise HTTPException(
            status_code=400,
            detail="用户名仅支持字母/数字/._-，长度 3-50",
        )
    return cleaned


def validate_password_or_raise(password: str) -> str:
    raw = password or ""
    if not (PASSWORD_MIN_LENGTH <= len(raw) <= PASSWORD_MAX_LENGTH):
        raise HTTPException(
            status_code=400,
            detail=f"密码长度必须在 {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} 位之间",
        )
    if not re.search(r"[A-Za-z]", raw) or not re.search(r"\d", raw):
        raise HTTPException(status_code=400, detail="密码必须包含字母和数字")
    return password


def normalize_materials(raw_materials, fallback_colors: Optional[List[str]] = None):
    if not raw_materials:
        return DEFAULT_MATERIALS
    effective_fallback_colors = fallback_colors or DEFAULT_COLORS
    normalized = []
    for m in raw_materials:
        name = str(m.get("name") or "").strip()
        if not name:
            continue
        density = float(m.get("density") or 0) or 1.0
        if "price_per_kg" in m:
            price_per_kg = float(m.get("price_per_kg") or 0) or 0.0
        else:
            price = float(m.get("price") or 0) or 0.0
            price_per_kg = price * 1000.0
        raw_colors = m.get("colors")
        if isinstance(raw_colors, list):
            colors = [str(c).strip() for c in raw_colors if str(c).strip()]
        else:
            colors = list(effective_fallback_colors)
        normalized.append({"name": name, "density": density, "price_per_kg": price_per_kg, "colors": colors})
    return normalized or DEFAULT_MATERIALS


def mask_email(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        local, domain = str(value).split("@", 1)
    except ValueError:
        return "***"
    if len(local) <= 2:
        masked = local[0] + "***"
    else:
        masked = local[0] + "***" + local[-1]
    return masked + "@" + domain


def mask_phone(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    s = str(value)
    if len(s) <= 4:
        return "***"
    return s[:3] + "****" + s[-4:]
