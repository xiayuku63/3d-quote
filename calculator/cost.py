import os
import json
import ast
import math
import re
import time
import tempfile
import uuid
import hashlib
from typing import List, Optional
from fastapi import UploadFile, Request

from parser.slicer import run_kirimoto_slice, kirimoto_support_diff_stats

def calculate_weight(volume, material_density):
    """Calculate weight (unit: g)"""
    return volume * material_density / 1000  # mm³ -> cm³ -> g

def merge_pricing_config(raw_config):
    from main import DEFAULT_PRICING_CONFIG
    if not raw_config:
        return dict(DEFAULT_PRICING_CONFIG)
    merged = dict(DEFAULT_PRICING_CONFIG)
    for k, v in raw_config.items():
        merged[k] = v
    return merged


def estimate_print_time_hours(volume_mm3, surface_area_mm2, layer_height_mm, infill_percent, pricing_config):
    cfg = merge_pricing_config(pricing_config)
    vol_cm3 = volume_mm3 / 1000.0
    area_cm2 = surface_area_mm2 / 100.0
    overhead_min = float(cfg.get("time_overhead_min") or 0.0)
    vol_min_per_cm3 = float(cfg.get("time_vol_min_per_cm3") or 0.0)
    area_min_per_cm2 = float(cfg.get("time_area_min_per_cm2") or 0.0)
    ref_layer = float(cfg.get("time_ref_layer_height_mm") or 0.2)
    layer_exp = float(cfg.get("time_layer_height_exponent") or 1.0)
    ref_infill = float(cfg.get("time_ref_infill_percent") or 20.0)
    infill_coeff = float(cfg.get("time_infill_coefficient") or 1.0)

    layer_factor = (ref_layer / max(layer_height_mm, 0.01)) ** layer_exp
    infill_factor = 1.0 + infill_coeff * max(0.0, (infill_percent - ref_infill) / 100.0)
    total_min = overhead_min + (vol_cm3 * vol_min_per_cm3 * layer_factor * infill_factor) + (area_cm2 * area_min_per_cm2)
    return max(0.0, total_min / 60.0)

def safe_eval_formula(expr: str, variables: dict) -> Optional[float]:
    if not expr:
        return None
    if len(expr) > 800:
        return None
    allowed_funcs = {"max": max, "min": min, "abs": abs, "round": round}
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Call,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.Mod,
        ast.UAdd,
        ast.USub,
    )

    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            return None
        if isinstance(node, ast.Name):
            if node.id not in variables and node.id not in allowed_funcs:
                return None
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                return None
            if node.func.id not in allowed_funcs:
                return None
            if node.keywords:
                return None

    try:
        compiled = compile(tree, "<formula>", "eval")
        result = eval(compiled, {"__builtins__": {}, **allowed_funcs}, dict(variables))
    except Exception:
        return None
    if isinstance(result, bool):
        return None
    if isinstance(result, (int, float)):
        if not math.isfinite(float(result)):
            return None
        return float(result)
    return None

FORMULA_ALIAS_TO_CANONICAL = {
    "有效重量_g": "effective_weight_g",
    "材料单价_元每kg": "price_per_kg",
    "单件时间_h": "unit_time_h",
    "机台费_元每小时": "machine_hourly_rate_cny",
    "后处理费_元每件": "post_process_fee_per_part_cny",
    "数量": "quantity",
    "上机费": "setup_fee_cny",
    "最低起步价": "min_job_fee_cny",
    "单件成本": "unit_cost_cny",
    "小计": "subtotal_cny",
    "难度系数": "difficulty_coefficient",
    "表面积体积比": "surface_area_to_volume_ratio",
    "难度得分": "difficulty_score",
    "难度倍率": "difficulty_multiplier",
    "难度加价百分比": "difficulty_markup_percent",
    "支撑重量_g": "support_weight_g",
    "支撑单价_元每g": "support_price_per_g",
    "支撑费_元每件": "support_cost_per_part_cny",
}

FORMULA_CANONICAL_VARS = {
    "effective_weight_g",
    "model_weight_g",
    "price_per_kg",
    "density",
    "unit_time_h",
    "machine_hourly_rate_cny",
    "post_process_fee_per_part_cny",
    "difficulty_coefficient",
    "surface_area_to_volume_ratio",
    "difficulty_score",
    "difficulty_multiplier",
    "difficulty_markup_percent",
    "support_weight_g",
    "support_price_per_g",
    "support_cost_per_part_cny",
    "quantity",
    "setup_fee_cny",
    "min_job_fee_cny",
    "material_waste_percent",
    "support_percent_of_model",
    "material_cost_cny",
    "machine_cost_cny",
    "volume_mm3",
    "surface_area_mm2",
    "volume_cm3",
    "surface_area_cm2",
    "unit_cost_cny",
    "subtotal_cny",
}

def with_formula_aliases(variables: dict) -> dict:
    out = dict(variables)
    for alias, canonical in FORMULA_ALIAS_TO_CANONICAL.items():
        if canonical in out and alias not in out:
            out[alias] = out[canonical]
    return out

def validate_formula_expression(expr: str) -> tuple[bool, str, list[str]]:
    if not expr:
        return False, "公式不能为空", []
    if len(expr) > 800:
        return False, "公式过长", []

    allowed_funcs = {"max", "min", "abs", "round"}
    allowed_vars = set(FORMULA_CANONICAL_VARS) | set(FORMULA_ALIAS_TO_CANONICAL.keys())
    used_vars: set[str] = set()

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return False, "公式语法错误", []

    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Call,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.Mod,
        ast.UAdd,
        ast.USub,
    )

    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            return False, "包含不支持的语法", []
        if isinstance(node, ast.Name):
            if node.id in allowed_funcs:
                continue
            if node.id not in allowed_vars:
                return False, f"未知变量：{node.id}", []
            used_vars.add(node.id)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                return False, "仅支持调用 max/min/abs/round", []
            if node.func.id not in allowed_funcs:
                return False, f"不支持的函数：{node.func.id}", []
            if node.keywords:
                return False, "不支持关键字参数", []

    return True, "", sorted(used_vars)


def _kirimoto_sets_from_quote_params(layer_height_mm: float, infill_percent: int, perimeters: Optional[int]) -> dict[str, str]:
    # TODO: Add Kiri:Moto specific parameters mapping if necessary
    d = {}
    return d


def calculate_cost(
    volume_mm3,
    surface_area_mm2,
    material,
    layer_height_mm,
    infill_percent,
    user_materials,
    pricing_config,
    quantity,
    model_path: Optional[str] = None,
    slicer_preset: Optional[dict] = None,
    perimeters: Optional[int] = None,
    current_user: Optional[dict] = None,
):
    from main import normalize_materials, DEFAULT_MATERIALS, _sanitize_filename_component, _user_base_dir, _date_folder_utc
    materials = normalize_materials(user_materials)
    spec = next((m for m in materials if m["name"] == material), None) or DEFAULT_MATERIALS[0]
    cfg = merge_pricing_config(pricing_config)

    model_weight_g = calculate_weight(volume_mm3, material_density=spec["density"])
    waste_percent = float(cfg.get("material_waste_percent") or 0.0)
    support_percent = float(cfg.get("support_percent_of_model") or 0.0)
    effective_weight_g = model_weight_g * (1.0 + max(0.0, waste_percent) / 100.0 + max(0.0, support_percent) / 100.0)
    material_cost = effective_weight_g * (float(spec.get("price_per_kg") or 0.0) / 1000.0)

    unit_time_h = estimate_print_time_hours(volume_mm3, surface_area_mm2, layer_height_mm, infill_percent, cfg)
    machine_hourly_rate = float(cfg.get("machine_hourly_rate_cny") or 0.0)
    machine_cost = unit_time_h * machine_hourly_rate

    setup_fee = float(cfg.get("setup_fee_cny") or 0.0)
    post_per_part = float(cfg.get("post_process_fee_per_part_cny") or 0.0)
    min_job_fee = float(cfg.get("min_job_fee_cny") or 0.0)

    base_unit_cost = material_cost + machine_cost + post_per_part

    volume_cm3 = float(volume_mm3) / 1000.0
    surface_area_cm2 = float(surface_area_mm2) / 100.0
    surface_area_to_volume_ratio = 0.0
    if volume_cm3 > 0:
        surface_area_to_volume_ratio = surface_area_cm2 / max(volume_cm3, 1e-9)
    ratio_low = float(cfg.get("difficulty_ratio_low") or 0.0)
    ratio_high = float(cfg.get("difficulty_ratio_high") or 0.0)
    difficulty_score = 0.0
    if ratio_high > ratio_low:
        difficulty_score = (surface_area_to_volume_ratio - ratio_low) / (ratio_high - ratio_low)
    difficulty_score = max(0.0, min(1.0, float(difficulty_score)))
    difficulty_coefficient = float(cfg.get("difficulty_coefficient") or 0.0)
    if difficulty_coefficient < 0:
        difficulty_coefficient = 0.0
    if difficulty_coefficient > 3:
        difficulty_coefficient = 3.0
    difficulty_multiplier = 1.0 + (difficulty_coefficient * difficulty_score)
    difficulty_markup_percent = max(0.0, (difficulty_multiplier - 1.0) * 100.0)

    raw_use = cfg.get("use_kirimoto") or cfg.get("use_curaengine") or cfg.get("use_prusaslicer") # fallback to old key if any
    use_kirimoto = False
    try:
        use_kirimoto = bool(int(raw_use))
    except Exception:
        use_kirimoto = str(raw_use or "").strip().lower() in {"1", "true", "yes", "y", "on"}
    kirimoto_support_mode = str(cfg.get("kirimoto_support_mode") or cfg.get("curaengine_support_mode") or cfg.get("prusaslicer_support_mode") or "diff").strip().lower() or "diff"
    
    slicer_time_s = None
    slicer_filament_g_per_part = None
    preset_used = None
    kirimoto_error_msg = None
    if use_kirimoto and model_path and os.path.exists(model_path):
        preset_tmp_path = None
        try:
            base_name = os.path.splitext(os.path.basename(model_path))[0]
            output_prefix = _sanitize_filename_component(base_name, fallback="model", max_len=60)
            user_folder = f"user_{current_user['id']}_{current_user['username']}" if current_user else "anonymous"
            # 结构: user/user_1_admin/outputs/20260421/8f3c..._model/
            outputs_job_dir = os.path.join(_user_base_dir(), user_folder, "outputs", _date_folder_utc(), output_prefix)
            os.makedirs(outputs_job_dir, exist_ok=True)
            
            extra_loads: list[str] = []
            extra_sets = _kirimoto_sets_from_quote_params(layer_height_mm, infill_percent, perimeters)
            if slicer_preset and isinstance(slicer_preset, dict) and slicer_preset.get("content"):
                try:
                    ext = str(slicer_preset.get("ext") or ".ini").strip().lower()
                    if ext not in {".ini", ".cfg", ".json"}:
                        ext = ".cfg"
                    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tf:
                        tf.write(bytes(slicer_preset.get("content")))
                        preset_tmp_path = tf.name
                    extra_loads.append(preset_tmp_path)
                    preset_used = str(slicer_preset.get("name") or "") or None
                except Exception:
                    preset_tmp_path = None
            if kirimoto_support_mode == "diff":
                st = kirimoto_support_diff_stats(
                    model_path,
                    extra_loads=extra_loads,
                    extra_sets=extra_sets,
                    output_dir=outputs_job_dir,
                    output_prefix=output_prefix,
                )
                support_weight_g_per_part = float(st.get("support_g") or 0.0)
                slicer_time_s = int(st.get("estimated_time_s")) if st.get("estimated_time_s") is not None else None
                if st.get("filament_g") is not None:
                    try:
                        slicer_filament_g_per_part = float(st.get("filament_g") or 0.0)
                    except Exception:
                        slicer_filament_g_per_part = None
            else:
                gcode_path = os.path.join(outputs_job_dir, f"{output_prefix}.gcode")
                st = run_kirimoto_slice(model_path, gcode_path, extra_loads=extra_loads, extra_sets=extra_sets)
                slicer_time_s = int(st.get("estimated_time_s")) if st.get("estimated_time_s") is not None else None
                if st.get("filament_g") is not None:
                    try:
                        slicer_filament_g_per_part = float(st.get("filament_g") or 0.0)
                    except Exception:
                        slicer_filament_g_per_part = None
        except Exception as e:
            logger.error(f"Kiri:Moto failed for {model_path}: {e}")
            # fallback to base estimation if slicer fails
            slicer_time_s = None
            slicer_filament_g_per_part = None
            kirimoto_error_msg = str(e)
        finally:
            try:
                if preset_tmp_path and os.path.exists(preset_tmp_path):
                    os.remove(preset_tmp_path)
            except Exception:
                pass
    if slicer_filament_g_per_part is not None and slicer_filament_g_per_part > 0:
        support_percent = 0.0
        effective_weight_g = float(slicer_filament_g_per_part) * (1.0 + max(0.0, waste_percent) / 100.0)
        material_cost = effective_weight_g * (float(spec.get("price_per_kg") or 0.0) / 1000.0)
    if slicer_time_s is not None and slicer_time_s > 0:
        unit_time_h = float(slicer_time_s) / 3600.0
        machine_cost = unit_time_h * machine_hourly_rate
        base_unit_cost = material_cost + machine_cost + post_per_part

    support_price_per_g = float(cfg.get("support_price_per_g") or 0.0)
    if support_price_per_g < 0:
        support_price_per_g = 0.0
    if support_price_per_g > 1000:
        support_price_per_g = 1000.0
    support_cost_per_part_cny = float(support_weight_g_per_part) * float(support_price_per_g)

    variables = {
        "effective_weight_g": float(effective_weight_g),
        "model_weight_g": float(model_weight_g),
        "price_per_kg": float(spec.get("price_per_kg") or 0.0),
        "density": float(spec.get("density") or 1.0),
        "unit_time_h": float(unit_time_h),
        "machine_hourly_rate_cny": float(machine_hourly_rate),
        "post_process_fee_per_part_cny": float(post_per_part),
        "difficulty_coefficient": float(difficulty_coefficient),
        "surface_area_to_volume_ratio": float(surface_area_to_volume_ratio),
        "difficulty_score": float(difficulty_score),
        "difficulty_multiplier": float(difficulty_multiplier),
        "difficulty_markup_percent": float(difficulty_markup_percent),
        "support_weight_g": float(support_weight_g_per_part),
        "support_price_per_g": float(support_price_per_g),
        "support_cost_per_part_cny": float(support_cost_per_part_cny),
        "quantity": float(quantity),
        "setup_fee_cny": float(setup_fee),
        "min_job_fee_cny": float(min_job_fee),
        "material_waste_percent": float(waste_percent),
        "support_percent_of_model": float(support_percent),
        "material_cost_cny": float(material_cost),
        "machine_cost_cny": float(machine_cost),
        "volume_mm3": float(volume_mm3),
        "surface_area_mm2": float(surface_area_mm2),
        "volume_cm3": float(volume_cm3),
        "surface_area_cm2": float(surface_area_cm2),
    }

    unit_formula = str(cfg.get("unit_cost_formula") or DEFAULT_UNIT_COST_FORMULA).strip()
    total_formula = str(cfg.get("total_cost_formula") or DEFAULT_TOTAL_COST_FORMULA).strip()

    unit_cost = safe_eval_formula(unit_formula, variables)
    if unit_cost is None or unit_cost < 0:
        unit_cost = (base_unit_cost * float(difficulty_multiplier)) + float(support_cost_per_part_cny)

    unit_cost_before_difficulty = float(base_unit_cost)

    subtotal = (unit_cost * quantity) + setup_fee
    variables["unit_cost_cny"] = float(unit_cost)
    variables["subtotal_cny"] = float(subtotal)
    variables = with_formula_aliases(variables)

    total = safe_eval_formula(total_formula, variables)
    if total is None or total < 0:
        total = max(subtotal, min_job_fee)
    total_time_h = unit_time_h * quantity

    breakdown = {
        "material_cost_cny": round(material_cost, 2),
        "machine_cost_cny": round(machine_cost, 2),
        "post_process_cost_per_part_cny": round(post_per_part, 2),
        "difficulty_surface_area_to_volume_ratio": round(surface_area_to_volume_ratio, 6),
        "difficulty_score": round(difficulty_score, 4),
        "difficulty_coefficient": round(difficulty_coefficient, 4),
        "difficulty_multiplier": round(difficulty_multiplier, 6),
        "difficulty_markup_percent": round(max(0.0, (difficulty_multiplier - 1.0) * 100.0), 2),
        "unit_cost_before_difficulty_cny": round(unit_cost_before_difficulty, 2),
        "support_weight_g_per_part": round(support_weight_g_per_part, 3),
        "support_price_per_g": round(support_price_per_g, 4),
        "support_cost_per_part_cny": round(support_cost_per_part_cny, 2),
        "kirimoto_used": bool(use_kirimoto and slicer_time_s is not None),
        "kirimoto_error": kirimoto_error_msg,
        "kirimoto_filament_g_per_part": round(float(slicer_filament_g_per_part), 3) if slicer_filament_g_per_part is not None else None,
        "kirimoto_preset_used": preset_used,
        "kirimoto_sets": _kirimoto_sets_from_quote_params(layer_height_mm, infill_percent, perimeters) if use_kirimoto else {},
        "kirimoto_estimated_time_s": int(slicer_time_s) if slicer_time_s is not None else None,
        "setup_fee_cny": round(setup_fee, 2),
        "min_job_fee_cny": round(min_job_fee, 2),
        "subtotal_cny": round(subtotal, 2),
        "unit_cost_formula": unit_formula,
        "total_cost_formula": total_formula,
    }

    return round(unit_cost, 2), round(model_weight_g, 2), round(unit_time_h, 3), round(total, 2), round(effective_weight_g, 2), round(total_time_h, 3), breakdown


async def process_single_file(
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
    from main import SUPPORTED_EXTENSIONS, MAX_FILE_SIZE_BYTES, _sanitize_filename_component, _user_base_dir, _date_folder_utc
    from parser.geometry import calculate_geometry

    filename = file.filename or "unnamed_file"
    _, ext = os.path.splitext(filename.lower())
    if ext not in SUPPORTED_EXTENSIONS:
        return {
            "filename": filename,
            "status": "failed",
            "error": f"不支持的文件格式: {ext}。支持: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        }

    file_content = await file.read()
    if len(file_content) >= MAX_FILE_SIZE_BYTES:
        return {
            "filename": filename,
            "status": "failed",
            "error": "文件大小必须小于 100MB"
        }

    try:
        safe_original = os.path.basename(filename)
        original_stem = os.path.splitext(safe_original)[0]
        safe_stem = _sanitize_filename_component(original_stem, fallback="model", max_len=80)
        job_id = uuid.uuid4().hex
        
        user_folder = f"user_{current_user['id']}_{current_user['username']}" if current_user else "anonymous"
        # 结构: user/user_1_admin/uploads/20260421/8f3c..._model/8f3c..._model.stl
        uploads_day_dir = os.path.join(_user_base_dir(), user_folder, "uploads", _date_folder_utc(), f"{job_id}_{safe_stem}")
        os.makedirs(uploads_day_dir, exist_ok=True)
        
        saved_name = f"{job_id}_{safe_stem}{ext}"
        model_saved_path = os.path.join(uploads_day_dir, saved_name)
        with open(model_saved_path, "wb") as f:
            f.write(bytes(file_content))

        volume, surface_area, dimensions = calculate_geometry(model_saved_path)
        if volume == 0:
            return {
                "filename": filename,
                "status": "failed",
                "error": "无法读取或计算模型体积，可能文件已损坏 (Failed to calculate volume)"
            }

        unit_cost, model_weight_g, unit_print_time_h, total_cost, effective_weight_g, total_print_time_h, breakdown = calculate_cost(
            volume,
            surface_area,
            material,
            layer_height,
            infill,
            user_materials,
            pricing_config,
            quantity,
            model_path=model_saved_path,
            slicer_preset=slicer_preset,
            perimeters=perimeters,
            current_user=current_user,
        )
        total_weight = round(model_weight_g * quantity, 2)
        try:
            filament_g = None
            if isinstance(breakdown, dict):
                filament_g = breakdown.get("kirimoto_filament_g_per_part")
            if filament_g is not None:
                total_weight = round(float(filament_g) * quantity, 2)
        except Exception:
            pass

        dimensions_str = f"{dimensions['x']} × {dimensions['y']} × {dimensions['z']} mm"

        return {
            "filename": filename,
            "status": "success",
            "volume_cm3": round(volume / 1000, 2),
            "surface_area_cm2": round(surface_area / 100, 2),
            "surface_area_to_volume_ratio": round(float((breakdown or {}).get("difficulty_surface_area_to_volume_ratio") or 0.0), 6),
            "difficulty_score": round(float((breakdown or {}).get("difficulty_score") or 0.0), 4),
            "difficulty_multiplier": round(float((breakdown or {}).get("difficulty_multiplier") or 1.0), 6),
            "difficulty_markup_percent": round(float((breakdown or {}).get("difficulty_markup_percent") or 0.0), 2),
            "dimensions": dimensions_str,
            "weight_g": total_weight,
            "estimated_time_h": total_print_time_h,
            "cost_cny": total_cost,
            "unit_cost_cny": unit_cost,
            "quantity": quantity,
            "color": color,
            "material": material,
            "layer_height": layer_height,
            "infill": infill,
            "cost_breakdown": breakdown,
            "effective_weight_g": round(effective_weight_g * quantity, 2)
        }
    except Exception as e:
        msg = str(e or "").strip()
        if len(msg) > 200:
            msg = msg[:200]
        return {
            "filename": filename,
            "status": "failed",
            "error": msg or "处理失败"
        }
