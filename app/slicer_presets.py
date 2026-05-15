"""Slicer preset management (DB + file system operations)."""

import os
import re
import base64
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from .database import get_db_conn

SLICER_PRESET_NAME_MAX_LEN = 60
SLICER_PRESET_NAME_DISALLOWED_CHARS = set('\\/:*?"<>|')
SYSTEM_SLICER_PRESET_ID = 0
SYSTEM_SLICER_PRESET_FILENAME = os.path.join("profiles", "prusa", "print.ini")
SYSTEM_SLICER_PRESET_DISPLAY_NAME = "PrusaSlicer 0.20mm Standard (系统内置)"


def _normalize_slicer_preset_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return "preset"
    normalized_chars: list[str] = []
    for ch in raw:
        code = ord(ch)
        if code < 32 or code == 127:
            normalized_chars.append("_")
            continue
        if ch in SLICER_PRESET_NAME_DISALLOWED_CHARS:
            normalized_chars.append("_")
            continue
        normalized_chars.append(ch)
    cleaned = re.sub(r"\s+", " ", "".join(normalized_chars)).strip()
    if not cleaned:
        cleaned = "preset"
    if len(cleaned) > SLICER_PRESET_NAME_MAX_LEN:
        cleaned = cleaned[:SLICER_PRESET_NAME_MAX_LEN].rstrip()
        if not cleaned:
            cleaned = "preset"
    return cleaned


def list_slicer_presets(user_id: int) -> list[dict]:
    uid = int(user_id or 0)
    if uid <= 0:
        return []
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, ext, created_at FROM slicer_presets WHERE user_id = ? ORDER BY id DESC",
            (uid,),
        ).fetchall()
    out = []
    for r in rows or []:
        out.append(
            {
                "id": int(r["id"]),
                "name": str(r["name"] or ""),
                "ext": str(r["ext"] or ""),
                "created_at": str(r["created_at"] or ""),
            }
        )
    return out


def get_system_slicer_preset() -> dict:
    template_path = os.path.join(os.path.dirname(__file__), "..", SYSTEM_SLICER_PRESET_FILENAME)
    if not os.path.exists(template_path):
        raise HTTPException(status_code=500, detail="系统预设文件丢失")
    try:
        with open(template_path, "rb") as f:
            content = f.read()
    except Exception:
        raise HTTPException(status_code=500, detail="读取系统预设失败")
    if not content:
        raise HTTPException(status_code=500, detail="系统预设内容为空")
    return {
        "id": SYSTEM_SLICER_PRESET_ID,
        "name": SYSTEM_SLICER_PRESET_DISPLAY_NAME,
        "ext": ".ini",
        "content": bytes(content),
        "created_at": "内置",
        "is_default": True,
    }


def get_slicer_preset_by_id(user_id: int, preset_id: int) -> Optional[dict]:
    uid = int(user_id or 0)
    pid = int(preset_id or 0)
    if uid <= 0 or pid <= 0:
        return None
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT id, name, ext, content_b64, created_at FROM slicer_presets WHERE id = ? AND user_id = ?",
            (pid, uid),
        ).fetchone()
    if not row:
        return None
    try:
        content = base64.b64decode(str(row["content_b64"] or "").encode("ascii"), validate=False)
    except Exception:
        content = b""
    return {
        "id": int(row["id"]),
        "name": str(row["name"] or ""),
        "ext": str(row["ext"] or ""),
        "content": bytes(content),
        "created_at": str(row["created_at"] or ""),
    }


def upsert_slicer_preset(user_id: int, name: str, ext: str, content: bytes) -> dict:
    uid = int(user_id or 0)
    if uid <= 0:
        raise HTTPException(status_code=401, detail="未登录")
    preset_name = _normalize_slicer_preset_name(name)
    safe_ext = (ext or "").strip().lower()
    if safe_ext not in {".ini", ".cfg", ".json"}:
        raise HTTPException(status_code=400, detail="预设文件格式不支持（仅支持 .cfg/.json/.ini）")
    raw = bytes(content or b"")
    if not raw or len(raw) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="预设文件内容不能为空且必须小于 2MB")
    created_at = datetime.now(timezone.utc).isoformat()
    b64 = base64.b64encode(raw).decode("ascii")
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO slicer_presets (user_id, name, ext, content_b64, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, name) DO UPDATE SET
                ext = excluded.ext,
                content_b64 = excluded.content_b64,
                created_at = excluded.created_at
            """,
            (uid, preset_name, safe_ext, b64, created_at),
        )
        row = conn.execute(
            "SELECT id, name, ext, created_at FROM slicer_presets WHERE user_id = ? AND name = ?",
            (uid, preset_name),
        ).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=500, detail="预设保存失败")
    return {"id": int(row["id"]), "name": str(row["name"]), "ext": str(row["ext"]), "created_at": str(row["created_at"])}


def delete_slicer_preset(user_id: int, preset_id: int) -> bool:
    uid = int(user_id or 0)
    pid = int(preset_id or 0)
    if uid <= 0 or pid <= 0:
        return False
    with get_db_conn() as conn:
        cur = conn.execute("DELETE FROM slicer_presets WHERE id = ? AND user_id = ?", (pid, uid))
        conn.commit()
        return int(cur.rowcount or 0) > 0
