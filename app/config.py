"""Application configuration – constants, env vars, defaults."""

import os
import re

EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")
PHONE_PATTERN = re.compile(r"^\+?[0-9]{7,15}$")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 100

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
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "mock").strip().lower() or "mock"
PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "").strip()
if not PAYMENT_WEBHOOK_SECRET:
    if IS_PRODUCTION:
        raise RuntimeError("生产环境必须设置 PAYMENT_WEBHOOK_SECRET")
    PAYMENT_WEBHOOK_SECRET = "dev-only-insecure-secret-change-me"

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", "").strip()
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "1").strip().lower() in {"1", "true", "yes", "y", "on"}
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "0").strip().lower() in {"1", "true", "yes", "y", "on"}

TERMS_VERSION = os.getenv("TERMS_VERSION", "v1").strip() or "v1"
PRIVACY_VERSION = os.getenv("PRIVACY_VERSION", "v1").strip() or "v1"

SUPPORTED_EXTENSIONS = {".stl", ".stp", ".step", ".obj", ".3mf"}
MAX_FILES_PER_REQUEST = 20
MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024
UPLOADS_DIR = os.getenv("UPLOADS_DIR", "uploads").strip() or "uploads"
OUTPUTS_DIR = os.getenv("OUTPUTS_DIR", "outputs").strip() or "outputs"
DB_PATH = os.getenv("DB_PATH", "app.db").strip() or "app.db"

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
QUOTE_CONCURRENCY = int(os.getenv("QUOTE_CONCURRENCY", "4"))

ADMIN_USERNAMES = {
    x.strip().lower()
    for x in os.getenv("ADMIN_USERNAMES", "admin").split(",")
    if x.strip()
}

SYSTEM_SLICER_PRESET_ID = 0

DEFAULT_COLORS = ["White", "Black", "Gray", "Red", "Blue"]
DEFAULT_MATERIALS = [
    {"name": "PLA", "density": 1.24, "price_per_kg": 200.0, "colors": DEFAULT_COLORS},
    {"name": "ABS", "density": 1.04, "price_per_kg": 250.0, "colors": DEFAULT_COLORS},
    {"name": "Resin", "density": 1.11, "price_per_kg": 800.0, "colors": DEFAULT_COLORS},
]
DEFAULT_UNIT_COST_FORMULA = "((effective_weight_g * (price_per_kg / 1000.0)) + (unit_time_h * machine_hourly_rate_cny) + post_process_fee_per_part_cny) * difficulty_multiplier + support_cost_per_part_cny"
DEFAULT_TOTAL_COST_FORMULA = "max((unit_cost_cny * quantity) + setup_fee_cny, min_job_fee_cny)"
DEFAULT_PRICING_CONFIG = {
    "machine_hourly_rate_cny": 15.0,
    "setup_fee_cny": 0.0,
    "min_job_fee_cny": 0.0,
    "material_waste_percent": 5.0,
    "support_percent_of_model": 0.0,
    "post_process_fee_per_part_cny": 0.0,
    "difficulty_coefficient": 0.25,
    "difficulty_ratio_low": 0.8,
    "difficulty_ratio_high": 4.0,
    "use_bambu": 0,
    "use_prusaslicer": 0,
    "prusa_time_correction": 0.44,
    "bambu_support_mode": "diff",
    "support_price_per_g": 0.0,
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

APP_DEFAULTS_KEY = "global_defaults_v1"
