"""Dependencies – FastAPI dependency injection (get_current_user, require_admin, etc.)."""

import time
from typing import Optional
from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from .config import JWT_SECRET_KEY, JWT_ALGORITHM, MEMBER_DISCOUNT_PERCENT, ADMIN_USERNAMES, TERMS_VERSION, PRIVACY_VERSION
from .database import get_db_conn
from .auth import get_user_by_id

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(status_code=401, detail="登录已失效，请重新登录")
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub", "0"))
    except (JWTError, ValueError):
        raise credentials_exception

    user = get_user_by_id(user_id)
    if not user:
        raise credentials_exception
    return user


def is_admin_user(user_row) -> bool:
    if not user_row:
        return False
    username = str(user_row["username"] or "").strip().lower()
    return username in ADMIN_USERNAMES


def get_membership_effective(user_row) -> tuple[str, Optional[int]]:
    if not user_row:
        return "free", None
    raw_level = str(user_row["membership_level"] or "free").strip().lower() or "free"
    if raw_level not in {"free", "member"}:
        raw_level = "free"
    expires_ts = None
    try:
        raw_exp = user_row["membership_expires_at"]
        if raw_exp is not None and str(raw_exp).strip() != "":
            expires_ts = int(float(str(raw_exp)))
    except Exception:
        expires_ts = None
    if raw_level != "member":
        return "free", expires_ts
    if expires_ts is not None and time.time() >= float(expires_ts):
        return "free", expires_ts
    return "member", expires_ts


def is_member_user(user_row) -> bool:
    level, _ = get_membership_effective(user_row)
    return level == "member"


def require_legal_acceptance_or_raise(accept_terms: bool, accept_privacy: bool) -> None:
    if not bool(accept_terms) or not bool(accept_privacy):
        raise HTTPException(status_code=400, detail="请先阅读并同意《用户协议》和《隐私政策》")


def record_legal_acceptance(user_id: int) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db_conn() as conn:
        conn.execute(
            """
            UPDATE users
            SET terms_accepted_at = ?, privacy_accepted_at = ?, terms_version = ?, privacy_version = ?
            WHERE id = ?
            """,
            (now_iso, now_iso, TERMS_VERSION, PRIVACY_VERSION, int(user_id)),
        )
        conn.commit()


def require_admin(current_user):
    if not is_admin_user(current_user):
        raise HTTPException(status_code=403, detail="无管理员权限")
    return current_user


def mask_email(value: Optional[str]) -> Optional[str]:
    raw = (value or "").strip()
    if not raw or "@" not in raw:
        return None
    local, domain = raw.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*" if local else "*"
    else:
        masked_local = local[0] + ("*" * max(1, len(local) - 2)) + local[-1]
    return f"{masked_local}@{domain}"


def mask_phone(value: Optional[str]) -> Optional[str]:
    raw = (value or "").strip()
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7:
        return "*" * len(digits)
    return digits[:3] + ("*" * (len(digits) - 7)) + digits[-4:]


# Need re for mask_phone
import re
