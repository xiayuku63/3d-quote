import os
import sqlite3
import tempfile
import json
import ast
import math
import re
import logging
import threading
import time
import secrets
import base64
import hashlib
import hmac
import io
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import trimesh
from jose import JWTError, jwt
import bcrypt
from pydantic import BaseModel, Field
from fastapi import Depends, FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer

app = FastAPI(title="3D Printing Quoting System DEMO")
logger = logging.getLogger("uvicorn.error")

APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

DEFAULT_ALLOWED_ORIGINS = [
    "https://www.pricer3d.top",
    "https://pricer3d.top",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", ",".join(DEFAULT_ALLOWED_ORIGINS)).split(",")
    if origin.strip()
]

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "").strip()
if not JWT_SECRET_KEY:
    if IS_PRODUCTION:
        raise RuntimeError("生产环境必须设置 JWT_SECRET_KEY")
    JWT_SECRET_KEY = "dev-only-insecure-secret-change-me"

PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "mock").strip().lower() or "mock"
PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "").strip()
if not PAYMENT_WEBHOOK_SECRET:
    if IS_PRODUCTION:
        raise RuntimeError("生产环境必须设置 PAYMENT_WEBHOOK_SECRET")
    PAYMENT_WEBHOOK_SECRET = "dev-only-payment-webhook-secret-change-me"

TERMS_VERSION = os.getenv("TERMS_VERSION", "v1").strip() or "v1"
PRIVACY_VERSION = os.getenv("PRIVACY_VERSION", "v1").strip() or "v1"

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"detail": "输入参数不合法，请检查后重试"},
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled server error on path %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试"},
    )

# Ensure static directory exists
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
)

SUPPORTED_EXTENSIONS = {".stl", ".stp", ".step", ".obj", ".3mf"}
MAX_FILES_PER_REQUEST = 20
MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024
DB_PATH = "app.db"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")
PHONE_PATTERN = re.compile(r"^\+?[0-9]{7,15}$")
PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 100
AUTH_RATE_LIMIT_PER_MIN = int(os.getenv("AUTH_RATE_LIMIT_PER_MIN", "12"))
QUOTE_RATE_LIMIT_PER_MIN = int(os.getenv("QUOTE_RATE_LIMIT_PER_MIN", "30"))
CAPTCHA_RATE_LIMIT_PER_MIN = int(os.getenv("CAPTCHA_RATE_LIMIT_PER_MIN", "60"))
CAPTCHA_TTL_SECONDS = int(os.getenv("CAPTCHA_TTL_SECONDS", "180"))
CAPTCHA_LENGTH = int(os.getenv("CAPTCHA_LENGTH", "4"))
CAPTCHA_MAX_ATTEMPTS = int(os.getenv("CAPTCHA_MAX_ATTEMPTS", "5"))
VERIFY_CODE_TTL_SECONDS = int(os.getenv("VERIFY_CODE_TTL_SECONDS", "600"))
VERIFY_CODE_MAX_ATTEMPTS = int(os.getenv("VERIFY_CODE_MAX_ATTEMPTS", "6"))
VERIFY_SEND_RATE_LIMIT_PER_10MIN = int(os.getenv("VERIFY_SEND_RATE_LIMIT_PER_10MIN", "6"))
VERIFY_SEND_COOLDOWN_SECONDS = int(os.getenv("VERIFY_SEND_COOLDOWN_SECONDS", "60"))
IDEMPOTENCY_TTL_SECONDS = int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "86400"))
MEMBER_DISCOUNT_PERCENT = float(os.getenv("MEMBER_DISCOUNT_PERCENT", "0"))
LOGIN_FAILED_MAX_ATTEMPTS = int(os.getenv("LOGIN_FAILED_MAX_ATTEMPTS", "6"))
LOGIN_FAILED_WINDOW_SECONDS = int(os.getenv("LOGIN_FAILED_WINDOW_SECONDS", "900"))
LOGIN_LOCK_SECONDS = int(os.getenv("LOGIN_LOCK_SECONDS", "900"))
AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "90"))
METRICS_MAX_EVENTS = int(os.getenv("METRICS_MAX_EVENTS", "2000"))
ADMIN_USERNAMES = {
    x.strip().lower()
    for x in os.getenv("ADMIN_USERNAMES", "admin").split(",")
    if x.strip()
}


class SimpleRateLimiter:
    def __init__(self):
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def is_allowed(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now = time.time()
        with self._lock:
            bucket = self._buckets[key]
            while bucket and (now - bucket[0]) > window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True


rate_limiter = SimpleRateLimiter()


class InMemoryMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._events: deque[tuple[float, str, int, float]] = deque()
        self._per_path: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0.0, "5xx": 0.0, "lat_sum": 0.0})

    def _normalize_path(self, path: str) -> str:
        p = (path or "").strip() or "/"
        if p.startswith("/api/auth/captcha/image/"):
            return "/api/auth/captcha/image/:id"
        if p.startswith("/static/"):
            return "/static/*"
        if re.fullmatch(r"/api/[^/]+/[^/]{16,}", p):
            return re.sub(r"/[^/]{16,}$", "/:id", p)
        return p

    def record(self, path: str, status_code: int, duration_ms: float) -> None:
        now = time.time()
        norm = self._normalize_path(path)
        sc = int(status_code or 0)
        dur = float(duration_ms or 0.0)
        with self._lock:
            self._events.append((now, norm, sc, dur))
            while len(self._events) > max(50, METRICS_MAX_EVENTS):
                self._events.popleft()
            slot = self._per_path[norm]
            slot["count"] += 1.0
            slot["lat_sum"] += dur
            if sc >= 500:
                slot["5xx"] += 1.0

    def snapshot(self) -> dict:
        with self._lock:
            events = list(self._events)
            per_path = {k: dict(v) for k, v in self._per_path.items()}
            started_at = float(self._started_at)
        total = len(events)
        latencies = [e[3] for e in events if e[3] >= 0]
        latencies.sort()
        p95 = 0.0
        if latencies:
            idx = max(0, min(len(latencies) - 1, int(len(latencies) * 0.95) - 1))
            p95 = float(latencies[idx])
        counts = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
        for _, _, sc, _ in events:
            if 200 <= sc < 300:
                counts["2xx"] += 1
            elif 300 <= sc < 400:
                counts["3xx"] += 1
            elif 400 <= sc < 500:
                counts["4xx"] += 1
            elif sc >= 500:
                counts["5xx"] += 1
        top_paths = sorted(
            [
                {
                    "path": p,
                    "count": int(v.get("count") or 0),
                    "errors_5xx": int(v.get("5xx") or 0),
                    "avg_latency_ms": round((float(v.get("lat_sum") or 0.0) / max(1.0, float(v.get("count") or 1.0))), 2),
                }
                for p, v in per_path.items()
            ],
            key=lambda x: (-x["count"], x["path"]),
        )[:25]
        avg_latency_ms = round(sum(latencies) / max(1, len(latencies)), 2) if latencies else 0.0
        uptime_s = round(time.time() - started_at, 2)
        return {
            "uptime_seconds": uptime_s,
            "events_tracked": total,
            "counts": counts,
            "avg_latency_ms": avg_latency_ms,
            "p95_latency_ms": round(p95, 2),
            "top_paths": top_paths,
        }


metrics = InMemoryMetrics()


class CaptchaStore:
    def __init__(self):
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def put(self, captcha_id: str, answer: str, expires_at: float, image_bytes: bytes, image_content_type: str) -> None:
        hashed = hashlib.sha256((answer + JWT_SECRET_KEY).encode("utf-8")).hexdigest()
        with self._lock:
            self._items[captcha_id] = {
                "h": hashed,
                "e": float(expires_at),
                "a": 0,
                "b": bytes(image_bytes),
                "ct": str(image_content_type),
            }

    def get_image(self, captcha_id: str) -> tuple[Optional[bytes], Optional[str]]:
        now = time.time()
        with self._lock:
            item = self._items.get(captcha_id)
            if not item:
                return None, None
            if now > float(item.get("e") or 0):
                self._items.pop(captcha_id, None)
                return None, None
            raw = item.get("b")
            ct = item.get("ct")
            if not raw or not ct:
                return None, None
            return bytes(raw), str(ct)

    def verify(self, captcha_id: str, code: str) -> bool:
        now = time.time()
        with self._lock:
            item = self._items.get(captcha_id)
            if not item:
                return False
            if now > float(item.get("e") or 0):
                self._items.pop(captcha_id, None)
                return False
            attempts = int(item.get("a") or 0) + 1
            item["a"] = attempts
            if attempts > CAPTCHA_MAX_ATTEMPTS:
                self._items.pop(captcha_id, None)
                return False
            expected = str(item.get("h") or "")
            supplied = hashlib.sha256((str(code or "").strip().upper() + JWT_SECRET_KEY).encode("utf-8")).hexdigest()
            if supplied != expected:
                return False
            self._items.pop(captcha_id, None)
            return True


captcha_store = CaptchaStore()


def _captcha_alphabet() -> str:
    return "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_captcha_text(length: int) -> str:
    length = max(4, min(int(length), 8))
    alphabet = _captcha_alphabet()
    return "".join(secrets.choice(alphabet) for _ in range(length))


def captcha_image_bytes(text: str) -> tuple[str, bytes]:
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except Exception:
        svg = captcha_svg_fallback(text)
        return "image/svg+xml", svg.encode("utf-8")

    rnd = secrets.SystemRandom()
    font_size = 30
    char_step = 28
    pad = 18
    width = max(150, (len(text) * char_step) + (pad * 2))
    height = 56
    img = Image.new("RGB", (width, height), (248, 250, 252))
    draw = ImageDraw.Draw(img)
    for _ in range(6):
        x1 = rnd.randint(0, width)
        y1 = rnd.randint(0, height)
        x2 = rnd.randint(0, width)
        y2 = rnd.randint(0, height)
        color = (100, 116, 139)
        draw.line((x1, y1, x2, y2), fill=color, width=1)

    for _ in range(160):
        x = rnd.randint(0, width - 1)
        y = rnd.randint(0, height - 1)
        draw.point((x, y), fill=(226, 232, 240))

    font = None
    font_candidates = [
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arial.ttf"),
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "segoeui.ttf"),
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for fp in font_candidates:
        try:
            if fp and (fp.startswith("/") or fp.startswith("\\") or ":" in fp):
                if not os.path.exists(fp):
                    continue
            font = ImageFont.truetype(fp, font_size)
            break
        except Exception:
            font = None
    if font is None:
        font = ImageFont.load_default()

    start_x = (width - (len(text) * char_step)) // 2
    for idx, ch in enumerate(text):
        x = 12 + idx * 22 + rnd.randint(-1, 2)
        x = int(start_x + idx * char_step + rnd.randint(-1, 1))
        y = int((height - font_size) // 2 + rnd.randint(-2, 2))
        angle = rnd.randint(-16, 16)
        glyph = Image.new("RGBA", (char_step + 14, height), (0, 0, 0, 0))
        glyph_draw = ImageDraw.Draw(glyph)
        glyph_draw.text((7, y), ch, font=font, fill=(17, 24, 39, 255))
        glyph = glyph.rotate(angle, resample=Image.Resampling.BICUBIC, expand=1)
        img.paste(glyph, (x, 0), glyph)
    img = img.filter(ImageFilter.SMOOTH_MORE)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "image/png", buf.getvalue()



def captcha_svg_fallback(text: str) -> str:
    rnd = secrets.SystemRandom()
    font_size = 24
    char_step = 26
    pad = 18
    width = max(150, (len(text) * char_step) + (pad * 2))
    height = 56
    start_x = (width - (len(text) * char_step)) // 2
    bg1 = "#f8fafc"
    bg2 = "#eef2ff"
    fg = "#111827"
    lines = []
    for _ in range(6):
        x1 = rnd.randint(0, width)
        y1 = rnd.randint(0, height)
        x2 = rnd.randint(0, width)
        y2 = rnd.randint(0, height)
        alpha = rnd.uniform(0.08, 0.18)
        lines.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#64748b" stroke-opacity="{alpha:.2f}" stroke-width="1"/>')
    chars = []
    for idx, ch in enumerate(text):
        x = int(start_x + idx * char_step + rnd.randint(-1, 1))
        y = 36 + rnd.randint(-2, 2)
        rot = rnd.randint(-16, 16)
        size = font_size + rnd.randint(-1, 2)
        chars.append(
            f'<text x="{x}" y="{y}" font-size="{size}" font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial" font-weight="700" fill="{fg}" transform="rotate({rot} {x} {y})">{ch}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="{bg1}"/><stop offset="100%" stop-color="{bg2}"/></linearGradient></defs>'
        f'<rect width="{width}" height="{height}" rx="8" fill="url(#g)"/>'
        + "".join(lines)
        + "".join(chars)
        + "</svg>"
    )


def verify_captcha_or_raise(captcha_id: str, code: str) -> None:
    if not captcha_id or not code:
        raise HTTPException(status_code=400, detail="请先完成验证码验证")
    ok = captcha_store.verify(str(captcha_id).strip(), str(code).strip())
    if not ok:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")


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


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex
    path = request.url.path
    method = request.method.upper()
    client_ip = get_client_ip(request)

    if path in {"/api/auth/login", "/api/auth/register"} and method == "POST":
        if not rate_limiter.is_allowed(f"auth:{client_ip}", AUTH_RATE_LIMIT_PER_MIN):
            resp = JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})
            resp.headers["X-Request-ID"] = request.state.request_id
            return resp
    if path == "/api/auth/register/check" and method == "POST":
        if not rate_limiter.is_allowed(f"auth_check:{client_ip}", AUTH_RATE_LIMIT_PER_MIN):
            resp = JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})
            resp.headers["X-Request-ID"] = request.state.request_id
            return resp
    if path == "/api/auth/verify/send" and method == "POST":
        if not rate_limiter.is_allowed(f"verify_send_ip:{client_ip}", VERIFY_SEND_RATE_LIMIT_PER_10MIN, window_seconds=600):
            resp = JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})
            resp.headers["X-Request-ID"] = request.state.request_id
            return resp
    if path == "/api/auth/captcha" and method == "GET":
        if not rate_limiter.is_allowed(f"captcha:{client_ip}", CAPTCHA_RATE_LIMIT_PER_MIN):
            resp = JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})
            resp.headers["X-Request-ID"] = request.state.request_id
            return resp
    if path == "/api/quote" and method == "POST":
        if not rate_limiter.is_allowed(f"quote:{client_ip}", QUOTE_RATE_LIMIT_PER_MIN):
            resp = JSONResponse(status_code=429, content={"detail": "报价请求过于频繁，请稍后再试"})
            resp.headers["X-Request-ID"] = request.state.request_id
            return resp

    started = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - started) * 1000.0
    try:
        metrics.record(path=path, status_code=int(getattr(response, "status_code", 0) or 0), duration_ms=duration_ms)
    except Exception:
        pass
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)
    register_channel: str = Field(..., min_length=4, max_length=10)
    email: Optional[str] = Field(default=None, min_length=3, max_length=254)
    phone: Optional[str] = Field(default=None, min_length=7, max_length=20)
    email_code: Optional[str] = Field(default=None, min_length=4, max_length=10)
    phone_code: Optional[str] = Field(default=None, min_length=4, max_length=10)
    captcha_id: str = Field(..., min_length=8, max_length=80)
    captcha_code: str = Field(..., min_length=4, max_length=10)
    accept_terms: bool
    accept_privacy: bool


class LoginRequest(BaseModel):
    identifier: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=6, max_length=100)
    captcha_id: str = Field(..., min_length=8, max_length=80)
    captcha_code: str = Field(..., min_length=4, max_length=10)
    accept_terms: bool
    accept_privacy: bool


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
        try:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN phone TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN phone_verified INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN membership_level TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN membership_expires_at TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN terms_accepted_at TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN privacy_accepted_at TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN terms_version TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN privacy_version TEXT")
        except sqlite3.OperationalError:
            pass

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS verification_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                target TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                used_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_verification_codes_target ON verification_codes (channel, target)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique ON users (email) WHERE email IS NOT NULL AND email != ''")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique ON users (phone) WHERE phone IS NOT NULL AND phone != ''")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                user_id INTEGER,
                username TEXT,
                action TEXT NOT NULL,
                ip TEXT,
                method TEXT,
                path TEXT,
                request_id TEXT,
                idempotency_key TEXT,
                detail_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_created_at ON audit_events (created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_user_id ON audit_events (user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_action ON audit_events (action)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                idem_key TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                response_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_idempotency_unique ON idempotency_responses (user_id, method, path, idem_key)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_idempotency_expires_at ON idempotency_responses (expires_at)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS login_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                fail_count INTEGER NOT NULL DEFAULT 0,
                first_failed_at TEXT,
                last_failed_at TEXT,
                locked_until TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_login_failures_locked_until ON login_failures (locked_until)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS membership_plans (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                price_cny REAL NOT NULL,
                currency TEXT NOT NULL,
                duration_days INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_membership_plans_active ON membership_plans (active)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                plan_code TEXT NOT NULL,
                amount_cny REAL NOT NULL,
                currency TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                paid_at TEXT,
                provider_txn_id TEXT,
                raw_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_orders_user_id ON payment_orders (user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_orders_status ON payment_orders (status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payment_orders_created_at ON payment_orders (created_at)")

        plans_count = conn.execute("SELECT COUNT(*) AS c FROM membership_plans").fetchone()
        if not plans_count or int(plans_count["c"] or 0) == 0:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT OR IGNORE INTO membership_plans (code, name, price_cny, currency, duration_days, active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                ("member_month", "会员（月）", 99.0, "CNY", 30, now_iso),
            )
            conn.execute(
                "INSERT OR IGNORE INTO membership_plans (code, name, price_cny, currency, duration_days, active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                ("member_year", "会员（年）", 999.0, "CNY", 365, now_iso),
            )
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
            "SELECT id, username, password_hash, created_at, email, phone, email_verified, phone_verified, membership_level, membership_expires_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    return row


def get_user_by_email(email: str):
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at, email, phone, email_verified, phone_verified, membership_level, membership_expires_at FROM users WHERE email = ?",
            (email,),
        ).fetchone()
    return row


def get_user_by_phone(phone: str):
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at, email, phone, email_verified, phone_verified, membership_level, membership_expires_at FROM users WHERE phone = ?",
            (phone,),
        ).fetchone()
    return row


def get_user_by_identifier(identifier: str):
    raw = (identifier or "").strip()
    if not raw:
        return None
    if "@" in raw:
        return get_user_by_email(normalize_email(raw))
    if re.fullmatch(r"[\d\+\-\s\(\)]+", raw or ""):
        return get_user_by_phone(normalize_phone(raw))
    return get_user_by_username(validate_username_or_raise(raw))


def get_user_by_id(user_id: int):
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT id, username, created_at, email, phone, email_verified, phone_verified, membership_level, membership_expires_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return row


def create_verification_code(channel: str, target: str) -> str:
    code = generate_numeric_code(6)
    now = time.time()
    expires_at = now + VERIFY_CODE_TTL_SECONDS
    created_at = datetime.now(timezone.utc).isoformat()
    with get_db_conn() as conn:
        conn.execute(
            "INSERT INTO verification_codes (channel, target, code_hash, expires_at, created_at, used_at, attempts) VALUES (?, ?, ?, ?, ?, NULL, 0)",
            (channel, target, hash_verify_code(code), str(expires_at), created_at),
        )
        conn.commit()
    return code


def consume_verification_code(channel: str, target: str, code: str) -> bool:
    now = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    supplied = hash_verify_code(code)
    with get_db_conn() as conn:
        row = conn.execute(
            """
            SELECT id, code_hash, expires_at, attempts
            FROM verification_codes
            WHERE channel = ? AND target = ? AND used_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (channel, target),
        ).fetchone()
        if not row:
            return False
        try:
            expires_at = float(row["expires_at"])
        except Exception:
            return False
        if now > expires_at:
            conn.execute("UPDATE verification_codes SET used_at = ? WHERE id = ?", (now_iso, row["id"]))
            conn.commit()
            return False
        attempts = int(row["attempts"] or 0) + 1
        if attempts > VERIFY_CODE_MAX_ATTEMPTS:
            conn.execute("UPDATE verification_codes SET attempts = ?, used_at = ? WHERE id = ?", (attempts, now_iso, row["id"]))
            conn.commit()
            return False
        if supplied != str(row["code_hash"] or ""):
            conn.execute("UPDATE verification_codes SET attempts = ? WHERE id = ?", (attempts, row["id"]))
            conn.commit()
            return False
        conn.execute("UPDATE verification_codes SET attempts = ?, used_at = ? WHERE id = ?", (attempts, now_iso, row["id"]))
        conn.commit()
        return True


def create_user(username: str, password: str, email: Optional[str], phone: Optional[str], email_verified: int, phone_verified: int):
    # Hard guard: do not rely only on DB unique constraints, block duplicates at application layer too.
    if get_user_by_username(username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    if email and get_user_by_email(email):
        raise HTTPException(status_code=409, detail="邮箱已存在")
    if phone and get_user_by_phone(phone):
        raise HTTPException(status_code=409, detail="手机号已存在")

    password_hash = get_password_hash(password)
    created_at = datetime.now(timezone.utc).isoformat()
    materials_json = json.dumps(DEFAULT_MATERIALS)
    colors_json = json.dumps(DEFAULT_COLORS)
    pricing_json = json.dumps(DEFAULT_PRICING_CONFIG)
    membership_level = "free"
    membership_expires_at = None
    accepted_at = datetime.now(timezone.utc).isoformat()
    try:
        with get_db_conn() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at, materials, colors, pricing_config, email, phone, email_verified, phone_verified, membership_level, membership_expires_at, terms_accepted_at, privacy_accepted_at, terms_version, privacy_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    username,
                    password_hash,
                    created_at,
                    materials_json,
                    colors_json,
                    pricing_json,
                    email,
                    phone,
                    email_verified,
                    phone_verified,
                    membership_level,
                    membership_expires_at,
                    accepted_at,
                    accepted_at,
                    TERMS_VERSION,
                    PRIVACY_VERSION,
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        if get_user_by_username(username):
            raise HTTPException(status_code=409, detail="用户名已存在")
        if email and get_user_by_email(email):
            raise HTTPException(status_code=409, detail="邮箱已存在")
        if phone and get_user_by_phone(phone):
            raise HTTPException(status_code=409, detail="手机号已存在")
        raise HTTPException(status_code=409, detail="注册信息已存在")

    user = get_user_by_username(username)
    return user


def authenticate_user(identifier: str, password: str):
    user = get_user_by_identifier(identifier)
    if not user:
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="账号或密码错误")
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


def _login_failure_key_hash(identifier: str) -> str:
    raw = (identifier or "").strip().lower()
    return hashlib.sha256((raw + "|" + JWT_SECRET_KEY).encode("utf-8")).hexdigest()


def is_login_locked(identifier: str) -> tuple[bool, int]:
    key_hash = _login_failure_key_hash(identifier)
    now = time.time()
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT locked_until FROM login_failures WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
    if not row:
        return False, 0
    try:
        locked_until = float(row["locked_until"] or 0)
    except Exception:
        locked_until = 0.0
    if locked_until and now < locked_until:
        return True, max(1, int(locked_until - now))
    return False, 0


def clear_login_failures(identifier: str) -> None:
    key_hash = _login_failure_key_hash(identifier)
    with get_db_conn() as conn:
        conn.execute("DELETE FROM login_failures WHERE key_hash = ?", (key_hash,))
        conn.commit()


def record_login_failure(identifier: str) -> tuple[bool, int]:
    key_hash = _login_failure_key_hash(identifier)
    now = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    window_start = now - LOGIN_FAILED_WINDOW_SECONDS
    with get_db_conn() as conn:
        row = conn.execute(
            "SELECT fail_count, first_failed_at, locked_until FROM login_failures WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
        if row:
            try:
                first_failed_at = float(row["first_failed_at"] or 0)
            except Exception:
                first_failed_at = 0.0
            try:
                locked_until = float(row["locked_until"] or 0)
            except Exception:
                locked_until = 0.0
            if locked_until and now < locked_until:
                return True, max(1, int(locked_until - now))
            if not first_failed_at or first_failed_at < window_start:
                fail_count = 1
                first_failed_at = now
            else:
                fail_count = int(row["fail_count"] or 0) + 1
            locked = False
            remaining = 0
            new_locked_until = 0.0
            if fail_count >= LOGIN_FAILED_MAX_ATTEMPTS:
                locked = True
                new_locked_until = now + LOGIN_LOCK_SECONDS
                remaining = int(LOGIN_LOCK_SECONDS)
            conn.execute(
                """
                UPDATE login_failures
                SET fail_count = ?, first_failed_at = ?, last_failed_at = ?, locked_until = ?
                WHERE key_hash = ?
                """,
                (int(fail_count), str(first_failed_at), str(now), str(new_locked_until), key_hash),
            )
            conn.commit()
            return locked, remaining
        else:
            conn.execute(
                """
                INSERT INTO login_failures (created_at, key_hash, fail_count, first_failed_at, last_failed_at, locked_until)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (now_iso, key_hash, 1, str(now), str(now), "0"),
            )
            conn.commit()
            return False, 0


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


@app.get("/api/auth/captcha")
def get_captcha(request: Request):
    text = generate_captcha_text(CAPTCHA_LENGTH)
    captcha_id = secrets.token_urlsafe(24)
    expires_at = time.time() + CAPTCHA_TTL_SECONDS
    ct, img = captcha_image_bytes(text)
    captcha_store.put(captcha_id=captcha_id, answer=text, expires_at=expires_at, image_bytes=img, image_content_type=ct)
    return {"captcha_id": captcha_id, "image_url": f"/api/auth/captcha/image/{captcha_id}", "expires_in": CAPTCHA_TTL_SECONDS}


@app.get("/api/auth/captcha/image/{captcha_id}")
def get_captcha_image(captcha_id: str):
    raw, ct = captcha_store.get_image(str(captcha_id).strip())
    if not raw or not ct:
        raise HTTPException(status_code=404, detail="验证码已过期")
    return Response(content=raw, media_type=ct, headers={"Cache-Control": "no-store"})


class VerifySendRequest(BaseModel):
    channel: str = Field(..., min_length=4, max_length=10)
    target: str = Field(..., min_length=3, max_length=254)


class VerifyConfirmRequest(BaseModel):
    channel: str = Field(..., min_length=4, max_length=10)
    target: str = Field(..., min_length=3, max_length=254)
    code: str = Field(..., min_length=4, max_length=10)


class RegisterCheckRequest(BaseModel):
    field: str = Field(..., min_length=5, max_length=20)
    value: str = Field(..., min_length=1, max_length=254)


def normalize_verify_target(channel: str, target: str) -> str:
    ch = (channel or "").strip().lower()
    if ch == "email":
        return normalize_email(target)
    if ch == "phone":
        return normalize_phone(target)
    raise HTTPException(status_code=400, detail="不支持的验证类型")


@app.post("/api/auth/register/check")
def check_register_exists(payload: RegisterCheckRequest):
    field = (payload.field or "").strip().lower()
    raw_value = (payload.value or "").strip()
    if field == "username":
        try:
            value = validate_username_or_raise(raw_value)
        except HTTPException as e:
            return {"field": field, "valid": False, "exists": False, "message": e.detail}
        exists = get_user_by_username(value) is not None
        return {"field": field, "valid": True, "exists": exists}
    if field == "email":
        try:
            value = normalize_email(raw_value)
        except HTTPException as e:
            return {"field": field, "valid": False, "exists": False, "message": e.detail}
        exists = get_user_by_email(value) is not None
        return {"field": field, "valid": True, "exists": exists}
    if field == "phone":
        try:
            value = normalize_phone(raw_value)
        except HTTPException as e:
            return {"field": field, "valid": False, "exists": False, "message": e.detail}
        exists = get_user_by_phone(value) is not None
        return {"field": field, "valid": True, "exists": exists}
    raise HTTPException(status_code=400, detail="不支持的检查字段")


@app.post("/api/auth/verify/send")
def send_verify_code(payload: VerifySendRequest, request: Request):
    channel = (payload.channel or "").strip().lower()
    target = normalize_verify_target(channel, payload.target)
    client_ip = get_client_ip(request)
    if not rate_limiter.is_allowed(f"verify_send_ip_cooldown:{client_ip}", 1, window_seconds=VERIFY_SEND_COOLDOWN_SECONDS):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    if not rate_limiter.is_allowed(f"verify_send_target_cooldown:{channel}:{target}", 1, window_seconds=VERIFY_SEND_COOLDOWN_SECONDS):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    code = create_verification_code(channel=channel, target=target)
    resp = {"status": "sent", "channel": channel, "target": target, "expires_in": VERIFY_CODE_TTL_SECONDS}
    if not IS_PRODUCTION:
        resp["dev_code"] = code
    masked = mask_email(target) if channel == "email" else mask_phone(target) if channel == "phone" else None
    write_audit_event(
        action="auth.verify.send",
        request=request,
        user=None,
        detail={"channel": channel, "target_masked": masked},
    )
    return resp


@app.post("/api/auth/verify/confirm")
def confirm_verify_code(payload: VerifyConfirmRequest, request: Request):
    channel = (payload.channel or "").strip().lower()
    target = normalize_verify_target(channel, payload.target)
    ok = consume_verification_code(channel=channel, target=target, code=payload.code)
    if not ok:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    masked = mask_email(target) if channel == "email" else mask_phone(target) if channel == "phone" else None
    write_audit_event(
        action="auth.verify.confirm",
        request=request,
        user=None,
        detail={"channel": channel, "target_masked": masked},
    )
    return {"status": "verified", "channel": channel, "target": target}


@app.post("/api/auth/register")
def register(payload: RegisterRequest, request: Request):
    verify_captcha_or_raise(payload.captcha_id, payload.captcha_code)
    require_legal_acceptance_or_raise(payload.accept_terms, payload.accept_privacy)
    username = validate_username_or_raise(payload.username)
    password = validate_password_or_raise(payload.password)
    channel = (payload.register_channel or "").strip().lower()
    email = None
    phone = None
    email_verified = 0
    phone_verified = 0
    if get_user_by_username(username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    if channel == "email":
        if not payload.email or not payload.email_code:
            raise HTTPException(status_code=400, detail="邮箱注册需要填写邮箱与验证码")
        email = normalize_email(payload.email)
        if get_user_by_email(email):
            raise HTTPException(status_code=409, detail="邮箱已存在")
        if not consume_verification_code("email", email, payload.email_code):
            raise HTTPException(status_code=400, detail="邮箱验证码错误或已过期")
        email_verified = 1
    elif channel == "phone":
        if not payload.phone or not payload.phone_code:
            raise HTTPException(status_code=400, detail="手机注册需要填写手机号与验证码")
        phone = normalize_phone(payload.phone)
        if get_user_by_phone(phone):
            raise HTTPException(status_code=409, detail="手机号已存在")
        if not consume_verification_code("phone", phone, payload.phone_code):
            raise HTTPException(status_code=400, detail="手机验证码错误或已过期")
        phone_verified = 1
    else:
        raise HTTPException(status_code=400, detail="不支持的注册方式")

    user = create_user(username, password, email=email, phone=phone, email_verified=email_verified, phone_verified=phone_verified)
    write_audit_event(
        action="auth.register",
        request=request,
        user=user,
        detail={
            "register_channel": channel,
            "email_masked": mask_email(email) if email else None,
            "phone_masked": mask_phone(phone) if phone else None,
            "terms_version": TERMS_VERSION,
            "privacy_version": PRIVACY_VERSION,
        },
    )
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
def login(payload: LoginRequest, request: Request):
    verify_captcha_or_raise(payload.captcha_id, payload.captcha_code)
    require_legal_acceptance_or_raise(payload.accept_terms, payload.accept_privacy)
    password = validate_password_or_raise(payload.password)
    locked, remaining = is_login_locked(payload.identifier)
    if locked:
        write_audit_event(
            action="auth.login_locked",
            request=request,
            user=None,
            detail={"key_hash_prefix": _login_failure_key_hash(payload.identifier)[:12], "retry_after_s": remaining},
        )
        raise HTTPException(
            status_code=429,
            detail="登录失败次数过多，请稍后再试",
            headers={"Retry-After": str(remaining)},
        )
    try:
        user = authenticate_user(payload.identifier, password)
    except HTTPException:
        locked2, remaining2 = record_login_failure(payload.identifier)
        write_audit_event(
            action="auth.login_failed",
            request=request,
            user=None,
            detail={"identifier": (payload.identifier or "").strip()[:120]},
        )
        if locked2:
            write_audit_event(
                action="auth.login_locked",
                request=request,
                user=None,
                detail={"key_hash_prefix": _login_failure_key_hash(payload.identifier)[:12], "retry_after_s": remaining2},
            )
            raise HTTPException(
                status_code=429,
                detail="登录失败次数过多，请稍后再试",
                headers={"Retry-After": str(remaining2)},
            )
        raise

    clear_login_failures(payload.identifier)
    record_legal_acceptance(int(user["id"]))
    write_audit_event(
        action="auth.login",
        request=request,
        user=user,
        detail={
            "identifier": (payload.identifier or "").strip()[:120],
            "terms_version": TERMS_VERSION,
            "privacy_version": PRIVACY_VERSION,
        },
    )
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
    name: str = Field(..., min_length=1, max_length=40)
    density: float = Field(..., gt=0, le=10)
    price_per_kg: float = Field(..., ge=0, le=100000)
    colors: List[str] = Field(default_factory=list, max_items=30)

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
    materials: List[MaterialItem] = Field(..., min_items=1, max_items=100)
    colors: Optional[List[str]] = Field(default=None, max_items=100)
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
def update_user_settings(payload: UserSettingsUpdate, request: Request, current_user=Depends(get_current_user)):
    seen_material_names = set()
    for item in payload.materials:
        normalized_name = item.name.strip()
        if not normalized_name:
            raise HTTPException(status_code=400, detail="材料名称不能为空")
        name_key = normalized_name.lower()
        if name_key in seen_material_names:
            raise HTTPException(status_code=400, detail=f"材料名称重复：{normalized_name}")
        seen_material_names.add(name_key)

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
    write_audit_event(
        action="user.settings.update",
        request=request,
        user=current_user,
        detail={"materials_count": len(payload.materials), "has_pricing_config": payload.pricing_config is not None},
    )
    return {"status": "success"}

@app.get("/api/auth/me")
def auth_me(current_user=Depends(get_current_user)):
    level, expires_ts = get_membership_effective(current_user)
    return {
        "id": current_user["id"],
        "username": current_user["username"],
        "created_at": current_user["created_at"],
        "email": current_user["email"],
        "phone": current_user["phone"],
        "email_verified": bool(current_user["email_verified"] or 0),
        "phone_verified": bool(current_user["phone_verified"] or 0),
        "is_admin": is_admin_user(current_user),
        "membership_level": level,
        "membership_expires_at": expires_ts,
        "is_member": level == "member",
    }


@app.get("/api/admin/users")
def admin_list_users(
    q: str = "",
    limit: int = 50,
    offset: int = 0,
    current_user=Depends(get_current_user),
):
    require_admin(current_user)
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))
    keyword = f"%{(q or '').strip()}%"
    with get_db_conn() as conn:
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM users
            WHERE username LIKE ? OR IFNULL(email, '') LIKE ? OR IFNULL(phone, '') LIKE ?
            """,
            (keyword, keyword, keyword),
        ).fetchone()
        rows = conn.execute(
            """
            SELECT id, username, email, phone, created_at, email_verified, phone_verified, membership_level, membership_expires_at
            FROM users
            WHERE username LIKE ? OR IFNULL(email, '') LIKE ? OR IFNULL(phone, '') LIKE ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (keyword, keyword, keyword, safe_limit, safe_offset),
        ).fetchall()
    items = []
    for row in rows:
        items.append(
            {
                "id": row["id"],
                "username": row["username"],
                "email_masked": mask_email(row["email"]),
                "phone_masked": mask_phone(row["phone"]),
                "email_verified": bool(row["email_verified"] or 0),
                "phone_verified": bool(row["phone_verified"] or 0),
                "membership_level": (str(row["membership_level"] or "free").strip().lower() or "free"),
                "membership_expires_at": row["membership_expires_at"],
                "created_at": row["created_at"],
            }
        )
    total = int(total_row["c"] or 0) if total_row else 0
    return {"total": total, "limit": safe_limit, "offset": safe_offset, "items": items}


class AdminMembershipUpdateRequest(BaseModel):
    membership_level: str = Field(..., min_length=1, max_length=20)


@app.post("/api/admin/users/{user_id}/membership")
def admin_update_user_membership(
    user_id: int,
    payload: AdminMembershipUpdateRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    require_admin(current_user)
    safe_id = int(user_id)
    level = (payload.membership_level or "").strip().lower()
    if level not in {"free", "member"}:
        raise HTTPException(status_code=400, detail="membership_level 仅支持 free / member")
    with get_db_conn() as conn:
        row = conn.execute("SELECT id, username, membership_level FROM users WHERE id = ?", (safe_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在")
        conn.execute("UPDATE users SET membership_level = ? WHERE id = ?", (level, safe_id))
        conn.commit()
    write_audit_event(
        action="admin.user.membership.update",
        request=request,
        user=current_user,
        detail={
            "target_user_id": safe_id,
            "target_username": row["username"],
            "membership_level": level,
        },
    )
    return {"status": "ok", "user_id": safe_id, "membership_level": level}


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


def _get_plan_or_404(plan_code: str) -> sqlite3.Row:
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


class BillingCheckoutRequest(BaseModel):
    plan_code: str = Field(..., min_length=2, max_length=40)


class BillingMockCompleteRequest(BaseModel):
    order_no: str = Field(..., min_length=8, max_length=80)


@app.get("/api/billing/plans")
def billing_plans():
    return {"items": _get_active_membership_plans()}


@app.post("/api/billing/checkout")
def billing_checkout(payload: BillingCheckoutRequest, request: Request, current_user=Depends(get_current_user)):
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


@app.get("/api/billing/orders")
def billing_orders(limit: int = 20, offset: int = 0, current_user=Depends(get_current_user)):
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


@app.post("/api/billing/mock/complete")
def billing_mock_complete(payload: BillingMockCompleteRequest, request: Request, current_user=Depends(get_current_user)):
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


@app.post("/api/billing/webhook")
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


@app.get("/api/admin/audit")
def admin_list_audit(
    q: str = "",
    action: str = "",
    username: str = "",
    limit: int = 100,
    offset: int = 0,
    current_user=Depends(get_current_user),
):
    require_admin(current_user)
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))
    keyword = f"%{(q or '').strip()}%"
    action_kw = f"%{(action or '').strip()}%"
    user_kw = f"%{(username or '').strip()}%"
    with get_db_conn() as conn:
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM audit_events
            WHERE (action LIKE ?)
              AND (IFNULL(username, '') LIKE ?)
              AND (? = '%%' OR action LIKE ? OR IFNULL(username, '') LIKE ? OR IFNULL(ip, '') LIKE ? OR IFNULL(method, '') LIKE ? OR IFNULL(path, '') LIKE ? OR IFNULL(request_id, '') LIKE ?)
            """,
            (action_kw, user_kw, keyword, keyword, keyword, keyword, keyword, keyword, keyword),
        ).fetchone()
        rows = conn.execute(
            """
            SELECT id, created_at, user_id, username, action, ip, method, path, request_id, idempotency_key, detail_json
            FROM audit_events
            WHERE (action LIKE ?)
              AND (IFNULL(username, '') LIKE ?)
              AND (? = '%%' OR action LIKE ? OR IFNULL(username, '') LIKE ? OR IFNULL(ip, '') LIKE ? OR IFNULL(method, '') LIKE ? OR IFNULL(path, '') LIKE ? OR IFNULL(request_id, '') LIKE ?)
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (action_kw, user_kw, keyword, keyword, keyword, keyword, keyword, keyword, keyword, safe_limit, safe_offset),
        ).fetchall()
    items = []
    for row in rows:
        detail = {}
        try:
            detail = json.loads(row["detail_json"] or "{}")
            if not isinstance(detail, dict):
                detail = {"_": detail}
        except Exception:
            detail = {}
        items.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "user_id": row["user_id"],
                "username": row["username"],
                "action": row["action"],
                "ip": row["ip"],
                "method": row["method"],
                "path": row["path"],
                "request_id": row["request_id"],
                "idempotency_key": row["idempotency_key"],
                "detail": detail,
            }
        )
    total = int(total_row["c"] or 0) if total_row else 0
    return {"total": total, "limit": safe_limit, "offset": safe_offset, "items": items}


@app.get("/api/admin/metrics")
def admin_metrics(current_user=Depends(get_current_user)):
    require_admin(current_user)
    return metrics.snapshot()


@app.post("/api/admin/maintenance/cleanup")
def admin_cleanup(request: Request, current_user=Depends(get_current_user)):
    require_admin(current_user)
    now = time.time()
    cutoff_audit = datetime.now(timezone.utc) - timedelta(days=max(1, AUDIT_RETENTION_DAYS))
    cutoff_audit_iso = cutoff_audit.isoformat()
    deleted = {"verification_codes": 0, "idempotency_responses": 0, "login_failures": 0, "audit_events": 0}
    with get_db_conn() as conn:
        cur = conn.execute(
            "DELETE FROM verification_codes WHERE used_at IS NOT NULL OR CAST(expires_at AS REAL) < ?",
            (float(now),),
        )
        deleted["verification_codes"] = int(cur.rowcount or 0)
        cur = conn.execute(
            "DELETE FROM idempotency_responses WHERE CAST(expires_at AS REAL) < ?",
            (float(now),),
        )
        deleted["idempotency_responses"] = int(cur.rowcount or 0)
        cur = conn.execute(
            """
            DELETE FROM login_failures
            WHERE (CAST(locked_until AS REAL) < ?)
              AND (CAST(last_failed_at AS REAL) < ?)
            """,
            (float(now), float(now - max(3600, LOGIN_FAILED_WINDOW_SECONDS))),
        )
        deleted["login_failures"] = int(cur.rowcount or 0)
        cur = conn.execute("DELETE FROM audit_events WHERE created_at < ?", (cutoff_audit_iso,))
        deleted["audit_events"] = int(cur.rowcount or 0)
        conn.commit()
    write_audit_event(action="admin.maintenance.cleanup", request=request, user=current_user, detail={"deleted": deleted})
    return {"status": "ok", "deleted": deleted, "audit_retention_days": AUDIT_RETENTION_DAYS}

@app.post("/api/quote")
async def get_quote(
    request: Request,
    files: List[UploadFile] = File(...),
    material: str = Form("PLA", min_length=1, max_length=40),
    layer_height: float = Form(0.2, ge=0.05, le=1.0),
    infill: int = Form(20, ge=0, le=100),
    quantity: int = Form(1, ge=1, le=5000),
    color: str = Form("White", min_length=1, max_length=40),
    current_user=Depends(get_current_user),
):
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

    results = []
    for file in files:
        result = await process_single_file(file, material, layer_height, infill, quantity, color, user_materials, pricing_config)
        results.append(result)

    success_items = [item for item in results if item["status"] == "success"]
    failed_items = [item for item in results if item["status"] == "failed"]

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
    return payload


class FormulaValidateRequest(BaseModel):
    unit_cost_formula: str = Field(..., min_length=1, max_length=800)
    total_cost_formula: str = Field(..., min_length=1, max_length=800)


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


@app.get("/register", response_class=HTMLResponse)
async def register_page():
    with open("static/register.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/legal/terms", response_class=HTMLResponse)
def legal_terms():
    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>用户协议</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen p-4 lg:p-6">
  <div class="max-w-3xl mx-auto bg-white rounded-xl shadow-md overflow-hidden">
    <div class="p-6 space-y-4">
      <div class="flex items-start justify-between gap-4">
        <div>
          <div class="uppercase tracking-wide text-sm text-indigo-500 font-semibold mb-1">Legal</div>
          <h2 class="text-2xl font-bold text-gray-900">用户协议</h2>
          <p class="text-xs text-gray-500 mt-1">版本：{TERMS_VERSION}</p>
        </div>
        <a href="/" class="text-sm px-3 py-1.5 border border-gray-300 text-gray-700 rounded-md hover:bg-gray-50">返回首页</a>
      </div>
      <div class="text-sm text-gray-700 leading-relaxed space-y-3">
        <p>本页面为示例协议文本占位。上线前请替换为你们正式的《用户协议》内容（含服务范围、免责条款、费用/退款、账号安全、争议解决等）。</p>
        <p>使用本系统即表示你已阅读、理解并同意本协议的全部条款。</p>
      </div>
    </div>
  </div>
</body>
</html>
"""


@app.get("/legal/privacy", response_class=HTMLResponse)
def legal_privacy():
    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>隐私政策</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen p-4 lg:p-6">
  <div class="max-w-3xl mx-auto bg-white rounded-xl shadow-md overflow-hidden">
    <div class="p-6 space-y-4">
      <div class="flex items-start justify-between gap-4">
        <div>
          <div class="uppercase tracking-wide text-sm text-indigo-500 font-semibold mb-1">Legal</div>
          <h2 class="text-2xl font-bold text-gray-900">隐私政策</h2>
          <p class="text-xs text-gray-500 mt-1">版本：{PRIVACY_VERSION}</p>
        </div>
        <a href="/" class="text-sm px-3 py-1.5 border border-gray-300 text-gray-700 rounded-md hover:bg-gray-50">返回首页</a>
      </div>
      <div class="text-sm text-gray-700 leading-relaxed space-y-3">
        <p>本页面为示例隐私政策文本占位。上线前请替换为你们正式的《隐私政策》内容（含收集信息类型、用途、共享/委托处理、保存期限、用户权利、未成年人条款等）。</p>
        <p>我们会在你同意后处理必要的账号信息用于登录、报价与会员服务。</p>
      </div>
    </div>
  </div>
</body>
</html>
"""


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page():
    with open("static/admin_users.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/pay/mock", response_class=HTMLResponse)
def pay_mock(order_no: str = ""):
    safe_order_no = (order_no or "").strip()[:80]
    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>模拟支付</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen p-4 lg:p-6">
  <div class="max-w-lg mx-auto bg-white rounded-xl shadow-md overflow-hidden">
    <div class="p-6 space-y-4">
      <div>
        <div class="uppercase tracking-wide text-sm text-indigo-500 font-semibold mb-1">Mock Payment</div>
        <h2 class="text-xl font-bold text-gray-900">会员充值（模拟支付）</h2>
        <p class="text-xs text-gray-500 mt-1">订单号：<span class="font-mono">{safe_order_no or "-"}</span></p>
      </div>
      <div class="text-sm text-gray-700 leading-relaxed">
        这是开发用的模拟支付页。点击“确认支付”后，系统会校验订单并将你的账号升级为会员。
      </div>
      <p id="msg" class="hidden text-xs"></p>
      <div class="flex gap-2">
        <button id="pay-btn" type="button" class="flex-1 py-2 px-3 rounded-md bg-indigo-600 text-white text-sm hover:bg-indigo-700">确认支付</button>
        <a href="/" class="py-2 px-3 rounded-md border border-gray-300 text-gray-700 text-sm hover:bg-gray-50">返回首页</a>
      </div>
    </div>
  </div>

  <script type="module">
    const TOKEN_STORAGE_KEY = "demo_access_token_v1";
    const authToken = localStorage.getItem(TOKEN_STORAGE_KEY) || "";
    const orderNo = {json.dumps(safe_order_no)};
    const msg = document.getElementById('msg');
    const payBtn = document.getElementById('pay-btn');

    function showMsg(text, ok = false) {{
      msg.textContent = text;
      msg.className = ok ? "text-xs text-green-600" : "text-xs text-red-600";
      msg.classList.remove('hidden');
    }}

    async function doPay() {{
      if (!orderNo) {{
        showMsg('订单号缺失', false);
        return;
      }}
      if (!authToken) {{
        showMsg('未登录，请先回到首页登录后再支付', false);
        return;
      }}
      payBtn.disabled = true;
      payBtn.textContent = '处理中...';
      try {{
        const resp = await fetch('/api/billing/mock/complete', {{
          method: 'POST',
          headers: {{
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${{authToken}}`
          }},
          body: JSON.stringify({{ order_no: orderNo }})
        }});
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '支付失败');
        showMsg(`支付成功，会员已生效。到期时间：${{data.membership_expires_at || '永久'}}`, true);
        payBtn.textContent = '已支付';
      }} catch (e) {{
        showMsg(e.message || '支付失败', false);
        payBtn.disabled = false;
        payBtn.textContent = '确认支付';
      }}
    }}

    payBtn.addEventListener('click', doPay);
  </script>
</body>
</html>
"""


@app.get("/healthz")
def healthz():
    return {"status": "ok", "env": APP_ENV}


@app.get("/readyz")
def readyz():
    try:
        with get_db_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "db": "ok", "env": APP_ENV}
    except Exception:
        raise HTTPException(status_code=503, detail="服务未就绪")
