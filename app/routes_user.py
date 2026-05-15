"""User settings routes."""

import json
import logging
from typing import List, Optional

from fastapi import Request, Depends, HTTPException
from pydantic import BaseModel, Field

from .config import DEFAULT_MATERIALS, DEFAULT_COLORS, DEFAULT_PRICING_CONFIG
from .database import get_db_conn
from .deps import get_current_user
from .utils import normalize_materials
from .audit import write_audit_event
from .database import merge_pricing_config
from calculator.cost import validate_formula_expression

logger = logging.getLogger(__name__)


class MaterialItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    density: float = Field(..., gt=0, le=10)
    price_per_kg: float = Field(..., ge=0, le=100000)
    colors: List[str] = Field(default_factory=list, max_length=30)


class PricingConfig(BaseModel):
    machine_hourly_rate_cny: float = 15.0
    setup_fee_cny: float = 0.0
    min_job_fee_cny: float = 0.0
    material_waste_percent: float = 5.0
    support_percent_of_model: float = 0.0
    post_process_fee_per_part_cny: float = 0.0
    difficulty_coefficient: float = 0.25
    difficulty_ratio_low: float = 0.8
    difficulty_ratio_high: float = 4.0
    use_prusaslicer: int = 0
    prusaslicer_support_mode: str = "diff"
    support_price_per_g: float = 0.0
    time_overhead_min: float = 5.0
    time_vol_min_per_cm3: float = 0.8
    time_area_min_per_cm2: float = 0.0
    time_ref_layer_height_mm: float = 0.2
    time_layer_height_exponent: float = 1.0
    time_ref_infill_percent: float = 20.0
    time_infill_coefficient: float = 1.0
    unit_cost_formula: str = ""
    total_cost_formula: str = ""


class UserSettingsUpdate(BaseModel):
    materials: List[MaterialItem] = Field(..., min_length=1, max_length=100)
    colors: Optional[List[str]] = Field(default=None, max_length=100)
    pricing_config: Optional[PricingConfig] = None


async def get_user_settings(current_user=Depends(get_current_user)):
    try:
        with get_db_conn() as conn:
            row = conn.execute("SELECT materials, colors, pricing_config FROM users WHERE id = ?", (current_user["id"],)).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="USER_NOT_FOUND: 用户不存在")

        raw_materials = json.loads(row["materials"]) if row and row["materials"] else DEFAULT_MATERIALS
        colors = json.loads(row["colors"]) if row and row["colors"] else DEFAULT_COLORS
        materials = normalize_materials(raw_materials, fallback_colors=colors)
        raw_pricing = json.loads(row["pricing_config"]) if row and row["pricing_config"] else DEFAULT_PRICING_CONFIG
        pricing_config = merge_pricing_config(raw_pricing)
        derived_colors = []
        for m in materials:
            for c in m.get("colors", []):
                if c not in derived_colors:
                    derived_colors.append(c)
        return {"materials": materials, "colors": derived_colors, "pricing_config": pricing_config}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户配置失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"INTERNAL_ERROR: 获取用户配置失败 ({str(e)})")


async def update_user_settings(payload: UserSettingsUpdate, request: Request, current_user=Depends(get_current_user)):
    seen_material_names = set()
    for item in payload.materials:
        normalized_name = item.name.strip()
        if not normalized_name:
            raise HTTPException(status_code=400, detail="材料名称不能为空")
        name_key = normalized_name.lower()
        if name_key in seen_material_names:
            raise HTTPException(status_code=400, detail=f"材料名称重复：{normalized_name}")
        seen_material_names.add(name_key)

    materials_json = json.dumps([m.model_dump() for m in payload.materials])
    if payload.colors is not None:
        derived_colors = payload.colors
    else:
        derived_colors = []
        for m in payload.materials:
            for c in m.colors:
                if c not in derived_colors:
                    derived_colors.append(c)
    colors_json = json.dumps(derived_colors)
    pricing_json = None
    if payload.pricing_config is not None:
        unit_ok, unit_err, _ = validate_formula_expression(payload.pricing_config.unit_cost_formula)
        total_ok, total_err, _ = validate_formula_expression(payload.pricing_config.total_cost_formula)
        if not unit_ok or not total_ok:
            messages = []
            if not unit_ok:
                messages.append(f"单件公式：{unit_err or '无效'}")
            if not total_ok:
                messages.append(f"总价公式：{total_err or '无效'}")
            raise HTTPException(status_code=400, detail="；".join(messages) or "公式无效")
        pricing_json = json.dumps(payload.pricing_config.model_dump())
    with get_db_conn() as conn:
        if pricing_json is None:
            conn.execute("UPDATE users SET materials = ?, colors = ? WHERE id = ?", (materials_json, colors_json, current_user["id"]))
        else:
            conn.execute("UPDATE users SET materials = ?, colors = ?, pricing_config = ? WHERE id = ?", (materials_json, colors_json, pricing_json, current_user["id"]))
        conn.commit()
    write_audit_event(
        action="user.settings.update",
        request=request,
        user=current_user,
        detail={"materials_count": len(payload.materials), "has_pricing_config": payload.pricing_config is not None},
    )
    return {"status": "success"}


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=100)
    new_password: str = Field(..., min_length=6, max_length=100)
    captcha_id: str = Field(..., min_length=8, max_length=80)
    captcha_code: str = Field(..., min_length=4, max_length=10)


async def change_password(req: ChangePasswordRequest, request: Request, current_user=Depends(get_current_user)):
    from .captcha import verify_captcha_or_raise
    from .auth import verify_password, get_password_hash, get_user_by_id
    from .utils import validate_password_or_raise

    verify_captcha_or_raise(req.captcha_id, req.captcha_code)
    new_password = validate_password_or_raise(req.new_password)

    full_user = get_user_by_id(int(current_user["id"]))
    if not full_user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # Need the full user record with password_hash
    from .database import get_db_conn as _get_db_conn
    with _get_db_conn() as conn:
        full_row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (current_user["id"],)).fetchone()
    if not full_row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not verify_password(req.old_password, full_row["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码错误")

    new_hash = get_password_hash(new_password)
    with get_db_conn() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, current_user["id"]))
        conn.commit()

    write_audit_event(action="user.change_password", request=request, user=current_user)
    return {"status": "ok"}
