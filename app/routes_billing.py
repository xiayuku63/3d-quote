"""Billing / membership routes."""

import json
import time
import secrets
import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import Request, Depends, HTTPException
from pydantic import BaseModel, Field

from .config import PAYMENT_PROVIDER, PAYMENT_WEBHOOK_SECRET
from .database import get_db_conn
from .deps import get_current_user, get_membership_effective
from .audit import write_audit_event
from .auth import get_user_by_id


class BillingCheckoutRequest(BaseModel):
    plan_code: str = Field(..., min_length=3, max_length=40)
    redirect_url: str | None = Field(default=None, max_length=500)


class BillingMockCompleteRequest(BaseModel):
    order_no: str = Field(..., min_length=10, max_length=80)


# ---------- internal helpers ----------

def _create_order_no() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rnd = secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:18]
    return f"PO{ts}{rnd}"


def _get_active_membership_plans() -> list[dict]:
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT code, name, price_cny, currency, duration_days FROM membership_plans WHERE active = 1 ORDER BY price_cny ASC, duration_days ASC"
        ).fetchall()
    items = []
    for r in rows:
        items.append(
            {
                "code": r["code"],
                "name": r["name"],
                "price_cny": float(r["price_cny"] or 0.0),
                "currency": r["currency"],
                "duration_days": int(r["duration_days"] or 0),
            }
        )
    return items


def _get_plan_or_404(plan_code: str):
    import sqlite3
    code = (plan_code or "").strip()
    if not code or len(code) > 40:
        raise HTTPException(status_code=400, detail="套餐不合法")
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT code, name, price_cny, currency, duration_days, active FROM membership_plans WHERE code = ?",
            (code,),
        ).fetchone()
    if not row or int(row["active"] or 0) != 1:
        raise HTTPException(status_code=404, detail="套餐不存在或已下架")
    return row


def _mark_order_paid_and_upgrade(order_no: str, provider_txn_id: str, raw_json: dict) -> dict:
    now = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db_conn() as conn:
        order = conn.execute(
            "SELECT id, user_id, plan_code, amount_cny, currency, status FROM payment_orders WHERE order_no = ?",
            (order_no,),
        ).fetchone()
        if not order:
            raise HTTPException(status_code=404, detail="订单不存在")
        if str(order["status"] or "") == "paid":
            user = conn.execute("SELECT id, membership_level, membership_expires_at FROM users WHERE id = ?", (int(order["user_id"]),)).fetchone()
            level, expires_ts = get_membership_effective(user)
            return {"status": "paid", "order_no": order_no, "membership_level": level, "membership_expires_at": expires_ts}
        if str(order["status"] or "") != "created":
            raise HTTPException(status_code=400, detail="订单状态不支持支付")

        plan = conn.execute(
            "SELECT code, duration_days, price_cny, currency FROM membership_plans WHERE code = ? AND active = 1",
            (order["plan_code"],),
        ).fetchone()
        if not plan:
            raise HTTPException(status_code=400, detail="套餐不可用")

        amount_cny = float(order["amount_cny"] or 0.0)
        if abs(amount_cny - float(plan["price_cny"] or 0.0)) > 0.0001:
            raise HTTPException(status_code=400, detail="订单金额异常")
        if str(order["currency"] or "") != str(plan["currency"] or ""):
            raise HTTPException(status_code=400, detail="订单币种异常")

        duration_days = int(plan["duration_days"] or 0)
        user = conn.execute("SELECT id, membership_expires_at FROM users WHERE id = ?", (int(order["user_id"]),)).fetchone()
        base = now
        try:
            existing_exp = user["membership_expires_at"]
            if existing_exp is not None and str(existing_exp).strip() != "":
                existing_ts = float(str(existing_exp))
                if existing_ts > base:
                    base = existing_ts
        except Exception:
            pass
        new_expires_ts = None
        if duration_days > 0:
            new_expires_ts = int(base + (duration_days * 86400))

        conn.execute(
            "UPDATE payment_orders SET status = 'paid', paid_at = ?, provider_txn_id = ?, raw_json = ? WHERE order_no = ?",
            (now_iso, str(provider_txn_id or ""), json.dumps(raw_json or {}, ensure_ascii=False), order_no),
        )
        if new_expires_ts is None:
            conn.execute("UPDATE users SET membership_level = 'member', membership_expires_at = NULL WHERE id = ?", (int(order["user_id"]),))
        else:
            conn.execute("UPDATE users SET membership_level = 'member', membership_expires_at = ? WHERE id = ?", (str(new_expires_ts), int(order["user_id"])))
        conn.commit()
    return {"status": "paid", "order_no": order_no, "membership_level": "member", "membership_expires_at": new_expires_ts}


# ---------- routes ----------

async def billing_plans():
    return {"items": _get_active_membership_plans()}


async def billing_checkout(payload: BillingCheckoutRequest, request: Request, current_user=Depends(get_current_user)):
    plan = _get_plan_or_404(payload.plan_code)
    order_no = _create_order_no()
    created_at = datetime.now(timezone.utc).isoformat()
    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO payment_orders (order_no, user_id, plan_code, amount_cny, currency, provider, status, created_at, paid_at, provider_txn_id, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, 'created', ?, NULL, NULL, NULL)
            """,
            (
                order_no,
                int(current_user["id"]),
                plan["code"],
                float(plan["price_cny"] or 0.0),
                plan["currency"],
                PAYMENT_PROVIDER,
                created_at,
            ),
        )
        conn.commit()
    write_audit_event(
        action="billing.order.created",
        request=request,
        user=current_user,
        detail={"order_no": order_no, "plan_code": plan["code"], "amount_cny": float(plan["price_cny"] or 0.0), "provider": PAYMENT_PROVIDER},
    )
    pay_url = f"/pay/mock?order_no={order_no}" if PAYMENT_PROVIDER == "mock" else ""
    return {"order_no": order_no, "plan": {"code": plan["code"], "name": plan["name"]}, "amount_cny": float(plan["price_cny"] or 0.0), "currency": plan["currency"], "pay_url": pay_url}


async def billing_orders(limit: int = 20, offset: int = 0, current_user=Depends(get_current_user)):
    safe_limit = max(1, min(int(limit), 100))
    safe_offset = max(0, int(offset))
    with get_db_conn() as conn:
        rows = conn.execute(
            """
            SELECT order_no, plan_code, amount_cny, currency, provider, status, created_at, paid_at
            FROM payment_orders
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (int(current_user["id"]), safe_limit, safe_offset),
        ).fetchall()
    items = []
    for r in rows:
        items.append(
            {
                "order_no": r["order_no"],
                "plan_code": r["plan_code"],
                "amount_cny": float(r["amount_cny"] or 0.0),
                "currency": r["currency"],
                "provider": r["provider"],
                "status": r["status"],
                "created_at": r["created_at"],
                "paid_at": r["paid_at"],
            }
        )
    return {"items": items, "limit": safe_limit, "offset": safe_offset}


async def billing_mock_complete(payload: BillingMockCompleteRequest, request: Request, current_user=Depends(get_current_user)):
    order_no = (payload.order_no or "").strip()
    if not order_no:
        raise HTTPException(status_code=400, detail="订单号不合法")
    with get_db_conn() as conn:
        order = conn.execute(
            "SELECT order_no, user_id, plan_code, amount_cny, currency, status FROM payment_orders WHERE order_no = ?",
            (order_no,),
        ).fetchone()
    if not order or int(order["user_id"]) != int(current_user["id"]):
        raise HTTPException(status_code=404, detail="订单不存在")
    provider_txn_id = f"MOCK{secrets.token_urlsafe(10)}"
    result = _mark_order_paid_and_upgrade(order_no=order_no, provider_txn_id=provider_txn_id, raw_json={"provider": "mock", "order_no": order_no, "paid_at": datetime.now(timezone.utc).isoformat()})
    write_audit_event(
        action="billing.order.paid",
        request=request,
        user=current_user,
        detail={"order_no": order_no, "provider": "mock", "membership_expires_at": result.get("membership_expires_at")},
    )
    return result


async def billing_webhook(request: Request):
    body = await request.body()
    provided = (request.headers.get("X-Payment-Signature") or "").strip()
    expected = hmac.new(PAYMENT_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="签名校验失败")
    try:
        event = json.loads(body.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="事件格式不合法")
    order_no = str(event.get("order_no") or "").strip()
    provider = str(event.get("provider") or "").strip().lower()
    provider_txn_id = str(event.get("provider_txn_id") or "").strip()
    if not order_no or not provider or not provider_txn_id:
        raise HTTPException(status_code=400, detail="事件缺少必要字段")
    with get_db_conn() as conn:
        row = conn.execute("SELECT provider, user_id FROM payment_orders WHERE order_no = ?", (order_no,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="订单不存在")
    if str(row["provider"] or "").strip().lower() != provider:
        raise HTTPException(status_code=400, detail="支付渠道不匹配")
    result = _mark_order_paid_and_upgrade(order_no=order_no, provider_txn_id=provider_txn_id, raw_json=event)
    user = get_user_by_id(int(row["user_id"]))
    write_audit_event(
        action="billing.webhook.paid",
        request=request,
        user=user,
        detail={"order_no": order_no, "provider": provider},
    )
    return {"status": "ok", "result": result}
