"""Quote route and formula validation."""

import asyncio
import concurrent.futures
import json
import logging
from typing import List, Optional

from fastapi import Depends, FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import DEFAULT_MATERIALS, DEFAULT_PRICING_CONFIG, MAX_FILES_PER_REQUEST, QUOTE_CONCURRENCY
from .database import get_db_conn
from .deps import get_current_user, get_membership_effective
from .audit import write_audit_event, get_idempotency_key_from_request, try_get_idempotent_response, save_idempotent_response
from .slicer_presets import get_system_slicer_preset, get_slicer_preset_by_id, SYSTEM_SLICER_PRESET_ID
from calculator.cost import (
    validate_formula_expression,
    FORMULA_ALIAS_TO_CANONICAL,
    process_single_file,
)

logger = logging.getLogger(__name__)


async def get_quote(
    request: Request,
    files: List[UploadFile] = File(...),
    material: str = Form("PLA", min_length=1, max_length=40),
    layer_height: float = Form(0.2, ge=0.05, le=1.0),
    infill: int = Form(20, ge=0, le=100),
    wall_count: int = Form(3, ge=1, le=20),
    slicer_preset_id: Optional[int] = Form(default=None),
    quantity: int = Form(1, ge=1, le=5000),
    color: str = Form("White", min_length=1, max_length=40),
    use_bambu: Optional[bool] = Form(default=None),
    use_prusaslicer: Optional[bool] = Form(default=None),
    printer_model: Optional[str] = Form(default=None),
    current_user=Depends(get_current_user),
):
    from .config import MEMBER_DISCOUNT_PERCENT
    try:
        idem_key = get_idempotency_key_from_request(request)
        if idem_key:
            cached = try_get_idempotent_response(int(current_user["id"]), request, idem_key)
            if cached:
                status_code, payload = cached
                write_audit_event(
                    action="quote.replay",
                    request=request,
                    user=current_user,
                    idempotency_key=idem_key,
                    detail={"status_code": status_code},
                )
                return JSONResponse(status_code=status_code, content=payload)

        if not files:
            raise HTTPException(status_code=400, detail="请至少上传一个模型文件")
        if len(files) > MAX_FILES_PER_REQUEST:
            raise HTTPException(status_code=400, detail=f"单次上传文件数量不能超过 {MAX_FILES_PER_REQUEST} 个")

        with get_db_conn() as conn:
            row = conn.execute("SELECT materials, pricing_config FROM users WHERE id = ?", (current_user["id"],)).fetchone()
        user_materials = json.loads(row["materials"]) if row and row["materials"] else DEFAULT_MATERIALS
        pricing_config = json.loads(row["pricing_config"]) if row and row["pricing_config"] else DEFAULT_PRICING_CONFIG

        if use_bambu is not None:
            pricing_config["use_bambu"] = use_bambu
        if use_prusaslicer is not None:
            pricing_config["use_prusaslicer"] = use_prusaslicer
        if printer_model is not None:
            pricing_config["printer_model"] = printer_model

        material_names = {str(m.get("name")) for m in user_materials if isinstance(m, dict)}
        if material not in material_names:
            raise HTTPException(status_code=400, detail="材料参数不合法")

        selected_material = next((m for m in user_materials if isinstance(m, dict) and str(m.get("name")) == material), None)
        allowed_colors = []
        if selected_material:
            raw_colors = selected_material.get("colors", [])
            if isinstance(raw_colors, list):
                allowed_colors = [str(c).strip() for c in raw_colors if str(c).strip()]
        if allowed_colors and color not in allowed_colors:
            raise HTTPException(status_code=400, detail="颜色参数不合法")

        slicer_preset = None
        if slicer_preset_id is not None:
            sid = int(slicer_preset_id)
            if sid == SYSTEM_SLICER_PRESET_ID:
                slicer_preset = get_system_slicer_preset()
            else:
                slicer_preset = get_slicer_preset_by_id(int(current_user["id"]), sid)
                if slicer_preset is None:
                    raise HTTPException(status_code=400, detail="切片预设不存在或无权限")

        # Parallel file processing with concurrency limit
        _semaphore = asyncio.Semaphore(max(1, QUOTE_CONCURRENCY))

        async def process_one(file):
            async with _semaphore:
                try:
                    return await asyncio.to_thread(
                        _process_single_file_sync,
                        file, material, layer_height, infill, quantity, color,
                        user_materials, pricing_config, slicer_preset, wall_count, current_user,
                    )
                except Exception as e:
                    fname = file.filename or "unknown"
                    logger.error(f"处理文件 {fname} 时发生未捕获的错误: {str(e)}", exc_info=True)
                    return {
                        "filename": fname,
                        "status": "failed",
                        "error": f"INTERNAL_ERROR: {str(e)}",
                        "cost_cny": 0, "weight_g": 0, "estimated_time_h": 0,
                    }

        tasks = [process_one(f) for f in files]
        results = await asyncio.gather(*tasks)

        success_items = [item for item in results if item.get("status") == "success"]
        failed_items = [item for item in results if item.get("status") == "failed"]

        membership_level, membership_expires_at = get_membership_effective(current_user)
        discount_percent = float(MEMBER_DISCOUNT_PERCENT or 0.0)
        if discount_percent < 0:
            discount_percent = 0.0
        if discount_percent > 90:
            discount_percent = 90.0
        if membership_level == "member" and discount_percent > 0 and success_items:
            for item in success_items:
                try:
                    original = float(item.get("cost_cny") or 0.0)
                except Exception:
                    original = 0.0
                discounted = round(original * (1.0 - (discount_percent / 100.0)), 2)
                item["cost_cny_original"] = round(original, 2)
                item["cost_cny"] = discounted
                breakdown = item.get("cost_breakdown")
                if isinstance(breakdown, dict):
                    breakdown["member_discount_percent"] = round(discount_percent, 2)
                    breakdown["member_discount_cny"] = round(max(0.0, original - discounted), 2)

        payload = {
            "total_files": len(results),
            "success_count": len(success_items),
            "failed_count": len(failed_items),
            "summary_total_cost_cny": round(sum(item.get("cost_cny", 0) for item in success_items), 2),
            "summary_total_weight_g": round(sum(item.get("weight_g", 0) for item in success_items), 2),
            "summary_total_time_h": round(sum(item.get("estimated_time_h", 0) for item in success_items), 2),
            "results": results,
            "membership_level": membership_level,
            "membership_expires_at": membership_expires_at,
            "member_discount_percent": round(discount_percent, 2) if membership_level == "member" else 0.0,
        }
        write_audit_event(
            action="quote.create",
            request=request,
            user=current_user,
            idempotency_key=idem_key,
            detail={
                "files": len(results),
                "success": len(success_items),
                "failed": len(failed_items),
                "material": material,
                "quantity": quantity,
            },
        )
        if idem_key:
            save_idempotent_response(int(current_user["id"]), request, idem_key, 200, payload)
        _save_quote_history(int(current_user["id"]), results)
        return payload
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"处理报价请求失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"INTERNAL_ERROR: 报价请求失败 ({str(e)})")


class FormulaValidateRequest(BaseModel):
    unit_cost_formula: str = Field(..., min_length=1, max_length=800)
    total_cost_formula: str = Field(..., min_length=1, max_length=800)


async def validate_formula(payload: FormulaValidateRequest, current_user=Depends(get_current_user)):
    unit_ok, unit_err, unit_vars = validate_formula_expression(payload.unit_cost_formula)
    total_ok, total_err, total_vars = validate_formula_expression(payload.total_cost_formula)
    ok = unit_ok and total_ok
    return {
        "ok": ok,
        "unit": {"ok": unit_ok, "error": unit_err, "used_vars": unit_vars},
        "total": {"ok": total_ok, "error": total_err, "used_vars": total_vars},
        "aliases": FORMULA_ALIAS_TO_CANONICAL,
    }


# ── Quote history ──


def _process_single_file_sync(
    file: UploadFile,
    material: str,
    layer_height: float,
    infill: int,
    quantity: int,
    color: str,
    user_materials: List[dict],
    pricing_config: dict,
    slicer_preset: Optional[dict] = None,
    perimeters: Optional[int] = None,
    current_user: Optional[dict] = None,
):
    """Synchronous wrapper for process_single_file — runs in thread pool."""
    import asyncio as _asyncio
    # Read file content synchronously (UploadFile can be read in thread)
    loop = _asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            process_single_file(
                file, material, layer_height, infill, quantity, color,
                user_materials, pricing_config, slicer_preset, perimeters, current_user,
            )
        )
    finally:
        loop.close()

def _save_quote_history(user_id: int, results: list) -> None:
    """Save quote results to history table."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with get_db_conn() as conn:
        for item in results:
            conn.execute(
                """INSERT INTO quote_history
                   (user_id, filename, material, color, quantity, volume_cm3, weight_g,
                    estimated_time_h, cost_cny, dimensions, status, error_msg, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    str(item.get("filename") or "")[:200],
                    str(item.get("material") or "")[:40],
                    str(item.get("color") or "")[:40],
                    int(item.get("quantity") or 1),
                    round(float(item.get("volume_cm3") or 0), 2),
                    round(float(item.get("weight_g") or 0), 2),
                    round(float(item.get("estimated_time_h") or 0), 2),
                    round(float(item.get("cost_cny") or 0), 2),
                    str(item.get("dimensions") or "")[:80],
                    str(item.get("status") or "success")[:20],
                    str(item.get("error") or "")[:300] if item.get("status") != "success" else None,
                    now,
                ),
            )
        conn.commit()


async def quote_history(limit: int = 20, offset: int = 0, current_user=Depends(get_current_user)):
    """Get quote history for current user."""
    safe_limit = max(1, min(int(limit), 100))
    safe_offset = max(0, int(offset))
    uid = int(current_user["id"])
    with get_db_conn() as conn:
        total_row = conn.execute(
            "SELECT COUNT(*) as c FROM quote_history WHERE user_id = ?", (uid,)
        ).fetchone()
        rows = conn.execute(
            """SELECT id, filename, material, color, quantity, volume_cm3, weight_g,
                      estimated_time_h, cost_cny, dimensions, status, error_msg, created_at
               FROM quote_history WHERE user_id = ?
               ORDER BY id DESC LIMIT ? OFFSET ?""",
            (uid, safe_limit, safe_offset),
        ).fetchall()
    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "filename": r["filename"],
            "material": r["material"],
            "color": r["color"],
            "quantity": r["quantity"],
            "volume_cm3": round(float(r["volume_cm3"] or 0), 2),
            "weight_g": round(float(r["weight_g"] or 0), 2),
            "estimated_time_h": round(float(r["estimated_time_h"] or 0), 2),
            "cost_cny": round(float(r["cost_cny"] or 0), 2),
            "dimensions": r["dimensions"],
            "status": r["status"],
            "error_msg": r["error_msg"],
            "created_at": r["created_at"],
        })
    total = int(total_row["c"] or 0) if total_row else 0
    return {"items": items, "total": total, "limit": safe_limit, "offset": safe_offset}
