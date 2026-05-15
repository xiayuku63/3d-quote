"""Audit event logging and idempotency support."""

import json
import time
from datetime import datetime, timezone
from typing import Optional
import sqlite3

from fastapi import Request

from .database import get_db_conn
from .config import IDEMPOTENCY_TTL_SECONDS
from .utils import get_client_ip


def write_audit_event(
    action: str,
    request: Optional[Request] = None,
    user: Optional[sqlite3.Row] = None,
    detail: Optional[dict] = None,
    idempotency_key: Optional[str] = None,
) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    user_id = None
    username = None
    if user is not None:
        try:
            user_id = int(user["id"])
        except Exception:
            user_id = None
        username = str(user.get("username") if hasattr(user, "get") else user["username"]) if user is not None else None
    ip = get_client_ip(request) if request is not None else None
    method = request.method if request is not None else None
    path = request.url.path if request is not None else None
    request_id = getattr(getattr(request, "state", None), "request_id", None) if request is not None else None
    detail_json = json.dumps(detail or {}, ensure_ascii=False)
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO audit_events (created_at, user_id, username, action, ip, method, path, request_id, idempotency_key, detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (created_at, user_id, username, action, ip, method, path, request_id, idempotency_key, detail_json),
        )
        conn.commit()


def get_idempotency_key_from_request(request: Request) -> Optional[str]:
    raw = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key")
    if not raw:
        return None
    key = raw.strip()
    if not key:
        return None
    if len(key) > 120:
        return None
    return key


def try_get_idempotent_response(user_id: int, request: Request, idem_key: str) -> Optional[tuple[int, dict]]:
    now = time.time()
    with get_db_conn() as conn:
        row = conn.execute(
            """
            SELECT status_code, response_json, expires_at
            FROM idempotency_responses
            WHERE user_id = ? AND method = ? AND path = ? AND idem_key = ?
            """,
            (int(user_id), request.method, request.url.path, idem_key),
        ).fetchone()
    if not row:
        return None
    try:
        expires_at = float(row["expires_at"])
    except Exception:
        return None
    if now > expires_at:
        return None
    try:
        payload = json.loads(row["response_json"])
    except Exception:
        return None
    return int(row["status_code"]), payload


def save_idempotent_response(user_id: int, request: Request, idem_key: str, status_code: int, payload: dict) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    expires_at = str(time.time() + IDEMPOTENCY_TTL_SECONDS)
    response_json = json.dumps(payload, ensure_ascii=False)
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO idempotency_responses (created_at, expires_at, user_id, method, path, idem_key, status_code, response_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (created_at, expires_at, int(user_id), request.method, request.url.path, idem_key, int(status_code), response_json),
        )
        conn.commit()
