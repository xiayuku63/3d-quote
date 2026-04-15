import os
import sqlite3
import tempfile
import json
import ast
import math
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import trimesh
from jose import JWTError, jwt
import bcrypt
from pydantic import BaseModel, Field
from fastapi import Depends, FastAPI, UploadFile, File, Form, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer

app = FastAPI(title="3D Printing Quoting System DEMO")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"detail": "输入参数有误，请检查用户名(至少3位)或密码(至少6位)"},
    )

# Ensure static directory exists
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPPORTED_EXTENSIONS = {".stl", ".stp", ".step", ".obj", ".3mf"}
MAX_FILES_PER_REQUEST = 20
MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024
DB_PATH = "app.db"
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-this-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)


def get_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

DEFAULT_COLORS = ["White", "Black", "Gray", "Red", "Blue"]
DEFAULT_MATERIALS = [
    {"name": "PLA", "density": 1.24, "price_per_kg": 200.0, "colors": DEFAULT_COLORS},
    {"name": "ABS", "density": 1.04, "price_per_kg": 250.0, "colors": DEFAULT_COLORS},
    {"name": "Resin", "density": 1.11, "price_per_kg": 800.0, "colors": DEFAULT_COLORS},
]
DEFAULT_UNIT_COST_FORMULA = "(effective_weight_g * (price_per_kg / 1000.0)) + (unit_time_h * machine_hourly_rate_cny) + post_process_fee_per_part_cny"
DEFAULT_TOTAL_COST_FORMULA = "max((unit_cost_cny * quantity) + setup_fee_cny, min_job_fee_cny)"
DEFAULT_PRICING_CONFIG = {
    "machine_hourly_rate_cny": 15.0,
    "setup_fee_cny": 0.0,
    "min_job_fee_cny": 0.0,
    "material_waste_percent": 5.0,
    "support_percent_of_model": 0.0,
    "post_process_fee_per_part_cny": 0.0,
    "time_overhead_min": 5.0,
    "time_vol_min_per_cm3": 0.8,
    "time_area_min_per_cm2": 0.0,
    "time_ref_layer_height_mm": 0.2,
    "time_layer_height_exponent": 1.0,
    "time_ref_infill_percent": 20.0,
    "time_infill_coefficient": 1.0,
    "unit_cost_formula": DEFAULT_UNIT_COST_FORMULA,
    "total_cost_formula": DEFAULT_TOTAL_COST_FORMULA,
}


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


def init_db() -> None:
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        try:
            conn.execute("ALTER TABLE users ADD COLUMN materials TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN colors TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN pricing_config TEXT")
        except sqlite3.OperationalError:
            pass
        conn.commit()


def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def verify_password(plain_password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), password_hash.encode('utf-8'))


def create_access_token(user_id: int, username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "username": username, "exp": expire}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def get_user_by_username(username: str):
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    return row


def get_user_by_id(user_id: int):
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT id, username, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return row


def create_user(username: str, password: str):
    password_hash = get_password_hash(password)
    created_at = datetime.now(timezone.utc).isoformat()
    materials_json = json.dumps(DEFAULT_MATERIALS)
    colors_json = json.dumps(DEFAULT_COLORS)
    pricing_json = json.dumps(DEFAULT_PRICING_CONFIG)
    try:
        with get_db_conn() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at, materials, colors, pricing_config) VALUES (?, ?, ?, ?, ?, ?)",
                (username, password_hash, created_at, materials_json, colors_json, pricing_json),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="用户名已存在")

    user = get_user_by_username(username)
    return user


def authenticate_user(username: str, password: str):
    user = get_user_by_username(username)
    if not user:
        raise HTTPException(status_code=401, detail="该用户名未注册")
    if not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="密码错误")
    return user


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


def calculate_geometry(model_path):
    """Calculate model geometry (volume, surface_area, dimensions)"""
    try:
        # trimesh can parse multiple 3D file formats
        mesh = trimesh.load(model_path, force="mesh")
        
        # In case the file contains multiple bodies
        if isinstance(mesh, trimesh.Scene):
            # concatenate all geometries
            geom = mesh.dump()
            mesh = trimesh.util.concatenate(geom)
            
        volume = mesh.volume
        surface_area = mesh.area
        
        # Calculate dimensions from bounding box
        extents = mesh.extents  # [x, y, z] array of lengths
        dimensions = {
            "x": round(extents[0], 2),
            "y": round(extents[1], 2),
            "z": round(extents[2], 2)
        }
        
        # If the mesh is not watertight (e.g., holes, inverted normals), volume might be None or 0
        if not volume or volume <= 0:
            # Fallback: attempt to calculate convex hull volume or use bounding box
            if mesh.convex_hull.volume > 0:
                volume = mesh.convex_hull.volume
                print("Warning: Mesh is not watertight, using convex hull volume as fallback.")
            else:
                volume = 0
                
        return volume, surface_area, dimensions
    except Exception as e:
        print(f"Error reading model with trimesh: {e}")
        return 0, 0, {"x": 0, "y": 0, "z": 0}

def calculate_weight(volume, material_density):
    """Calculate weight (unit: g)"""
    return volume * material_density / 1000  # mm³ -> cm³ -> g

def merge_pricing_config(raw_config):
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
}

FORMULA_CANONICAL_VARS = {
    "effective_weight_g",
    "model_weight_g",
    "price_per_kg",
    "density",
    "unit_time_h",
    "machine_hourly_rate_cny",
    "post_process_fee_per_part_cny",
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


def calculate_cost(volume_mm3, surface_area_mm2, material, layer_height_mm, infill_percent, user_materials, pricing_config, quantity):
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

    variables = {
        "effective_weight_g": float(effective_weight_g),
        "model_weight_g": float(model_weight_g),
        "price_per_kg": float(spec.get("price_per_kg") or 0.0),
        "density": float(spec.get("density") or 1.0),
        "unit_time_h": float(unit_time_h),
        "machine_hourly_rate_cny": float(machine_hourly_rate),
        "post_process_fee_per_part_cny": float(post_per_part),
        "quantity": float(quantity),
        "setup_fee_cny": float(setup_fee),
        "min_job_fee_cny": float(min_job_fee),
        "material_waste_percent": float(waste_percent),
        "support_percent_of_model": float(support_percent),
        "material_cost_cny": float(material_cost),
        "machine_cost_cny": float(machine_cost),
        "volume_mm3": float(volume_mm3),
        "surface_area_mm2": float(surface_area_mm2),
        "volume_cm3": float(volume_mm3) / 1000.0,
        "surface_area_cm2": float(surface_area_mm2) / 100.0,
    }

    unit_formula = str(cfg.get("unit_cost_formula") or DEFAULT_UNIT_COST_FORMULA).strip()
    total_formula = str(cfg.get("total_cost_formula") or DEFAULT_TOTAL_COST_FORMULA).strip()

    unit_cost = safe_eval_formula(unit_formula, variables)
    if unit_cost is None or unit_cost < 0:
        unit_cost = base_unit_cost

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
    user_materials: list,
    pricing_config: dict
):
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

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(file_content)
        tmp_path = tmp.name

    try:
        volume, surface_area, dimensions = calculate_geometry(tmp_path)
        if volume == 0:
            return {
                "filename": filename,
                "status": "failed",
                "error": "无法读取或计算模型体积，可能文件已损坏 (Failed to calculate volume)"
            }

        unit_cost, model_weight_g, unit_print_time_h, total_cost, effective_weight_g, total_print_time_h, breakdown = calculate_cost(
            volume, surface_area, material, layer_height, infill, user_materials, pricing_config, quantity
        )
        total_weight = round(model_weight_g * quantity, 2)

        dimensions_str = f"{dimensions['x']} × {dimensions['y']} × {dimensions['z']} mm"

        return {
            "filename": filename,
            "status": "success",
            "volume_cm3": round(volume / 1000, 2),
            "surface_area_cm2": round(surface_area / 100, 2),
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
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.on_event("startup")
def on_startup():
    init_db()


@app.post("/api/auth/register")
def register(payload: RegisterRequest):
    username = payload.username.strip()
    password = payload.password
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")

    user = create_user(username, password)
    access_token = create_access_token(user_id=user["id"], username=user["username"])
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "created_at": user["created_at"],
        },
    }


@app.post("/api/auth/login")
def login(payload: LoginRequest):
    username = payload.username.strip()
    password = payload.password
    user = authenticate_user(username, password)

    access_token = create_access_token(user_id=user["id"], username=user["username"])
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "created_at": user["created_at"],
        },
    }


class MaterialItem(BaseModel):
    name: str
    density: float
    price_per_kg: float
    colors: List[str] = []

class PricingConfig(BaseModel):
    machine_hourly_rate_cny: float = 15.0
    setup_fee_cny: float = 0.0
    min_job_fee_cny: float = 0.0
    material_waste_percent: float = 5.0
    support_percent_of_model: float = 0.0
    post_process_fee_per_part_cny: float = 0.0
    time_overhead_min: float = 5.0
    time_vol_min_per_cm3: float = 0.8
    time_area_min_per_cm2: float = 0.0
    time_ref_layer_height_mm: float = 0.2
    time_layer_height_exponent: float = 1.0
    time_ref_infill_percent: float = 20.0
    time_infill_coefficient: float = 1.0
    unit_cost_formula: str = DEFAULT_UNIT_COST_FORMULA
    total_cost_formula: str = DEFAULT_TOTAL_COST_FORMULA

class UserSettingsUpdate(BaseModel):
    materials: List[MaterialItem]
    colors: Optional[List[str]] = None
    pricing_config: Optional[PricingConfig] = None

@app.get("/api/user/settings")
def get_user_settings(current_user=Depends(get_current_user)):
    with get_db_conn() as conn:
        row = conn.execute("SELECT materials, colors, pricing_config FROM users WHERE id = ?", (current_user["id"],)).fetchone()
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

@app.put("/api/user/settings")
def update_user_settings(payload: UserSettingsUpdate, current_user=Depends(get_current_user)):
    materials_json = json.dumps([m.dict() for m in payload.materials])
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
        pricing_json = json.dumps(payload.pricing_config.dict())
    with get_db_conn() as conn:
        if pricing_json is None:
            conn.execute("UPDATE users SET materials = ?, colors = ? WHERE id = ?", (materials_json, colors_json, current_user["id"]))
        else:
            conn.execute("UPDATE users SET materials = ?, colors = ?, pricing_config = ? WHERE id = ?", (materials_json, colors_json, pricing_json, current_user["id"]))
        conn.commit()
    return {"status": "success"}

@app.get("/api/auth/me")
def auth_me(current_user=Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "username": current_user["username"],
        "created_at": current_user["created_at"],
    }

@app.post("/api/quote")
async def get_quote(
    files: List[UploadFile] = File(...),
    material: str = Form("PLA"),
    layer_height: float = Form(0.2),
    infill: int = Form(20),
    quantity: int = Form(1),
    color: str = Form("White"),
    current_user=Depends(get_current_user),
):
    if quantity < 1:
        raise HTTPException(status_code=400, detail="数量必须大于等于 1")
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个模型文件")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(status_code=400, detail=f"单次上传文件数量不能超过 {MAX_FILES_PER_REQUEST} 个")

    with get_db_conn() as conn:
        row = conn.execute("SELECT materials, pricing_config FROM users WHERE id = ?", (current_user["id"],)).fetchone()
    user_materials = json.loads(row["materials"]) if row and row["materials"] else DEFAULT_MATERIALS
    pricing_config = json.loads(row["pricing_config"]) if row and row["pricing_config"] else DEFAULT_PRICING_CONFIG

    results = []
    for file in files:
        result = await process_single_file(file, material, layer_height, infill, quantity, color, user_materials, pricing_config)
        results.append(result)

    success_items = [item for item in results if item["status"] == "success"]
    failed_items = [item for item in results if item["status"] == "failed"]

    return {
        "total_files": len(results),
        "success_count": len(success_items),
        "failed_count": len(failed_items),
        "summary_total_cost_cny": round(sum(item.get("cost_cny", 0) for item in success_items), 2),
        "summary_total_weight_g": round(sum(item.get("weight_g", 0) for item in success_items), 2),
        "summary_total_time_h": round(sum(item.get("estimated_time_h", 0) for item in success_items), 2),
        "results": results
    }


class FormulaValidateRequest(BaseModel):
    unit_cost_formula: str
    total_cost_formula: str


@app.post("/api/formula/validate")
def validate_formula(payload: FormulaValidateRequest, current_user=Depends(get_current_user)):
    unit_ok, unit_err, unit_vars = validate_formula_expression(payload.unit_cost_formula)
    total_ok, total_err, total_vars = validate_formula_expression(payload.total_cost_formula)
    ok = unit_ok and total_ok
    return {
        "ok": ok,
        "unit": {"ok": unit_ok, "error": unit_err, "used_vars": unit_vars},
        "total": {"ok": total_ok, "error": total_err, "used_vars": total_vars},
        "aliases": FORMULA_ALIAS_TO_CANONICAL,
    }

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()
