"""Slicer preset routes."""

import os
import logging
from typing import Optional

from fastapi import Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from jose import JWTError, jwt

from .config import JWT_SECRET_KEY, JWT_ALGORITHM
from .deps import get_current_user
from .utils import _sanitize_filename_component, _user_base_dir
from .audit import write_audit_event
from .slicer_presets import (
    list_slicer_presets,
    get_system_slicer_preset,
    get_slicer_preset_by_id,
    upsert_slicer_preset,
    delete_slicer_preset,
    SYSTEM_SLICER_PRESET_ID,
)
from .auth import get_user_by_id

logger = logging.getLogger(__name__)


class SlicerPresetGenerateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60, description="预设名称")
    bed_width: float = Field(256.0, ge=50.0, le=1000.0, description="打印机X轴尺寸(mm)")
    bed_depth: float = Field(256.0, ge=50.0, le=1000.0, description="打印机Y轴尺寸(mm)")
    bed_height: float = Field(256.0, ge=50.0, le=1000.0, description="打印机Z轴尺寸(mm)")
    nozzle_size: float = Field(0.4, description="喷嘴大小(mm)")
    infill: int = Field(20, description="默认填充(%)")
    wall_count: int = Field(3, description="默认墙层数")
    layer_height: Optional[float] = Field(default=None, ge=0.05, le=1.0, description="层高(mm)")


async def api_list_slicer_presets(current_user=Depends(get_current_user)):
    try:
        items = list_slicer_presets(int(current_user["id"]))

        user_folder = f"user_{current_user['id']}_{current_user['username']}"
        user_configs_dir = os.path.join(_user_base_dir(), user_folder, "configs")

        valid_items = []

        # 系统内置预设
        sys_preset = get_system_slicer_preset()
        valid_items.append({
            "id": 0,
            "name": sys_preset["name"],
            "ext": sys_preset["ext"],
            "created_at": "内置",
            "is_default": True
        })

        for item in items:
            safe_preset_name = _sanitize_filename_component(item["name"], fallback="preset", max_len=60)
            config_saved_path = os.path.join(user_configs_dir, f"{safe_preset_name}{item['ext']}")
            if os.path.exists(config_saved_path):
                item["is_default"] = False
                valid_items.append(item)
            else:
                try:
                    delete_slicer_preset(int(current_user["id"]), int(item["id"]))
                except Exception:
                    pass

        return {"items": valid_items}
    except Exception as e:
        logger.error(f"获取切片预设列表失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"INTERNAL_ERROR: 获取预设失败 ({str(e)})")


async def api_generate_slicer_preset(payload: SlicerPresetGenerateRequest, request: Request, current_user=Depends(get_current_user)):
    valid_nozzles = [0.2, 0.4, 0.6, 0.8]
    if not any(abs(payload.nozzle_size - v) < 0.001 for v in valid_nozzles):
        raise HTTPException(status_code=400, detail="喷嘴大小只允许 0.2, 0.4, 0.6, 0.8")

    valid_infills = [5, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100]
    if payload.infill not in valid_infills:
        raise HTTPException(status_code=400, detail="填充率只允许推荐值 (如 10, 15, 20, 50, 100)")

    valid_walls = [2, 3, 4, 5, 6, 8, 10]
    if payload.wall_count not in valid_walls:
        raise HTTPException(status_code=400, detail="墙层数只允许推荐值 (如 2, 3, 4, 5, 6)")

    preset_name = payload.name.strip()

    from parser.prusa_slicer import generate_prusa_config

    config_path = generate_prusa_config(
        layer_height=payload.layer_height or 0.2,
        infill_percent=payload.infill,
        perimeters=payload.wall_count,
    )
    with open(config_path, "r") as f:
        raw = f.read().encode("utf-8")
    os.unlink(config_path)
    ext = ".ini"

    user_folder = f"user_{current_user['id']}_{current_user['username']}"
    user_configs_dir = os.path.join(_user_base_dir(), user_folder, "configs")
    os.makedirs(user_configs_dir, exist_ok=True)
    safe_preset_name = _sanitize_filename_component(preset_name, fallback="preset", max_len=60)
    config_saved_path = os.path.join(user_configs_dir, f"{safe_preset_name}{ext}")

    with open(config_saved_path, "wb") as f:
        f.write(raw)

    saved = upsert_slicer_preset(int(current_user["id"]), preset_name, ext, raw)
    write_audit_event(
        action="slicer.preset.generate",
        request=request,
        user=current_user,
        detail={"preset_id": int(saved["id"]), "name": str(saved["name"]), "ext": ext, "bytes": len(raw)},
    )
    return {"status": "ok", "preset": saved}


async def api_upsert_slicer_preset(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form("", min_length=0, max_length=60),
    current_user=Depends(get_current_user),
):
    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].strip().lower()
    if not ext:
        ext = ".cfg"
    raw = await file.read()
    inferred_name = os.path.splitext(os.path.basename(filename))[0].strip()
    preset_name = (name or "").strip() or inferred_name or "preset"

    user_folder = f"user_{current_user['id']}_{current_user['username']}"
    user_configs_dir = os.path.join(_user_base_dir(), user_folder, "configs")
    os.makedirs(user_configs_dir, exist_ok=True)
    safe_preset_name = _sanitize_filename_component(preset_name, fallback="preset", max_len=60)
    config_saved_path = os.path.join(user_configs_dir, f"{safe_preset_name}{ext}")
    with open(config_saved_path, "wb") as f:
        f.write(raw)

    saved = upsert_slicer_preset(int(current_user["id"]), preset_name, ext, raw)
    write_audit_event(
        action="slicer.preset.upsert",
        request=request,
        user=current_user,
        detail={"preset_id": int(saved["id"]), "name": str(saved["name"]), "ext": str(saved["ext"]), "bytes": int(len(raw or b""))},
    )
    return {"status": "ok", "preset": saved}


async def api_download_slicer_preset(preset_id: int, token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub", "0"))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="登录已失效")

    current_user = get_user_by_id(user_id)
    if not current_user:
        raise HTTPException(status_code=401, detail="用户不存在")

    if preset_id == 0:
        template_path = os.path.join(os.path.dirname(__file__), "..", "profiles", "prusa", "print.ini")
        if not os.path.exists(template_path):
            raise HTTPException(status_code=404, detail="系统预设文件丢失")
        return FileResponse(template_path, filename="PrusaSlicer_A1_0.20mm_Standard.ini")

    preset = get_slicer_preset_by_id(int(current_user["id"]), int(preset_id))
    if not preset:
        raise HTTPException(status_code=404, detail="预设不存在或无权限")

    user_folder = f"user_{current_user['id']}_{current_user['username']}"
    user_configs_dir = os.path.join(_user_base_dir(), user_folder, "configs")
    safe_preset_name = _sanitize_filename_component(preset["name"], fallback="preset", max_len=60)
    config_saved_path = os.path.join(user_configs_dir, f"{safe_preset_name}{preset['ext']}")

    if not os.path.exists(config_saved_path):
        raise HTTPException(status_code=404, detail="预设文件实体已丢失")

    return FileResponse(config_saved_path, filename=f"{safe_preset_name}{preset['ext']}")


async def api_delete_slicer_preset(preset_id: int, request: Request, current_user=Depends(get_current_user)):
    if preset_id == 0:
        raise HTTPException(status_code=400, detail="系统预设不可删除")

    preset = get_slicer_preset_by_id(int(current_user["id"]), int(preset_id))
    if not preset:
        raise HTTPException(status_code=404, detail="预设不存在或无权限")

    ok = delete_slicer_preset(int(current_user["id"]), int(preset_id))
    if not ok:
        raise HTTPException(status_code=404, detail="预设不存在或无权限")

    try:
        user_folder = f"user_{current_user['id']}_{current_user['username']}"
        user_configs_dir = os.path.join(_user_base_dir(), user_folder, "configs")
        safe_preset_name = _sanitize_filename_component(preset["name"], fallback="preset", max_len=60)
        config_saved_path = os.path.join(user_configs_dir, f"{safe_preset_name}{preset['ext']}")
        if os.path.exists(config_saved_path):
            os.remove(config_saved_path)
    except Exception as e:
        logger.error(f"删除预设文件实体失败: {e}")

    write_audit_event(
        action="slicer.preset.delete",
        request=request,
        user=current_user,
        detail={"preset_id": int(preset_id)},
    )
    return {"status": "ok"}
